"""GMR (native_qp) on the 10 T3 fp16_s1 inputs with the IK model redirected to a chosen .mjb. env gmr (mujoco 3.13).
usage: drv_gmr.py <full|v1|v2|...>  -> out/gmr/<variant>/<clip>/retarget/*.npz + out/gmr/<variant>.json"""
import json, os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE)); from paths_o1a import *
var = sys.argv[1]; import mujoco
mjb = (HERE / f"out/ik_full_{mujoco.__version__}.mjb") if var == "full" else (MODELS / f"ik_{var}_{mujoco.__version__}.mjb")
T3 = HERE.parent / "t3_gmr_sonic/out/native/fp16_s1"; OUT = HERE / f"out/gmr/{var}"; OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
audit = X.verify_assets(); cpu = X.constrain_process()
sys.path.insert(0, str(X.RETARGETER)); sys.path.insert(0, str(X.GMR_ROOT)); os.chdir(X.GMR_ROOT)
import torch, batch_retarget_x2 as batch
from general_motion_retargeting.params import ROBOT_XML_DICT
from general_motion_retargeting.utils.smpl import get_gvhmr_data_offline_fast as fn
orig = mujoco.MjModel.from_xml_path; tl = {}
def fx(path, *a, **k):
    if str(path).endswith("x2_mocap_toe_proxy.xml"): t = time.perf_counter(); m = mujoco.MjModel.from_binary_path(str(mjb)); tl["load_s"] = time.perf_counter() - t; return m
    return orig(path, *a, **k)
mujoco.MjModel.from_xml_path = staticmethod(fx)
ROBOT_XML_DICT["agibot_x2"] = X.IK_XML; batch.X2_XML = X.IK_XML
so = sys.stdout; t0 = time.perf_counter(); batch.worker_init(canonical=True, canonical_height=X.CANONICAL_HEIGHT_M); sys.stdout = so
torch.set_num_threads(1); torch.set_grad_enabled(False); mujoco.mj_setCacheCapacity(mujoco.mj_getCache(), 0); trim()
print("init", round(time.perf_counter() - t0, 1), "s; model load", tl, "rss/pss", rss_pss(), flush=True)
rows = {}
for clip in sorted(d.name for d in T3.iterdir() if d.is_dir()):
    t = time.perf_counter()
    d = X.retarget_one(clip, batch, fn, cpu, audit, T3 / clip / "inputs", OUT / clip / "retarget", "native_qp")
    rows[clip] = dict(status=d["status"], qc=d["qc"], frames=d["source_frames"], wall_s=time.perf_counter() - t); trim()
    print("DONE", clip, d["status"], rows[clip]["wall_s"], rss_pss(), flush=True)
json.dump(dict(variant=var, mujoco=mujoco.__version__, mjb=str(mjb), load=tl, rows=rows), open(HERE / f"out/gmr/{var}.json", "w"), indent=1, default=str)
