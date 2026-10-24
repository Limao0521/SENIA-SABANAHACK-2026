"""SONIC convert+rollout (unchanged x2_sonic.convert_one/rollout_one) on the 10 T3 GMR references with the MJCF redirected to a chosen .mjb. env physicalai (3.11).
usage: drv_sonic.py <full|v1|v2|...> -> out/sonic/<variant>/<clip>/traces/full_<clip>.csv + out/sonic/<variant>.json"""
import json, os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE)); from paths_o1a import *
var = sys.argv[1]; os.environ.setdefault("MUJOCO_GL", "egl"); import mujoco
mjb = (HERE / f"out/sonic_full_{mujoco.__version__}.mjb") if var == "full" else (MODELS / f"sonic_{var}_{mujoco.__version__}.mjb")
T3 = HERE.parent / "t3_gmr_sonic/out/native/fp16_s1"; OUT = HERE / f"out/sonic/{var}"
orig = mujoco.MjModel.from_xml_path; tl = []
def fx(path, *a, **k):
    if str(path).endswith("x2_ultra.xml"): t = time.perf_counter(); m = mujoco.MjModel.from_binary_path(str(mjb)); tl.append(time.perf_counter() - t); return m
    return orig(path, *a, **k)
mujoco.MjModel.from_xml_path = staticmethod(fx)
sys.path.insert(0, str(P.MOTION_DIR)); import x2_sonic as X
sys.path.insert(0, str(X.SONIC_SCRIPTS)); sys.path.insert(0, str(X.RETARGETER))
import eval_x2_mujoco_onnx as runner
actor = runner.OnnxActor(str(X.SONIC_ROOT / "models/x2_sonic_14000_g1.onnx"))
class Cached:
    def __new__(cls, *a, **k): return actor
runner.OnnxActor = Cached; orig_rec = runner.VideoRecorder
rows = {}
for clip in sorted(d.name for d in T3.iterdir() if d.is_dir()):
    b = OUT / clip
    X.RETARGET = T3 / clip / "retarget"; X.SONIC_OUT = b / "sonic"; X.LOGS = b / "logs"; X.LOGS.mkdir(parents=True, exist_ok=True)
    os.environ["X2_TRACE_DIR"] = str(b / "traces"); os.environ["X2_TRACE_TAG"] = clip
    n0 = len(tl); t = time.perf_counter(); c = X.convert_one(clip, True); conv = time.perf_counter() - t; t = time.perf_counter()
    runner.VideoRecorder = orig_rec
    r = X.rollout_one(clip, True, None, record=False); roll = time.perf_counter() - t; trim()
    import csv; nrow = sum(1 for _ in open(b / "traces" / f"full_{clip}.csv")) - 1
    rows[clip] = dict(convert_s=conv, rollout_s=roll, sim_s=r["simulated_seconds"], ticks=nrow, ms_per_tick=1000 * roll / max(nrow, 1), status=c["status"], model_loads=len(tl) - n0, rss_pss=rss_pss())
    print("DONE", clip, rows[clip], flush=True)
json.dump(dict(variant=var, mujoco=mujoco.__version__, mjb=str(mjb), load_s=tl, rows=rows), open(HERE / f"out/sonic/{var}.json", "w"), indent=1, default=str)
