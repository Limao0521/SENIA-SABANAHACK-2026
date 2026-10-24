#!/usr/bin/env python3
"""GMR worker memory probe (env gmr or physicalai+mink PYTHONPATH). Mirrors workers/gmr_worker.load with snapshots; variants via env:
VAR, SUB=share (reuse canonical neutral SMPL-X for the subject model), CHUNK=N (chunked SMPL-X forward), TRIM=1, CLIPS=n, THREADS."""
import os, sys, time, json
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE)); import memlib
from memlib import snap, trim
VAR = os.environ.get("VAR", "gmr_prod"); SUB = os.environ.get("SUB", ""); CHUNK = int(os.environ.get("CHUNK", "0")); TRIM = os.environ.get("TRIM", "0") == "1"
LOG = HERE / "logs" / f"{VAR}.jsonl"; LOG.unlink(missing_ok=True); S0 = lambda l, **k: snap(l, LOG, extra=k or None)
sys.path.insert(0, str(HERE.parent.parent)); sys.path.insert(0, str(HERE.parent.parent / "workers"))
import paths as P
S0("00_start")
MJB = os.environ.get("MJB", "")
if MJB:   # redirect IK-model XML loads to a precompiled .mjb (no parse/compile transients, no asset cache)
    import mujoco as _mj
    _orig = _mj.MjModel.from_xml_path
    def _fx(path, *a, **k):
        if str(path).endswith("x2_mocap_toe_proxy.xml"): return _mj.MjModel.from_binary_path(str(HERE / "out/mjb" / (f"ik_{MJB}.mjb" if _mj.__version__.startswith("3.13") else f"ik_{MJB}_{_mj.__version__}.mjb")))
        return _orig(path, *a, **k)
    _mj.MjModel.from_xml_path = staticmethod(_fx)
sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
S0("01_import_x2_retarget(numpy)")
audit = X.verify_assets(); cpu = X.constrain_process()
sys.path.insert(0, str(X.RETARGETER)); sys.path.insert(0, str(X.GMR_ROOT)); os.chdir(X.GMR_ROOT)
import torch; torch.set_num_threads(1); torch.set_grad_enabled(False); S0("02_import_torch")
import mujoco; S0("03_import_mujoco")
import mink; S0("04_import_mink(qpsolvers)")
import batch_retarget_x2 as batch; S0("05_import_batch_retarget_x2(native_gmr,GMR pkg,smplx,scipy)")
from general_motion_retargeting.params import ROBOT_XML_DICT
from general_motion_retargeting.utils.smpl import get_gvhmr_data_offline_fast
ROBOT_XML_DICT["agibot_x2"] = X.IK_XML; batch.X2_XML = X.IK_XML
so = sys.stdout; batch.worker_init(canonical=True, canonical_height=X.CANONICAL_HEIGHT_M); sys.stdout = so
batch._worker["frames"] = get_gvhmr_data_offline_fast; S0("06_worker_init(canon smplx + MjModel)")
if SUB == "share":   # reuse canonical neutral model as the subject model (same npz) -> no 2nd SMPL-X
    batch._worker["subj_models"]["neutral"] = batch._worker["canon"]
if TRIM: trim(); S0("06b_trim")
S0("06c_top_objects", top=[(round(a), k, s, d) for a, k, s, d in memlib.top_objects(8)])
fn = get_gvhmr_data_offline_fast
if CHUNK:
    canon = batch._worker["canon"]; orig = canon.forward
    class Cat:  # minimal container with the fields GMR uses
        pass
    def fwd(**kw):
        n = kw["global_orient"].shape[0]; outs = []
        for s in range(0, n, CHUNK):
            sl = {k: (v[s:s + CHUNK] if (hasattr(v, "shape") and v.shape[0] == n and v.dim() > 1) else v) for k, v in kw.items()}
            outs.append(orig(**sl))
        o = Cat(); o.global_orient = torch.cat([x.global_orient for x in outs]); o.full_pose = torch.cat([x.full_pose for x in outs]); o.joints = torch.cat([x.joints for x in outs]); return o
    canon.forward = fwd
import numpy as np
clips = json.load(open(HERE.parent / "t2_streamtalk_precision/clips.json")); names = os.environ.get("CLIPS", "c010,c001,c010").split(",")
T2 = HERE.parent / "t2_streamtalk_precision/out/fp16_s1"
res = {"var": VAR, "clips": []}
for i, name in enumerate(names):
    z = np.load(T2 / name / f"{name}.npz", allow_pickle=False); pose, trans = z["poses"].astype(np.float32), z["trans"].astype(np.float32)
    betas = np.zeros(16, np.float32); betas[:10] = z["betas"].reshape(-1)[:10]
    out = HERE / "out" / f"{VAR}_{i}_{name}"; inp = out / "inputs"; inp.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(inp / f"{name}_gmr_stageii.npz", gender=np.asarray("neutral"), root_orient=pose[:, :3].copy(), pose_body=pose[:, 3:66].copy(), betas=betas, trans=trans.copy(), mocap_frame_rate=np.asarray(30.0, np.float32), source_model=np.asarray(name), source_frame_count=np.asarray(len(pose), np.int32), common_frame_count=np.asarray(len(pose), np.int32), adapter_schema=np.asarray("RCP01C_SMPLX_TO_X2_GMR_STAGEII_V1"))
    t = time.time(); d = X.retarget_one(name, batch, fn, cpu, audit, inp, out / "retarget"); w = time.time() - t
    r = S0(f"10_clip{i}_{name}_{len(pose)}fr", wall=round(w, 2), status=d["status"]); res["clips"].append(dict(name=name, wall=round(w, 2), status=d["status"]))
    if TRIM: trim()
S0("11_after_trim") if not TRIM else None
trim(); S0("12_final_after_malloc_trim")
res["vmhwm_mb"] = round(int([l for l in open("/proc/self/status") if l.startswith("VmHWM")][0].split()[1]) / 1024); json.dump(res, open(HERE / "logs" / f"{VAR}_run.json", "w"), indent=1); print("DONE", VAR, file=sys.stderr)
