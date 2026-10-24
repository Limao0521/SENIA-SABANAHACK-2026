"""TTS por frase para el runtime online (T6).

Orden de preferencia (``make_tts``):
1. **Supertonic** (ONNX CPU, el motor de los WAV del reto 2) si el paquete ``supertonic``
   es importable y hay modelo: ``SUPERTONIC_MODEL_DIR`` o ``auto_download``. Prosodia por
   rango desde ``personas.VOICE`` (preset M1–M5 + velocity; spec: VIP = M5 vel 0.92).
2. **espeak-ng** (binario del sistema; mapea velocity → velocidad/pitch).
3. **NullTts**: silencio con duración estimada a ``chars_per_s`` — para tests offline y
   para medir la cadena sin motor de voz instalado (la latencia TTS queda marcada
   ``estimated=True``; NO cuenta como audio real).

Cada motor devuelve ``(pcm float32 mono, sr, meta)``; el app corta el texto en frases
(``split_sentences``) y sintetiza frase a frase para arrancar el audio cuanto antes.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Optional

import numpy as np

from .personas import VOICE

_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
MIN_WORDS_PER_CHUNK = 4
DEFAULT_CHARS_PER_S = 13.5          # habla cortesiosa ~2.3 palabras/s
SUPERTONIC_RTF_EST = 0.35           # coste estimado de síntesis Supertonic CPU (8 pasos)


def split_sentences(text: str) -> list[str]:
    """Frases para TTS incremental: corta por puntuación y agrupa fragmentos cortos."""
    parts = [p.strip() for p in _SENT_SPLIT.split(text or "") if p.strip()]
    out: list[str] = []
    for p in parts:
        if out and len(re.findall(r"\w+", out[-1])) < MIN_WORDS_PER_CHUNK:
            out[-1] = out[-1] + " " + p
        else:
            out.append(p)
    if out and len(re.findall(r"\w+", out[-1])) <= 2 and len(out) > 1:
        out[-2] = out[-2] + " " + out[-1]      # cola mínima ("Gracias.", "Sí.") ⇒ se une
        out.pop()
    return out or ([text.strip()] if (text or "").strip() else [])


def anchor_estimate(sentence: str, trigger_text: str, duration_s: float) -> float:
    """Ancla online del gesto dentro de la frase (s desde inicio del audio).

    Aproximación proporcional del ancla CTC offline (QuartzNet, align/anchor_plan.py):
    posición relativa del ``trigger_text`` en la frase × duración. Con TTS real se podría
    alinear con CTC sobre el WAV generado; la aproximación queda a ≤ ~0.3 s en frases
    de ≤ 20 palabras (ver out_reto2/ONLINE_REPORT.md).
    """
    if not trigger_text or not sentence:
        return 0.0
    s = sentence.lower()
    t = trigger_text.strip(" \t\n¡!¿?,.;:…").lower()
    i = s.find(t)
    if i < 0:
        return 0.0
    frac = (i + len(t) / 2.0) / max(1, len(s))
    return float(np.clip(frac, 0.0, 1.0)) * float(duration_s)


def voice_for(rank: str) -> dict:
    return dict(VOICE.get(rank, VOICE["GUEST"]))


class TtsBase:
    name = "base"
    estimated = True

    def synthesize(self, sentence: str, voice: dict) -> tuple[np.ndarray, int, dict]:
        raise NotImplementedError


class NullTts(TtsBase):
    """Sin motor: silencio de duración proporcional al texto (tests / máquina sin TTS)."""

    name = "null"
    estimated = True

    def __init__(self, chars_per_s: float = DEFAULT_CHARS_PER_S):
        self.chars_per_s = chars_per_s

    def synthesize(self, sentence: str, voice: dict) -> tuple[np.ndarray, int, dict]:
        sr = 16000
        dur = max(0.4, len(sentence or "") / self.chars_per_s)
        t0 = time.perf_counter()
        pcm = np.zeros(int(dur * sr), dtype=np.float32)
        return pcm, sr, {"engine": self.name, "dur_s": dur, "estimated": True,
                         "synth_s": time.perf_counter() - t0,
                         "voice": voice.get("preset"), "velocity": voice.get("velocity")}


class EspeakTts(TtsBase):
    """espeak-ng del sistema (fallback con voz robótica; prosodia por velocity)."""

    name = "espeak-ng"
    estimated = False

    def synthesize(self, sentence: str, voice: dict) -> tuple[np.ndarray, int, dict]:
        t0 = time.perf_counter()
        wpm = int(round(150 * float(voice.get("velocity", 1.0))))
        pitch = {"M1": 40, "M2": 35, "M3": 30, "M5": 25}.get(str(voice.get("preset", "M2")), 35)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            path = f.name
        try:
            subprocess.run(["espeak-ng", "-v", "es", "-s", str(wpm), "-p", str(pitch),
                            "-w", path, sentence], check=True, capture_output=True, timeout=30)
            with wave.open(path) as w:
                sr = w.getframerate()
                n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
                raw = w.readframes(n)
            pcm = (np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0)
            if ch > 1:
                pcm = pcm.reshape(-1, ch).mean(1)
            return np.ascontiguousarray(pcm), sr, {"engine": self.name, "dur_s": n / sr,
                                                   "estimated": False,
                                                   "synth_s": time.perf_counter() - t0,
                                                   "voice": voice.get("preset"),
                                                   "velocity": voice.get("velocity")}
        finally:
            os.unlink(path)


class SupertonicTts(TtsBase):
    """Supertonic (ONNX CPU): el motor de los WAV del reto 2. Preset/velocity por rango
    (``personas.VOICE``); 8 pasos de difusión como en hriego_v2/supertonic_worker.py."""

    name = "supertonic"
    estimated = False

    def __init__(self, model_dir: Optional[str] = None, auto_download: bool = False,
                 total_steps: int = 8, threads: int = 4):
        from supertonic import TTS  # importa aquí: el paquete puede no existir
        model_dir = model_dir or os.environ.get("SUPERTONIC_MODEL_DIR")
        kw = {"auto_download": auto_download, "intra_op_num_threads": threads,
              "inter_op_num_threads": 1}
        if model_dir:
            kw["model_dir"] = Path(model_dir)
        self._tts = TTS(**kw)
        self._total_steps = total_steps
        self._styles: dict[str, object] = {}

    def _style(self, preset: str):
        if preset not in self._styles:
            self._styles[preset] = self._tts.get_voice_style(preset)
        return self._styles[preset]

    def synthesize(self, sentence: str, voice: dict) -> tuple[np.ndarray, int, dict]:
        t0 = time.perf_counter()
        preset = str(voice.get("preset", "M2"))
        speed = float(voice.get("velocity", 1.0))
        audio, duration = self._tts.synthesize(sentence, voice_style=self._style(preset),
                                               total_steps=self._total_steps, speed=speed, lang="es")
        pcm = np.asarray(audio, dtype=np.float32).reshape(-1)
        sr = int(getattr(self._tts, "sample_rate", 44100))
        # numpy ≥ 2 devuelve duration como array de 1 elemento: float() directo falla
        dur = np.asarray(duration, dtype=np.float64).reshape(-1) if duration is not None else np.zeros(0)
        dur_s = float(dur[0]) if dur.size and dur[0] > 0 else len(pcm) / sr
        return pcm, sr, {"engine": self.name, "dur_s": dur_s,
                         "estimated": False, "synth_s": time.perf_counter() - t0,
                         "voice": preset, "velocity": speed}


class TtsPrefetch:
    """Envoltorio de cualquier motor con pre-síntesis en segundo plano (HRI_TTS_PREFETCH=1).

    ``prefetch(frase, voz)`` lanza la síntesis en un hilo mientras el LLM aún genera los
    campos que van después de speech (trigger_text, intent, area). ``synthesize`` devuelve
    el audio ya hecho si la frase y la voz coinciden exactamente; si no (reintento, frase
    reescrita por la KB…), sintetiza como siempre: el resultado es idéntico, solo cambia
    cuándo se calcula."""

    def __init__(self, inner):
        import concurrent.futures as cf
        self.inner = inner
        self.name = getattr(inner, "name", "tts")
        self.estimated = bool(getattr(inner, "estimated", False))
        self._pool = cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-prefetch")
        self._cache: dict[tuple, object] = {}
        self.hits = 0

    @staticmethod
    def _key(sentence: str, voice: dict) -> tuple:
        return (sentence, str(voice.get("preset")), float(voice.get("velocity", 1.0)))

    def prefetch(self, sentence: str, voice: dict) -> None:
        if not sentence:
            return
        key = self._key(sentence, voice)
        if key in self._cache:
            return
        self._cache = {k: v for k, v in list(self._cache.items())[-3:]}   # acotada
        self._cache[key] = self._pool.submit(self.inner.synthesize, sentence, dict(voice))

    def synthesize(self, sentence: str, voice: dict) -> tuple[np.ndarray, int, dict]:
        fut = self._cache.pop(self._key(sentence, voice), None)
        if fut is not None:
            try:
                pcm, sr, meta = fut.result()
                self.hits += 1
                return pcm, sr, dict(meta, prefetch=True)
            except Exception:
                pass                                   # el fallo del hilo no rompe el turno
        return self.inner.synthesize(sentence, voice)


def make_tts(prefer: str = "auto") -> TtsBase:
    """Mejor motor disponible (auto) o el pedido explícitamente (null|espeak|supertonic)."""
    if prefer in ("auto", "supertonic"):
        try:
            import supertonic  # noqa: F401
            model_dir = os.environ.get("SUPERTONIC_MODEL_DIR")
            if prefer == "supertonic" or model_dir:
                # SUPERTONIC_STEPS / SUPERTONIC_THREADS: en el Orin cargado, 8 pasos dan RTF ~2.
                return SupertonicTts(model_dir=model_dir,
                                     auto_download=bool(os.environ.get("SUPERTONIC_AUTO_DOWNLOAD")),
                                     total_steps=int(os.environ.get("SUPERTONIC_STEPS", 8)),
                                     threads=int(os.environ.get("SUPERTONIC_THREADS", 4)))
        except Exception:
            if prefer == "supertonic":
                raise
    if prefer in ("auto", "espeak") and shutil.which("espeak-ng"):
        return EspeakTts()
    return NullTts()
