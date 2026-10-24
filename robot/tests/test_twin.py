"""Gemelo digital: las piezas que no dependen de ROS (modelo HAL, física, CDR, URS emulado, sensores).

Las que necesitan ROS 2 Humble y aimdk_msgs están en ``tests_twin/`` y se ejecutan en el contenedor
(``official_sim/twin.sh test``)."""
import math

import mujoco
import numpy as np
import pytest

from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H
from x2_ultra.twin import cdr
from x2_ultra.twin import contract as C
from x2_ultra.twin import urs
from x2_ultra.twin.physics import TwinPhysics
from x2_ultra.twin.shared import SharedState


@pytest.fixture(scope="module")
def phys():
    return TwinPhysics(scene="vacia")


def test_hal_model_uses_pure_torque_motors(phys):
    m = phys.model
    assert m.opt.timestep == pytest.approx(0.001)
    for name in J.BODY_JOINTS:
        a = m.actuator(name)
        assert m.actuator_gainprm[a.id, 0] == 1.0 and not m.actuator_biasprm[a.id].any()
        assert m.actuator_ctrlrange[a.id, 1] == pytest.approx(J.EFFORT_LIMIT[name])
    for key in C.CAMERAS:
        assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_CAMERA, f"cam_{key}") >= 0
    # sin compensación de gravedad en brazos y cabeza (en el robot real la pone quien manda)
    assert m.body_gravcomp[m.body("left_elbow_link").id] == 0


def test_gantry_holds_and_releases(phys):
    phys.reset(gantry=True)
    z0 = phys.data.xpos[phys.pelvis][2]
    phys.step(1000)                                    # sin órdenes: par 0, el pórtico sujeta
    assert abs(phys.data.xpos[phys.pelvis][2] - z0) < 0.03
    phys.set_gantry(False)
    phys.step(1500)
    assert phys.data.xpos[phys.pelvis][2] < z0 - 0.15  # sin pórtico ni MC, se cae


def test_pd_law_matches_joint_command(phys):
    phys.reset(gantry=True)
    names = C.JOINT_GROUPS["arm"]
    q_ref = np.array([J.DEFAULT_POSE[n] for n in names]) + 0.3
    phys.set_joint_command("arm", q_ref, np.zeros(14), np.full(14, 0.5), np.full(14, 40.0), np.full(14, 2.0))
    q, dq, _ = phys.joint_state("arm")
    tau = phys.joint_torques()[phys._slices["arm"]]
    expected = np.clip(40.0 * (q_ref - q) + 2.0 * (0 - dq) + 0.5, -phys.tau_max["arm"], phys.tau_max["arm"])
    np.testing.assert_allclose(tau, expected)


def _joint_command_cdr(names, vals) -> bytes:
    """JointCommandArray serializado a mano con el escritor CDR (cabecera + nombre y 5 float64 por eje)."""
    w = cdr.CdrWriter()
    w.time()
    w.string("frame")
    w.u32(7)
    w.time()
    w.u32(len(names))
    for n, row in zip(names, vals):
        w.string(n)
        for v in row:
            w.f64(float(v))
    return bytes(w.buf)


def test_cdr_command_roundtrip_and_state_template():
    names = C.JOINT_GROUPS["leg"]
    vals = np.random.default_rng(0).normal(size=(len(names), 5))
    got_names, got = cdr.parse_joint_command_array(_joint_command_cdr(names, vals))
    assert got_names == tuple(names)
    np.testing.assert_allclose(got, vals)
    t = cdr.joint_state_array(names)
    q = np.arange(len(names), dtype=float)
    out = t.pack(5, 6, {"position": q, "velocity": -q, "effort": 2 * q})
    view = np.frombuffer(out, dtype="<f8", offset=4, count=(len(out) - 4) // 8)
    np.testing.assert_allclose(view[t.idx["position"]], q)
    np.testing.assert_allclose(view[t.idx["effort"]], 2 * q)


def test_urs_hand_modes():
    joint = np.linspace(-0.5, 0.5, 20)
    np.testing.assert_allclose(urs.hand_targets(urs.HAND_DEXTEROUS_JOINT, joint), joint)
    claw = urs.hand_targets(urs.HAND_CLAW, [1.0, 0.0])         # izquierda abierta, derecha cerrada
    assert np.allclose(claw[:10], 0.0) and np.abs(claw[10:]).max() > 0.3
    fist = urs.hand_targets(urs.HAND_DEXTEROUS_GESTURE, [1, 1.0, 0, 1.0])
    np.testing.assert_allclose(fist[:10], H.to_radians("L", H.HAND_POSES["puño"]))
    assert np.allclose(fist[10:], 0.0)
    assert urs.hand_targets(urs.HAND_DEXTEROUS_JOINT, [0.1] * 4) is None


def test_urs_rules_age_mode_and_head():
    emu = urs.UpperBodyEmulator()
    arms = np.zeros(14)
    assert not emu.on_command(2, [0, 0], arms, [0] * 20, None)          # sin modo: se ignora
    emu.set_mode(urs.URS)
    assert not emu.on_command(2, [0, 0], arms, [0] * 20, 0.35)          # más de 200 ms: descartada
    assert emu.on_command(2, [0.3, 0.1], arms, [0] * 20, None)          # stamp 0: vale
    assert emu.head_target is None                                      # en URS la cabeza se ignora
    emu.set_mode(urs.HEAD_ONLY)
    assert emu.on_command(0, [0.3, 0.1], arms, [], 0.05)
    np.testing.assert_allclose(emu.head_target, [0.3, 0.1])


def test_urs_drives_arms_and_hands(phys):
    phys.reset(gantry=True)
    emu = urs.UpperBodyEmulator()
    emu.set_mode(urs.URS)
    target = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
    target[0] = target[7] = -1.0                                        # los dos hombros hacia delante
    hand = H.to_radians("L", H.HAND_POSES["agarre"]).tolist() + H.to_radians("R", H.HAND_POSES["agarre"]).tolist()
    emu.on_command(2, [0, 0], target, hand, None)
    for _ in range(2000):
        emu.apply(phys)
        phys.step(1)
    q, _, _ = phys.joint_state("arm")
    assert np.abs(q - target).max() < 0.06
    qh, _, _ = phys.hand_state()
    assert np.abs(qh - np.array(hand)).max() < 0.05


def test_shared_state_roundtrip():
    s = SharedState(5)
    try:
        r = SharedState(5, name=s.name)
        s.write(np.arange(5.0), 1.25, True)
        q, t, gantry, seq = r.read()
        np.testing.assert_allclose(q, np.arange(5.0))
        assert t == 1.25 and gantry and seq % 2 == 0
        r.close()
    finally:
        s.close()


def test_camera_intrinsics_and_lidar_cloud(phys):
    from x2_ultra.twin.sensors import POINT_DTYPE, LidarScanner, camera_setups
    for s in camera_setups("real"):
        assert (s.width, s.height) == (s.spec.width, s.spec.height)
        assert s.fy == pytest.approx((s.height / 2) / math.tan(math.radians(s.spec.fovy_deg) / 2))
    right = [s for s in camera_setups("reducida") if s.key == "stereo_right"][0]
    assert right.tx == pytest.approx(-right.fx * C.STEREO_BASELINE)
    assert POINT_DTYPE.itemsize == 32 and POINT_DTYPE.fields["timestamp"][1] == 24
    phys.reset(gantry=True)
    pts = LidarScanner(phys.model).scan(phys.data)
    assert len(pts) > 1000                                 # el suelo delante del robot
    d = np.sqrt(pts["x"] ** 2 + pts["y"] ** 2 + pts["z"] ** 2)
    assert d.min() >= C.LIDAR_RANGE[0] and d.max() <= C.LIDAR_RANGE[1]
