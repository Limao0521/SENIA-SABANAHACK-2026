import sys, time, numpy as np
sys.path.insert(0, "../m2_motion_server")
from m2common import *
import gmr_stream as GS
GS.load(str(O1A_IK)) if False else GS.load("/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/o1a_meshes/models/ik_v2_3.11.0.mjb")
z0 = np.load(P.READY_PROMPT, allow_pickle=True); betas = z0["betas"].astype(np.float32)
d = np.load(HERE / "out/offline_ref/c010/c010_smplx.npz"); p, t = d["poses"][:52], d["trans"][:52]
g = GS.GmrStream("native", 15, betas=betas, warm_iters=3)
for n in (52, 12, 52, 12, 8, 52, 12, 8, 26):
    t0 = time.perf_counter(); g.frames(p[:n].copy(), t[:n].copy()); print(n, round(1000 * (time.perf_counter() - t0), 1), "ms", flush=True)
import os; os._exit(0)
