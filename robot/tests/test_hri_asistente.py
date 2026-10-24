"""Pausa del asistente de voz de fábrica (hri_runtime/asistente.py), sin ROS."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from x2_ultra.hri_runtime import asistente as A  # noqa: E402


def test_pausa_only_voice_y_reanuda_normal():
    calls = []
    a = A.Asistente(caller=lambda modo: (calls.append(modo) or True, "code=0"), log=lambda m: None)
    assert a.pause() and a.paused
    assert a.resume() and not a.paused
    assert calls == ["only_voice", "normal"]          # nunca no_voice (nos quitaría el micrófono)


def test_no_reanuda_si_no_se_pauso_y_desactivable(monkeypatch):
    calls = []
    a = A.Asistente(caller=lambda modo: (calls.append(modo) or False, "sin servicio"), log=lambda m: None)
    assert not a.pause() and not a.paused
    assert a.resume() and calls == ["only_voice"]     # no envía normal si nunca se pausó
    monkeypatch.setenv("HRI_PAUSAR_ASISTENTE", "0")
    off = A.Asistente.from_env(log=lambda m: None)
    assert not off.enabled and not off.pause()


def test_un_fallo_de_ros_no_tumba_la_sesion():
    def boom(modo):
        raise RuntimeError("DDS caído")
    a = A.Asistente(caller=boom, log=lambda m: None)
    assert a.pause() is False


def test_lista_blanca_incluye_solo_el_servicio_del_asistente():
    from hriego_v2 import robot_io
    assert A.SERVICE in robot_io.ALLOWED_SERVICES
