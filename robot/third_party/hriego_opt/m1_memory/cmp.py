"""usage: cmp.py <var> : geodesic angle (deg) of SMPL-X local joint rotations vs T2 fp16_s1 (production) and fp32_s1 per clip; also seed ref fp32_s2."""
import sys, numpy as np, json
from pathlib import Path
T2 = Path(__file__).resolve().parent.parent / "t2_streamtalk_precision/out"
def R(aa):
    th = np.linalg.norm(aa, axis=-1, keepdims=True); k = aa / np.maximum(th, 1e-9); K = np.zeros(aa.shape[:-1] + (3, 3))
    K[..., 0, 1] = -k[..., 2]; K[..., 0, 2] = k[..., 1]; K[..., 1, 0] = k[..., 2]; K[..., 1, 2] = -k[..., 0]; K[..., 2, 0] = -k[..., 1]; K[..., 2, 1] = k[..., 0]
    s, c = np.sin(th)[..., None], np.cos(th)[..., None]; return np.eye(3) + s * K + (1 - c) * K @ K
def err(a, b):
    n = min(len(a), len(b)); A = R(a[:n].reshape(n, 55, 3)); B = R(b[:n].reshape(n, 55, 3))
    tr = np.einsum("tjab,tjab->tj", A, B); ang = np.degrees(np.arccos(np.clip((tr - 1) / 2, -1, 1))); return float(ang.mean()), float(np.percentile(ang, 95)), float(ang[:, :22].mean())
var = sys.argv[1]; out = {}
for f in sorted((Path(__file__).resolve().parent / "out" / f"st_{var}").glob("*.npz")):
    n = f.stem; a = np.load(f)["poses"]; row = {}
    for ref in ("fp16_s1", "fp32_s1", "fp32_s2"):
        p = T2 / ref / n / f"{n}.npz"
        if p.exists(): b = np.load(p)["poses"]; row[ref] = [round(x, 3) for x in err(a, b)] + [len(a), len(b)]
    out[n] = row; print(n, row)
json.dump(out, open(Path(__file__).resolve().parent / "logs" / f"cmp_{var}.json", "w"), indent=1)
