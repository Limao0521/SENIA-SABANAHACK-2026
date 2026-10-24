"""Chequeo cinemático de los emotes propios (backends/emotes.py) en el MJCF oficial del X2 Ultra:
autocolisiones (todas las geometrías activadas; MuJoCo excluye padre-hijo) y velocidad pico.

    MUJOCO_GL=glfw python scripts/check_emotes_mujoco.py
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

import mujoco  # noqa: E402
import numpy as np  # noqa: E402

from x2_ultra.hri_runtime.backends.emotes import LIBRARY, _pose  # noqa: E402
from x2_ultra.robot import joints as J  # noqa: E402

XML = REPO / "third_party/agibot_x2_urdf/X2_URDF-v1.3.0/x2_ultra.xml"
m = mujoco.MjModel.from_xml_path(str(XML))
m.geom_contype[:] = 1
m.geom_conaffinity[:] = 1
d = mujoco.MjData(m)
qadr = {n: m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, n)] for n in J.ARM_JOINTS}


def body_of(g: int) -> str:
    return mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, m.geom_bodyid[g]) or "?"


def contacts(q) -> list:
    for i, n in enumerate(J.ARM_JOINTS):
        d.qpos[qadr[n]] = q[i]
    mujoco.mj_forward(m, d)
    return sorted({tuple(sorted((body_of(c.geom1), body_of(c.geom2))))
                   for c in d.contact[:d.ncon] if c.dist < -0.002})


ctrl = contacts(_pose(r_pitch=0.0, r_roll=0.05, r_yaw=1.5, r_elbow=-2.3))
print("control (debe chocar):", ctrl or "NINGUNA: el chequeo no es fiable")
bad = 0
for name, clip in LIBRARY.items():
    traj = np.array([clip.sample(float(t))[0] for t in np.arange(0, clip.duration + 1e-9, 0.02)])
    vmax = float(np.abs(np.diff(traj, axis=0) / 0.02).max())
    hits = sorted({h for q in traj for h in contacts(q)})
    bad += bool(hits)
    print(f"{name:18s} dur {clip.duration:4.1f}s vmax {vmax:4.2f} rad/s  colisiones: {hits or 'ninguna'}")
sys.exit(1 if bad or not ctrl else 0)
