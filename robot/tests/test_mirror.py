"""Espejo izquierda↔derecha de la policy propia: las convenciones de signo se comprueban contra la física."""
import mujoco
import numpy as np
import pytest

from x2_ultra import paths
from x2_ultra.control import obs_spec as S
from x2_ultra.control.locomotion import ArmsFreePolicy, _mirror_maps
from x2_ultra.robot import joints as J

pytestmark = pytest.mark.skipif(not paths.default_policy().exists(), reason="policy 'brazos libres' no instalada")


def _mirror_state(m: mujoco.MjModel, d: mujoco.MjData) -> mujoco.MjData:
    """Estado reflejado en el plano sagital del mundo (y → -y)."""
    dm = mujoco.MjData(m)
    dm.qpos[:] = d.qpos
    dm.qvel[:] = d.qvel
    dm.qpos[1] *= -1                                   # posición base
    w, x, y, z = d.qpos[3:7]
    dm.qpos[3:7] = (w, -x, y, -z)                      # R → M R M, M = diag(1, -1, 1)
    dm.qvel[1] *= -1                                   # velocidad lineal (mundo)
    dm.qvel[3:6] = d.qvel[3:6] * (-1, 1, -1)           # angular (marco local): vector axial
    perm, sign = _mirror_maps(J.BODY_JOINTS)
    jid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)
    qadr = np.array([m.jnt_qposadr[jid(n)] for n in J.BODY_JOINTS])
    vadr = np.array([m.jnt_dofadr[jid(n)] for n in J.BODY_JOINTS])
    dm.qpos[qadr] = d.qpos[qadr][perm] * sign
    dm.qvel[vadr] = d.qvel[vadr][perm] * sign
    return dm


def test_mirror_maps_are_involutions_and_keep_default_pose():
    for joints in (S.OBS_JOINTS, S.ACTION_JOINTS):
        perm, sign = _mirror_maps(joints)
        v = np.random.default_rng(0).normal(size=len(joints))
        np.testing.assert_allclose((v[perm] * sign)[perm] * sign, v)
        default = np.array([J.DEFAULT_POSE[n] for n in joints])
        np.testing.assert_allclose(default[perm] * sign, default)


def test_mirrored_observation_matches_physically_mirrored_state(full_model):
    m = full_model
    d = mujoco.MjData(m)
    rng = np.random.default_rng(1)
    mujoco.mj_resetDataKeyframe(m, d, 0)
    d.qpos[3:7] = (0.98, 0.08, -0.12, 0.15)            # orientación y estado articulares cualesquiera
    d.qpos[3:7] /= np.linalg.norm(d.qpos[3:7])
    d.qvel[:] = rng.normal(scale=0.5, size=m.nv)
    for n in J.BODY_JOINTS:
        j = m.joint(n)
        lo, hi = m.jnt_range[j.id]
        d.qpos[m.jnt_qposadr[j.id]] = rng.uniform(max(lo, -0.3), min(hi, 0.3))
    cmd = np.array([0.3, 0.2, -0.7])
    last = rng.normal(size=S.NUM_ACTIONS)

    pol = ArmsFreePolicy(m)
    pol.reset()
    pol.last_action = last
    pol.act(d, cmd)                                    # llena el historial (fresco) con el estado original
    mirrored_obs = pol._mirror_history()

    pol_m = ArmsFreePolicy(m)
    pol_m.reset()
    perm, sign = _mirror_maps(S.ACTION_JOINTS)
    pol_m.last_action = last[perm] * sign
    pol_m.act(_mirror_state(m, d), cmd * (1, -1, -1))
    direct = np.concatenate([pol_m.hist.buf[name].reshape(-1) for name, _ in S.OBS_TERMS])
    np.testing.assert_allclose(mirrored_obs, direct, atol=1e-9)


@pytest.mark.parametrize("wz", [1.0, -1.0])
def test_turns_in_place_both_ways(wz):
    """Lo que hacen Q y E en la GUI (rumbo mantenido, orden ±1 rad/s): girar en el sitio hacia los dos lados."""
    import math

    from x2_ultra.sim.engine import SimEngine
    eng = SimEngine(scene="vacia")
    eng.reset()
    eng.run_for(1.0)
    start = eng.data.qpos[:2].copy()
    eng.set_velocity_target(0.0, 0.0, wz)
    turned, prev = 0.0, eng.info.yaw
    for _ in range(200):                                # 4 s
        eng.step_control()
        turned += math.remainder(eng.info.yaw - prev, 2 * math.pi)
        prev = eng.info.yaw
    assert eng.state == "activo"
    assert turned * wz > 2.5, f"giró {turned:.2f} rad en 4 s"
    assert np.linalg.norm(eng.data.qpos[:2] - start) < 0.3
