"""Component numeric checks: ONNX (CUDA EP) vs torch references. Run with Face .venv-face python."""
import numpy as np, onnxruntime as ort, json, sys
ort.preload_dlls()
R = np.load("scratch/ref_components.npz")
def sess(n): 
    so = ort.SessionOptions(); so.log_severity_level = 3
    return ort.InferenceSession(f"models/{n}.onnx", so, providers=["CUDAExecutionProvider"])
def cmp(a, b):
    a = a.astype(np.float64).ravel(); b = b.astype(np.float64).ravel(); d = np.abs(a - b)
    return dict(max=float(d.max()), mean=float(d.mean()), cos=float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b))), rel=float(np.linalg.norm(a - b) / np.linalg.norm(b)))
res = {}
print("numpy norm maxdiff", float(R["np_norm_maxdiff"]))
for v in ("fp32", "fp16", "fp16ln"):
    s = sess(f"wavlm_{v}"); out = np.concatenate([s.run(None, {"audio": R["audio_norm"][i:i + 1]})[0] for i in range(2)])
    res[f"wavlm_{v}"] = {"vs_torch_fp16": cmp(out, R["wavlm_fp16"]), "vs_torch_fp32": cmp(out, R["wavlm_fp32"])}
    if v == "fp32": res["wavlm_torch_fp16_vs_fp32"] = cmp(R["wavlm_fp16"], R["wavlm_fp32"])
    del s
for v in ("fp32", "fp16", "fp16ln"):
    s = sess(f"simple_{v}"); e, eu = s.run(None, {"hubert": R["h60"]})
    res[f"simple_{v}"] = {"expr_vs_torch_fp32": cmp(e, R["expr"]), "expr_u_vs_torch_fp32": cmp(eu, R["expr_u"]), "expr_vs_torch_autocast": cmp(e, R["expr_ac"])}
res["simple_torch_autocast_vs_fp32"] = cmp(R["expr_ac"], R["expr"])
for v in ("fp32", "fp16", "fp16ln"):
    s = sess(f"step_{v}"); r = {}
    for t in (0.0, 0.5, 0.9):
        out = s.run(None, {"x": R["step_noise"], "t": np.array([t], np.float32), "poses_prompt": R["step_pp"], "prompt_mask": R["step_mask"], "trans_prompt": R["step_tp"], "trans_mask": R["step_mask"],
                           "hubert": R["h60"], "expressions": R["expr"], "expressions_uncond": R["expr_u"]})[0]
        r[f"t{t}"] = {"vs_torch_fp16": cmp(out, R[f"step16_t{t}"]), "vs_torch_fp32": cmp(out, R[f"step32_t{t}"])}
    res[f"step_{v}"] = r
    del s
for t in (0.0, 0.5, 0.9): res.setdefault("step_torch_fp16_vs_fp32", {})[f"t{t}"] = cmp(R[f"step16_t{t}"], R[f"step32_t{t}"])
json.dump(res, open("logs/component_check.json", "w"), indent=1)
for k, v in res.items(): print(k, json.dumps(v)[:900])
