"""One load in a fresh process. usage: mem_bench.py <ik|sonic> <full|v1|v2> <mjb|xml> -> json line (RSS/PSS after load+MjData+trim, peak RSS, load s, nbuffer, step ms)."""
import sys, time, json
import numpy as np, mujoco
from paths_o1a import *
name, var, srcK = sys.argv[1:4]
r0, p0 = rss_pss(); t = time.perf_counter()
if srcK == "mjb": m = mujoco.MjModel.from_binary_path(str((HERE / f"out/{name}_full_{mujoco.__version__}.mjb") if var == "full" else (MODELS / f"{name}_{var}_{mujoco.__version__}.mjb")))
else: m = mujoco.MjModel.from_xml_path(str(SRC[name] if var == "full" else MODELS / f"{name}_{var}.xml"))
load = time.perf_counter() - t; mujoco.mj_setCacheCapacity(mujoco.mj_getCache(), 0); d = mujoco.MjData(m); trim(); r1, p1 = rss_pss()
hwm = [int(l.split()[1]) for l in open("/proc/self/status") if l.startswith("VmHWM")][0] / 1024
d.qpos[:] = m.qpos0; d.qpos[2] += 0.05; mujoco.mj_forward(m, d); t = time.perf_counter()
for _ in range(400): mujoco.mj_step(m, d)
step_ms = (time.perf_counter() - t) / 400 * 1000
print(json.dumps(dict(model=name, variant=var, src=srcK, mujoco=mujoco.__version__, load_s=round(load, 3), rss_base=round(r0), rss_mb=round(r1, 1), pss_mb=round(p1, 1), model_rss_mb=round(r1 - r0, 1), model_pss_mb=round(p1 - p0, 1), peak_rss_mb=round(hwm), nbuffer_mb=round(m.nbuffer / 2**20, 2), mj_step_ms=round(step_ms, 3))))
