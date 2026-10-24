import sys, os, mujoco
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture"); import paths as P; sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap, trim
mode = sys.argv[1]; out = "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory/out/mjb"
if mode == "build":
    m = mujoco.MjModel.from_xml_path(str(X.IK_XML)); mujoco.mj_saveModel(m, out + "/ik_full" + ("" if mujoco.__version__.startswith("3.13") else "_" + mujoco.__version__) + ".mjb"); 
    spec = mujoco.MjSpec.from_file(str(X.IK_XML))
    for g in list(spec.geoms):
        if g.contype == 0 and g.conaffinity == 0: spec.delete(g)
    used = {g.meshname for g in spec.geoms if g.meshname}
    for me in list(spec.meshes):
        if me.name not in used: spec.delete(me)
    mujoco.mj_saveModel(spec.compile(), out + "/ik_lean" + ("" if mujoco.__version__.startswith("3.13") else "_" + mujoco.__version__) + ".mjb"); print(os.listdir(out), [os.path.getsize(out + "/" + f) >> 20 for f in os.listdir(out)])
else:
    snap("pre"); m = mujoco.MjModel.from_binary_path(f"{out}/ik_{mode}.mjb"); snap("loaded_mjb"); trim(); snap("trim"); d = mujoco.MjData(m); snap("data", extra=dict(nbuffer_mb=round(m.nbuffer / 2**20)))
