#!/usr/bin/env python3
"""Persistent GMR/X2 retarget worker (env gmr). Loads the pinned X2 IK model (batch_retarget_x2.worker_init) once.
Request {"id","smplx","name","out_dir"} -> SMPL-X npz is adapted to a GMR stageii npz (as sonic_batch.py does) and retargeted with the
unchanged c03 x2_retarget.retarget_one. Reply carries status (PASS/FAIL) and the GMR QC dict."""
import importlib.util, os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "workers"))
from common import serve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import paths as P
MOTION = P.MOTION_DIR  # vendored x2_retarget.py
S = {}

def load():
    sys.path.insert(0, str(MOTION)); import x2_retarget as X
    audit = X.verify_assets(); cpu = X.constrain_process()
    sys.path.insert(0, str(X.RETARGETER)); sys.path.insert(0, str(X.GMR_ROOT)); os.chdir(X.GMR_ROOT)
    import torch, batch_retarget_x2 as batch
    from general_motion_retargeting.params import ROBOT_XML_DICT
    from general_motion_retargeting.utils.smpl import get_gvhmr_data_offline_fast
    ROBOT_XML_DICT["agibot_x2"] = X.IK_XML; batch.X2_XML = X.IK_XML
    so = sys.stdout; batch.worker_init(canonical=True, canonical_height=X.CANONICAL_HEIGHT_M); sys.stdout = so
    batch._worker["frames"] = get_gvhmr_data_offline_fast
    torch.set_num_threads(1); torch.set_grad_enabled(False)
    if os.environ.get("GMR_SOLVER")=="analytic_gmr":
        from general_motion_retargeting.motion_retarget import GeneralMotionRetargeting
        batch._worker["GMR"] = GeneralMotionRetargeting
    S.update(X=X, batch=batch, audit=audit, cpu=cpu, fn=get_gvhmr_data_offline_fast)
    return {"cpu_limit": cpu}

def handle(req):
    import numpy as np
    X, name = S["X"], req["name"]; out = Path(req["out_dir"]); inp = out.parent / "inputs"; inp.mkdir(parents=True, exist_ok=True)
    t = time.time(); z = np.load(req["smplx"], allow_pickle=False)
    pose, trans = np.asarray(z["poses"], np.float32), np.asarray(z["trans"], np.float32)
    betas = np.zeros(16, np.float32); betas[:10] = np.asarray(z["betas"], np.float32).reshape(-1)[:10]
    np.savez_compressed(inp / f"{name}_gmr_stageii.npz", gender=np.asarray("neutral"), root_orient=pose[:, :3].copy(), pose_body=pose[:, 3:66].copy(), betas=betas, trans=trans.copy(),
                        mocap_frame_rate=np.asarray(30.0, np.float32), source_model=np.asarray(name), source_frame_count=np.asarray(len(pose), np.int32),
                        common_frame_count=np.asarray(len(pose), np.int32), adapter_schema=np.asarray("RCP01C_SMPLX_TO_X2_GMR_STAGEII_V1"))
    adapt = time.time() - t; t = time.time()
    d = X.retarget_one(name, S["batch"], S["fn"], S["cpu"], S["audit"], inp, out, os.environ.get("GMR_SOLVER","native_qp"))
    return dict(status=d["status"], qc=d["qc"], frames=d["source_frames"], adapt_s=round(adapt, 3), retarget_s=round(time.time() - t, 3), decision=str(out / f"{name}_DECISION_SUMMARY.json"))

if __name__ == "__main__": serve("gmr", load, handle)
