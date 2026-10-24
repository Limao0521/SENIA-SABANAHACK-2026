"""Persistent Supertonic CPU child process for the optional HRIEGO voice trial."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from supertonic import TTS


def save_wav(tts: TTS, audio, output: Path) -> None:
    """tts.save_audio (soundfile, PCM_16); sin soundfile (a bordo del X2), lo mismo con wave."""
    try:
        import soundfile  # noqa: F401
    except ImportError:
        import wave

        import numpy as np

        pcm = (np.clip(np.asarray(audio, dtype=np.float32).squeeze(), -1.0, 1.0) * 32767).astype("<i2")
        output.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(tts.sample_rate)
            wav.writeframes(pcm.tobytes())
        return
    tts.save_audio(audio, str(output))


def emit(value: dict) -> None:
    print("HRIEGO_WORKER_JSON=" + json.dumps(value, ensure_ascii=False), flush=True)


def main() -> None:
    model_dir = Path(sys.argv[1])
    started = time.perf_counter()
    tts = TTS(model_dir=model_dir, auto_download=False,
              intra_op_num_threads=4, inter_op_num_threads=1)
    style = tts.get_voice_style("M1")
    emit({"ok": True, "ready": True, "load_s": round(time.perf_counter() - started, 4),
          "sample_rate": tts.sample_rate})
    speeds = {"GREETING": 1.05, "GUIDANCE": 1.0, "FAREWELL": 0.98}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            speech = str(request["speech"]).strip()
            state = str(request["state"])
            output = Path(request["output"])
            if state not in speeds or not speech or len(speech) > 1000:
                raise ValueError("Solicitud de voz inválida")
            started = time.perf_counter()
            audio, duration = tts.synthesize(speech, voice_style=style, total_steps=8,
                                             speed=speeds[state], lang="es")
            save_wav(tts, audio, output)
            emit({"ok": True, "synthesis_s": round(time.perf_counter() - started, 4),
                  "duration_s": round(float(duration[0]), 4),
                  "sample_rate": tts.sample_rate, "speed": speeds[state]})
        except Exception as exc:
            emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
