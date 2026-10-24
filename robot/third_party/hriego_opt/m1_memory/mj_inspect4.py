import sys, os, mujoco, numpy as np
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture"); import paths as P; sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap, trim
mode = sys.argv[1]; snap("pre")
if mode == "cap0":
    c = mujoco.mj_getCache(); print("cache cap", mujoco.mj_getCacheCapacity(c), file=sys.stderr); mujoco.mj_setCacheCapacity(c, 0)
m = mujoco.MjModel.from_xml_path(str(X.IK_XML)); snap("model")
c = mujoco.mj_getCache(); print("cache size", mujoco.mj_getCacheSize(c), "cap", mujoco.mj_getCacheCapacity(c), file=sys.stderr); mujoco.mj_clearCache(c); snap("after_clearCache")
trim(); snap("trim"); del m; trim(); snap("deleted")
