"""Capa HRI del reto 2: planes de visita, temporización de gestos e interlock de seguridad.

Pruebas ligeras (sin simulación MuJoCo): lecturas de los datos precomputados de HRIEGO,
invariantes de ``plan_turn`` (ancla CTC ↔ stroke, retardo de audio acotado), overlay de
señalamiento y el interlock no negociable «nunca gestos caminando».
"""
from types import SimpleNamespace

import numpy as np
import pytest

from x2_ultra.robot import joints as J
from x2_ultra.show.engine import (WAIST_BOUNDS, WAIST_BOUNDS_PRESET, WAIST_SLEW, WAIST_SLEW_PRESET,
                                  WaistFilter)
from x2_ultra.show.hri.data import (DIRECTION_EMOTE, ROUTE_DIRECTION, HRIEGO_ROOT, VisitStore)
from x2_ultra.show.hri.motion import (AUDIO_LEADIN, FADE_OUT_S, REST_ARMS, TRACK_LAG_S, CsvPreset,
                                      SonicClip)
from x2_ultra.show.hri.player import CMD_EPS, MAX_AUDIO_SHIFT, HriPlayer, TurnPlan, plan_turn
from x2_ultra.show.hri.pointing import HEAD_AMP, HEAD_SIGN, LEAD_S, PREP_S, PointOverlay

CATEGORIES = ["vip", "socio", "invitado", "personal", "externo"]
GREETING_EMOTE = {"vip": "GREETING_VIP", "socio": "GREETING_REGULAR", "invitado": "GREETING_GUEST",
                  "personal": "GREETING_STAFF", "externo": "GREETING_EXTERNAL"}

pytestmark = pytest.mark.skipif(not HRIEGO_ROOT.is_dir(),
                                reason="datos precomputados de HRIEGO no disponibles")


@pytest.fixture(scope="module")
def store():
    return VisitStore()


class FakeRig:
    """Rig mínimo para el reproductor: solo lo que usa ``HriPlayer.tick``."""

    def __init__(self):
        self.dance = None
        self.cmd = np.zeros(3)
        self.cmd_target = np.zeros(3)
        self.arm_cmd = REST_ARMS.copy()
        self.waist_delta = np.zeros(3)
        self.head = (0.0, 0.0)
        self.arms_log: list[np.ndarray] = []
        self.gate_log: list[bool] = []
        self.gentle_log: list[bool] = []

    def set_arm_targets(self, q):
        self.arm_cmd = np.asarray(q, dtype=float).copy()
        self.arms_log.append(self.arm_cmd.copy())

    def set_waist_delta(self, q3):
        self.waist_delta = np.asarray(q3, dtype=float).copy()

    def set_waist_gate(self, on):
        self.gate_log.append(bool(on))

    def set_waist_gentle(self, gentle):
        self.gentle_log.append(bool(gentle))

    def set_head_targets(self, yaw, pitch):
        self.head = (float(yaw), float(pitch))

    def walk(self):
        self.cmd = np.array([0.4, 0.0, 0.0])

    def halt(self):
        self.cmd = np.zeros(3)
        self.cmd_target = np.zeros(3)


# ----------------------------------------------------------------------------- datos / planes
@pytest.mark.parametrize("category", CATEGORIES)
def test_visit_plan_structure(store, category):
    plan = store.visit_plan(category)
    assert [t.state for t in plan] == ["GREETING", "GUIDANCE", "DIALOGUE", "DIALOGUE", "DIALOGUE",
                                       "FAREWELL"]
    assert plan[0].emote == GREETING_EMOTE[category]
    guidance = plan[1]
    assert guidance.emote == DIRECTION_EMOTE[ROUTE_DIRECTION[category]]
    assert guidance.is_point and guidance.direction == ROUTE_DIRECTION[category]
    for t in plan:
        assert t.wav.is_file(), f"falta el WAV de {t.turn_id}"
        assert t.duration_s > 0.5
        assert t.sonic is None or SonicClip.load(t.sonic).cmd.shape[1] == 14


@pytest.mark.parametrize("category", CATEGORIES)
def test_plan_turn_timing(store, category):
    for turn in store.visit_plan(category):
        plan = plan_turn(turn, store)
        assert np.isfinite(plan.end) and plan.end > 0.0
        # audio: con SONIC el t0 del WAV cae en el cuadro 100 (2,0 s); retardo extra acotado
        assert AUDIO_LEADIN <= plan.audio_start <= AUDIO_LEADIN + MAX_AUDIO_SHIFT + 1e-9
        assert plan.end >= plan.audio_start + turn.duration_s
        if plan.preset is not None:
            # el desvanecido de salida debe caber antes del CSV (política del anchor_plan)
            assert plan.csv_start >= FADE_OUT_S
            offset = store.stroke_offset(turn.emote)
            assert plan.stroke_at == pytest.approx(plan.csv_start + offset + TRACK_LAG_S, abs=1e-6)
            # el stroke debe caer dentro de la habla
            assert plan.audio_start <= plan.stroke_at <= plan.audio_start + turn.duration_s + 0.5
            assert CsvPreset.load(store.csv_for(turn.emote)).duration == pytest.approx(
                plan.csv_end - plan.csv_start, abs=1e-6)
        elif plan.point is not None:
            anchor = turn.anchor_start_s if turn.anchor_start_s is not None else 0.35 * turn.duration_s
            assert plan.point.ts == pytest.approx(plan.audio_start + anchor - LEAD_S, abs=1e-6)
            assert plan.end >= plan.point.t_end


# ----------------------------------------------------------------------------- señalamiento
def test_point_overlay_geometry(store):
    targets = store.point_targets()
    base_arms, base_head = np.full(14, 0.1), np.array([0.05, 0.0])
    for direction in ("LEFT", "RIGHT", "FRONT"):
        ov = PointOverlay(direction, anchor_t=3.0, targets=targets)
        assert ov.weight(ov.ts - PREP_S - 0.1) == 0.0
        assert ov.weight(ov.ts + 0.5) == 1.0
        assert ov.weight(ov.t_end + 0.1) == 0.0
        arms, head = ov.apply(ov.ts + 0.5, base_arms, base_head)
        side = np.array([n.startswith(ov.side) for n in J.ARM_JOINTS])
        moved = np.isfinite(ov.target) & side
        assert np.allclose(arms[moved], ov.target[moved])          # brazo que señala → objetivo
        assert np.allclose(arms[~moved], base_arms[~moved])        # el resto sigue la base
        lo, hi = J.JOINT_RANGE["head_yaw_joint"]
        assert head[0] == pytest.approx(np.clip(0.05 + HEAD_SIGN[direction] * HEAD_AMP, lo, hi))


# ----------------------------------------------------------------------------- interlock
def test_no_gestures_while_walking(store):
    plans = [plan_turn(t, store) for t in store.visit_plan("vip")]
    rig = FakeRig()
    player = HriPlayer("ultra", plans)
    rig.walk()                                   # caminando: la secuencia no puede arrancar
    assert player.tick(0.0, rig) is True
    assert np.isnan(player.t0) and player.index < 0
    assert not [e for e in player.events if e["event"] == "turn_start"]
    rig.halt()                                   # parado: arranca y gesta
    player.tick(0.02, rig)
    assert player.t0 == pytest.approx(0.02)
    assert player.index == 0
    rig.walk()                                   # si vuelve a caminar: supresión inmediata
    player.tick(0.04, rig)
    assert player.violations == 1
    assert [e for e in player.events if e["event"] == "safety_gesture_suppressed"]
    assert np.allclose(rig.arms_log[-1], REST_ARMS)
    assert np.allclose(rig.waist_delta, 0.0)
    assert HriPlayer.standing(rig) is False
    rig.halt()
    assert HriPlayer.standing(rig) is True


# ----------------------------------------------------------------------------- filtro de waist (W1)
DT = 0.02


def test_waist_filter_bounds_slew_and_excess():
    """Bounds por eje, slew por eje y exceso (para el reparto a hombros/cabeza)."""
    wf = WaistFilter(3)
    ys = [wf.step(np.array([0.35, 0.15, 0.10]), DT) for _ in range(400)]
    Y = np.array(ys)
    assert np.all(np.abs(Y) <= WAIST_BOUNDS + 1e-9)                        # yaw ±0,10 pitch ±0,05 roll ±0,03
    assert np.all(np.abs(np.diff(Y, axis=0)) <= WAIST_SLEW * DT + 1e-9)    # yaw 0,4 pitch/roll 0,2 rad/s
    assert np.allclose(Y[-1], WAIST_BOUNDS)                                # converge al bound (crítico: sin overshoot)
    assert np.all(np.abs(Y[:, 0]) <= WAIST_BOUNDS[0] + 1e-9)               # nunca pasa de largo
    assert np.allclose(wf.excess, [0.35 - 0.10, 0.15 - 0.05, 0.10 - 0.03])  # lo recortado → spill
    assert np.allclose(WAIST_BOUNDS, [0.10, 0.05, 0.03])                   # legs2 c_b10k07


def test_waist_filter_gentle_preset_bow():
    """Modo preset cinemático (venia VIP 3001): cap de pitch 0,10 rad + slew 0,06 rad/s (D2 fix a)."""
    wf = WaistFilter(3)
    wf.set_gentle(True)
    ys = [wf.step(np.array([0.0, 0.60, 0.0]), DT) for _ in range(400)]     # el CSV pide pitch 0,60
    Y = np.array(ys)
    assert np.all(Y[:, 1] <= WAIST_BOUNDS_PRESET[1] + 1e-9)                # cap 0,10
    assert np.all(np.diff(Y[:, 1]) <= WAIST_SLEW_PRESET[1] * DT + 1e-9)    # slew 0,06 rad/s
    assert Y[-1, 1] == pytest.approx(0.10, abs=2e-3)
    ramp_s = float(np.argmax(Y[:, 1] >= 0.09)) * DT
    assert ramp_s >= 0.8 * 0.10 / 0.06                                     # la rampa dura ≈ cap/slew
    wf.set_gentle(False)                                                   # al salir: límites normales
    assert np.allclose(wf.bounds, WAIST_BOUNDS) and np.allclose(wf.slew, WAIST_SLEW)


def test_waist_filter_gain_and_reset():
    wf = WaistFilter(3)
    for _ in range(400):
        out = wf.step(np.array([0.1, 0.0, 0.0]), DT, gain=0.5)             # g=0,5 (equilibrio/gate)
    assert out[0] == pytest.approx(0.05, abs=2e-3)                         # la ganancia escala la salida
    wf.reset()
    assert np.allclose(wf.y, 0.0) and np.allclose(wf.excess, 0.0)
    assert np.allclose(wf.step(np.zeros(3), DT), 0.0)


def test_sonic_arm_amplitude_scaling():
    """Fix legs2 c_b10k07: los brazos SONIC se escalan k=0,7 alrededor del reposo
    (cmd = REST + k·(cmd − REST)); el pointing conserva la amplitud plena."""
    from x2_ultra.show.hri.motion import SONIC_ARM_AMP_K
    assert SONIC_ARM_AMP_K == pytest.approx(0.7)

    class FakeSonic:
        duration = 5.0
        def arms(self, t): return REST_ARMS + np.full(14, 0.5)
        def waist(self, t): return np.zeros(3)
        def head(self, t): return np.zeros(2)

    player = HriPlayer("ultra", [])
    plan = SimpleNamespace(sonic=FakeSonic(), point=None)
    arms, _, _ = player._base(plan, 1.0)
    assert np.allclose(arms, REST_ARMS + 0.7 * 0.5)                        # desplazamiento al 70 %
    plan.point = SimpleNamespace()                                         # turno de pointing: plena
    arms_p, _, _ = player._base(plan, 1.0)
    assert np.allclose(arms_p, REST_ARMS + 0.5)


def test_player_waist_gate_and_gentle(store):
    """g(t): el player abre la gate al empezar el turno, marca gentle en la ventana del preset
    (venia) y la cierra al terminar la secuencia."""
    plans = [plan_turn(t, store) for t in store.visit_plan("vip")[:1]]    # GREETING_VIP: con preset
    rig = FakeRig()
    player = HriPlayer("ultra", plans)
    t = 0.0
    while not player.done and t < 60.0:
        player.tick(t, rig)
        t += DT
    assert player.done
    assert rig.gate_log[0] is True, "el primer turno debe abrir la gate"
    assert rig.gate_log[-1] is False, "el fin de secuencia debe cerrar la gate"
    assert any(rig.gentle_log), "la ventana del preset debe activar el modo gentle"
    assert not rig.gentle_log[-1]


def test_sequence_starts_only_standing(store):
    plans = [plan_turn(t, store) for t in store.visit_plan("socio")]
    rig = FakeRig()
    rig.cmd_target = np.array([0.0, 0.0, CMD_EPS * 2])   # todavía asentando el comando
    player = HriPlayer("ultra", plans)
    player.tick(0.0, rig)
    assert np.isnan(player.t0)
    rig.cmd_target = np.zeros(3)
    player.tick(0.02, rig)
    assert player.t0 == pytest.approx(0.02)


def test_abort_to_rest(store):
    plans = [plan_turn(t, store) for t in store.visit_plan("vip")[:2]]
    rig = FakeRig()
    player = HriPlayer("ultra", plans)
    player.abort_to_rest(rig)
    assert player.done and np.allclose(rig.arm_cmd, REST_ARMS)
    assert player.tick(1.0, rig) is False


def test_detect_stroke_t_long_ramp():
    """Rampa larga (estilo SOCIAL_HEART): el argmax de velocidad cae a mitad de la subida, pero
    el stroke oficial (c08/c12) es el primer cruce del 90 % del desplazamiento máximo."""
    from x2_ultra.show.hri.logger import detect_stroke_t
    t = np.arange(0.0, 2.0, 0.02)
    dev = np.minimum(np.clip((t - 0.2) / 1.2, 0, None), 1.0) * 1.3   # rampa lenta → meseta 1,3 rad
    peak_i = int(np.argmax(np.abs(np.gradient(dev) / 0.02)))         # velocidad pico: en la rampa
    s = detect_stroke_t(t, dev)
    assert s == pytest.approx(0.2 + 1.2 * 0.9, abs=0.02)             # 90 % de 1,3 → ≈ 1,28 s
    assert t[peak_i] < s - 0.1                                       # el detector viejo adelantaría


def test_detect_stroke_t_handles_nan_and_flat():
    from x2_ultra.show.hri.logger import detect_stroke_t
    t = np.arange(0.0, 1.0, 0.02)
    assert detect_stroke_t(t, np.full_like(t, np.nan)) is None
    assert detect_stroke_t(t, np.zeros_like(t)) is None


def test_stroke_probe_matches_timeline(store):
    for emote in ("GREETING_VIP", "GREETING_REGULAR", "GREETING_GUEST", "GREETING_STAFF",
                  "GREETING_EXTERNAL", "SOCIAL_HEART", "FAREWELL_WAVE"):
        probe = store.stroke_probe(emote)
        assert probe is not None, emote
        joint, rest = probe
        assert isinstance(joint, str) and joint.endswith("_joint")
        assert isinstance(rest, float)
