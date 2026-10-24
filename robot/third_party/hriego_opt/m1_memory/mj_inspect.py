import sys, os, mujoco
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture"); import paths as P; sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap
snap("pre")
m = mujoco.MjModel.from_xml_path(str(X.IK_XML)); snap("model")
for a in "nbody ngeom nmesh nmeshvert nmeshface nmeshgraph nmeshpoly ntex ntexdata nhfielddata nsdfdata nnames npaths nuserdata nbuffer nv nq nJ nM nC narena nconmax njmax nu nsensor nskin nnumeric ntext nB".split():
    if hasattr(m, a): print(a, getattr(m, a), file=sys.stderr)
d = mujoco.MjData(m); snap("data", extra={"narena": d.narena if hasattr(d,'narena') else None})
print(open(X.IK_XML).read()[:1500], file=sys.stderr)
