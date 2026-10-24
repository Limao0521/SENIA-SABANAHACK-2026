"""Tests del identity flag (HRI_IDENTITY_REAL / identity_real=).

Rápidos, sin GPU y sin modelos: la confirmación real se monkeypachea. Cubren:
flag OFF ⇒ passthrough simulado; flag ON + confirm fallo/excepción ⇒ REJECTED
sin saludo de rango conocido (sin fallback silencioso); flag ON + confirm OK ⇒
rango del confirm.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from x2_ultra.hri_runtime import confirm as confirm_mod  # noqa: E402
from x2_ultra.hri_runtime import identity  # noqa: E402
from x2_ultra.hri_runtime.app import ClubHriApp, NullBackend  # noqa: E402


class FakeTTS:
    def synthesize(self, text, voice):
        return np.zeros(1600, dtype=np.float32), 16000, {"dur_s": 0.1}


SIM_PERSON = {"person_id": "P-1", "name": "Ana", "rank": "VIP", "status": "ok_sim"}


def _app(monkeypatch, *, env=None, confirm_fn=None):
    if env is None:
        monkeypatch.delenv(identity.ENV_FLAG, raising=False)
    else:
        monkeypatch.setenv(identity.ENV_FLAG, env)
    return ClubHriApp(NullBackend(), tts=FakeTTS(), confirm_fn=confirm_fn)


def test_flag_off_simulated_passthrough(monkeypatch):
    """Flag OFF (default "0"): person dict pasa tal cual, identity_mode=simulated."""
    app = _app(monkeypatch, confirm_fn=lambda fr, qr, ced: dict(SIM_PERSON))
    person = app.admit(face_result={"name": "Ana"})
    assert person["rank"] == "VIP" and person["person_id"] == "P-1"
    rec = app.turns[-1]
    assert rec["kind"] == "admission" and rec["identity_mode"] == "simulated"
    assert person.get("identity_mode") == "simulated"
    assert app.identity_real is False


def test_flag_off_explicit_param(monkeypatch):
    """identity_real=False explícito ⇒ simulado aunque el env diga 1."""
    monkeypatch.setenv(identity.ENV_FLAG, "1")
    app = ClubHriApp(NullBackend(), tts=FakeTTS(), identity_real=False,
                     confirm_fn=lambda fr, qr, ced: dict(SIM_PERSON))
    person = app.admit()
    assert person["rank"] == "VIP"
    assert app.turns[-1]["identity_mode"] == "simulated"


def test_flag_on_confirm_failure_rejects(monkeypatch):
    """Flag ON + confirm NO confirmado ⇒ REJECTED, sin saludo de rango conocido."""
    calls = {}

    def fail(face_result, qr_image=None, cedula=None):
        calls["args"] = (face_result, qr_image, cedula)
        return {"person_id": None, "name": None, "rank": None, "worker_subtype": None,
                "work_order": None, "status": confirm_mod.STATUS_HUMAN,
                "reason": "QR_UNREADABLE", "detail": None}

    monkeypatch.setattr(confirm_mod, "confirm_identity", fail)
    app = _app(monkeypatch, env="1")
    person = app.admit(qr_image="qr.png", cedula="123")
    assert person["status"] == identity.STATUS_REJECTED
    assert person["rank"] is None                       # sin fallback silencioso
    assert person["identity_mode"] == "real"
    assert calls["args"][1] == "qr.png" and calls["args"][2] == "123"
    rec = app.turns[-1]
    assert rec["identity_mode"] == "real" and rec["spoken"] == []
    acts = [e.get("act") for e in app.fsm.log]
    assert "CONFIRMED" not in acts and "IDENTITY_REJECTED" in acts
    assert app.fsm.person is None                       # la FSM no adoptó persona


def test_flag_on_confirm_exception_rejects(monkeypatch):
    """Flag ON + excepción en confirm ⇒ REJECTED (no propaga, no saluda)."""
    def boom(face_result, qr_image=None, cedula=None):
        raise RuntimeError("decoder caído")

    monkeypatch.setattr(confirm_mod, "confirm_identity", boom)
    app = _app(monkeypatch, env="1")
    person = app.admit(qr_image="qr.png")
    assert person["status"] == identity.STATUS_REJECTED
    assert person["reason"] == "IDENTITY_ERROR" and person["rank"] is None
    assert app.turns[-1]["identity_mode"] == "real"
    assert app.turns[-1]["spoken"] == []


def test_flag_on_confirm_success_rank(monkeypatch):
    """Flag ON + confirm CONFIRMED ⇒ rango del confirm y saludo de la FSM."""
    ok = {"person_id": "P-7", "name": "Ana", "rank": "VIP", "worker_subtype": None,
          "work_order": None, "status": confirm_mod.STATUS_CONFIRMED, "source": "qr"}
    monkeypatch.setattr(confirm_mod, "confirm_identity",
                        lambda face_result, qr_image=None, cedula=None: dict(ok))
    app = _app(monkeypatch, env="1")
    person = app.admit(qr_image="qr.png")
    assert person["rank"] == "VIP" and person["person_id"] == "P-7"
    assert person["identity_mode"] == "real"
    assert app.fsm.person["rank"] == "VIP"              # FSM saluda como VIP real
    rec = app.turns[-1]
    assert rec["identity_mode"] == "real" and rec["spoken"]
    assert any(e.get("act") == "CONFIRMED" for e in app.fsm.log)


def test_env_flag_parsing(monkeypatch):
    monkeypatch.setenv(identity.ENV_FLAG, "1")
    assert identity.identity_real_from_env() is True
    monkeypatch.setenv(identity.ENV_FLAG, "0")
    assert identity.identity_real_from_env() is False
    monkeypatch.delenv(identity.ENV_FLAG, raising=False)
    assert identity.identity_real_from_env() is False   # default "0"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
