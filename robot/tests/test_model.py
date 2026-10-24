import mujoco
import numpy as np

from x2_ultra.model.builder import BuildOptions, build_model
from x2_ultra.rl.env import build_train_model
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H


def test_full_model_structure(full_model):
    m = full_model
    assert m.nu == len(J.BODY_JOINTS) + len(H.HAND_JOINTS) == 51
    names = [mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_ACTUATOR, i) for i in range(m.nu)]
    assert names == list(J.BODY_JOINTS + H.HAND_JOINTS)        # orden de actuadores = orden SDK
    assert m.neq == len(H.MIMIC) == 12
    robot_mass = sum(m.body_mass[1:])
    assert abs(robot_mass - 45.182) < 0.01                       # URDF X2-Ultra_omnihand
    for side in "LR":
        for region in ("palm", "thumb", "index", "middle", "ring", "pinky"):
            assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SENSOR, f"{side}_touch_{region}") >= 0
        assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, f"{side}_grasp") >= 0


def test_joint_ranges_match_constants(full_model):
    m = full_model
    for name, (lo, hi) in {**J.JOINT_RANGE, **H.HAND_RANGE}.items():
        jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)
        assert jid >= 0, name
        assert np.allclose(m.jnt_range[jid], [lo, hi], atol=2e-3), name


def test_home_keyframe_feet_on_ground(full_model):
    m = full_model
    d = mujoco.MjData(m)
    mujoco.mj_resetDataKeyframe(m, d, 0)
    mujoco.mj_forward(m, d)
    contacts = [c for c in d.contact[:d.ncon]
                if mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, c.geom1) != "floor"
                and mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, c.geom2) != "floor"]
    assert not contacts, "la postura inicial no debe tener autocolisiones"
    assert 0.6 < d.qpos[2] < 0.7


def test_train_model_is_light():
    m = build_train_model()
    assert m.nmesh == 0 and m.nu == 31
    assert abs(sum(m.body_mass[1:]) - 45.182) < 0.01


def test_table_scene_has_objects():
    m = build_model(BuildOptions(scene="mesa"))
    for name in ("mesa", "cubo", "botella", "pelota"):
        assert mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, name) >= 0
