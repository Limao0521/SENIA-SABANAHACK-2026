import json, numpy as np, pandas as pd
from pathlib import Path
H = Path(__file__).parent; T3 = H.parent / "t3_gmr_sonic/out/native/fp16_s1"
clips = sorted(d.name for d in T3.iterdir() if d.is_dir()); rows = []
def tr(p): return pd.read_csv(p)
for v in ["full", "v1", "v2"]:
    for c in clips:
        a = tr(H / f"out/sonic/full_run1/{c}/traces/full_{c}.csv"); b = tr(H / f"out/sonic/{v}/{c}/traces/full_{c}.csv"); t3 = tr(T3 / c / "traces" / f"full_{c}.csv")
        act = [k for k in a.columns if k.endswith("_actual_rad")]
        n = min(len(a), len(b)); d = lambda k, x=a, y=b: float(np.abs(x[k].values[:n] - y[k].values[:n]).max())
        rows.append(dict(variant=v, clip=c, n=len(b), same_len=len(a) == len(b), joints=max(d(k) for k in act), pelvis_z=d("pelvis_z_actual_m"), tilt=d("tilt_deg"), contacts_maxdiff=int(np.abs(a.contact_count.values[:n] - b.contact_count.values[:n]).max()),
                         contacts_ticks_differ=int((a.contact_count.values[:n] != b.contact_count.values[:n]).sum()), vs_T3stored_joints=max(float(np.abs(t3[k].values[:n] - b[k].values[:n]).max()) for k in act) if len(t3) >= n else None))
json.dump(rows, open(H / "out/sonic_cmp.json", "w"), indent=1)
for r in rows: print(f'{r["variant"]:5s} {r["clip"]:32s} n={r["n"]:4d} joints={r["joints"]:.2e} pelvis_z={r["pelvis_z"]:.2e} tilt={r["tilt"]:.2e} contacts_diff_ticks={r["contacts_ticks_differ"]} (max {r["contacts_maxdiff"]}) vsT3={r["vs_T3stored_joints"]}')
