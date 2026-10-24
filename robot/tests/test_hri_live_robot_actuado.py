"""Tests del runner en vivo CON actuación (hri_runtime/live_robot_actuado.py).

Corren en el PC sin ROS ni mujoco: el backend real va en dry-run (``DryLink``)::

    PYTHONPATH=src python -m pytest tests/test_hri_live_robot_actuado.py -q --noconftest
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT / "src"), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from x2_ultra.hri_runtime import live_robot as L  # noqa: E402
from x2_ultra.hri_runtime import live_robot_actuado as A  # noqa: E402
from x2_ultra.hri_runtime.backends.aimdk import AimdkBackend  # noqa: E402

ARMED_ENV = {"X2_REAL": "robot", "HRI_ACTUATION": "1"}


# --------------------------------------------------------------------------- preflight
def test_preflight_exige_etapa():
    with pytest.raises(L.SafetyAbort):
        A.safety_preflight([], env={})


def test_preflight_seco_no_admite_arm():
    assert A.safety_preflight(["--etapa", "seco"], env={}) == "seco"
    with pytest.raises(L.SafetyAbort):
        A.safety_preflight(["--etapa", "seco", "--arm"], env=ARMED_ENV)


@pytest.mark.parametrize("etapa", ["lenta", "completa"])
def test_preflight_armado_exige_arm_x2real_y_confirmacion(etapa):
    assert A.safety_preflight(["--etapa", etapa, "--arm"], env=ARMED_ENV) == etapa
    with pytest.raises(L.SafetyAbort):
        A.safety_preflight(["--etapa", etapa], env=ARMED_ENV)
    with pytest.raises(L.SafetyAbort):
        A.safety_preflight(["--etapa", etapa, "--arm"], env={"HRI_ACTUATION": "1"})
    with pytest.raises(L.SafetyAbort):
        A.safety_preflight(["--etapa", etapa, "--arm"], env={"X2_REAL": "robot"})
    with pytest.raises(L.SafetyAbort):
        A.safety_preflight([f"--etapa={etapa}", "--arm"], env={**ARMED_ENV, "HRI_NO_ACTUATION": "1"})


def test_config_lenta_limites_bajos_cintura_bloqueada_y_sin_rutas(monkeypatch):
    monkeypatch.setenv("HRI_WAIST_MODE", "lowpass")
    cfg = A.make_config("lenta", env={})
    assert cfg.arm and cfg.joint_vmax == 5.0 and cfg.emote_vmax == 10.0
    assert cfg.waist_mode == "off" and cfg.routes == {} and not cfg.use_presets
    full = A.make_config("completa", env={})
    assert full.joint_vmax == 10.0 and full.waist_mode == "lowpass"
    assert A.make_config("seco", env={}).arm is False


def test_preflight_robot_dryrun_ok_y_falla_sin_odometria():
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        assert A.preflight_robot(be, wait_s=0.1, log=lambda m: None) == []
        be.link.pose = {"z": None, "tilt": None}
        probs = A.preflight_robot(be, wait_s=0.1, log=lambda m: None)
        assert any(p.startswith("sin pose") for p in probs)
        be.link.pose = {"z": 0.5, "tilt": 0.0}
        assert any("pelvis" in p for p in A.preflight_robot(be, wait_s=0.1, log=lambda m: None))
        be.link.pose = {"z": 0.72, "tilt": 0.0}
        be.link.obs.battery = 27
        assert any("batería al 27" in p for p in A.preflight_robot(be, wait_s=0.1, log=lambda m: None))
        be.link.obs.battery = None
        assert any("batería desconocida" in p for p in A.preflight_robot(be, wait_s=0.1, log=lambda m: None))
    finally:
        be.close()


# --------------------------------------------------------------------------- sincronía
class _Backend:
    def __init__(self):
        self.calls, self.events = [], []

    def play_emote(self, motion_id, area, t):
        self.calls.append(("emote", motion_id, area, t))

    def point(self, direction, t, hold_s=1.5):
        self.calls.append(("point", direction, t))

    def log(self, event, **kw):
        self.events.append({"event": event, **kw})


class _Speech:
    def __init__(self):
        self.q = []

    def enqueue(self, pcm, sr, meta):
        self.q.append((len(pcm), sr, meta))


def test_gesture_sync_despacha_anclado_al_inicio_real_del_audio():
    be, sp = _Backend(), _Speech()
    gs = A.GestureSync(be, sp, log=lambda m: None)
    gs.clock = lambda: 100.0                       # reloj del app (otra base que el monotónico)
    gs.play_emote(3001, 11, 100.0 + A.AUDIO_LEAD_S + 0.4)    # ancla 0.4 s
    assert not be.calls                            # retenido hasta tener audio
    t0 = time.monotonic()
    gs.sink(np.zeros(16000, np.float32), 16000, {"gap_s": 0.15, "index": 0})
    kind, mid, area, t = be.calls[0]
    start = sp.q[0][2]["not_before"]
    assert (kind, mid, area) == ("emote", 3001, 11)
    assert start >= t0 + A.GESTURE_LEAD_S - 1e-3
    assert t == pytest.approx(start + 0.4, abs=1e-6)
    # 2ª frase sin gestos: no se retrasa y encadena tras la 1ª
    gs.sink(np.zeros(8000, np.float32), 16000, {"gap_s": 0.15, "index": 1})
    assert "not_before" not in sp.q[1][2]
    assert gs.free_at == pytest.approx(start + 1.15 + 0.65, abs=1e-6)


def test_gesture_sync_respeta_la_cola_de_voz_y_flush():
    be, sp = _Backend(), _Speech()
    gs = A.GestureSync(be, sp, log=lambda m: None)
    gs.free_at = time.monotonic() + 5.0           # aún suena una frase anterior
    gs.point("LEFT", gs.clock() + A.AUDIO_LEAD_S)
    gs.sink(np.zeros(1600, np.float32), 16000, {})
    assert sp.q[0][2]["not_before"] == pytest.approx(gs.free_at - 0.1, abs=1e-6)
    gs.play_emote(1, 2, gs.clock())
    gs.flush()                                     # sin audio: se ejecuta igual
    assert be.calls[-1][0] == "emote" and not gs.pending


def test_gesture_sync_rechaza_locomocion():
    be = _Backend()
    gs = A.GestureSync(be, _Speech(), log=lambda m: None)
    gs.walk_to("biblioteca")
    gs.set_velocity(0.3, 0.0)
    assert [e["event"] for e in be.events] == ["walk_refused", "velocity_refused"]


# --------------------------------------------------------------------------- watchdog
def test_watchdog_estop_por_interlock_y_por_feedback_perdido():
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        wd = A.Watchdog(be, on_trip=lambda why: None)
        assert wd.check() is None
        be.interlock.tripped = True
        assert "interlock" in (wd.check() or "")
        be.interlock.tripped = False
        be.link.latest = lambda group, max_age_s=0: None
        assert (wd.check(now=time.monotonic() + 2.0) or "").startswith("sin feedback")
    finally:
        be.close()


def test_watchdog_hilo_hace_estop():
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    tripped = []
    try:
        wd = A.Watchdog(be, on_trip=tripped.append, period_s=0.02).start()
        be.unsafe = True
        t0 = time.monotonic()
        while not tripped and time.monotonic() - t0 < 2.0:
            time.sleep(0.02)
        assert tripped and be.estopped
        assert any(e["event"] == "estop" for e in be.events)
    finally:
        be.close()


# --------------------------------------------------------------------------- sesión en seco
class _NullPlayer:
    def play(self, path):
        return {}

    def stop(self):
        pass


class _ToneTts:
    name, estimated = "tone", False

    def synthesize(self, sentence, voice):
        sr = 16000
        return np.full(int(0.1 * sr), 0.1, np.float32), sr, {"engine": "tone", "estimated": False}


def _args(**kw):
    ns = A.build_parser().parse_args(["--etapa", "seco", "--entrada", "stdin", "--sin-voz", "--log-dir", ""])
    ns.mic_canal = 0
    ns.identity_real = False
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


@pytest.mark.parametrize("visit, lines", [
    ("STAFF", ["adiós, gracias"]),
    ("VIP", ["Quiero ir a la biblioteca", "sí", "adiós, gracias"]),
])
def test_sesion_seco_ejecuta_gestos_en_el_backend_real(tmp_path, visit, lines):
    be = AimdkBackend(A.make_config("seco", env={}))       # bomba 50 Hz real, DryLink
    it = iter(lines)
    try:
        assert be.arm_session() == []
        sess = A.ActuatedSession(_args(visit=visit), backend=be, llm=None, tts=_ToneTts(),
                                 player=_NullPlayer(), text_source=lambda: next(it, None),
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True))
        assert sess.run() == 0
        assert sess.app.backend_raw is sess.sync and sess.app.motion is None
        ev = [e["event"] for e in be.events]
        assert sess.sync.dispatched, ev
        assert {"emote_dispatch", "emote_unavailable", "point_dispatch"} & set(ev)
        assert "walk_start" not in ev and "set_velocity" not in ev
        cmds = list(be.commands)
        assert any(c["kind"] == "upper_body" for c in cmds)
        assert not [c for c in cmds if c["kind"] == "velocity" and (c["vx"] or c["wz"])]
        # cintura bloqueada: McBodyPose sin twist/bend (solo puede llevar turn_head)
        assert all(not any(c["u"][:3]) for c in cmds if c["kind"] == "body_pose")
        import json
        recs = [json.loads(x) for x in next(tmp_path.glob("live_*.jsonl")).read_text("utf-8").splitlines()]
        motors = [m for r in recs for m in r["motor_intents"]]
        if visit == "STAFF":                           # STAFF: señalamiento al destino de rol
            assert any(m["act"] == "POINT" and m["ejecutado"] for m in motors)
        if not be.store.animations:                    # sin CSV de AgiBot: emote propio
            assert all(m["ejecutado"] for m in motors if m["act"] == "EMOTE")
            assert "emote_unavailable" not in ev
            assert any(e.get("path") == "procedural" for e in be.events if e["event"] == "emote_dispatch")
        if visit == "VIP":
            assert "walk_refused" in ev
            assert all(not m["ejecutado"] for m in motors if m["act"] == "WALK_WITH")
    finally:
        be.stop()
        be.close()


# --------------------------------------------------------------------------- armado
def test_no_se_publica_nada_antes_de_armar_y_la_bomba_arranca_tras_arm_session():
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        time.sleep(0.1)
        assert not [c for c in be.commands if c["kind"] == "upper_body"]
        assert A.arm_and_start(be, log=lambda m: None) == []
        t0 = time.monotonic()
        while not any(c["kind"] == "upper_body" for c in be.commands) and time.monotonic() - t0 < 2:
            time.sleep(0.02)
        kinds = [c["kind"] for c in be.commands]
        assert kinds.index("register_source") < kinds.index("upper_body")
    finally:
        be.stop()
        be.close()


def test_main_solo_preflight_no_arma(monkeypatch, tmp_path):
    rc = A.main(["--etapa", "seco", "--solo-preflight", "--entrada", "stdin", "--log-dir", str(tmp_path)])
    assert rc == 0
    ev = next(tmp_path.glob("actuado_seco_*_eventos.jsonl")).read_text("utf-8")
    assert "armed" not in ev and "register_source" not in ev and "upper_body" not in ev


def test_publicadores_del_sistema_agibot_no_cuentan_como_ajenos():
    from types import SimpleNamespace as NS
    infos = [NS(node_name=n) for n in ("app_proxy", "soc0_rc3563", "soc2_interaction_slave1611",
                                       "aimrt_planning_planning_node4732", "hri_club_act", "teleop_x")]
    node = NS(get_publishers_info_by_topic=lambda t: infos, get_name=lambda: "hri_club_act")
    be = NS(link=NS(link=NS(node=node)))
    assert A.foreign_publishers(be, "/aima/mc/locomotion/velocity") == ["teleop_x"]
    assert A.foreign_publishers(NS(link=object()), "/x") is None


def test_watchdog_estop_si_la_pose_deja_de_llegar():
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        wd = A.Watchdog(be, on_trip=lambda why: None)
        be.link.pose = dict(be.link.pose, t=time.monotonic())
        assert wd.check() is None
        assert "pose" in (wd.check(now=time.monotonic() + 1.0) or "")
    finally:
        be.close()


# --------------------------------------------------------------------------- identidad real
FIXTURES = ROOT / "tests" / "fixtures"
FACES = {"Laura": ("P005", "VIP"), "Sebastian": ("P003", "REGULAR"), "Luis": ("P001", "STAFF"),
         "Josaed": ("P002", "EXTERNAL")}


def _face(name):
    pid, rank = FACES[name]
    return {"status": "KNOWN", "top1_id": pid, "top1_score": 0.6, "bbox": [0, 0, 150, 150],
            "profile": {"person_id": pid, "display_name": name, "rank_code": rank,
                        "identity_status": "CONFIRMED"}}


class _Cam:
    def __init__(self, frames):
        self.frames = list(frames)

    def read(self, timeout_s=1.0):
        return (True, self.frames.pop(0)) if self.frames else (False, None)

    def release(self):
        pass


def _sess_ident(tmp_path, monkeypatch, face, frames, timeout=0.3):
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    args = _args(identity_real=True, camara="/aima/hal/sensor/rgb_head_front_center/rgb_image/compressed")
    sess = A.ActuatedSession(args, backend=be, llm=None, tts=_ToneTts(), player=_NullPlayer(),
                             text_source=lambda: None, logger=A.ActuatedTurnLogger(tmp_path, quiet=True))
    monkeypatch.setattr(sess, "wait_for_visitor", lambda: ("f.jpg", None, face))
    cam = _Cam(frames)
    orig = sess.scan_qr
    monkeypatch.setattr(sess, "scan_qr", lambda: orig(timeout_s=timeout, capture=cam))
    said = []
    monkeypatch.setattr(sess, "say_direct", said.append)
    return be, sess, said


def _qr(png):
    import cv2
    return cv2.imread(str(FIXTURES / png))


def test_miembro_conocido_debe_mostrar_qr_y_el_qr_fija_el_rango(tmp_path, monkeypatch):
    be, sess, said = _sess_ident(tmp_path, monkeypatch, _face("Luis"),
                                 [None, _qr("qr_club_staff_compacto.png")])
    try:
        p = sess.admit()
        assert said and "credencial QR" in said[0] and "Luis" in said[0]
        assert p["status"] != "REJECTED" and p["rank"] == "STAFF" and p["source"] == "qr"
        assert sess.app.fsm.state.value == "CONVERSATION"
    finally:
        be.close()


def test_cara_conocida_sin_qr_no_confirma_y_va_a_personal(tmp_path, monkeypatch):
    be, sess, said = _sess_ident(tmp_path, monkeypatch, _face("Laura"), [])
    try:
        p = sess.admit()
        assert p["status"] == "REJECTED" and p["reason"] == "QR_TIMEOUT"
        assert sess.app.metrics.spoken == [A.QR_MISSING_PHRASE]
        assert sess.app.fsm.state.value == "END"
    finally:
        be.close()


def test_qr_de_otra_persona_se_rechaza(tmp_path, monkeypatch):
    be, sess, said = _sess_ident(tmp_path, monkeypatch, _face("Sebastian"), [_qr("qr_vip_compacto.png")])
    try:
        p = sess.admit()
        assert p["status"] == "REJECTED" and p["reason"] == "QR_OTHER_PERSON"
        assert A.QR_REJECTED_PHRASE in sess.app.metrics.spoken
        assert sess.app.fsm.state.value == "END"
    finally:
        be.close()


def test_cara_no_miembro_va_directo_a_personal_sin_pedir_qr(tmp_path, monkeypatch):
    be, sess, said = _sess_ident(tmp_path, monkeypatch, {"status": "UNKNOWN", "top1_score": 0.2}, [])
    try:
        p = sess.admit()
        assert p["status"] == "REJECTED" and p["reason"] == "NO_MIEMBRO" and not said
        assert sess.app.metrics.spoken == [A.REJECTED_PHRASE]
    finally:
        be.close()


def test_espera_cara_cercana_y_confirma_desconocida(tmp_path, monkeypatch):
    be, sess, _ = _sess_ident(tmp_path, monkeypatch, None, [])
    monkeypatch.undo()
    try:
        seq = iter([{"status": "NO_FACE"},
                    {"status": "UNKNOWN", "bbox": [0, 0, 30, 30]},          # lejana: se ignora
                    {"status": "UNKNOWN", "bbox": [0, 0, 120, 120]},        # 1ª vez: confirma
                    {"status": "UNKNOWN", "bbox": [0, 0, 120, 120]}])
        calls = []
        got = sess.wait_for_visitor(grab=lambda: (calls.append(1) or "f.jpg", "frame"),
                                    identify=lambda p: next(seq))
        assert got is not None and len(calls) == 4
        got = sess.wait_for_visitor(grab=lambda: ("f.jpg", "frame"), identify=lambda p: _face("Laura"))
        assert got is not None and got[2]["status"] == "KNOWN"
    finally:
        be.close()


# --------------------------------------------------------------------------- gesto co-verbal
def test_clips_cospeech_disponibles_y_frame_en_la_ventana():
    clips = A.cospeech_clips()
    assert clips["greeting"] and clips["dialogue"]
    calls = []

    class _B:
        def walking(self):
            return False

        def stream_upper_body(self, t, a, w, h):
            calls.append(a)

    cs = A.CoSpeech(_B(), clips=clips)
    now = time.monotonic()
    cs.schedule(now + 1.0, 3.0)
    assert cs.frame(now) is None                                 # antes de la ventana
    arms, waist, head = cs.frame(now + 2.0)
    assert arms.shape == (14,) and not waist.any() and head.shape == (2,)
    assert cs.frame(now + 4.5) is None                           # después: el backend vuelve a READY
    # amplitud 70 % y velocidad de clip << 5 rad/s (límite de la etapa lenta)
    now = time.monotonic()
    cs.schedule(now + 1.0, 3.0)
    ts = np.arange(0.7, 3.9, 0.02) + now
    traj = np.array([cs.frame(t)[0] for t in ts])
    assert np.abs(np.diff(traj, axis=0) / 0.02).max() < 5.0


def test_sesion_seco_mueve_los_brazos_mientras_habla(tmp_path):
    from x2_ultra.hri_runtime.backends.aimdk import READY_ARMS

    class _LongTts(_ToneTts):
        def synthesize(self, sentence, voice):
            sr = 16000
            return np.full(int(1.5 * sr), 0.1, np.float32), sr, {"engine": "tone", "estimated": False}

    be = AimdkBackend(A.make_config("seco", env={}))
    it = iter(["adiós, gracias"])
    try:
        assert be.arm_session() == []
        sess = A.ActuatedSession(_args(visit="REGULAR"), backend=be, llm=None, tts=_LongTts(),
                                 player=_NullPlayer(), text_source=lambda: next(it, None),
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True))
        class _P:          # reproductor que tarda lo que dura el audio (como el altavoz)
            def play(self, path):
                import wave
                with wave.open(str(path)) as w:
                    time.sleep(w.getnframes() / w.getframerate())
            def stop(self):
                pass
        sess.speech.player = _P()
        assert sess.run() == 0
        sess.stop()
        assert sess.cospeech is not None and sess.cospeech.frames > 20
        arms = np.array([c["arms"] for c in be.commands if c["kind"] == "upper_body"])
        assert np.abs(arms - READY_ARMS).max() > 0.1        # se movieron lejos de READY
    finally:
        be.stop()
        be.close()


# --------------------------------------------------------------------------- latencia del LLM
def test_repair_trigger_evita_el_reintento():
    from x2_ultra.hri_runtime import contract
    sp = "Señorita Laura, bienvenida a Club N, nuestro lugar de encuentro. Quedo a su disposición."
    long_ = {"type": "GREETING_VIP", "speech": sp, "trigger_text": "bienvenida a Club N, nuestro lugar de encuentro"}
    fixed = contract.repair_trigger(long_)
    assert contract._words(fixed["trigger_text"]) <= contract.TRIGGER_MAX_WORDS
    assert contract._literal_trigger(fixed["trigger_text"], sp) and fixed["speech"] == sp
    none = contract.repair_trigger({"type": "NONE", "speech": sp, "trigger_text": "Quedo"})
    assert none["trigger_text"] == ""


def test_respond_repara_en_local_sin_segunda_llamada():
    import json
    from x2_ultra.hri_runtime.llm_client import HriLLM
    calls = []

    class _C:
        def stream_chat(self, messages, schema, temperature=0.2):
            calls.append(1)
            obj = {"type": "NONE", "speech": "Con gusto le ayudo con lo que necesite hoy, Sebastian.",
                   "trigger_text": "Con gusto", "intent": "OTRO", "area": None}
            return {"raw": json.dumps(obj), "metrics": {}}

    r = HriLLM(client=_C()).respond("CONVERSATION", "REGULAR", {"name": "Sebastian", "rank": "REGULAR"},
                                    utterance="hola")
    assert len(calls) == 1 and not r["errors"] and not r["retry_used"]
    assert r["obj"]["trigger_text"] == "" and r["repaired"] == ["trigger_text must be empty when type=NONE"]


def test_primera_clausula_aparte():
    from x2_ultra.hri_runtime.app import split_first_clause
    s = ["Señorita Laura, bienvenida a Club N, nuestro lugar de encuentro.", "Quedo a su disposición."]
    assert split_first_clause(s) == ["Señorita Laura,", "bienvenida a Club N, nuestro lugar de encuentro.",
                                     "Quedo a su disposición."]
    assert split_first_clause(["Hola, Luis."]) == ["Hola, Luis."]


def test_worker_va_directo_a_personal_sin_qr_y_se_menciona_a_draco(tmp_path, monkeypatch):
    be, sess, said = _sess_ident(tmp_path, monkeypatch, _face("Josaed"), [_qr("qr_vip_compacto.png")])
    try:
        p = sess.admit()
        assert p["status"] == "REJECTED" and p["reason"] == "WORKER_A_PERSONAL" and not said
        spoken = sess.app.metrics.spoken[0]
        assert "Josaed" in spoken and "Draco" in spoken
    finally:
        be.close()


# --------------------------------------------------------------------------- emotes propios
def test_emotes_propios_cubren_el_catalogo_y_respetan_limites():
    import json
    from x2_ultra.hri_runtime.backends import emotes as E
    from x2_ultra.robot import joints as J
    cat = json.loads((ROOT / "assets/hri/HRIEGO_gesture/contract/catalog.json").read_text("utf-8"))["emotes"]
    lo = np.array([J.JOINT_RANGE[n][0] for n in J.ARM_JOINTS])
    hi = np.array([J.JOINT_RANGE[n][1] for n in J.ARM_JOINTS])
    for name, v in cat.items():
        if v.get("motion_id") is None:
            continue                                          # señalamiento: lo hace el backend
        clip = E.procedural_for(v["motion_id"], v["area"])
        assert clip is not None, name
        traj = np.array([clip.sample(t)[0] for t in np.arange(0, clip.duration, 0.02)])
        assert (traj >= lo).all() and (traj <= hi).all(), name
        assert np.abs(np.diff(traj, axis=0) / 0.02).max() < 5.0, name   # bajo el límite de la etapa lenta
        assert np.allclose(traj[0], E.READY), name                       # empieza en READY


def test_backend_sin_csv_reproduce_el_emote_propio():
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        be.store = type("S", (), {"animations": {}, "csv_for_id": lambda self, m, a: None,
                                  "point_targets": lambda self: {}})()
        be.play_emote(3031, 11, time.monotonic() + 0.6)      # FAREWELL_WAVE
        ev = [e for e in be.events if e["event"] == "emote_dispatch"]
        assert ev and ev[-1]["path"] == "procedural" and be.gesture_active()
    finally:
        be.close()


# --------------------------------------------------------------------------- cierre con pregunta
@pytest.mark.parametrize("visit", ["VIP", "REGULAR", "GUEST", "STAFF", "WORKER"])
def test_tras_la_admision_el_robot_termina_con_una_pregunta(tmp_path, visit):
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        sess = A.ActuatedSession(_args(visit=visit), backend=be, llm=None, tts=_ToneTts(),
                                 player=_NullPlayer(), text_source=lambda: None,
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True))
        sess.admit()
        assert sess.app.fsm.spoken and sess.app.fsm.spoken[-1].strip().endswith("?"), sess.app.fsm.spoken
    finally:
        be.close()


# --------------------------------------------------------------------------- latencia del audio
def test_acuse_inmediato_solo_en_conversacion_normal(tmp_path):
    be = AimdkBackend(A.make_config("seco", env={}), pump=False)
    try:
        sess = A.ActuatedSession(_args(visit="VIP"), backend=be, llm=None, tts=_ToneTts(),
                                 player=_NullPlayer(), text_source=lambda: None,
                                 logger=A.ActuatedTurnLogger(tmp_path, quiet=True))
        assert sess._acuses and set(sess._acuses) == set(A.ACUSES)
        sess.admit()
        assert sess.acuse("¿a qué hora abre la biblioteca?") in A.ACUSES["VIP"]
        assert sess.acuse("adiós, muchas gracias por todo") is None       # despedida: sin acuse
        assert sess.acuse("sí") is None                                    # muy corta
    finally:
        be.close()


def test_despedida_inequivoca_hace_una_sola_llamada_al_llm():
    from x2_ultra.hri_runtime.fsm import HriFSM
    calls = []

    class _LLM:
        on_attempt = None

        def respond(self, state, *a, **k):
            calls.append(state)
            return {"obj": {"type": "FAREWELL_WAVE", "speech": "Gracias por su visita, que tenga buen día.",
                            "trigger_text": "Gracias"}, "errors": []}

    fsm = HriFSM(type("B", (), {"play_emote": lambda *a: None, "point": lambda *a: None,
                                "walk_to": lambda *a: None, "dispatch_d1": lambda *a: None,
                                "state": lambda self: {}})(), _LLM(),
                 confirm_fn=lambda *a, **k: {"person_id": "P003", "name": "Sebastian", "rank": "REGULAR"})
    fsm.arrival({"name": "Sebastian"})
    calls.clear()
    fsm.on_utterance("muchas gracias, adiós")
    assert calls == ["FAREWELL"] and fsm.state.value == "END"


def test_prefetch_de_la_primera_clausula_mientras_el_llm_escribe():
    from x2_ultra.hri_runtime.llm_client import _emit_speech
    got = []
    buf = '{"type": "NONE", "speech": "Señor Jorge Castellanos, la biblioteca abre a las ocho de la ma'
    _emit_speech(buf, got.append, partial=True)
    assert got == ["Señor Jorge Castellanos, la biblioteca abre a las ocho de la ma"]
    got.clear()
    _emit_speech(buf, got.append)                  # sin partial: solo con speech cerrado
    assert got == []
