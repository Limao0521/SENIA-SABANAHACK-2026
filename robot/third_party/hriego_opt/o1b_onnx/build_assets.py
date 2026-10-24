"""physicalai/numpy+onnx only. Builds torch-free runtime assets: assets/st_assets.npz (index arrays, READY prompt, SMPL-X rest joints for the READY betas, parents)
and models/retrieval.onnx (L1 nearest-neighbour over the 160173x165 fp16 joint table, chunked, fp16 Sub/Abs/ReduceSum like the torch GPU loop)."""
import numpy as np, onnx, sys
from onnx import helper as h, TensorProto as T, numpy_helper as nh
from common_paths import *
(HERE / "assets").mkdir(exist_ok=True)
add_st_paths()
from Common.SMPLXConfig import GetBodyPartIndices     # numpy only (SMPLXConfig imports numpy)
sm = np.load(P.smplx_neutral_npz(), allow_pickle=True); z = np.load(P.READY_PROMPT, allow_pickle=True)
betas = z["betas"].astype(np.float32)
v = sm["v_template"].astype(np.float64) + np.einsum("vcb,b->vc", sm["shapedirs"][:, :, :300].astype(np.float64), betas.astype(np.float64))
J = (sm["J_regressor"].astype(np.float64) @ v)           # (55,3), identical to smplx lbs vertices2joints(J_regressor, v_shaped)
par = sm["kintree_table"][0].astype(np.int64); par[0] = -1
from Common.SMPLX_Util import LocalToGlobalAA
from Common.Util import axis_angle_to_rotation_6d
pose_prompt6d = axis_angle_to_rotation_6d(LocalToGlobalAA(z["poses"].astype(np.float32)[:60].copy())).astype(np.float32)   # scipy-based; precomputed so the runtime needs no scipy
np.savez(HERE / "assets/st_assets.npz", pose_prompt6d=pose_prompt6d, hand=GetBodyPartIndices("hand", "rot6d"), upper=GetBodyPartIndices("upper", "rot6d"), lower=GetBodyPartIndices("lower", "rot6d"), face=GetBodyPartIndices("face", "rot6d"),
         J_rest=J.astype(np.float32), parents=par, betas=betas, poses0=z["poses"].astype(np.float32)[:60], trans0=z["trans"].astype(np.float32)[:60])
# retrieval graph
db = np.load(KD_CACHE / "joints_f16.npy"); N = db.shape[0]; NCH = 16; edges = np.linspace(0, N, NCH + 1).astype(int)
inits = [nh.from_array(db, "db"), nh.from_array(np.array([0], np.int64), "ax0"), nh.from_array(np.array([1], np.int64), "ax1"), nh.from_array(np.array([-1], np.int64), "axm1")]
nodes = [h.make_node("Unsqueeze", ["q", "ax1"], ["qe"])]   # (8,1,165)
for i in range(NCH):
    a, b = int(edges[i]), int(edges[i + 1])
    for nm, val in ((f"s{i}", a), (f"e{i}", b)): inits.append(nh.from_array(np.array([val], np.int64), nm))
    nodes += [h.make_node("Slice", ["db", f"s{i}", f"e{i}", "ax0"], [f"dbc{i}"]), h.make_node("Sub", [f"dbc{i}", "qe"], [f"df{i}"]) , h.make_node("Abs", [f"df{i}"], [f"ab{i}"]),
              h.make_node("ReduceSum", [f"ab{i}", "axm1"], [f"d{i}"], keepdims=0)]   # (8,n)
nodes += [h.make_node("Concat", [f"d{i}" for i in range(NCH)], ["dist"], axis=1), h.make_node("ArgMin", ["dist"], ["idx2"], axis=1, keepdims=1),
          h.make_node("GatherElements", ["dist", "idx2"], ["bd2"], axis=1), h.make_node("Squeeze", ["idx2", "ax1"], ["idx"]), h.make_node("Squeeze", ["bd2", "ax1"], ["bd"])]
g = h.make_graph(nodes, "retrieval", [h.make_tensor_value_info("q", T.FLOAT16, [8, 165])], [h.make_tensor_value_info("bd", T.FLOAT16, [8]), h.make_tensor_value_info("idx", T.INT64, [8])], initializer=inits)
m = h.make_model(g, opset_imports=[h.make_opsetid("", 18)]); m.ir_version = 10
onnx.checker.check_model(m); onnx.save_model(m, str(MODELS / "retrieval.onnx"), save_as_external_data=True, all_tensors_to_one_file=True, location="retrieval.onnx.data", size_threshold=1024)
print("assets+retrieval done", db.shape)
