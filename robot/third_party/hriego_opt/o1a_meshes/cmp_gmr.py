import json, sys, numpy as np
from pathlib import Path
H = Path(__file__).parent; T3 = H.parent / "t3_gmr_sonic/out/native/fp16_s1"
def q(p): return np.load(p)["qpos"].astype(np.float64)
base = "full_run1"; rows = []
clips = sorted(d.name for d in T3.iterdir() if d.is_dir())
for v in ["full", "v1", "v2"]:
    for c in clips:
        a = q(H / f"out/gmr/{base}/{c}/retarget/{c}_x2_gmr_full.npz"); b = q(H / f"out/gmr/{v}/{c}/retarget/{c}_x2_gmr_full.npz"); t3 = q(T3 / c / "retarget" / f"{c}_x2_gmr_full.npz")
        ja = json.load(open(H / f"out/gmr/{base}/{c}/retarget/{c}_DECISION_SUMMARY.json")); jb = json.load(open(H / f"out/gmr/{v}/{c}/retarget/{c}_DECISION_SUMMARY.json"))
        rows.append(dict(variant=v, clip=c, n=len(a), maxdiff=float(np.abs(a - b).max()), maxdiff_vs_T3stored=float(np.abs(t3 - b).max()), qc_equal=ja["qc"] == jb["qc"], status=jb["status"]))
json.dump(rows, open(H / "out/gmr_cmp.json", "w"), indent=1)
for r in rows: print(f'{r["variant"]:5s} {r["clip"]:32s} n={r["n"]:4d} maxdiff={r["maxdiff"]:.3e} vsT3={r["maxdiff_vs_T3stored"]:.3e} qc_equal={r["qc_equal"]} {r["status"]}')
