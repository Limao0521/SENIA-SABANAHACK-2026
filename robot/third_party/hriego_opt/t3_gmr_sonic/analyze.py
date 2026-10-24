import json, csv, numpy as np
from pathlib import Path
H = Path(__file__).parent; O = H / "out"
G = {tag: json.load(open(O / f"gmr_{tag}.json")) for tag in ("native", "analytic")}
S = {tag: json.load(open(O / f"sonic_{tag}.json")) for tag in ("native", "analytic")}
def load(tag, v, c):
    p = O / tag / v / c / "arms.npz"; return np.load(p) if p.exists() else None
def kin(z):
    a = z["sonic_actual"]; dt = 0.02; v = np.diff(a, axis=0) / dt; j = np.diff(a, 3, axis=0) / dt**3
    return dict(vel=np.abs(v).mean(), vel_p95=np.percentile(np.abs(v), 95), jerk=np.abs(j).mean(), jerk_p95=np.percentile(np.abs(j), 95), peak_vel=np.abs(v).max())
def cmp(tag, v, ref_tag, ref_v, clips):
    arm_se, wa_se, errs, wad, per = [], [], [], [], {}
    for c in clips:
        a, b = load(tag, v, c), load(ref_tag, ref_v, c)
        if a is None or b is None: continue
        n = min(len(a["sonic_actual"]), len(b["sonic_actual"]))
        d = a["sonic_actual"][:n] - b["sonic_actual"][:n]; w = a["waist_head"][:n, :3] - b["waist_head"][:n, :3]
        arm_se.append(d**2); wa_se.append(w**2); errs.append(np.abs(d)); wad.append(np.abs(w)); per[c] = float(np.sqrt((d**2).mean()))
    A = np.concatenate(arm_se); W = np.concatenate(wa_se); E = np.concatenate(errs)
    return dict(arm_rmse=float(np.sqrt(A.mean())), arm_rmse_clip_mean=float(np.mean(list(per.values()))), waist_rmse=float(np.sqrt(W.mean())),
                arm_p95_joint_mean=float(np.mean(np.percentile(E, 95, axis=0))), arm_p95_joint_max=float(np.max(np.percentile(E, 95, axis=0))),
                waist_p95=float(np.percentile(np.concatenate(wad), 95)), per_clip=per)
def runstats(tag, v, clips):
    rows = [r for r in S[tag]["rows"] if r["variant"] == v and r["clip"] in clips]; g = [r for r in G[tag if tag == "native" else "analytic"]["rows"] if r["variant"] == v and r["clip"] in clips]
    ks = [kin(load(tag, v, r["clip"])) for r in rows if r.get("ok")]
    cl = sum(r["prep"]["clamped_samples"] for r in rows if r.get("ok")); ns = sum(r["prep"]["rows"] * 14 for r in rows if r.get("ok"))
    qcf = [str(x["qc"].get("fail")) for x in g if x["status"] != "PASS"]
    return dict(n=len(rows), gmr_pass=sum(x["status"] == "PASS" for x in g), gmr_n=len(g), gmr_fail=qcf, sonic_ok=sum(bool(r.get("ok")) for r in rows),
                tilt_max=max(r["prep"]["sonic_tilt_max_deg"] for r in rows if r.get("ok")), tilt_mean_of_max=float(np.mean([r["prep"]["sonic_tilt_max_deg"] for r in rows if r.get("ok")])),
                pelvis_min=min(r["prep"]["sonic_pelvis_z_min"] for r in rows if r.get("ok")), clamp_frac=cl / ns,
                track_mae=float(np.mean([r["prep"]["sonic_vs_gmr_arm_mae_rad"] for r in rows if r.get("ok")])),
                **{k: float(np.mean([x[k] for x in ks])) for k in ks[0]})
allc = [r["clip"] for r in G["native"]["rows"] if r["variant"] == "fp32_s1"]
VARS = ["fp32_s1", "fp32_s2", "fp16_s1", "bf16_s1", "i8sim_gen_s1", "i8sim_all_s1", "i8dyn_gen_s1", "i8dyn_all_s1"]
out = {}
for v in VARS:
    clips = [r["clip"] for r in G["native"]["rows"] if r["variant"] == v]
    ref = "fp32_s1"; d = dict(clips=clips, **runstats("native", v, clips))
    d["vs_fp32_s1"] = cmp("native", v, "native", ref, clips) if v != ref else None
    d["baseline_same_clips"] = runstats("native", ref, clips)
    d["seed_ref_same_clips"] = cmp("native", "fp32_s2", "native", ref, clips)
    out[v] = d
# verdicts
for v in VARS[2:]:
    d = out[v]; e, s = d["vs_fp32_s1"], d["seed_ref_same_clips"]; b = d["baseline_same_clips"]
    s2 = runstats("native", "fp32_s2", d["clips"])
    jr = d["jerk"] / b["jerk"]; sr = s2["jerk"] / b["jerk"]; band = max(0.25, abs(sr - 1))
    checks = dict(arm_rmse_le_seed=e["arm_rmse"] <= s["arm_rmse"], waist_rmse_le_seed=e["waist_rmse"] <= s["waist_rmse"], jerk_in_band=abs(jr - 1) <= band,
                  qc_ok=d["gmr_pass"] / d["gmr_n"] >= b["gmr_pass"] / b["gmr_n"], tilt_ok=d["tilt_max"] <= max(b["tilt_max"], s2["tilt_max"]) * 1.25, clamp_ok=d["clamp_frac"] <= max(b["clamp_frac"], s2["clamp_frac"]) * 1.5 + 1e-4)
    d["jerk_ratio"], d["seed_jerk_ratio"], d["checks"] = jr, sr, checks; d["verdict"] = "PASS" if all(checks.values()) else "FAIL: " + ",".join(k for k, x in checks.items() if not x)
json.dump(out, open(H / "metrics_variants.json", "w"), indent=1)
cols = ["variant", "n_clips", "arm_rmse_rad", "seed_ref_arm_rmse", "waist_rmse_rad", "seed_ref_waist_rmse", "arm_p95_joint_mean", "arm_p95_joint_max", "vel_rad_s", "jerk_rad_s3", "jerk_ratio_vs_s1", "gmr_pass", "sonic_ok", "tilt_max_deg", "pelvis_min_m", "clamp_frac", "track_mae", "verdict"]
with open(H / "metrics_variants.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(cols)
    for v in VARS:
        d = out[v]; e = d["vs_fp32_s1"] or {}; s = d["seed_ref_same_clips"]; b = d["baseline_same_clips"]
        w.writerow([v, d["n"], *(round(e.get(k, 0), 4) if e else "" for k in ("arm_rmse",)), round(s["arm_rmse"], 4), round(e.get("waist_rmse", 0), 4) if e else "", round(s["waist_rmse"], 4),
                    round(e.get("arm_p95_joint_mean", 0), 4) if e else "", round(e.get("arm_p95_joint_max", 0), 4) if e else "", round(d["vel"], 3), round(d["jerk"], 1), round(d.get("jerk_ratio", 1), 2),
                    f'{d["gmr_pass"]}/{d["gmr_n"]}', d["sonic_ok"], d["tilt_max"], d["pelvis_min"], round(d["clamp_frac"], 5), round(d["track_mae"], 4), d.get("verdict", "baseline")])
# ---- solver comparison
gn = {r["clip"]: r for r in G["native"]["rows"] if r["variant"] == "fp32_s1"}; ga = {r["clip"]: r for r in G["analytic"]["rows"]}
cmpr = cmp("analytic", "fp32_s1", "native", "fp32_s1", allc)
rn, ra = runstats("native", "fp32_s1", allc), runstats("analytic", "fp32_s1", allc)
# qpos-level diff too
qd = {}
for c in allc:
    a = np.load(O / "analytic/fp32_s1" / c / "retarget" / f"{c}_x2_gmr_full.npz"); b = np.load(O / "native/fp32_s1" / c / "retarget" / f"{c}_x2_gmr_full.npz")
    n = min(len(a["qpos"]), len(b["qpos"])); names = list(a["joint_names"]); idx = [i for i, nm in enumerate(names) if "arm" in nm or "shoulder" in nm or "elbow" in nm or "wrist" in nm]
    qd[c] = float(np.sqrt(((a["qpos"][:n, 7:][:, idx] - b["qpos"][:n, 7:][:, idx])**2).mean()))
sol = dict(per_clip=[dict(clip=c, frames=gn[c]["frames"], native_wall_s=gn[c]["wall_s"], analytic_wall_s=ga[c]["wall_s"], native_cpu_s=gn[c]["cpu_s"], analytic_cpu_s=ga[c]["cpu_s"], native_rss=gn[c]["rss_mb"], analytic_rss=ga[c]["rss_mb"],
           native_hwm=gn[c]["hwm_mb"], analytic_hwm=ga[c]["hwm_mb"], native_threads=gn[c]["threads"], analytic_threads=ga[c]["threads"], native_qc=gn[c]["status"], analytic_qc=ga[c]["status"], gmr_arm_qpos_rmse=qd[c],
           sonic_arm_rmse=cmpr["per_clip"][c]) for c in allc],
           workers=dict(native=G["native"]["ready"], analytic=G["analytic"]["ready"]), sonic_native=rn, sonic_analytic=ra, diff=cmpr, qpos_arm_rmse_mean=float(np.mean(list(qd.values()))),
           cold_first_clip=dict(native=next(r for r in G["native"]["rows"] if r["first"])["wall_s"], analytic=ga[allc[0]]["wall_s"]))
json.dump(sol, open(H / "metrics_gmr_solver.json", "w"), indent=1)
with open(H / "metrics_gmr_solver.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(sol["per_clip"][0])); w.writeheader(); w.writerows(sol["per_clip"])
print(json.dumps({v: {k: out[v].get(k) for k in ("n", "gmr_pass", "tilt_max", "pelvis_min", "clamp_frac", "jerk", "jerk_ratio", "verdict")} | {"arm": (out[v]["vs_fp32_s1"] or {}).get("arm_rmse"), "waist": (out[v]["vs_fp32_s1"] or {}).get("waist_rmse"), "seed_arm": out[v]["seed_ref_same_clips"]["arm_rmse"]} for v in VARS}, indent=1))
print(json.dumps({k: v for k, v in sol.items() if k != "per_clip"}, indent=1, default=str)); [print(r) for r in sol["per_clip"]]
