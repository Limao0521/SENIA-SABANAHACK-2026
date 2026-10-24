"""Walking must wait until the club guide has lowered its arms."""
from types import SimpleNamespace

import numpy as np

from x2_ultra.show.guide import GuideTour, REST_ARMS, Walk


def _guide_with_raised_arms():
    velocities, poses, stops = [], [], []
    rig = SimpleNamespace(
        state="activo", dance=None, arm_cmd=REST_ARMS + 1.0,
        q_arms=np.arange(len(REST_ARMS)),
        info=SimpleNamespace(base_pos=np.zeros(3), yaw=0.0),
        stop_walking=lambda: stops.append(True),
        play_pose=lambda pose, t: poses.append((pose, t)),
        set_head_targets=lambda *args: None,
        set_velocity_target=lambda *args: velocities.append(args),
    )
    engine = SimpleNamespace(rigs={"ultra": rig}, data=SimpleNamespace(qpos=REST_ARMS + 1.0))
    tour = GuideTour("ultra", [Walk([(1.0, 0.0)])])
    return tour, engine, rig, velocities, poses, stops


def test_walk_waits_for_commanded_and_measured_arm_rest():
    tour, engine, rig, velocities, poses, stops = _guide_with_raised_arms()
    tour.update(engine, 0.0)
    assert poses == [("reposo", 0.0)] and stops and not velocities
    assert tour.status == "brazos a reposo antes de caminar"
    rig.arm_cmd = REST_ARMS.copy()
    tour.update(engine, 0.02)
    assert not velocities  # the real joints have not reached rest yet
    engine.data.qpos = REST_ARMS.copy()
    tour.update(engine, 0.04)
    assert velocities and velocities[-1][0] > 0


def test_walk_stops_if_arms_cannot_reach_rest():
    tour, engine, _rig, velocities, _poses, _stops = _guide_with_raised_arms()
    tour.update(engine, 0.0)
    tour.update(engine, 4.0)
    assert tour.done and tour.status == "detenida: brazos no llegaron a reposo"
    assert not velocities
