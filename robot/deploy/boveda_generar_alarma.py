#!/usr/bin/env python3
"""Genera una vez el WAV de la alarma de la bóveda con Supertonic (voz M1, español).

Autónomo (no importa nada del repo). Lo lanza `boveda.sh generar-alarma` en un
contenedor desechable de x2-hri:latest, sin red, con el modelo montado en solo lectura
(está dentro de la bóveda: hace falta abrirla una vez para generar el WAV):

  python3 boveda_generar_alarma.py <model_dir> <salida.wav> "texto"

Mismas llamadas que deploy/hri_probar_voz.py y el mismo WAV PCM 16 bits mono que
save_wav() de hriego_v2/supertonic_worker.py (con el módulo wave, sin soundfile).
"""
from __future__ import annotations

import os
import sys
import time
import wave

import numpy as np
from supertonic import TTS

SILENCIO_INICIAL_S = 0.3  # margen para que el altavoz no recorte la primera sílaba


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    model_dir, salida, texto = sys.argv[1], sys.argv[2], sys.argv[3]
    t0 = time.perf_counter()
    tts = TTS(model_dir=model_dir, auto_download=False,
              intra_op_num_threads=4, inter_op_num_threads=1)
    style = tts.get_voice_style("M1")
    audio, duration = tts.synthesize(texto, voice_style=style, total_steps=8, speed=1.0, lang="es")
    pcm = (np.clip(np.asarray(audio, dtype=np.float32).squeeze(), -1.0, 1.0) * 32767).astype("<i2")
    silencio = np.zeros(int(tts.sample_rate * SILENCIO_INICIAL_S), dtype="<i2")
    tmp = f"{salida}.tmp"
    with wave.open(tmp, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(tts.sample_rate)
        wav.writeframes(np.concatenate([silencio, pcm]).tobytes())
    os.replace(tmp, salida)
    print(f"alarma.wav: {float(np.asarray(duration).ravel()[0]):.2f} s de audio, "
          f"síntesis {time.perf_counter() - t0:.2f} s, {tts.sample_rate} Hz", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
