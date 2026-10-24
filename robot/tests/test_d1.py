"""Perro guía AgiBot D1: modelo, trote cinemático y acompañamiento por su cuenta."""
from __future__ import annotations

import numpy as np
import pytest

from x2_ultra import paths

try:
    paths.d1_root()
    HAVE_D1 = True
except FileNotFoundError:
    HAVE_D1 = False
needs_d1 = pytest.mark.skipif(not HAVE_D1, reason="falta el modelo del D1 (scripts/fetch_d1.sh)")


def test_leg_ik_inverts_the_forward_kinematics():
    from x2_ultra.model import d1
    for x, z in [(0.0, -0.3), (0.06, -0.32), (-0.07, -0.27)]:
        hip, knee = d1.leg_ik(x, z)
        np.testing.assert_allclose(d1._leg_fk(hip, knee), (x, z), atol=1e-9)
        assert knee < 0


@needs_d1
def test_d1_stands_sits_and_trots_with_its_feet_on_the_floor_and_inside_its_limits():
    import mujoco

    from x2_ultra.model import d1
    m = d1.build_d1_spec().compile()
    d = mujoco.MjData(m)
    feet = [g for g in range(m.ngeom) if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_SPHERE]
    assert len(feet) == 4 and not m.geom_contype.any() and (m.body_gravcomp[1:] == 1).all()
    lo, hi = m.jnt_range[1:].T
    for height, q in [(d1.STAND_HEIGHT, d1.stand_pose()), (d1.sit_height(), d1.sit_pose())]:
        d.qpos[:] = 0
        d.qpos[2], d.qpos[3], d.qpos[7:] = height, 1.0, q
        mujoco.mj_forward(m, d)
        np.testing.assert_allclose([d.geom_xpos[g][2] - m.geom_size[g][0] for g in feet], 0.0, atol=1e-6)
    for phase in np.linspace(0, 1, 40):
        q = d1.trot_pose(phase, 0.14)
        assert ((q >= lo) & (q <= hi)).all()


class _Guest:
    def __init__(self, xy):
        self.xy = np.asarray(xy, float)


@needs_d1
def test_d1_waits_for_a_guest_left_behind_and_comes_back_home():
    import dataclasses

    import mujoco

    from x2_ultra.show import guide
    from x2_ultra.show.club import DOG_HOME
    from x2_ultra.show.dog import DogRig
    from x2_ultra.show.stage import CLUB, DOG_PREFIX, build_stage_model
    model = build_stage_model(dataclasses.replace(CLUB, robots=()))
    data = mujoco.MjData(model)
    dog = DogRig(model, DOG_HOME, prefix=DOG_PREFIX)
    points, face, _ = guide._route("socio", 3)
    guest = _Guest((6.0, 0.6))
    dog.guide(guide.dog_route(points), face, guest)
    for _ in range(int(20 / 0.02)):
        dog.tick(data, 0.02)
    assert dog.mode == "esperando" and np.linalg.norm(dog.xy - guest.xy) > 3.0      # se quedó atrás: espera
    for _ in range(int(120 / 0.02)):
        guest.xy += np.clip(dog.xy - guest.xy, -0.02, 0.02) * (np.linalg.norm(dog.xy - guest.xy) > 1.2)
        dog.tick(data, 0.02)
        if dog.arrived:
            break
    assert dog.arrived and abs(dog.yaw - face) < 0.1
    dog.guest = None
    for _ in range(int(80 / 0.02)):
        dog.tick(data, 0.02)
        if dog.mode == "tumbado":
            break
    assert dog.mode == "tumbado" and np.linalg.norm(dog.xy - np.array(DOG_HOME[:2])) < 0.2
    qbase = dog.qbase
    np.testing.assert_allclose(data.qpos[qbase:qbase + 2], DOG_HOME[:2], atol=0.2)
