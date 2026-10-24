"""T4 — movimiento co-speech ONLINE (sin precomputación) para el runtime HRI.

``MotionServer.start_turn(pcm, sr, t_audio0)`` → iterator 50 Hz de
``(t, arms14, waist3, head2)`` mientras suena el audio. El cómputo pesado
(StreamTalk → GMR 15 Hz → SONIC online, modelos V2, first-block split) vive en un
proceso propio (``server_proc.py``, entorno physicalai) porque mezcla dependencias
incompatibles con el venv del show; el transporte es un UNIX socket con JSON-lines +
float32 (``protocol.py``). ``limits.py`` trae los recortes probados del Reto 2
(prep_arms, k=0.7, waist ±0.10/0.05/0.03) y el limitador 10 rad/s + 40 rad/s² de la spec.
"""
from .client import DEFAULT_SOCK, PREFIX_N, MotionServer
from .limits import (ACC_LIMIT, ARM_HI, ARM_LO, HEAD_LIMIT, JointLimiter, SPEC_VEL_EMOTE,
                     VEL_LIMIT, WAIST_BOUNDS_ONLINE, postprocess)

__all__ = ["MotionServer", "JointLimiter", "postprocess", "PREFIX_N", "DEFAULT_SOCK",
           "VEL_LIMIT", "ACC_LIMIT", "WAIST_BOUNDS_ONLINE", "ARM_LO", "ARM_HI",
           "HEAD_LIMIT", "SPEC_VEL_EMOTE"]
