import os, sys, time
from pathlib import Path
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE)); from memlib import snap, trim, top_objects
LOG = HERE / "logs" / "gmr_steps.jsonl"; LOG.unlink(missing_ok=True); S0 = lambda l, **k: snap(l, LOG, extra=k or None)
sys.path.insert(0, str(HERE.parent.parent)); import paths as P; sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
sys.path.insert(0, str(X.RETARGETER)); sys.path.insert(0, str(X.GMR_ROOT)); os.chdir(X.GMR_ROOT)
import torch; torch.set_grad_enabled(False); S0("a_torch")
import mujoco, numpy as np; import batch_retarget_x2 as batch; S0("b_batch_import")
from native_gmr import NativeGMR; S0("c_native_gmr_import(GMR pkg+mink)")
from general_motion_retargeting.utils.smpl import get_smplx_data_offline_fast; S0("d_smpl_utils")
import smplx
t = time.time(); d = np.load("assets/body_models/smplx/SMPLX_NEUTRAL.npz", allow_pickle=True); keys = {k: (d[k].shape, d[k].dtype.str, round(d[k].nbytes / 2**20, 1)) for k in d.files}; big = sorted(keys.items(), key=lambda x: -x[1][2])[:8]; print("NPZ big arrays", big, file=sys.stderr); S0("e_npz_listed_only"); del d
canon = smplx.create("assets/body_models", "smplx", gender="neutral", use_pca=False); S0("f_smplx_create", s=round(time.time() - t, 2))
print("model buffers MB:", sorted([(round(b.numel() * b.element_size() / 2**20, 1), n, tuple(b.shape)) for n, b in list(canon.named_buffers()) + list(canon.named_parameters())], reverse=True)[:10], file=sys.stderr)
trim(); S0("g_after_trim")
h = batch._tpose_height(canon, torch.zeros(1, 16), torch); S0("h_tpose_height")
model = mujoco.MjModel.from_xml_path(str(X.IK_XML)); data = mujoco.MjData(model); S0("i_mjmodel", nbody=model.nbody, ngeom=model.ngeom, nbytes=int(model.nbuffer))
# the 2nd (subject) neutral model created lazily on first request:
sub = smplx.create("assets/body_models", "smplx", gender="neutral", use_pca=False); S0("j_second_smplx_neutral(subj)")
del sub; trim(); S0("k_after_del_trim")
# forward on N frames: peak
for n in (100, 300):
    out = canon(betas=torch.zeros(1, 16), global_orient=torch.zeros(n, 3), body_pose=torch.zeros(n, 63), transl=torch.zeros(n, 3), left_hand_pose=torch.zeros(n, 45), right_hand_pose=torch.zeros(n, 45), jaw_pose=torch.zeros(n, 3), leye_pose=torch.zeros(n, 3), reye_pose=torch.zeros(n, 3), return_full_pose=True); S0(f"l_forward_{n}fr_live"); del out; S0(f"l_forward_{n}fr_freed"); trim(); S0(f"l_forward_{n}fr_trim")
print("HWM", [l for l in open("/proc/self/status") if l.startswith("VmHWM")], file=sys.stderr)
