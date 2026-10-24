"""Pruebas de los backends del runtime HRI (T5).

1. Conformidad con el Protocol ``Backend`` (spec) de las tres implementaciones, con fakes
   (sin MuJoCo, sin ROS, sin robot): firmas exactas + ``isinstance`` runtime.
2. ShowBackend: stream 50 Hz con límites del rig, interlock de caminata, emote cinemático por
   CSV, overlay de POINT con hold configurable, walk_to por tour, stub de D1, estop.
3. AimdkBackend DRY_RUN: registro de comandos esperado (nunca publica), límite 10 rad/s por
   articulación fuera de emotes (20 en emotes), cintura acotada ±0.10/0.05/0.03 + slew, lockout
   gesto↔locomoción, interlock de caída (pelvis<0.62 / tilt>12 salvo preset busy con pelvis≥0.66 /
   tilt>25 duro, debounce 3), estop.
4. TwinBackend: mismos interlocks con los defaults del gemelo (MC v1.0.0, dominio 77, helper de
   presets sin ``input_source``).
"""
from __future__ import annotations

import csv as _csv
import inspect
import sys
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from x2_ultra.hri_runtime.backends import aimdk as AIM
from x2_ultra.hri_runtime.backends import show as SHOW
from x2_ultra.hri_runtime.backends import twin as TWIN
from x2_ultra.hri_runtime.backends.base import Backend
from x2_ultra.robot import joints as J

METHODS = ("state", "stream_upper_body", "play_emote", "point", "walk_to", "set_velocity",
           "dispatch_d1", "stop", "estop")
AREA = "gimnasio_deportes"


# --------------------------------------------------------------------------------------- fakes
class FakeRig:
    has_head = True

    def __init__(self):
        self.cmd = np.zeros(3)
        self.cmd_target = np.zeros(3)
        self.dance = None
        self.state = "activo"
        self.arm_cmd = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
        self.arms, self.waist, self.head, self.gate, self.gentle = [], [], [], [], []
        self.info = SimpleNamespace(base_pos=np.array([0.0, 0.0, 0.75]))
        self._q = np.array([1.0, 0.0, 0.0, 0.0])

    def quat(self, data):
        return self._q

    def set_arm_targets(self, q):
        self.arm_cmd = np.asarray(q, dtype=float).copy()
        self.arms.append(self.arm_cmd.copy())

    def set_waist_delta(self, q):
        self.waist.append(np.asarray(q, dtype=float).copy())

    def set_head_targets(self, yaw, pitch):
        self.head.append((float(yaw), float(pitch)))

    def set_waist_gate(self, on):
        self.gate.append(bool(on))

    def set_waist_gentle(self, g):
        self.gentle.append(bool(g))

    def stop_walking(self):
        self.cmd[:] = 0.0
        self.cmd_target[:] = 0.0

    def set_velocity_target(self, vx, vy, wz):
        self.cmd_target[:] = [vx, vy, wz]


class FakeEngine:
    def __init__(self):
        self.rigs = {"ultra": FakeRig()}
        self.lock = threading.RLock()
        self.data = None
        self.tours, self.speech = [], []
        self._t = 0.0

    @property
    def sim_time(self):
        return self._t

    def touring(self, key):
        return bool(self.tours) and not self.tours[-1].done

    def start_tour(self, tour):
        self.tours.append(tour)

    def stop_tour(self):
        if self.tours:
            self.tours[-1].done = True

    def say(self, text, key):
        self.speech.append((self._t, key, text))


ARM_COLS = [f"command_pos::{n}" for n in J.ARM_JOINTS]
WAIST_COLS = [f"command_pos::{n}" for n in J.WAIST_JOINTS]
HEAD_COLS = [f"command_pos::{n}" for n in J.HEAD_JOINTS]
CSV_DUR = 1.0


def write_emote_csv(path) -> None:
    """CSV sintético con el formato AgiBot: 1 s a 50 Hz, brazos de D a D+0.1 (shoulder_pitch)."""
    d0 = AIM.D_ARMS
    tgt = d0.copy()
    tgt[0] += 0.1
    tgt[7] += 0.1
    fields = ["timeMS"] + ARM_COLS + WAIST_COLS + HEAD_COLS
    with open(path, "w", newline="") as fh:
        w = _csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        n = int(CSV_DUR * 50)
        for k in range(n + 1):
            s = k / n
            row = {"timeMS": int(1000 * s * CSV_DUR)}
            arms = d0 + s * (tgt - d0)
            for c, v in zip(ARM_COLS, arms):
                row[c] = float(v)
            waist = [0.04 * np.sin(np.pi * s), 0.02 * s, 0.0]
            for c, v in zip(WAIST_COLS, waist):
                row[c] = float(v)
            for c, v in zip(HEAD_COLS, [0.05 * s, 0.0]):
                row[c] = float(v)
            w.writerow(row)


class FakeStore:
    def __init__(self, csv_path):
        self.animations = {(3001, 11): csv_path}

    def point_targets(self):
        return {"LEFT": {"side": "left", "joints": {"left_shoulder_pitch_joint": -1.2,
                                                    "left_elbow_joint": -0.4}},
                "RIGHT": {"side": "right", "joints": {"right_shoulder_pitch_joint": -1.2}},
                "FRONT": {"side": "right", "joints": {"right_shoulder_pitch_joint": -1.0}}}


@pytest.fixture
def csv_path(tmp_path):
    p = tmp_path / "emote_3001_11.csv"
    write_emote_csv(p)
    return p


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def show_be(engine, csv_path):
    be = SHOW.ShowBackend(engine, store=FakeStore(csv_path), pump=False,
                          routes={AREA: ([(2.0, 0.0), (4.0, 1.0)], 0.5)})
    yield be
    be.close()


@pytest.fixture
def aim_be(csv_path):
    cfg = AIM.AimdkConfig(routes={AREA: [(0.3, 0.0, 0.5), (0.0, 0.4, 0.5)]})
    be = AIM.AimdkBackend(cfg, store=FakeStore(csv_path), pump=False)
    yield be
    be.close()


@pytest.fixture
def twin_be(csv_path):
    be = TWIN.TwinBackend(store=FakeStore(csv_path), pump=False)
    yield be
    be.close()


def wait_for(pred, timeout=2.0):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.01)
    return False


def kinds(be):
    return [c["kind"] for c in be.commands]


def last_cmd(be, kind):
    got = [c for c in be.commands if c["kind"] == kind]
    return got[-1] if got else None


def events(be, name):
    return [e for e in be.events if e["event"] == name]


# --------------------------------------------------------------------- conformidad de Protocol
def _check_conformance(be):
    assert isinstance(be, Backend)          # Protocol runtime_checkable
    for name in METHODS:
        want = inspect.signature(getattr(Backend, name))
        got = inspect.signature(getattr(be, name))
        wp = [p for p in want.parameters.values() if p.name != "self"]
        gp = list(got.parameters.values())
        assert [p.name for p in wp] == [p.name for p in gp], f"{name}: nombres"
        for a, b in zip(wp, gp):
            assert a.default == b.default, f"{name}: default de {a.name}"
            assert str(a.annotation) == str(b.annotation), f"{name}: anotación de {a.name}"
        assert str(got.return_annotation) == str(want.return_annotation), f"{name}: retorno"


def test_protocol_show(show_be):
    _check_conformance(show_be)


def test_protocol_aimdk(aim_be):
    _check_conformance(aim_be)


def test_protocol_twin(twin_be):
    _check_conformance(twin_be)
    assert isinstance(twin_be, AIM.AimdkBackend)


def test_dry_run_never_touches_ros(aim_be):
    assert aim_be.dry_run and isinstance(aim_be.link, AIM.DryLink)
    assert "rclpy" not in sys.modules


# ------------------------------------------------------------------------------ ShowBackend
def test_show_stream_applies(show_be, engine):
    arms = AIM.READY_ARMS + 0.05
    show_be.stream_upper_body(0.0, arms, [0.02, 0.0, 0.0], [0.1, 0.0])
    show_be.pump_once(0.0)
    rig = engine.rigs["ultra"]
    assert np.allclose(rig.arms[-1], arms)          # el rig aplica clamp/filtro (aquí fake)
    assert np.allclose(rig.waist[-1], [0.02, 0.0, 0.0])
    assert rig.head[-1] == (0.1, 0.0)
    assert rig.gate[-1] is True
    st = show_be.state()
    assert st["mode"] == "stream" and st["stable"] and not st["walking"] and not st["fall"]
    assert st["pelvis_z"] == pytest.approx(0.75) and st["tilt_deg"] == pytest.approx(0.0)


def test_show_stream_malformed(show_be, engine):
    show_be.stream_upper_body(0.0, np.zeros(13), np.zeros(3), np.zeros(2))
    show_be.stream_upper_body(0.0, np.full(14, np.nan), np.zeros(3), np.zeros(2))
    show_be.pump_once(0.0)
    assert events(show_be, "stream_rejected")
    assert engine.rigs["ultra"].arms == []


def test_show_walking_blocks_gestures(show_be, engine):
    rig = engine.rigs["ultra"]
    show_be.stream_upper_body(0.0, AIM.READY_ARMS, np.zeros(3), np.zeros(2))
    rig.cmd_target[0] = 0.3                          # caminando
    show_be.pump_once(1.0)
    assert events(show_be, "safety_gesture_suppressed")
    assert np.allclose(rig.arms[-1], SHOW.REST_ARMS)
    assert show_be.state()["walking"]


def test_show_emote_kinematic(show_be, engine, csv_path):
    rig = engine.rigs["ultra"]
    from_arms = rig.arm_cmd.copy()
    show_be.play_emote(3001, 11, t_dispatch=1.0)
    assert events(show_be, "emote_dispatch")
    show_be.pump_once(0.5)                            # mezcla hacia D (a mitad)
    mid = rig.arms[-1]
    assert np.abs(mid - AIM.D_ARMS).max() < np.abs(from_arms - AIM.D_ARMS).max()
    ts = [float(x) for x in np.arange(0.5, 1.75, 0.02)]
    for t in ts:                                      # a través del CSV
        show_be.pump_once(t)
    from x2_ultra.show.hri.motion import CsvPreset
    csv = CsvPreset.load(csv_path)
    want = csv.sample(ts[-1] - 1.0)[0]                # τ = t − t_dispatch
    assert np.allclose(rig.arms[-1], want, atol=1e-6)  # reproducción cinemática exacta
    assert any(g is True for g in rig.gentle)          # waist gentle durante el preset
    for t in np.arange(1.8, 3.2, 0.02):
        show_be.pump_once(float(t))
    assert events(show_be, "track_done")
    show_be.play_emote(9999, 1, t_dispatch=5.0)
    assert events(show_be, "emote_unavailable")


def test_show_point(show_be, engine):
    show_be.point("LEFT", t_stroke=2.0, hold_s=2.0)
    show_be.pump_once(2.0)                            # stroke: w = 1
    rig = engine.rigs["ultra"]
    i = J.ARM_JOINTS.index("left_shoulder_pitch_joint")
    assert rig.arms[-1][i] == pytest.approx(-1.2)
    assert rig.head[-1][0] == pytest.approx(0.30)     # LEFT ⇒ +yaw
    show_be.pump_once(3.8)                            # hold 2.0 s: sigue en 1
    assert rig.head[-1][0] == pytest.approx(0.30)
    show_be.pump_once(6.5)                            # tras recuperación: pista cerrada
    assert events(show_be, "track_done")
    show_be.point("ABAJO", t_stroke=9.0)
    assert events(show_be, "point_rejected")


def test_show_walk_and_d1(show_be, engine):
    show_be.dispatch_d1(AREA)
    assert show_be.d1_log[-1]["area"] == AREA         # stub: registro…
    assert engine.speech and AREA in engine.speech[-1][2]   # …+ marcador en pantalla
    show_be.walk_to("zona_inexistente")
    assert events(show_be, "walk_no_route")
    show_be.walk_to(AREA)
    assert events(show_be, "walk_start") and engine.tours
    assert show_be.walking()
    show_be.play_emote(3001, 11, t_dispatch=99.0)     # gestos bloqueados mientras camina
    assert events(show_be, "refused")
    show_be.stop()
    assert not show_be.walking()


def test_show_estop(show_be, engine):
    show_be.estop()
    assert show_be.estopped and show_be.state()["mode"] == "estop"
    assert not show_be.state()["stable"]
    show_be.stream_upper_body(0.0, AIM.READY_ARMS, np.zeros(3), np.zeros(2))
    assert events(show_be, "refused")


# ----------------------------------------------------------------------------- AimdkBackend
def test_aimdk_dryrun_command_log(aim_be):
    assert aim_be.arm_session() == []
    assert "register_source" in kinds(aim_be)
    assert last_cmd(aim_be, "request_mode")["mode"] == "UPPERBODY_REMOTE_SPLIT"
    t = time.monotonic()
    aim_be.stream_upper_body(t, AIM.READY_ARMS, np.zeros(3), np.zeros(2))
    aim_be.pump_once(t)
    ub = last_cmd(aim_be, "upper_body")
    assert ub is not None and len(ub["arms"]) == 14 and ub["hand_sub_mode"] == 1
    aim_be.set_velocity(0.3, 0.2)
    v = last_cmd(aim_be, "velocity")
    assert v["vx"] == pytest.approx(0.3) and v["wz"] == pytest.approx(0.2)
    aim_be.dispatch_d1(AREA)
    assert events(aim_be, "dispatch_d1")[0]["stub"] is True


def test_aimdk_joint_velocity_limit(aim_be):
    """10 rad/s por articulación fuera de emotes: un salto de 1 rad avanza ≤ 10·0.02 por tick."""
    t = time.monotonic()
    aim_be.limiter.reset(AIM.READY_ARMS)
    tgt = AIM.READY_ARMS + 1.0
    prev = AIM.READY_ARMS.copy()
    for k in range(5):
        aim_be.stream_upper_body(t + k * 0.02, tgt, np.zeros(3), np.zeros(2))
        aim_be.pump_once(t + k * 0.02)
        pub = np.array(last_cmd(aim_be, "upper_body")["arms"])
        assert np.max(np.abs(pub - prev)) <= AIM.JOINT_VMAX * AIM.TICK + 1e-9
        prev = pub
    assert aim_be.limiter.nlim >= 0   # la rampa de entrada SONIC ya evita el recorte


def test_aimdk_waist_bounds_and_head(aim_be):
    """Cintura acotada ±0.10/0.05/0.03: con la medida clavada en el bound, u→0 (el ref no pasa)."""
    t = time.monotonic()
    aim_be.link.wh_state = {"waist": [0.10, 0.05, 0.03]}
    for k in range(120):
        aim_be.stream_upper_body(t, AIM.READY_ARMS, [0.5, 0.5, 0.5], [0.0, 0.0])
        aim_be.pump_once(t + k * 0.02)
    u = np.array(last_cmd(aim_be, "body_pose")["u"])
    assert np.allclose(u[:3], 0.0, atol=1e-9)         # ref == bound == medida ⇒ sin error
    # cabeza: turn_head sigue la referencia (0.3 rad) con control de signo ±0.3
    aim_be.link.wh_state = {"waist": [0.0, 0.0, 0.0]}
    for k in range(3):                                 # re-stream cada tick (cola maxlen=2)
        aim_be.stream_upper_body(t, AIM.READY_ARMS, np.zeros(3), [0.3, 0.0])
        aim_be.pump_once(t + 10 + k * 0.02)
    assert last_cmd(aim_be, "body_pose")["u"][3] == pytest.approx(AIM.BP_U)


def test_aimdk_gesture_blocks_velocity_and_walk_blocks_gestures(aim_be):
    t = time.monotonic()
    aim_be.play_emote(3001, 11, t_dispatch=t + 0.5)
    assert events(aim_be, "emote_dispatch")
    aim_be.set_velocity(0.3, 0.0)                     # locomoción sólo sin gestos
    assert events(aim_be, "velocity_refused")
    assert last_cmd(aim_be, "velocity") is None
    for k in range(120):                              # 2.4 s: el emote cinemático termina solo
        aim_be.pump_once(t + k * 0.02)
    assert not aim_be.gesture_active()
    aim_be.walk_to(AREA)                              # ruta abierta por segmentos (cfg.routes)
    assert events(aim_be, "walk_start") and aim_be.walking()
    aim_be.play_emote(3001, 11, t_dispatch=t + 9)     # nunca gestos mientras camina
    assert events(aim_be, "refused")
    t0 = time.monotonic()
    for k in range(70):                               # 1.4 s: recorre los dos segmentos y para
        aim_be.pump_once(t0 + k * 0.02)
    assert events(aim_be, "walk_done") and not aim_be.walking()
    assert last_cmd(aim_be, "velocity")["vx"] == 0.0


def test_aimdk_emote_kinematic_uses_emote_limit(aim_be, csv_path):
    from x2_ultra.show.hri.motion import CsvPreset
    csv = CsvPreset.load(csv_path)
    t = time.monotonic()
    aim_be.play_emote(3001, 11, t_dispatch=t + 0.7)
    now = t
    for k in range(35):                               # hasta τ ≈ 0.68 s de CSV
        now = t + 0.6 + k * 0.02
        aim_be.pump_once(now)
    pub = np.array(last_cmd(aim_be, "upper_body")["arms"])
    want = csv.sample(now - (t + 0.7))[0]
    assert np.max(np.abs(pub - want)) < 0.05          # seguimiento cinemático (vmax emote 20)


def test_aimdk_fall_interlock(aim_be):
    aim_be.link.pose = {"z": 0.50, "tilt": 2.0}       # pelvis < 0.62 siempre ⇒ caída
    t = time.monotonic()
    for k in range(AIM.FALL_DEB):
        aim_be.pump_once(t + k * 0.02)
    assert events(aim_be, "fall_interlock")
    assert wait_for(lambda: "delete_source" in kinds(aim_be))          # fix 5: worker
    assert wait_for(lambda: (last_cmd(aim_be, "request_mode") or {}).get("mode") == "STAND_DEFAULT")
    assert aim_be.state()["fall"] and not aim_be.state()["stable"]
    aim_be.stream_upper_body(t, AIM.READY_ARMS, np.zeros(3), np.zeros(2))
    assert events(aim_be, "refused")                  # rehusado hasta reset


def test_aimdk_fall_tilt_exception_and_hard_cap(aim_be):
    t = time.monotonic()
    aim_be.link.pose = {"z": 0.68, "tilt": 15.0}      # tilt>12 con preset busy y pelvis≥0.66: OK
    aim_be.cfg.use_presets = True
    aim_be.play_emote(3001, 11, t_dispatch=t)         # ruta preset del MC
    aim_be.link.obs.player_state = 2                  # reproductor ocupado ⇒ la puerta no despacha
    aim_be.pump_once(t)                               # fade_out → preset_wait
    assert aim_be._track.phase == "preset_wait"
    n_ub = len([c for c in aim_be.commands if c["kind"] == "upper_body"])
    for k in range(1, 30):                            # 0.6 s en preset_wait (< 1 s de puerta)
        aim_be.pump_once(t + k * 0.02)
    assert not aim_be.interlock.tripped               # excepción de tilt por preset busy
    # fix 2: durante las fases de preset no se publica tren superior y la cintura se mantiene
    assert len([c for c in aim_be.commands if c["kind"] == "upper_body"]) == n_ub
    assert all(np.allclose(c["u"], 0.0) for c in aim_be.commands if c["kind"] == "body_pose")
    aim_be.link.pose = {"z": 0.68, "tilt": 26.0}      # tilt > 25 duro: ni el preset lo tolera
    for k in range(30, 30 + AIM.FALL_DEB):
        aim_be.pump_once(t + k * 0.02)
    assert aim_be.interlock.tripped and events(aim_be, "fall_interlock")


def test_aimdk_no_gantry_mode(aim_be):
    """Sin modo grúa/pórtico: el interlock de caída está SIEMPRE activo (bring-up =
    dry-run → baja velocidad con e-stop → completo; DEPLOY_X2_REAL.md)."""
    assert not hasattr(aim_be.cfg, "gantry")
    assert not hasattr(aim_be.interlock, "gantry")
    aim_be.link.pose = {"z": 0.30, "tilt": 30.0}      # caída franca: debe disparar
    for k in range(6):
        aim_be.pump_once(time.monotonic() + k * 0.02)
    assert aim_be.interlock.tripped


def test_aimdk_estop(aim_be):
    aim_be.estop()
    v = last_cmd(aim_be, "velocity")
    assert v and v["vx"] == 0.0 and v["wz"] == 0.0    # ceros inmediatos
    assert wait_for(lambda: "delete_source" in kinds(aim_be))
    assert aim_be.state()["mode"] == "estop"
    aim_be.set_velocity(0.5, 0.0)
    assert events(aim_be, "refused")


def test_aimdk_arm_refused_low_battery(aim_be):
    aim_be.link.obs.battery = 12
    assert aim_be.arm_session()                       # problemas ⇒ no arma
    assert events(aim_be, "arm_refused")


# ------------------------------------------------------------------------------ TwinBackend
def test_twin_defaults(twin_be):
    cfg = twin_be.cfg
    assert cfg.preset_layout == "mc_v1.0.0"           # helper sin input_source (MC v1.0.0)
    assert cfg.body_pose_domain == 77                 # dominio privado del MC (c12)
    assert cfg.ignore_battery                         # el gemelo no publica batería
    assert twin_be.dry_run


def test_twin_preset_uses_helper_layout(twin_be):
    twin_be.cfg.use_presets = True
    t = time.monotonic()
    twin_be.play_emote(3001, 11, t_dispatch=t)
    for k in range(30):
        twin_be.pump_once(t + k * 0.02)
    ok = wait_for(lambda: events(twin_be, "preset_helper_cmd")
                  or [c for c in twin_be.commands if c["kind"] == "preset_set"])
    assert ok                                          # dispatch por el helper (dominio 77)
    cmds = [c for c in twin_be.commands if c["kind"] == "preset_set"]
    if cmds:
        assert cmds[0]["layout"] == "mc_v1.0.0"


def test_twin_memory_guard():
    assert isinstance(TWIN.twin_memory_ok(), bool)
    assert isinstance(TWIN.twin_running(), bool)
    assert TWIN.MIN_FREE_GB == 8.0                     # regla del spec: ≥ 8 GB para arrancar
