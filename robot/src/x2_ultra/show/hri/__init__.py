"""Capa HRI del reto 2: recepción guiada con los gestos precomputados de HRIEGO.

Módulos:
- ``llm_script``: contrato Face/HRIEGO original (LLM en vivo; se conserva tal cual).
- ``data``: carga de los datos precomputados (visits_v2, catálogo de emotes, CSV de animación,
  latencias de preset, planes de anclas CTC, pistas SONIC).
- ``motion``: reproducción cinemática de presets (CSV de AgiBot) y de clips SONIC con mezclas
  minimum-jerk (patrón V2 de c12: SONIC → D → preset → SONIC/reposo).
- ``pointing``: overlay de señalamiento (prep 0,8 s, hold ≤ 2 s, recover 1,65 s) anclado al CTC.
- ``player``: cola de comandos a 50 Hz por robot (brazos 14, waist delta acotado solo parado,
  cabeza 2), reloj de audio y eventos; interlock de seguridad: nada de gestos mientras camina.
- ``tour``: pasos ``HriTurns``/``GuestArrive``/``Settle`` sobre ``GuideTour`` y la recepción
  completa ``hri_reception``.
- ``face_id``: identificación de una foto (cámara simulada) con el venv de Face.
- ``logger``: registro a 50 Hz (pelvis, tilt, velocidades/jerk, cmd vs real, reloj de audio).
- ``render``: vídeo MP4 por persona, sin ventana (EGL) y con el audio mezclado.

Los nombres de ``llm_script`` se cargan perezosamente (PEP 562): ``llm_script`` arrastra
``show.club`` → ``mujoco``, y el backend real (``AimdkBackend``, imagen a bordo sin mujoco)
necesita importar ``data`` y ``motion`` sin ello.
"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from x2_ultra.show.hri.llm_script import (CATEGORY_RANK, GESTURE_POSE, IDENTITY, RANK_CATEGORY,  # noqa: F401
                                              Intervention, category_for_profile, generate_club_script,
                                              generate_stage_intro, scenario_for_club, welcome_text)

__all__ = ["CATEGORY_RANK", "GESTURE_POSE", "IDENTITY", "RANK_CATEGORY", "Intervention", "category_for_profile",
           "generate_club_script", "generate_stage_intro", "scenario_for_club", "welcome_text"]


def __getattr__(name):
    if name in __all__:
        from x2_ultra.show.hri import llm_script
        return getattr(llm_script, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
