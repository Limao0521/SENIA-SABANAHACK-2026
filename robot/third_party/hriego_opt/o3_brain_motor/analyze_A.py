import json, sys
import numpy as np
from pathlib import Path
O = Path(__file__).resolve().parent / "out"
def L(tag, c): return np.load(O / tag / f"{c}.npz")
def jerk(q): return float(np.abs(np.diff(q, 3, axis=0)).mean() * 50 ** 3)
names = [r["name"] for r in json.load(open(O / "A_v1/summary.json"))["rows"]]
S = {t: json.load(open(O / t / "summary.json"))["rows"] for t in ("A_v1", "A_v2", "A_v1b") if (O / t / "summary.json").exists()}
def cmp(a, b):
    rows = []
    for i, c in enumerate(names):
        x, y = L(a, c), L(b, c); pn = S[a][i]["prefix_n"]; n = min(len(x["arms"]), len(y["arms"]))
        d = x["arms"][pn:n] - y["arms"][pn:n]; dw = x["wh"][pn:n, :3] - y["wh"][pn:n, :3]; dh = x["wh"][pn:n, 3:] - y["wh"][pn:n, 3:]
        qn = min(len(x["gmr_q"]), len(y["gmr_q"]))
        rows.append(dict(clip=c, len_a=len(x["arms"]), len_b=len(y["arms"]), arm_rmse=float(np.sqrt((d ** 2).mean())), arm_max=float(np.abs(d).max()), waist_rmse=float(np.sqrt((dw ** 2).mean())), head_rmse=float(np.sqrt((dh ** 2).mean())),
                         jerk_a=jerk(x["arms"][pn:n]), jerk_b=jerk(y["arms"][pn:n]), jerk_ratio=jerk(y["arms"][pn:n]) / jerk(x["arms"][pn:n]),
                         tilt_a=float(x["tilt"].max()), tilt_b=float(y["tilt"].max()), pel_a=float(x["pelvis_z"].min()), pel_b=float(y["pelvis_z"].min()),
                         tilt_mean_abs_diff=float(np.abs(x["tilt"][:n] - y["tilt"][:n]).mean()), pelvis_mean_abs_diff=float(np.abs(x["pelvis_z"][:n] - y["pelvis_z"][:n]).mean()),
                         gmr_q_maxdiff=float(np.abs(x["gmr_q"][:qn] - y["gmr_q"][:qn]).max()), gmr_arm_rmse=float(np.sqrt(((x["gmr_q"][:qn, 7:] - y["gmr_q"][:qn, 7:]) ** 2).mean()))))
    return rows
out = {}
for a, b in (("A_v1", "A_v2"), ("A_v1", "A_v1b")):
    if b in S: out[f"{a}_vs_{b}"] = cmp(a, b)
agg = {}
for k, rows in out.items():
    agg[k] = dict(arm_rmse_mean=np.mean([r["arm_rmse"] for r in rows]), arm_rmse_max=max(r["arm_rmse"] for r in rows), arm_max_abs=max(r["arm_max"] for r in rows), waist_rmse_mean=np.mean([r["waist_rmse"] for r in rows]),
                  head_rmse_mean=np.mean([r["head_rmse"] for r in rows]), jerk_ratio_mean=np.mean([r["jerk_ratio"] for r in rows]), jerk_ratio_range=[min(r["jerk_ratio"] for r in rows), max(r["jerk_ratio"] for r in rows)],
                  gmr_arm_rmse_mean=np.mean([r["gmr_arm_rmse"] for r in rows]), gmr_q_maxdiff_max=max(r["gmr_q_maxdiff"] for r in rows), len_equal=all(r["len_a"] == r["len_b"] for r in rows))
for t, rows in S.items():
    agg[t] = dict(tilt_max=max(r["tilt_max"] for r in rows), tilt_mean_of_max=float(np.mean([r["tilt_max"] for r in rows])), pelvis_min=min(r["pelvis_min"] for r in rows), falls=sum(r["fell"] for r in rows),
                  qc_fail=[r["name"] for r in rows if r["qc"].get("fail")], selfcol_max=max(r["qc"]["selfcol_frac"] for r in rows), first_cmd_mean=float(np.mean([r["first_cmd_s"] for r in rows])), unexpected_at_limit=[r["name"] for r in rows if r["qc"]["unexpected_at_limit"] != "{}"])
# base dynamics of SONIC arms (V1): speed / accel / jerk distribution and what a 2.0 / 0.35 rad/s limiter would do
dyn = {}
V = []; A = []; J = []; sp = []
for i, c in enumerate(names):
    a = L("A_v1", c)["arms"][S["A_v1"][i]["prefix_n"]:].astype(float); v = np.diff(a, axis=0) * 50; ac = np.diff(v, axis=0) * 50; j = np.diff(ac, axis=0) * 50
    V.append(np.abs(v).ravel()); A.append(np.abs(ac).ravel()); J.append(np.abs(j).ravel())
V, A, J = map(np.concatenate, (V, A, J))
dyn["speed_p99_p999_max"] = [float(np.percentile(V, 99)), float(np.percentile(V, 99.9)), float(V.max())]
dyn["accel_p99_p999_max"] = [float(np.percentile(A, 99)), float(np.percentile(A, 99.9)), float(A.max())]
dyn["jerk_p99_p999_max"] = [float(np.percentile(J, 99)), float(np.percentile(J, 99.9)), float(J.max())]
def vlim(a, vmax):
    o = a.copy(); n = 0
    for t in range(1, len(a)):
        d = a[t] - o[t - 1]; lim = vmax * 0.02; c = np.clip(d, -lim, lim); n += int((c != d).any()); o[t] = o[t - 1] + c
    return o, n
for vm in (2.0, 0.35):
    fr, rm, mx = [], [], 0
    for i, c in enumerate(names):
        a = L("A_v1", c)["arms"][S["A_v1"][i]["prefix_n"]:].astype(float); o, n = vlim(a, vm); fr.append(n / len(a)); rm.append(np.sqrt(((o - a) ** 2).mean())); mx = max(mx, np.abs(o - a).max())
    dyn[f"vlim_{vm}"] = dict(frac_ticks_limited_mean=float(np.mean(fr)), frac_max=float(np.max(fr)), rmse_mean=float(np.mean(rm)), rmse_max=float(np.max(rm)), max_dev=float(mx))
json.dump(dict(per_clip=out, agg=agg, dyn=dyn), open(O.parent / "metrics_A.json", "w"), indent=1, default=float)
print(json.dumps(agg, indent=1, default=float)); print(json.dumps(dyn, indent=1))
