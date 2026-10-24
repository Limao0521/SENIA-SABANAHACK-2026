"""physicalai: numpy ports vs torch/smplx originals (CPU). FK joints, rot6d/matrix/axis-angle chain, ONNX retrieval vs torch L1 pick."""
import json, sys, numpy as np, torch
from common_paths import *
add_st_paths(); sys.path.insert(0, str(HERE))
import onnx_st as O
from Common.SMPLX_Util import GetSMPLXModel, SplitPosesIntoSmplxParams, global_to_local_batch
from Common.Util import rotation_6d_to_matrix, rotation_matrix_to_axis_angle, NormalizeRot6d
a = np.load(HERE / "assets/st_assets.npz"); O.PARENTS = a["parents"]; res = {}
rng = np.random.RandomState(0)
# 1) FK on realistic poses = production output of a clip (+ random small perturbations)
poses = np.load(HERE / "out/torch_fp16/c001/c001.npz")["poses"][:120].astype(np.float32)
sm = GetSMPLXModel(str(P.smplx_neutral_npz())).eval(); betas = torch.from_numpy(a["betas"]).float()
with torch.no_grad():
    p = SplitPosesIntoSmplxParams(torch.from_numpy(poses)); p["global_orient"] = torch.zeros_like(p["global_orient"])
    jt = sm(betas=betas.expand(len(poses), -1), **p, transl=torch.zeros(len(poses), 3), expression=torch.zeros(len(poses), 100))["joints"][:, :55].reshape(len(poses), -1).numpy()
jn = O.fk_joints(a["J_rest"], a["parents"], poses); d = np.abs(jt - jn)
res["fk_joints"] = dict(max_m=float(d.max()), mean_m=float(d.mean()), q_f16_equal_frac=float(np.mean(jt.astype(np.float16) == jn.astype(np.float16))))
# 2) 6D -> local axis-angle chain on random + production-like 6D (noisy)
g = np.random.RandomState(1).randn(200, 55, 6).astype(np.float32)
t6 = torch.from_numpy(g); tm = rotation_6d_to_matrix(NormalizeRot6d(t6.reshape(-1, 6)).reshape(-1, 55, 6)); ta = rotation_matrix_to_axis_angle(global_to_local_batch(tm)).reshape(-1, 165).numpy()
na = O.matrix_to_axis_angle(O.global_to_local(O.rot6d_to_matrix(O.normalize_rot6d(g.reshape(-1, 6)).reshape(-1, 55, 6)))).reshape(-1, 165)
res["rot_chain_random6d"] = dict(max_abs=float(np.abs(ta - na).max()), mean_abs=float(np.abs(ta - na).mean()))
# 3) retrieval: ONNX (CPU EP here) vs torch L1 loop, on real queries (FK joints of production windows) + random DB rows
import onnxruntime as ort
db = np.load(KD_CACHE / "joints_f16.npy"); s = ort.InferenceSession(str(MODELS / "retrieval.onnx"), providers=["CPUExecutionProvider"])
dbt = torch.from_numpy(db).cuda() if torch.cuda.is_available() else torch.from_numpy(db)
agree = 0; n = 0; dd = []
for i in range(0, 112, 8):
    q = jn[i:i + 8].astype(np.float16); bd, bi = s.run(None, {"q": q})
    qt = torch.from_numpy(q).to(dbt.device); best = torch.full((8,), float("inf"), dtype=qt.dtype, device=qt.device); bidx = torch.zeros(8, dtype=torch.long, device=qt.device)
    for st in range(0, db.shape[0], 1024):
        e = min(st + 1024, db.shape[0]); cb, ci = torch.abs(dbt[None, st:e] - qt[:, None]).sum(-1).min(-1); r = cb < best; best[r] = cb[r]; bidx[r] = ci[r] + st
    agree += int((bidx.cpu().numpy() == bi).sum()); n += 8; dd.append(float(np.abs(best.float().cpu().numpy() - bd.astype(np.float32)).max()))
res["retrieval_onnx_vs_torch"] = dict(index_agree=f"{agree}/{n}", max_dist_diff=max(dd))
json.dump(res, open(HERE / "logs/numpy_ports_check.json", "w"), indent=1); print(json.dumps(res, indent=1))
