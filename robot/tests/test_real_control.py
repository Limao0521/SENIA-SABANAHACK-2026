"""Programas del X2 real sin ROS ni robot: lógica de control y protecciones con un enlace falso."""
import copy
import time
from types import SimpleNamespace

import pytest

from x2_ultra.real import control as K
from x2_ultra.real.arms import ArmsController
from x2_ultra.real.walk import WalkController
from x2_ultra.robot.joints import ARM_JOINTS, HEAD_JOINTS, JOINT_RANGE

# Postura de brazos medida el 19/10 (aprox.): hombro roll izquierdo a 0,02 rad de su tope.
ANCHOR = {n: 0.0 for n in ARM_JOINTS}
ANCHOR.update({"left_elbow_joint": -1.17, "right_elbow_joint": -1.17, "left_shoulder_roll_joint": -0.04,
               "right_shoulder_roll_joint": 0.04})


def good_obs(mode=K.MODE_STAND, **kw):
    obs = K.Observed(mode=mode, action_status=K.ACTION_RUNNING, input_source="app_proxy", body_status=K.BODY_STAND,
                     player_state=K.PLAYER_IDLE, mc_age_s=0.05, battery=80, clock_offset_ms=6800.0, arm_age_s=0.01,
                     head_age_s=0.01, hands=K.HANDS_NONE, fsm=K.FSM_STABLE, squat_offset=0.0)
    for k, v in kw.items():
        setattr(obs, k, v)
    return obs


class FakeLink:
    source = "sdk_x2_ultra_prueba_000000"

    def __init__(self, mode=K.MODE_STAND):
        self.obs = good_obs(mode)
        self.registered = False
        self.velocity, self.upper, self.modes, self.body_pose = [], [], [], []
        self.body_pose_pub = object()
        self.arms = dict(ANCHOR)
        self.node = SimpleNamespace(count_subscribers=lambda _t: 1)

    def observed(self, graph=True):
        return copy.copy(self.obs)

    def register_source(self, priority):
        self.registered = True

    def delete_source(self):
        self.registered = False

    def publish_velocity(self, vx, vy, wz, stamp_mode):
        self.velocity.append((vx, vy, wz))

    def publish_body_pose(self, rate, stamp_mode):
        self.body_pose.append(rate)
        if self.obs.squat_offset is not None:              # el MC integra a 0,1/s con ±0,5
            self.obs.squat_offset = min(0.0, max(K.SQUAT_LOW, self.obs.squat_offset + rate * 0.2 * 0.02))
            self.obs.squat_enable = self.obs.squat_offset < 0

    def publish_upper_body(self, head, arms, hand_sub_mode, hand_pos, stamp_mode):
        self.upper.append((list(head), list(arms), hand_sub_mode, list(hand_pos)))

    def request_mode(self, desc, **kw):
        self.modes.append(desc)
        self.obs.mode = desc

    def wait_mode(self, desc, timeout_s):
        return self.obs.mode == desc

    def wait_stable(self, timeout_s):
        return self.obs.fsm == K.FSM_STABLE

    def latest(self, group, max_age_s=0.15):
        return dict(self.arms) if group == "arms" else dict.fromkeys(HEAD_JOINTS, 0.0)

    def hand_positions(self):
        return None

    def odometry(self):
        return None


class NullLog:
    def write(self, *a, **k):
        pass


# --- marcha ------------------------------------------------------------------------------------------------------

def test_shaper_starts_at_threshold_ramps_and_stops_at_once():
    shaper = K.VelocityShaper(K.WalkLimits(vx=0.3))
    assert shaper.step({"w"}, False, 0.02) == (0.2, 0.0, 0.0)
    for _ in range(50):
        vx = shaper.step({"w"}, False, 0.02)[0]
    assert vx == pytest.approx(0.3)
    assert shaper.step(set(), False, 0.02) == (0.0, 0.0, 0.0)


def test_shaper_reverses_through_zero_and_caps_fast():
    shaper = K.VelocityShaper(K.WalkLimits(vx=0.4, fast=3.0))
    shaper.step({"w"}, True, 0.02)
    assert shaper.step({"s"}, False, 0.02)[0] == 0.0
    assert shaper.step({"s"}, False, 0.02)[0] == -0.2
    assert shaper.targets({"w"}, True)[0] == K.WALK_CAPS["vx"]
    assert shaper.targets({"w", "s"}, False)[0] == 0.0
    assert shaper.targets({"q"}, False)[2] > 0 and shaper.targets({"a"}, False)[1] > 0   # + izquierda


@pytest.mark.parametrize("kw", [{"vx": 0.1}, {"vx": 0.9}, {"wz": 0.05}, {"vy": 0.9}, {"wz": 1.1}, {"fast": 5.0}])
def test_walk_limits_reject_out_of_range(kw):
    with pytest.raises(ValueError):
        K.WalkLimits(**kw).validate()


def test_speed_levels_scale_within_start_thresholds_and_caps():
    shaper = K.VelocityShaper(K.WalkLimits())                    # 0,3 / 0,2 / 0,5 / 0,6 en el nivel 3
    assert shaper.axis_speeds() == pytest.approx({"vx": 0.3, "vx_back": 0.2, "vy": 0.5, "wz": 0.6})
    for _ in range(10):
        shaper.change_level(+1)
    assert shaper.level == len(K.SPEED_LEVELS) - 1
    assert shaper.axis_speeds() == pytest.approx({"vx": 0.6, "vx_back": 0.4, "vy": 0.8, "wz": 1.0})
    assert shaper.axis_speeds(fast=True) == pytest.approx(K.WALK_CAPS)
    for _ in range(10):
        shaper.change_level(-1)
    # Lateral y giro no bajan de lo que necesitó el robot para arrancar; adelante y atrás sí bajan.
    assert shaper.axis_speeds() == pytest.approx({"vx": 0.2, "vx_back": 0.2, "vy": 0.5, "wz": 0.6})
    slow = K.VelocityShaper(K.WalkLimits(vy=0.3))                # un valor elegido más bajo se respeta
    slow.change_level(-2)
    assert slow.axis_speeds()["vy"] == pytest.approx(0.3)


def walk(link=None, **kw):
    link = link or FakeLink()
    args = dict(priority=65, stamp_mode="robot", ignore_battery=False, max_s=600.0, log=NullLog())
    args.update(kw)
    return link, WalkController(link, K.WalkLimits(), **args)


def run_walk(ctl, keys, seconds, now):
    ctl.set_input(keys, False)
    for _ in range(int(seconds / 0.02)):
        now += 0.02
        ctl.ui_beat = now
        ctl.tick(now, 0.02)
    return now


def test_walk_arms_publishes_and_disarms_when_mc_changes_mode():
    link, ctl = walk()
    ctl._arm()
    assert ctl.armed and link.registered
    link.obs.input_source = link.source
    now = run_walk(ctl, {"w"}, 0.5, time.monotonic())
    assert link.velocity[0] == (0.2, 0.0, 0.0) and link.velocity[-1][0] > 0.2
    link.obs.mode = "DAMPING_DEFAULT"
    run_walk(ctl, {"w"}, 0.04, now)
    assert not ctl.armed and link.velocity[-1] == (0.0, 0.0, 0.0)


def test_walk_refuses_to_arm_without_battery_or_standing():
    link, ctl = walk()
    link.obs.body_status = 0
    ctl._arm()
    assert not ctl.armed and not link.registered and "de pie" in ctl.message
    link.obs = good_obs(battery=None)
    ctl._arm()
    assert not ctl.armed and "batería" in ctl.message
    _, ctl = walk(link, ignore_battery=True)
    ctl._arm()
    assert ctl.armed


def test_walk_disarms_if_mc_never_selects_our_source():
    link, ctl = walk()
    ctl._arm()
    now = run_walk(ctl, {"w"}, 0.9, time.monotonic())
    assert ctl.armed
    run_walk(ctl, {"w"}, 0.3, now)
    assert not ctl.armed and "app_proxy" in ctl.message and link.velocity[-1] == (0.0, 0.0, 0.0)


def test_walk_sends_zero_when_window_freezes_and_stops_at_duration():
    link, ctl = walk(max_s=5.0)
    ctl._arm()
    link.obs.input_source = link.source
    now = run_walk(ctl, {"w"}, 0.2, time.monotonic())
    ctl.tick(now + 0.3, 0.02)                      # sin latido de la ventana
    assert ctl.armed and link.velocity[-1] == (0.0, 0.0, 0.0)
    ctl.ui_beat = now + 6.0
    ctl.tick(now + 6.0, 0.02)
    assert not ctl.armed and "duracion" in ctl.message


def test_preflight_by_program():
    assert K.preflight(good_obs(), "andar", need_clock=True) == []
    assert K.preflight(good_obs(K.MODE_URS), "brazos", need_clock=False) == []
    assert K.preflight(good_obs(K.MODE_URS), "andar", need_clock=True)
    assert K.preflight(good_obs(K.MODE_LOCOMOTION), "brazos", need_clock=False)
    assert K.preflight(good_obs(clock_offset_ms=None), "andar", need_clock=True)
    assert K.preflight(good_obs(player_state=1), "andar", need_clock=True)
    assert K.preflight(good_obs(battery=25), "andar", need_clock=True)
    assert K.preflight(good_obs(mc_age_s=2.0), "andar", need_clock=True)
    assert K.preflight(good_obs(hands=(0, 0, 0, 1)), "brazos", need_clock=False)
    assert K.preflight(good_obs(upper_body_publishers=1), "brazos", need_clock=False)
    assert K.preflight(good_obs(velocity_publishers=2), "andar", need_clock=True) == []


def test_clock_offset_takes_least_delayed_sample():
    est = K.ClockOffsetEstimator()
    offset = 6_800_000_000
    for i in range(30):
        receipt = 1_000_000_000_000 + i * 2_000_000
        est.add(receipt + offset - (3_000_000 if i % 2 else 1_000_000), receipt, i * 2_000_000)
    assert est.estimate(60_000_000)["offset_ns"] == offset - 1_000_000
    assert est.estimate(60_000_000 + 300_000_000)["offset_ns"] is None      # muestras viejas


# --- brazos ------------------------------------------------------------------------------------------------------

def test_arm_jog_bounds_respect_limits_envelope_and_never_force_motion():
    jog = K.ArmJog(ANCHOR, envelope=0.4, speed=0.15)
    low, high = jog.bounds["left_shoulder_roll_joint"]
    assert low == -0.04                                                     # ya dentro del margen: no baja más
    assert high == pytest.approx(0.36)
    low, high = jog.bounds["left_elbow_joint"]
    assert (low, high) == (pytest.approx(-1.57), pytest.approx(-0.77))
    jog = K.ArmJog({**ANCHOR, "left_elbow_joint": -0.2}, envelope=0.4)
    assert jog.bounds["left_elbow_joint"][1] == JOINT_RANGE["left_elbow_joint"][1] - K.LIMIT_MARGIN
    jog = K.ArmJog(ANCHOR, envelope=0.4, speed=0.15)
    for _ in range(100):
        jog.jog("left_shoulder_roll_joint", -1, 0.02)
    assert jog.target["left_shoulder_roll_joint"] == -0.04
    for _ in range(50):
        jog.jog("left_elbow_joint", -1, 0.02)
    assert jog.target["left_elbow_joint"] == pytest.approx(-1.17 - 0.15)
    assert not jog.home_step(0.02)
    for _ in range(60):
        jog.home_step(0.02)
    assert jog.distance_home() == 0.0


@pytest.mark.parametrize("kw", [{"envelope": 0.9}, {"speed": 1.0}, {"envelope": 0.0}])
def test_arm_jog_rejects_unsafe_settings(kw):
    with pytest.raises(ValueError):
        K.ArmJog(ANCHOR, **kw)


def test_tracking_monitor_needs_a_sustained_error():
    mon = K.TrackingMonitor(limit=0.12, hold_s=0.5)
    target, measured = dict(ANCHOR), dict(ANCHOR)
    target["right_elbow_joint"] -= 0.2
    assert not mon.update(target, measured, 10.0)
    assert not mon.update(target, measured, 10.4)
    assert mon.update(target, measured, 10.5) and mon.worst[0] == "right_elbow_joint"
    assert not mon.update(dict(ANCHOR), measured, 10.6)


def arms(link=None, **kw):
    link = link or FakeLink()
    args = dict(envelope=0.4, speed=0.15, tracking=0.12, priority=65, stamp_mode="cero", ignore_battery=False,
                log=NullLog())
    args.update(kw)
    return link, ArmsController(link, **args)


def run_arms(ctl, direction, seconds, now, *, follow=None):
    ctl.set_direction(direction)
    for _ in range(int(round(seconds / 0.02))):
        now += 0.02
        ctl.ui_beat = now
        ctl.tick(now, 0.02)
        if follow is not None:
            follow()
    return now


def test_arms_request_urs_anchor_and_move_one_joint_only():
    link, ctl = arms()
    ctl._arm()
    assert link.modes == [K.MODE_URS] and ctl.armed and link.registered
    ctl.select(side="derecho", index=3)
    assert ctl.joint == "right_elbow_joint"

    def follow():                                   # el robot sigue el objetivo
        link.arms = dict(ctl.jog.target)
    run_arms(ctl, -1, 1.0, time.monotonic(), follow=follow)
    head, arm_pos, hand_mode, hand_pos = link.upper[-1]
    moved = {n: arm_pos[i] - ANCHOR[n] for i, n in enumerate(ARM_JOINTS) if abs(arm_pos[i] - ANCHOR[n]) > 1e-9}
    assert list(moved) == ["right_elbow_joint"] and moved["right_elbow_joint"] == pytest.approx(-0.15, abs=0.004)
    assert hand_mode == K.HAND_SUB_MODE_CLAW and hand_pos == [0.0, 0.0] and head == [0.0, 0.0]


def test_arms_freeze_when_tracking_fails_and_reanchor():
    link, ctl = arms()
    ctl._arm()
    now = run_arms(ctl, 1, 1.5, time.monotonic())   # el robot no se mueve
    frozen_target = ctl.jog.target["left_shoulder_pitch_joint"]
    assert ctl.frozen.startswith("seguimiento") and frozen_target < 0.12 + 0.15 * 0.6
    run_arms(ctl, 1, 0.5, now)
    assert ctl.jog.target["left_shoulder_pitch_joint"] == frozen_target and ctl.armed
    ctl.reanchor()
    assert not ctl.frozen and ctl.jog.target["left_shoulder_pitch_joint"] == 0.0


def test_arms_stop_publishing_when_mc_leaves_urs():
    link, ctl = arms()
    ctl._arm()
    now = run_arms(ctl, 0, 0.1, time.monotonic())
    sent = len(link.upper)
    link.obs.mode = K.MODE_STAND
    run_arms(ctl, 0, 0.1, now)
    assert not ctl.armed and len(link.upper) == sent


def test_arms_stand_request_only_at_home():
    link, ctl = arms()
    ctl._arm()
    def follow():
        link.arms = dict(ctl.jog.target)
    now = run_arms(ctl, -1, 0.5, time.monotonic(), follow=follow)
    ctl.request_stand()
    assert link.modes == [K.MODE_URS] and "postura inicial" in ctl.message
    ctl.home()
    run_arms(ctl, 0, 1.0, now, follow=follow)
    assert ctl.jog.distance_home() == 0.0
    ctl.request_stand()
    for _ in range(100):
        if not ctl.busy:
            break
        time.sleep(0.01)
    assert link.modes == [K.MODE_URS, K.MODE_STAND]


def test_arms_refuse_when_another_writer_or_unknown_hands():
    link, ctl = arms()
    link.obs.upper_body_publishers = 1
    ctl._arm()
    assert not ctl.armed and link.modes == []
    link.obs = good_obs(hands=(2, 2, 2, 2))
    ctl._arm()
    assert not ctl.armed


def test_walk_offroad_on_off_only_when_still_and_cleared_on_exit():
    link, ctl = walk()
    link.request_mode = lambda desc, **kw: link.modes.append(desc)      # en reposo el MC sigue en STAND_DEFAULT
    ctl._arm()
    link.obs.input_source = link.source
    now = run_walk(ctl, {"w"}, 0.2, time.monotonic())
    ctl.toggle_offroad()
    assert link.modes == [] and not ctl.offroad and "parado" in ctl.message
    now = run_walk(ctl, set(), 0.1, now)
    ctl.toggle_offroad()
    wait_idle(ctl)
    assert ctl.offroad and link.modes == [K.MODE_OFFROAD] and "ACTIVADO" in ctl.message   # ya estaba en STAND
    link.obs.mode = K.MODE_OFFROAD                                 # al andar se ve el estilo memorizado
    now = run_walk(ctl, {"w"}, 0.3, now)
    assert ctl.armed and link.velocity[-1][0] > 0 and ctl.gait == K.MODE_OFFROAD
    link.obs.mode = K.MODE_STAND
    now = run_walk(ctl, set(), 0.1, now)
    ctl.toggle_offroad()
    wait_idle(ctl)
    assert not ctl.offroad and link.modes[-2:] == [K.MODE_LOCOMOTION, K.MODE_STAND]   # LOCOMOTION_DEFAULT lo quita
    link.obs.mode = K.MODE_LOCOMOTION                              # tras LOCOMOTION_DEFAULT el MC se queda ahí
    now = run_walk(ctl, set(), 0.1, now)

    def mc(desc, **kw):                                            # STAND_DEFAULT se ve; LOCOMOTION_STEP no
        link.modes.append(desc)
        if desc == K.MODE_STAND:
            link.obs.mode = K.MODE_STAND
    link.request_mode = mc
    n = len(link.modes)
    ctl.toggle_offroad()
    wait_idle(ctl)
    assert link.modes[n:] == [K.MODE_STAND, K.MODE_OFFROAD] and ctl.offroad   # desde LOCOMOTION, antes STAND
    link.obs.mode = K.MODE_STAND
    ctl.shutdown()
    assert link.modes[-2:] == [K.MODE_LOCOMOTION, K.MODE_STAND] and not link.registered
    assert not set(link.modes) & {"DAMPING_DEFAULT", "PASSIVE_DEFAULT", "JOINT_DEFAULT", "ZERO_TORQUE_DEFAULT"}


def test_walk_warns_when_mc_walks_without_the_requested_offroad():
    link, ctl = walk()
    ctl._arm()
    link.obs.input_source = link.source
    ctl.offroad = True
    link.obs.mode = K.MODE_LOCOMOTION
    run_walk(ctl, {"w"}, 0.2, time.monotonic())
    assert ctl.gait == K.MODE_LOCOMOTION and "aviso" in ctl.message


def wait_idle(ctl):
    for _ in range(200):
        if not ctl.busy:
            return
        time.sleep(0.01)


def test_walk_mode_change_waits_for_stable_mc_and_blocks_walking():
    link, ctl = walk()
    ctl._arm()
    link.obs.input_source = link.source
    link.obs.fsm = 5                                               # MOVING: el MC aún no acepta cambios
    link.wait_stable = lambda t: (time.sleep(0.2), link.obs.fsm == K.FSM_STABLE)[1]
    now = run_walk(ctl, set(), 0.04, time.monotonic())
    ctl.toggle_offroad()
    assert ctl.changing
    sent = len(link.velocity)
    run_walk(ctl, {"w"}, 0.1, now)                     # no anda ni publica mientras cambia
    assert len(link.velocity) == sent
    wait_idle(ctl)
    assert link.modes == [] and not ctl.offroad and "no se estabilizó" in ctl.message


def test_walk_squat_down_up_only_still_and_blocks_walking():
    link, ctl = walk()
    ctl._arm()
    link.obs.input_source = link.source
    now = run_walk(ctl, {"f"}, 1.0, time.monotonic())                 # agacharse 1 s
    assert link.body_pose[-1] == -K.SQUAT_RATE and link.obs.squat_offset == pytest.approx(-0.1, abs=0.01)
    now = run_walk(ctl, set(), 0.1, now)                              # soltar: quieto a esa altura
    assert link.body_pose[-1] == 0.0
    now = run_walk(ctl, {"w"}, 0.3, now)                              # agachado no anda
    assert link.velocity[-1] == (0.0, 0.0, 0.0) and "levantarse" in ctl.message
    ctl.toggle_offroad()                                              # ni cambia de modo
    assert link.modes == [] and "agachado" in ctl.message
    now = run_walk(ctl, {"r"}, 2.0, now)                              # subir hasta de pie y parar solo
    assert not K.crouched(link.obs) and link.body_pose[-1] == 0.0
    run_walk(ctl, {"w"}, 0.2, now)
    assert link.velocity[-1][0] > 0


def test_walk_squat_refused_while_moving_or_unstable():
    link, ctl = walk()
    ctl._arm()
    link.obs.input_source = link.source
    now = run_walk(ctl, {"w"}, 0.2, time.monotonic())
    run_walk(ctl, {"w", "f"}, 0.2, now)
    assert set(link.body_pose) == {0.0} and "parado" in ctl.message
    link2, ctl2 = walk()
    ctl2._arm()
    link2.obs.mode, link2.obs.fsm = K.MODE_LOCOMOTION, 5               # recién parado: aún MOVING
    run_walk(ctl2, {"f"}, 0.2, time.monotonic())
    assert set(link2.body_pose) == {0.0} and "estable" in ctl2.message


def test_walk_stands_up_before_exit_and_squat_does_not_disarm():
    link, ctl = walk()
    ctl._arm()
    link.obs.input_source = link.source
    now = run_walk(ctl, {"f"}, 1.0, time.monotonic())
    for body in (K.BODY_SIT, K.BODY_SQUAT, K.BODY_UNKNOWN):           # lo que informó el robot al agacharse
        link.obs.body_status = body
        now = run_walk(ctl, {"f"}, 0.2, now)
        assert ctl.armed, body
    now = run_walk(ctl, set(), 0.1, now)
    assert ctl.armed
    ctl.shutdown()
    assert not K.crouched(link.obs) and link.body_pose[-1] == 0.0 and not link.registered


def test_walk_without_body_pose_type_reports_it():
    link, ctl = walk()
    link.body_pose_pub = None
    ctl._arm()
    run_walk(ctl, {"f"}, 0.1, time.monotonic())
    assert link.body_pose == [] and "build_aimdk_overlay" in ctl.message


def test_arms_refuse_while_crouched():
    assert K.preflight(good_obs(squat_offset=-0.2, body_status=K.BODY_SQUAT, squat_enable=True), "brazos",
                       need_clock=False)


@pytest.mark.parametrize("body", [2, 3, 6])
def test_walk_disarms_if_lying_or_falling_even_while_crouched(body):
    link, ctl = walk()
    ctl._arm()
    now = run_walk(ctl, {"f"}, 0.5, time.monotonic())
    link.obs.body_status = body
    run_walk(ctl, set(), 0.04, now)
    assert not ctl.armed


def test_walk_disarms_on_mc_safe_state():
    link, ctl = walk()
    ctl._arm()
    link.obs.fsm = K.FSM_SAFE
    run_walk(ctl, set(), 0.04, time.monotonic())
    assert not ctl.armed and "seguridad" in ctl.message


def test_unknown_body_status_is_not_standing_when_upright():
    assert not K.standing(good_obs(body_status=K.BODY_UNKNOWN))
    assert K.standing(good_obs(body_status=K.BODY_UNKNOWN, squat_offset=-0.4, squat_enable=True))


def test_arms_wait_for_stable_mc_before_urs_and_never_ask_unsafe_modes():
    link, ctl = arms()
    link.obs.fsm = 5                                               # recién andado: MOVING
    link.wait_stable = lambda t: False
    ctl._arm()
    assert not ctl.armed and link.modes == [] and "estabiliz" in ctl.message
    link.wait_stable = lambda t: True
    ctl._arm()
    assert ctl.armed and link.modes == [K.MODE_URS]
    now = run_arms(ctl, 0, 0.1, time.monotonic())
    ctl.request_stand()
    for _ in range(100):
        if not ctl.busy:
            break
        time.sleep(0.01)
    ctl.shutdown()
    assert link.modes == [K.MODE_URS, K.MODE_STAND]
    assert not set(link.modes) & {"DAMPING_DEFAULT", "PASSIVE_DEFAULT", "JOINT_DEFAULT", "ZERO_TORQUE_DEFAULT"}


def test_request_mode_retries_while_the_mc_is_busy_then_explains_in_spanish(monkeypatch):
    from x2_ultra.real import link as L
    replies = [(9, 2, "当前动作正在播放"), (9, 2, "当前动作正在播放"), (0, 1, "")]
    fake = L.RobotLink.__new__(L.RobotLink)
    fake.S = SimpleNamespace(SetMcAction=SimpleNamespace(Request=lambda: None))
    fake.set_action = None
    sent = []

    def send(client, make, name, **kw):
        code, status, msg = replies[len(sent)] if len(sent) < len(replies) else replies[-1]
        sent.append(name)
        return SimpleNamespace(response=SimpleNamespace(header=SimpleNamespace(code=code),
                                                        status=SimpleNamespace(value=status), message=msg))
    fake.send = send
    monkeypatch.setattr(L.time, "sleep", lambda s: None)
    fake.request_mode("STAND_DEFAULT", busy_retry_s=12.0)
    assert len(sent) == 3
    replies[:] = [(9, 2, "当前动作正在播放")]
    sent.clear()
    with pytest.raises(L.ModeRejected) as err:
        fake.request_mode("STAND_DEFAULT")                         # sin reintento: falla al primero
    assert len(sent) == 1 and err.value.code == 9 and "ocupado" in str(err.value)

