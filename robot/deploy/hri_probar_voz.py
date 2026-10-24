"""Prueba aislada de la voz de HRIEGO a bordo: Supertonic → WAV → altavoces del X2 (AimDK).

Sin cámara, sin LLM, sin movimiento (HRIEGO_NO_ACTUATION=1: solo se permite el
publicador de audio). La usa `deploy/hri_sin_actuacion.sh probar-voz`.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("HRIEGO_NO_ACTUATION", "1")

from hriego_v2.robot_io import AimdkAudioPlayer  # noqa: E402
from hriego_v2.settings import Settings  # noqa: E402
from hriego_v2.supertonic_worker import save_wav  # noqa: E402
from supertonic import TTS  # noqa: E402


def main() -> int:
    text = " ".join(sys.argv[1:]) or "Hola, soy el robot. Esta es una prueba de audio."
    settings = Settings()
    t0 = time.perf_counter()
    tts = TTS(model_dir=settings.supertonic_model_dir, auto_download=False,
              intra_op_num_threads=4, inter_op_num_threads=1)
    style = tts.get_voice_style("M1")
    audio, duration = tts.synthesize(text, voice_style=style, total_steps=8, speed=1.0, lang="es")
    wav = Path(tempfile.gettempdir()) / "hriego_probar_voz.wav"
    save_wav(tts, audio, wav)
    print(f"síntesis: {time.perf_counter() - t0:.2f}s · audio {float(duration[0]):.2f}s · {wav}", flush=True)
    events = []
    result = AimdkAudioPlayer(progress=events.append).play(wav)
    print(f"reproducido por AimDK: {result} · eventos: {events}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
