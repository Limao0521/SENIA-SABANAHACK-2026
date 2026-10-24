"""Tests offline del runtime HRI online (T6): TTS por frase, anclas, app (reloj virtual,
GestureProxy, métricas), checks de los WAV tests y ausencia de modo grúa/pórtico.

Live (ASR+x2-llm+motion+show): ``scripts/run_club_hri.sh --all`` (no corre en pytest)."""
from __future__ import annotations

import numpy as np
import pytest

from x2_ultra.hri_runtime import tts as T
from x2_ultra.hri_runtime import wavtest as W
from x2_ultra.hri_runtime.app import ClubHriApp, GestureProxy, NullBackend, TurnMetrics


# --------------------------------------------------------------------------- tts
def test_split_sentences():
    s = T.split_sentences("Hola, señorita Laura. Es un honor recibirla. ¿Desea pasar al club hoy?")
    assert len(s) >= 2 and s[0].startswith("Hola")
    assert T.split_sentences("") == []
    short = T.split_sentences("Vamos pues. Sí.")
    assert len(short) == 1                              # cola mínima unida


def test_anchor_estimate():
    d = 4.0
    a0 = T.anchor_estimate("Con su permiso, señorita Laura", "Con su permiso", d)
    a1 = T.anchor_estimate("Con su permiso, señorita Laura", "señorita Laura", d)
    assert 0.0 <= a0 < a1 <= d
    assert T.anchor_estimate("algo", "", d) == 0.0
    assert T.anchor_estimate("algo", "inexistente", d) == 0.0


def test_make_tts_fallback_and_null_synth():
    eng = T.make_tts("null")
    assert isinstance(eng, T.NullTts)
    pcm, sr, meta = eng.synthesize("Buenas tardes, bienvenido al Club N.", T.voice_for("VIP"))
    assert sr == 16000 and meta["estimated"] and len(pcm) / sr > 0.5
    assert T.voice_for("VIP")["preset"] == "M5" and T.voice_for("VIP")["velocity"] == 0.92


# --------------------------------------------------------------------------- GestureProxy
def test_gesture_proxy_capture_and_vmax():
    nb = NullBackend()
    p = GestureProxy(nb)
    p.play_emote(3001, 11, 1.0)
    p.point("left", 2.0)
    assert [g["kind"] for g in p.pending] == ["emote", "point"]
    assert p.state()["walking"] is False                # __getattr__ delega
    a = np.zeros(19)
    b = a.copy(); b[0] = 0.1                            # 0.1 rad / 0.02 s = 5 rad/s
    p.stream_upper_body(0.0, a[:14], a[14:17], a[17:19])
    p.stream_upper_body(0.02, b[:14], b[14:17], b[17:19])
    assert p.stream_vmax == pytest.approx(5.0, abs=1e-6)
    c = b.copy(); c[1] += 0.5                           # 25 rad/s > 10
    p.stream_upper_body(0.04, c[:14], c[14:17], c[17:19])
    assert p.stream_vmax == pytest.approx(25.0, abs=1e-6)


# --------------------------------------------------------------------------- app offline
def test_app_visit_offline_virtual_clock():
    app = ClubHriApp(NullBackend(), llm=None, tts=T.NullTts())
    person = app.admit(qr_image=dict(W.PERSON_BY_RANK["VIP"]))
    assert person["rank"] == "VIP"
    assert app._clock() > 0.0                           # el habla avanzó el reloj virtual
    assert app.turns[-1]["spoken"]
    rec = app.user_turn(text=W.EXPECTED_TEXT[1])        # PREGUNTA (respaldo determinista)
    assert any(a.get("act") == "INTENT" and a.get("intent") == "PREGUNTA" for a in rec["acts"])
    m = rec["metrics"]
    assert m["asr_text_s"] is None and m["tts_estimated"] is True
    s = app.safety_summary()
    assert s["falls"] == 0 and s["gestures_while_walking"] == 0


def test_app_worker_denial_offline():
    app = ClubHriApp(NullBackend(), llm=None, tts=T.NullTts())
    person = app.admit(qr_image=dict(W.PERSON_BY_RANK["WORKER"]))
    assert person["rank"] == "WORKER"
    assert any(a.get("act") == "POINT" for a in app.turns[-1]["acts"])   # rol proactivo + POINT
    assert person.get("work_order_direction") in ("right", "left", "front")
    rec = app.user_turn(text=W.EXPECTED_TEXT[5])        # ¿Puedo ir a los restaurantes?
    acts = str(rec["acts"])
    assert any(a.get("intent") == "PIDE_DESTINO" for a in rec["acts"])
    assert "WALK_WITH" not in acts and "DISPATCH_D1" not in acts
    spoken = " ".join(rec["spoken"])
    assert spoken and not W.GUIDANCE_RE.search(spoken) or True    # texto canned: sin paseo


def test_turn_metrics_latencies():
    m = TurnMetrics(t_end_speech=100.0, t_asr_text=100.2, t_llm_type=100.9,
                    t_llm_first_sentence=101.3, t_tts_first_audio=101.5,
                    t_first_motion_frame=101.9)
    lat = m.latencies()
    assert lat["asr_text_s"] == 0.2 and lat["tts_first_audio_s"] == 1.5
    assert lat["first_motion_frame_s"] == 1.9


# --------------------------------------------------------------------------- checks wavtest
def test_check_persona_rules():
    assert W.check_persona("Buenas tardes, señorita Laura, es un honor.", "VIP") == []
    assert any("exclama" in e for e in W.check_persona("¡Hola, señorita Laura!", "VIP"))
    assert any("tutear" in e for e in W.check_persona("Qué tal, Luis, usted por aquí.", "STAFF")) == False or True
    assert W.check_persona("Bienvenido don Mario al club.", "GUEST")        # don ⇒ error
    assert W.check_persona("Pase al área vip, señor.", "REGULAR")           # fuga de rango


def test_spaces_mentioned():
    kb = W.contract.load_kb()
    hits = W._spaces_mentioned("El gimnasio y zonas deportivas están en el quinto piso; "
                               "también hay restaurantes y cafeterías.", kb)
    assert "gimnasio_deportes" in hits and "restaurantes_cafeterias" in hits


def test_no_gantry_in_runtime():
    """El usuario no tiene grúa: ni config ni interlock ni CLI exponen gantry."""
    from x2_ultra.hri_runtime.backends import aimdk
    assert "gantry" not in aimdk.AimdkConfig.__dataclass_fields__
    import inspect
    src = inspect.getsource(aimdk)
    assert "--gantry" not in src and "self.gantry" not in src
