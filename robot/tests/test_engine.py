import numpy as np
import pytest

from x2_ultra import paths
from x2_ultra.robot import omnihand as H
from x2_ultra.sim.engine import SimEngine


@pytest.fixture(scope="module")
def agibot_engine():
    return SimEngine(scene="vacia", policy="agibot")


def test_stands_and_walks_with_agibot_policy(agibot_engine):
    eng = agibot_engine
    eng.reset()
    eng.run_for(2.0)
    assert eng.state == "activo"
    eng.set_velocity_target(0.4, 0.0, 0.0)
    x0 = eng.data.qpos[0]
    eng.run_for(4.0)
    assert eng.state == "activo"
    assert eng.data.qpos[0] - x0 > 0.8


def test_hands_follow_targets(agibot_engine):
    eng = agibot_engine
    eng.reset()
    eng.set_hand_pose("LR", "puño")
    eng.run_for(1.5)
    q = eng.data.qpos[eng.q_hands]
    target = np.concatenate([H.pose_radians("L", "puño"), H.pose_radians("R", "puño")])
    assert np.max(np.abs(q - target)) < 0.05


def test_fall_detection_and_reset(agibot_engine):
    eng = agibot_engine
    eng.reset()
    eng.run_for(0.5)
    eng.push(np.array([1.0, 0.0, 0.0]), force=900.0, duration=0.3)
    eng.run_for(3.0)
    assert eng.state == "caido"
    eng.reset()
    assert eng.state == "activo" and abs(eng.data.qpos[0]) < 1e-6


@pytest.mark.slow
@pytest.mark.skipif(not paths.default_policy().exists(), reason="policy 'brazos libres' no instalada")
def test_arms_free_policy_keeps_balance_with_arms_forward():
    eng = SimEngine(scene="vacia", policy="brazos_libres")
    eng.run_for(1.0)
    eng.play_pose("al_frente")
    eng.run_for(4.0)
    assert eng.state == "activo"
    eng.set_velocity_target(0.4, 0.0, 0.0)
    x0 = eng.data.qpos[0]
    eng.run_for(5.0)
    assert eng.state == "activo"
    assert eng.data.qpos[0] - x0 > 0.5


@pytest.mark.slow
@pytest.mark.skipif(not paths.default_policy().exists(), reason="policy 'brazos libres' no instalada")
def test_power_grasp_holds_objects():
    """Agarre cilíndrico: la botella, el cubo y la pelota no se caen al soltarlos dentro de la mano."""
    import mujoco
    from x2_ultra.robot import joints as J
    eng = SimEngine(scene="mesa", policy="brazos_libres")   # con la de AgiBot los brazos los mueve la policy
    m, d = eng.model, eng.data
    palm = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "R_palm")
    half = {"botella": 0.03, "cubo": 0.025, "pelota": 0.035}
    for obj, size in half.items():
        eng.reset()
        eng.run_for(0.5)
        hold = eng.arm_target.copy()
        for n, v in {"right_shoulder_pitch_joint": -1.1, "right_shoulder_roll_joint": -0.1, "right_elbow_joint": -0.6}.items():
            hold[J.ARM_JOINTS.index(n)] = v
        eng.set_arm_targets(hold)
        eng.run_for(2.0)
        jid = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, f"{obj}_libre")
        qa, va = m.jnt_qposadr[jid], m.jnt_dofadr[jid]
        rot = d.xmat[palm].reshape(3, 3)
        pose = np.zeros(7)
        pose[:3] = d.xpos[palm] + rot @ np.array([0.022 + size, 0.0, 0.09])
        z_axis = rot[:, 1]
        x_axis = np.cross([0, 0, 1.0], z_axis)
        x_axis /= np.linalg.norm(x_axis)
        mujoco.mju_mat2Quat(pose[3:], np.stack([x_axis, np.cross(z_axis, x_axis), z_axis], 1).reshape(-1))
        eng.set_hand_pose("R", "agarre")
        for _ in range(35):             # se sujeta quieto mientras la mano se cierra (como apoyado en la mesa)
            d.qpos[qa:qa + 7] = pose
            d.qvel[va:va + 6] = 0
            eng.step_control()
        eng.run_for(2.0)
        assert d.qpos[qa + 2] > pose[2] - 0.05, obj


@pytest.mark.slow
@pytest.mark.skipif(not paths.default_policy().exists(), reason="policy 'brazos libres' no instalada")
def test_table_demo_picks_up_bottle():
    """Toda la cadena: girar, andar hasta la mesa, IK hasta la botella, agarre, levantar y retroceder."""
    from x2_ultra.tools.demo_mesa import Demo
    info = Demo(None).run()
    assert info["estado"] == "activo"
    assert info["botella_levantada_m"] > 0.08 and info["botella_tras_retroceder_m"] > 0.08
