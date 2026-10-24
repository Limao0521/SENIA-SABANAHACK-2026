import numpy as np

from x2_ultra.control.arm_ik import REST, ArmIK, mirror_pose
from x2_ultra.control.poses import ARM_POSES, POSE_BY_KEY
from x2_ultra.robot import joints as J


def test_ik_reaches_reachable_targets(full_model):
    ik = ArmIK(full_model)
    rng = np.random.default_rng(1)
    ok = 0
    for _ in range(30):
        q_true = rng.uniform(ik.lo["L"], ik.hi["L"])
        p, R = ik.fk("L", q_true)
        res = ik.solve("L", REST["L"], p, None, iters=100)
        ok += res.pos_err < 0.005
    assert ok >= 25


def test_ik_incremental_tracking(full_model):
    """Pequeños pasos desde la solución anterior (uso interactivo): debe seguir con error milimétrico."""
    ik = ArmIK(full_model)
    q = np.array([J.DEFAULT_POSE[n] for n in J.LEFT_ARM_JOINTS])
    p0, R0 = ik.fk("L", q)
    for k in range(40):
        target = p0 + np.array([0.004 * k, 0.0, 0.003 * k])
        res = ik.solve("L", q, target, R0, iters=25, rot_weight=0.2)
        q = res.q
        assert res.pos_err < 0.01


def test_mirror_symmetry(full_model):
    ik = ArmIK(full_model)
    p, R = ik.fk("L", REST["L"])
    pr, Rr = mirror_pose(p, R)
    p_right, R_right = ik.fk("R", REST["R"])
    assert np.allclose(pr, p_right, atol=2e-3) and np.allclose(Rr, R_right, atol=2e-2)


def test_poses_within_limits_and_keys_unique():
    lo = np.array([J.JOINT_RANGE[n][0] for n in J.ARM_JOINTS])
    hi = np.array([J.JOINT_RANGE[n][1] for n in J.ARM_JOINTS])
    for name, pose in ARM_POSES.items():
        for t in np.linspace(0, 2, 9):
            q = pose.target(t)
            assert np.all(q >= lo - 1e-6) and np.all(q <= hi + 1e-6), name
    assert len(POSE_BY_KEY) == len(ARM_POSES)
