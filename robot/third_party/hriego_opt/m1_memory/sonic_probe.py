import os, sys, time, json
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE)); from memlib import snap, trim
MODE = sys.argv[1]   # full | minimal
LOG = HERE / "logs" / f"sonic_{MODE}.jsonl"; LOG.unlink(missing_ok=True); S0 = lambda l, **k: snap(l, LOG, extra=k or None)
sys.path.insert(0, str(HERE.parent.parent)); import paths as P
os.environ.setdefault("MUJOCO_GL", "egl"); S0("00_start")
if os.environ.get("MJB"):   # precompiled .mjb for the SONIC MJCF (built once)
    import mujoco as _mj; SX = "/home/luis/GitHub/Laplace/mimo/X2/vendor/x2_external_evidence/sonic_x2_c9959443/assets/mjcf/x2_ultra.xml"; MB = HERE / "out/mjb/sonic_x2_ultra.mjb"
    _orig = _mj.MjModel.from_xml_path
    if not MB.exists(): _mj.mj_saveModel(_orig(SX), str(MB))
    _mj.MjModel.from_xml_path = staticmethod(lambda path, *a, **k: _mj.MjModel.from_binary_path(str(MB)) if os.path.abspath(str(path)) == SX else _orig(path, *a, **k))
sys.path.insert(0, str(P.MOTION_DIR)); import x2_sonic as X; S0("01_import_x2_sonic(mujoco,scipy,numpy)")
sys.path.insert(0, str(X.SONIC_SCRIPTS)); sys.path.insert(0, str(X.RETARGETER))
import eval_x2_mujoco_onnx as runner; S0("02_import_eval_x2_mujoco_onnx(onnxruntime,viewer)", torch="torch" in sys.modules)
onnx = str(X.SONIC_ROOT / "models/x2_sonic_14000_g1.onnx"); actor = runner.OnnxActor(onnx); S0("03_onnx_session"); trim(); S0("03b_trim")
from eval_x2_mujoco import MJCF_PATH
import mujoco
if MODE == "minimal":
    m = mujoco.MjModel.from_xml_path(MJCF_PATH); d = mujoco.MjData(m); S0("04_mujoco_model", nbuffer_mb=round(m.nbuffer / 2**20), nmeshvert=m.nmeshvert); trim(); S0("04b_trim")
    mc = mujoco.mj_getCache(); S0("04c_cache", cache_mb=round(mujoco.mj_getCacheSize(mc) / 2**20)); mujoco.mj_clearCache(mc); trim(); S0("04d_clear_cache_trim"); sys.exit()
class Cached:
    def __new__(cls, *a, **k): return actor
runner.OnnxActor = Cached; orig_rec = runner.VideoRecorder
src = HERE / "out/gmr_prod_0_c010/retarget"; name = "c010"; o = HERE / ("out/sonic_probe_mjb" if os.environ.get("MJB") else "out/sonic_probe"); o.mkdir(exist_ok=True)
for i in range(2):
    X.RETARGET = src; X.SONIC_OUT = o / "sonic"; X.LOGS = o / "logs"; X.LOGS.mkdir(exist_ok=True); os.environ["X2_TRACE_DIR"] = str(o / "traces"); os.environ["X2_TRACE_TAG"] = name
    t = time.time(); c = X.convert_one(name, True); S0(f"10_convert_{i}", s=round(time.time() - t, 2)); runner.VideoRecorder = orig_rec; t = time.time()
    r = X.rollout_one(name, True, None, record=False); S0(f"11_rollout_{i}", s=round(time.time() - t, 2), sim_s=round(r["simulated_seconds"], 1))
trim(); S0("12_trim")
print("HWM", [l.strip() for l in open("/proc/self/status") if l.startswith("VmHWM")], file=sys.stderr)
