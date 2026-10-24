import numpy as np
import pytest

from x2_ultra.robot import omnihand as H


@pytest.mark.parametrize("side", ["L", "R"])
def test_normalized_roundtrip(side):
    u = np.linspace(0, 1, 10)
    assert np.allclose(H.to_normalized(side, H.to_radians(side, u)), u)


@pytest.mark.parametrize("side", ["L", "R"])
@pytest.mark.parametrize("pose", list(H.HAND_POSES))
def test_poses_within_range(side, pose):
    q = H.pose_radians(side, pose)
    joints = H.LEFT_HAND_JOINTS if side == "L" else H.RIGHT_HAND_JOINTS
    for qi, j in zip(q, joints):
        lo, hi = H.HAND_RANGE[j]
        assert lo - 1e-9 <= qi <= hi + 1e-9


def test_sdk_order_and_mirroring():
    assert len(H.HAND_JOINTS) == 20 and H.HAND_JOINTS[0] == "L_thumb_roll_joint" and H.HAND_JOINTS[10] == "R_thumb_roll_joint"
    for lj, rj in zip(H.LEFT_HAND_JOINTS, H.RIGHT_HAND_JOINTS):
        llo, lhi = H.HAND_RANGE[lj]
        rlo, rhi = H.HAND_RANGE[rj]
        assert np.isclose(abs(H.closed_end(lj)), abs(H.closed_end(rj)))   # mismo recorrido, signos espejados


def test_pinch_tips_touch(full_model):
    import mujoco
    m = full_model
    d = mujoco.MjData(m)
    mujoco.mj_resetDataKeyframe(m, d, 0)
    for side in "LR":
        joints = H.LEFT_HAND_JOINTS if side == "L" else H.RIGHT_HAND_JOINTS
        for j, v in zip(joints, H.pose_radians(side, "pinza")):
            d.qpos[m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, j)]] = v
    for mimic, (drv, k) in H.MIMIC.items():
        qa = lambda n: m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)]
        d.qpos[qa(mimic)] = k * d.qpos[qa(drv)]
    mujoco.mj_kinematics(m, d)
    sid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, n)
    for side in "LR":
        gap = np.linalg.norm(d.site_xpos[sid(f"{side}_thumb_tip")] - d.site_xpos[sid(f"{side}_index_tip")])
        assert gap < 0.02
