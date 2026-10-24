"""T3 — ASR español para el recepcionista human-in-the-loop (Club N).

Modelo: faster-whisper large-v3-turbo (int8, GPU) — licencia MIT.
Endpointing: silero-vad.

Contrato (HRI_RUNTIME_SPEC.md):
    class Asr: def transcribe(self, wav_path_or_pcm, sr) -> dict
        # {"text","t_start","t_end","latency_s"}

Uso:
    from x2_ultra.hri_runtime.asr import Asr, StreamingAsr
    asr = Asr()                       # carga modelo (lazy, ~1.3 GB VRAM int8)
    out = asr.transcribe("w.wav")     # o numpy float32 PCM + sr
    print(out["text"], out["latency_s"])

    # Streaming con VAD: detectar fin de utterance -> transcript
    stream = StreamingAsr(asr)
    for chunk in mic_chunks:          # numpy float32 mono a 16 kHz
        res = stream.feed(chunk)
        if res is not None:
            print("usuario dijo:", res["text"])

Nota venv: este módulo requiere `.venv-asr` (ver asr/requirements.txt).
"""

from __future__ import annotations

import os
import time
from typing import Optional, Union

import numpy as np

SR = 16_000

__all__ = ["Asr", "StreamingAsr", "SR"]

# A bordo (sin internet) HRI_ASR_MODEL apunta al modelo CTranslate2 ya copiado,
# p. ej. models/faster-whisper-large-v3-turbo; sin la variable, igual que antes.
_DEFAULT_MODEL = os.getenv("HRI_ASR_MODEL", "large-v3-turbo")
_DEFAULT_DEVICE = os.getenv("HRI_ASR_DEVICE", "cuda")
_DEFAULT_COMPUTE = os.getenv("HRI_ASR_COMPUTE", "int8")
_DEFAULT_LANG = "es"


def _to_pcm(wav_path_or_pcm, sr: Optional[int] = None) -> np.ndarray:
    """Acepta ruta de archivo de audio o PCM (np.ndarray) y devuelve float32 mono 16 kHz."""
    if isinstance(wav_path_or_pcm, (str, bytes)) or hasattr(wav_path_or_pcm, "__fspath__"):
        import soundfile as sf

        pcm, file_sr = sf.read(str(wav_path_or_pcm), dtype="float32", always_2d=False)
        if pcm.ndim > 1:  # multicanal -> mono
            pcm = pcm.mean(axis=1)
        sr = file_sr
    else:
        pcm = np.asarray(wav_path_or_pcm, dtype=np.float32)
        if pcm.ndim > 1:
            pcm = pcm.mean(axis=1)
        if sr is None:
            sr = SR
    if sr != SR:
        import soundfile as sf  # noqa: F401  (consistencia)
        # resample simple con scipy si está disponible; si no, lineal
        try:
            from scipy.signal import resample_poly

            pcm = resample_poly(pcm, SR, int(sr)).astype(np.float32)
        except ImportError:
            n_out = int(round(len(pcm) * SR / sr))
            idx = np.linspace(0, len(pcm) - 1, n_out)
            pcm = np.interp(idx, np.arange(len(pcm)), pcm).astype(np.float32)
    return pcm


class Asr:
    """Wrapper fino sobre faster-whisper (large-v3-turbo, int8 GPU por defecto)."""

    def __init__(
        self,
        model_size: str = _DEFAULT_MODEL,
        device: str = _DEFAULT_DEVICE,
        compute_type: str = _DEFAULT_COMPUTE,
        language: str = _DEFAULT_LANG,
    ):
        from faster_whisper import WhisperModel

        self.language = language
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        # Warmup: evita que la primera transcripción pague inicialización CUDA/grafo.
        self._model.transcribe(np.zeros(SR, dtype=np.float32), language=language,
                               beam_size=1, without_timestamps=True)

    def transcribe(self, wav_path_or_pcm, sr: Optional[int] = None) -> dict:
        """Transcribe archivo o PCM. Devuelve {"text","t_start","t_end","latency_s"}."""
        t_start = time.perf_counter()
        pcm = _to_pcm(wav_path_or_pcm, sr)
        segments, _info = self._model.transcribe(
            pcm,
            language=self.language,
            beam_size=1,               # greedy: mínima latencia
            condition_on_previous_text=False,
            without_timestamps=True,
            vad_filter=True,           # VAD interno de whisper para silencios
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        t_end = time.perf_counter()
        return {
            "text": text,
            "t_start": t_start,
            "t_end": t_end,
            "latency_s": t_end - t_start,
            "audio_dur_s": len(pcm) / SR,
        }


class StreamingAsr:
    """Endpointing con silero-vad: acumula audio, al cerrar utterance transcribe.

    feed(chunk) -> dict (mismo formato que Asr.transcribe) cuando hay fin de
    utterance; None en caso contrario.
    """

    def __init__(
        self,
        asr: Optional[Asr] = None,
        threshold: float = 0.5,
        min_silence_ms: int = 500,
        min_speech_ms: int = 250,
        speech_pad_ms: int = 200,
    ):
        import torch
        from silero_vad import load_silero_vad

        self._torch = torch
        self.asr = asr or Asr()
        self.vad = load_silero_vad()
        self.threshold = threshold
        self.min_silence_ms = min_silence_ms
        self.min_speech_ms = min_speech_ms
        self.speech_pad_ms = speech_pad_ms
        self.reset()

    def reset(self) -> None:
        self.vad.reset_states()
        self._buf: list[np.ndarray] = []
        self._in_speech = False
        self._speech_start_i = 0   # índice (muestras) del inicio de voz
        self._silence_s = 0.0
        self._n = 0                # muestras totales recibidas

    def feed(self, chunk: np.ndarray, sr: int = SR) -> Optional[dict]:
        """chunk: float32 mono 16 kHz (otros sr se remuestrean)."""
        torch = self._torch
        pcm = _to_pcm(chunk, sr)
        hop = 512  # ventana de silero-vad para 16 kHz (32 ms)
        self._buf.append(pcm)
        offset = 0
        while offset + hop <= len(pcm):
            frame = torch.from_numpy(pcm[offset : offset + hop])
            prob = float(self.vad(frame, SR).item())
            is_speech = prob >= self.threshold
            t_ms = (self._n + offset) * 1000 / SR
            if is_speech:
                self._silence_s = 0.0
                if not self._in_speech:
                    self._in_speech = True
                    self._speech_start_i = max(0, self._n + offset - self.speech_pad_ms * SR // 1000)
            else:
                if self._in_speech:
                    self._silence_s += hop / SR
            offset += hop
        self._n += len(pcm)

        if self._in_speech:
            speech_ms = (self._n - self._speech_start_i) * 1000 / SR
            if self._silence_s * 1000 >= self.min_silence_ms and speech_ms - self._silence_s * 1000 >= self.min_speech_ms:
                audio = np.concatenate(self._buf)[self._speech_start_i :]
                self.reset()
                return self.asr.transcribe(audio, SR)
            # utterance demasiado larga de momento: seguir acumulando
        return None

    def flush(self) -> Optional[dict]:
        """Fuerza transcript de lo acumulado (fin de sesión)."""
        if self._buf:
            audio = np.concatenate(self._buf)
            start = self._speech_start_i if self._in_speech else 0
            audio = audio[start:]
            self.reset()
            if len(audio) > self.min_speech_ms * SR // 1000:
                return self.asr.transcribe(audio, SR)
        self.reset()
        return None
