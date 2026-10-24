import sys, os, mujoco, numpy as np, ctypes
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture"); import paths as P; sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap, trim
print("mujoco", mujoco.__version__, file=sys.stderr)
mode = sys.argv[1]
snap("pre")
if mode == "full":
    m = mujoco.MjModel.from_xml_path(str(X.IK_XML))
else:
    spec = mujoco.MjSpec.from_file(str(X.IK_XML))
    for g in list(spec.geoms):
        if g.contype == 0 and g.conaffinity == 0: spec.delete(g)
    used = {g.meshname for g in spec.geoms if g.meshname}
    for me in list(spec.meshes):
        if me.name not in used: spec.delete(me)
    snap("spec_built"); m = spec.compile(); del spec
snap("model"); trim(); snap("model_trim", extra=dict(nbuffer_mb=round(m.nbuffer / 2**20), nmeshvert=m.nmeshvert))
d = mujoco.MjData(m); snap("data"); del d; del m; trim(); snap("deleted_model")
