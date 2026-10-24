"""P4 metrics: runs/p4_commercial/traces/{ultra,original}_r<rep>.{csv,json} -> metrics/{events.csv,summary.json}. usage: analyze_p4.py [REP] (host python3 + numpy)."""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import csv, json, sys, os
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, H)
from c12_io import load
O = H + "/runs/p4_commercial/"; REP = sys.argv[1] if len(sys.argv) > 1 else "1"
C8 = str(_LR / "experiments/RCP-01/runs/20261021_rcp01c08_preset_latency_twin/metrics/preset_csv_timeline.json")
TL8 = json.load(open(C8))
ARM = [f"{s}_{j}_joint" for s in ("left", "right") for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")]
COL = {"waist_yaw_joint": "wmeas_twist_waist", "waist_pitch_joint": "wmeas_bend_forward", "waist_roll_joint": "wmeas_bend_sideways", "head_yaw_joint": "wmeas_turn_head", "head_pitch_joint": "head_pitch"}
TICK = 0.02
def speed(M, t, h=2):
    v = np.zeros(len(t))
    for i in range(len(t)):
        a, b = max(0, i - h), min(len(t) - 1, i + h); v[i] = np.nanmax(np.abs(M[b] - M[a])) / max(t[b] - t[a], 1e-6)
    return v
def still_runs(v, t, thr=0.05, min_s=0.3):
    tot = longest = 0.0; i = 0; n = len(v)
    while i < n:
        if v[i] < thr:
            j = i
            while j < n and v[j] < thr: j += 1
            d = t[j - 1] - t[i]
            if d > min_s: tot += d; longest = max(longest, d)
            i = j
        else: i += 1
    return tot, longest
def jerk(c, dt=TICK): return float(np.nanmax(np.abs(np.diff(c, 3, axis=0))) / dt ** 3) if len(c) >= 5 else float("nan")
ev_rows, summ = [], {"rep": REP, "robots": {}}
STR = {}
for rb in ("ultra", "original"):
    name = f"{rb}_r{REP}"
    if not os.path.exists(O + f"traces/{name}.csv"): continue
    m = json.load(open(O + f"traces/{name}.json")); T = json.load(open(O + f"timeline_{rb}.json")); d = load(O + f"traces/{name}.csv")
    au = d["audio_s"]; ph = d["phase"]; t = d["t"]; T0 = m["T0"]
    M = np.stack([d["meas_" + n] for n in ARM], 1); C = np.stack([d["cmd_" + n] for n in ARM], 1)
    Wh = np.stack([d["wmeas_twist_waist"], d["wmeas_bend_forward"], d["wmeas_bend_sideways"], d["wmeas_turn_head"]], 1)
    ok = ~np.isnan(au) & (au >= 0)
    S = {"completed": bool(m.get("completed")), "stable_after": bool(m.get("stable_after")), "mode_after": m.get("mode_after"), "presets": {}}
    pz = d["pelvis_z"]; S.update(pelvis_z_min=float(np.nanmin(pz[ok])), tilt_max_deg=float(np.nanmax(d["tilt_deg"][ok])), falls=int(np.nanmin(pz[ok]) < 0.50))
    sp = speed(M, t); spb = speed(np.hstack([M, Wh]), t)
    # preset windows on the clock
    PW = [(p["t_fade0"] - 0.1, (p.get("idle_audio_s") or p["dispatch"] + p["preset_dur_s"]) + 0.9) for p in m["presets"]]
    inpw = lambda x: any(a <= x <= b for a, b in PW)
    # ---- per preset
    for p in m["presets"]:
        td = p.get("t_disp")
        sel = [i for i in range(len(t)) if td and td <= t[i] <= (p.get("t_idle") or t[-1]) and d["player_state"][i] in (1, 2)]
        mot = {str(int(d["motion"][i])) for i in sel if not np.isnan(d["motion"][i])}
        tl8 = TL8[f"{p['motion']}/{p['area']}"]; jd = tl8["dominant_joint"]; rest = tl8["rest_q"][jd]; qd = M[:, ARM.index(jd)] if jd in ARM else d[COL[jd]]
        pre = (t >= td - 0.3) & (t < td); base = np.nanmedian(qd[pre]); post = np.where(t >= td)[0]; kk = post[np.where(np.abs(qd[post] - base) > 0.05)[0]]; onset = au[kk[0]] if len(kk) else np.nan
        tidle = p.get("t_idle") or t[-1]; seg = np.where((t >= td) & (t <= tidle))[0]; p2 = [i for i in seg if d["player_state"][i] == 2]
        if p2:
            dd = np.abs(qd[p2[0]:seg[-1] + 1] - rest); stroke = au[p2[0] + int(np.where(dd >= 0.9 * dd.max())[0][0])]
        else: stroke = np.nan
        disp_c = td - T0; exe = bool(p.get("set_code") == 0 and mot == {str(p["motion"])} and p.get("t_idle"))
        # transitions
        fo = ph == "fade_out"; fi = ph == "fade_in"; tdel = p["t_delete"]
        out_speed = float(sp[(au >= p["t_fade0"] - 0.05) & (au <= p["t_handoff"] + 0.6)].max()); snap = float(sp[(t >= tdel) & (t <= tdel + 0.5)].max())
        fo_m = fo & (au >= p["t_fade0"] - 0.01) & (au <= p["t_handoff"] + 0.01); fi_m = fi & (au >= p.get("t_in0", 1e9) - 0.01) & (au <= p.get("t_in0", 1e9) + 0.75)
        row = dict(robot=rb, id=p["id"], line=p["line"], label=p["label"], motion=p["motion"], area=p["area"], policy=p["policy"], anchor_clock_s=p["anchor_abs"], dispatch_plan_s=p["dispatch"], dispatch_exec_s=disp_c, dispatch_err_ms=1e3 * (disp_c - p["dispatch"]),
                   set_code=p.get("set_code"), mc_motion_seen=",".join(sorted(mot)), idle_clock_s=p.get("idle_audio_s"), executed_eq_plan=exe, onset_clock_s=onset, stroke_clock_s=stroke,
                   stroke_minus_anchor_ms=1e3 * (stroke - p["anchor_abs"]), onset_minus_anchor_ms=1e3 * (onset - p["anchor_abs"]),
                   fadeout_max_speed=out_speed, fadeout_max_cmd_jerk=jerk(C[fo_m]), sd_snap_max_speed=snap, fadein_max_speed=float(sp[(au >= p.get("t_in0", 1e9)) & (au <= p.get("t_in0", 1e9) + 0.8)].max()) if "t_in0" in p else np.nan,
                   fadein_max_cmd_jerk=jerk(C[fi_m]) if fi_m.sum() > 4 else np.nan, delete_to_set_ms=1e3 * (td - tdel), idle_to_urs_ms=1e3 * p.get("dt_idle_to_urs_s", np.nan), idle_to_add_ms=1e3 * p.get("dt_idle_to_add_s", np.nan), fadein_start_dev_from_D=p.get("p0_dev_from_D"))
        ev_rows.append(row); STR[(rb, p["line"])] = stroke; S["presets"][p["id"]] = row
    # ---- SONIC tracking (lag-4: |meas(t) - cmd(t-4 ticks)|) in speaking lines away from preset windows, and still time
    trk, stl, stlb, lsum = [], 0.0, 0.0, []
    for l in T["lines"]:
        if not l["speaks"]: continue
        w = (au >= l["start"] + 0.3) & (au <= l["end"] - 0.3) & (ph == "wait") & ~np.array([inpw(x) if not np.isnan(x) else True for x in au])
        idx = np.where(w)[0]; idx = idx[idx > 4]
        if len(idx) < 25: lsum.append(dict(line=l["n"], n=len(idx), lag4_mean=None, note="line mostly covered by preset window")); continue
        e = np.abs(M[idx] - C[idx - 4]); trk.append(e.mean()); lsum.append(dict(line=l["n"], n=int(len(idx)), lag4_mean=float(e.mean()), lag4_max=float(e.max()), lag4_p95=float(np.percentile(e, 95))))
        ww = (au >= l["start"]) & (au <= l["end"]); s1, _ = still_runs(sp[ww], t[ww]); s2, _ = still_runs(spb[ww], t[ww]); stl += s1; stlb += s2
    S["sonic_tracking_lag4"] = dict(per_line=lsum, mean_of_line_means=float(np.mean(trk)) if trk else None, gate=0.015, pass_all=bool(trk and all((x["lag4_mean"] or 0) <= 0.015 for x in lsum if x.get("lag4_mean") is not None)))
    S["still_arms_s_in_speaking_lines"] = stl; S["still_body_s_in_speaking_lines"] = stlb
    spk = sum(l["dur"] for l in T["lines"] if l["speaks"]); S["speaking_s"] = spk; S["still_arms_frac"] = stl / spk; S["still_body_frac"] = stlb / spk
    # ---- head
    hy = Wh[:, 3]; hr = d["wref_turn_head"]; S["head_yaw_meas_min_max_rad"] = [float(np.nanmin(hy[ok])), float(np.nanmax(hy[ok]))]
    if rb == "ultra":
        best = None
        for lg in range(0, 31, 2):
            idx = np.arange(lg, len(hy)); msk = ~np.isnan(hr[idx - lg]) & ~np.isnan(hy[idx]) & ok[idx] & ~np.array([inpw(x) if not np.isnan(x) else True for x in au[idx]])
            e = np.abs(hy[idx][msk] - hr[idx - lg][msk]); r = (float(e.mean()), float(e.max()), lg)
            if best is None or r[0] < best[0]: best = r
        S["head_tracking"] = dict(mean_abs_err_rad=best[0], max_abs_err_rad=best[1], best_lag_ticks=best[2], note="measured vs reference shifted by the best lag, outside preset windows (head is frozen by the MC during presets)")
        # head frozen during presets: value at preset start vs end
        S["head_at_preset_start_rad"] = {p["id"]: float(np.nanmedian(hy[(au > p["dispatch"] - 0.3) & (au < p["dispatch"])])) for p in m["presets"]}
    else: S["head_abs_max_rad_original(no neck, must be 0)"] = float(np.nanmax(np.abs(hy[ok])))
    S["waist_yaw_range_rad"] = [float(np.nanmin(Wh[ok, 0])), float(np.nanmax(Wh[ok, 0]))]
    summ["robots"][rb] = S
# ---- inter-robot sync
sy = {}
for ln, nm in ((1, "bow"), (19, "farewell"), (5, "heart_vs_applause")):
    a, b = STR.get(("ultra", ln)), STR.get(("original", ln))
    if a is not None and b is not None: sy[nm] = dict(stroke_ultra=a, stroke_original=b, sync_error_ms=1e3 * (a - b))
summ["inter_robot_sync"] = sy
if ev_rows:
    os.makedirs(O + "metrics", exist_ok=True)
    with open(O + "metrics/events.csv", "w", newline="") as f: w = csv.DictWriter(f, fieldnames=list(ev_rows[0])); w.writeheader(); w.writerows(ev_rows)
json.dump(summ, open(O + "metrics/summary.json", "w"), indent=1, default=str)
for r in ev_rows: print(r["robot"], r["id"], "exec==plan", r["executed_eq_plan"], "stroke-anchor %.0f ms" % r["stroke_minus_anchor_ms"], "disp err %.0f ms" % r["dispatch_err_ms"])
print(json.dumps(summ["inter_robot_sync"]))
for rb, S in summ["robots"].items(): print(rb, {k: S[k] for k in ("completed", "stable_after", "pelvis_z_min", "tilt_max_deg", "falls", "still_arms_frac", "still_body_frac")}, S["sonic_tracking_lag4"]["mean_of_line_means"], S.get("head_tracking"))
