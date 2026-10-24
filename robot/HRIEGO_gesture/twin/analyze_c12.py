"""c12 metrics: runs/c12_transitions/traces/*.csv|json -> metrics/{takes.csv,summary.json}. usage: analyze_c12.py [--tag NAME] (host, python3 + numpy)."""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import csv, json, sys, glob, os
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, H)
from c12_io import load
R = H + "/runs/c12_transitions/"; V = os.path.join(os.path.dirname(H), "visits_v2")
C8 = str(_LR / "experiments/RCP-01/runs/20261021_rcp01c08_preset_latency_twin")
TL = json.load(open(C8 + "/metrics/preset_csv_timeline.json"))
ARM = [f"{s}_{j}_joint" for s in ("left", "right") for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")]
COL = {"waist_yaw_joint": "wmeas_twist_waist", "waist_pitch_joint": "wmeas_bend_forward", "waist_roll_joint": "wmeas_bend_sideways", "head_yaw_joint": "wmeas_turn_head", "head_pitch_joint": "head_pitch"}
TICK = 0.02
def stroke_index(d, frac=0.9): return int(np.where(d >= frac * d.max())[0][0])
def speed(M, t, h=2):
    v = np.zeros(len(t)); 
    for i in range(len(t)):
        a, b = max(0, i - h), min(len(t) - 1, i + h); v[i] = np.nanmax(np.abs(M[b] - M[a])) / max(t[b] - t[a], 1e-6)
    return v
def still_runs(v, t, thr=0.05, min_s=0.3):
    tot = 0.0; longest = 0.0; i = 0; n = len(v)
    while i < n:
        if v[i] < thr:
            j = i
            while j < n and v[j] < thr: j += 1
            d = t[j - 1] - t[i]
            if d > min_s: tot += d; longest = max(longest, d)
            i = j
        else: i += 1
    return tot, longest
def jerk(c, dt=TICK):
    if len(c) < 5: return np.nan
    return float(np.nanmax(np.abs(np.diff(c, 3, axis=0))) / dt ** 3)
def analyze(name):
    m = json.load(open(R + f"traces/{name}.json")); d = load(R + f"traces/{name}.csv"); var = m["variant"]; tid = m["turn_id"]; rank = m["rank"]
    t = d["t"]; au = d["audio_s"]; ph = d["phase"]; M = np.stack([d["meas_" + n] for n in ARM], 1); C = np.stack([d["cmd_" + n] for n in ARM], 1); S = np.stack([d["sonic_" + n] for n in ARM], 1)
    z = np.load(f"{V}/{rank}/sonic/{tid}/{tid}_arms.npz"); cmdc = z["cmd"][100:]
    dur = m["dur_s"]; T0 = m["T0"]; row = {"take": name, "turn_id": tid, "variant": var, "rep": name.rsplit("_r", 1)[1], "emote": m["emote"], "binding": m["binding"], "policy": m["policy"], "audio_s": round(dur, 3)}
    win = (au >= 0) & (au <= dur) & ~np.isnan(au); tw = t[win]
    v = speed(M, t); st, lg = still_runs(v[win], tw)
    row.update(still_s=st, still_longest_s=lg, still_frac=st / dur)
    Wh = np.stack([d["wmeas_twist_waist"], d["wmeas_bend_forward"], d["wmeas_bend_sideways"], d["wmeas_turn_head"]], 1); vb = speed(np.hstack([M, Wh]), t); sb, lb = still_runs(vb[win], tw)
    row.update(still_body_s=sb, still_body_frac=sb / dur)
    vs = np.abs(np.gradient(cmdc, axis=0)).max(1) / TICK; tt = np.arange(len(cmdc)) * TICK; w2 = tt <= dur; s_st, s_lg = still_runs(vs[w2], tt[w2])
    row.update(sonic_ref_still_s=s_st, sonic_ref_max_speed=float(vs[w2].max()), sonic_ref_p99_jerk=float(np.percentile(np.abs(np.diff(cmdc[w2], 3, axis=0)).max(1) / TICK ** 3, 99)))
    ev = {e["event"]: e for e in m["events"]}
    tot_ok = lambda: None
    # ---- preset contract / timing
    ps = d["player_state"]; mot = {str(int(x)) for x, p in zip(d["motion"], ps) if p in (1, 2) and not np.isnan(x)}
    td = m.get("t_disp"); tdel = m.get("t_delete")
    row["set_code"] = m.get("set_code"); row["completed"] = bool(m.get("completed")); row["mc_motion_seen"] = ",".join(sorted(mot))
    row["executed_eq_contract"] = bool(m.get("set_code") == 0 and mot == {str(m["motion_id"])} and m.get("completed"))
    row["stable_after"] = bool(m.get("stable_after")); row["mode_after"] = m.get("mode_after")
    if td is not None:
        row["set_rtt_ms"] = 1e3 * (m["t_resp"] - td) if m.get("t_resp") else None
        jd = TL[m["binding"]]["dominant_joint"]; rest = TL[m["binding"]]["rest_q"][jd]
        qd = (M[:, ARM.index(jd)] if jd in ARM else d[COL[jd]])
        pre = (t >= td - 0.3) & (t < td); base = np.nanmedian(qd[pre]) if pre.any() else qd[0]
        post = np.where(t >= td)[0]; k = post[np.where(np.abs(qd[post] - base) > 0.05)[0]]; onset = au[k[0]] if len(k) else np.nan
        tidle = m.get("t_idle") or t[-1]; seg = np.where((t >= td) & (t <= tidle))[0]; p2 = [i for i in seg if ps[i] == 2]
        if p2:
            dd = np.abs(qd[p2[0]:seg[-1] + 1] - rest); stroke = au[p2[0] + stroke_index(dd)]
        else: stroke = np.nan
        a0 = m["anchor_s"]; row.update(onset_audio_s=onset, stroke_audio_s=stroke, onset_minus_anchor_ms=1e3 * (onset - a0), stroke_minus_anchor_ms=1e3 * (stroke - a0), dispatch_audio_s=td - T0, clamp_flag=(m["binding"] == "3029/11"))
    # ---- transitions (URS variants)
    if var != "V0":
        h = ev.get("handoff"); row["delete_dur_ms"] = 1e3 * h["delete_dur_s"]; row["last_cmd_dev_from_D"] = h["last_cmd_dev_from_D"]
        th = h["audio_s"]; row["handoff_audio_s"] = th; row["handoff_shift_vs_plan_s"] = th - m["D_a"]
        wo = (au >= th - m["L_out"] - 0.05) & (au <= th + 0.6); Mo = M[wo]; Co = C[(ph == "fade_out")]
        row["out_max_speed_meas"] = float(speed(M, t)[wo].max()) if wo.any() else np.nan; row["out_max_jerk_cmd"] = jerk(Co)
        row["sd_snap_max_speed_meas"] = float(speed(M, t)[(t >= tdel) & (t <= tdel + 0.5)].max()) if tdel else np.nan
        isd = np.where((t >= tdel) & (d["mode"] == "STAND_DEFAULT"))[0]; row["delete_to_sd_observed_ms"] = 1e3 * (t[isd[0]] - tdel) if len(isd) else np.nan
        row["delete_to_set_accepted_ms"] = 1e3 * (m["t_resp"] - tdel) if m.get("t_resp") and tdel else np.nan
        if "urs_active" in ev:
            u = ev["urs_active"]; row.update(idle_to_urs_ms=1e3 * u["dt_idle_to_urs_s"], idle_to_add_ms=1e3 * u["dt_idle_to_add_s"], p0_dev_from_D=u["p0_dev_from_D"], resumed=True)
            ti = m["t_in0_audio"]; wi = (au >= ti - 0.1) & (au <= ti + m["L_in"] + 0.4); row["in_max_speed_meas"] = float(speed(M, t)[wi].max()); row["in_max_jerk_cmd"] = jerk(C[ph == "fade_in"]); row["resume_audio_s"] = ti
            row["in_sonic_sync_err_end"] = float(np.nanmax(np.abs(C[ph == "sonic2"][:1] - S[ph == "sonic2"][:1]))) if (ph == "sonic2").any() else np.nan
        else: row["resumed"] = False
        ms = (ph == "pre") & (au >= -3); row["sonic_jerk_ref"] = row["sonic_ref_p99_jerk"]
        for nm, mask in (("before", ph == "pre"), ("after", ph == "sonic2")):
            idx = np.where(mask)[0]; idx = idx[idx + 4 < len(t)]
            if len(idx) > 10: e = np.abs(M[idx + 4] - C[idx]); row[f"track_lag4_{nm}_mean"] = float(np.nanmean(e)); row[f"track_lag4_{nm}_max"] = float(np.nanmax(e)); row[f"track_{nm}_n"] = int(len(idx))
            else: row[f"track_lag4_{nm}_mean"] = np.nan; row[f"track_{nm}_n"] = int(len(idx))
        row["sonic_driven_frac"] = float(np.mean(np.isin(ph[win], ["pre", "fade_out", "fade_in", "sonic2", "end_fade"])))
    else: row["sonic_driven_frac"] = 0.0
    row["pelvis_z_min"] = float(np.nanmin(d["pelvis_z"])); row["tilt_max_deg"] = float(np.nanmax(d["tilt_deg"])); row["fall"] = row["pelvis_z_min"] < 0.5
    return row
if __name__ == "__main__":
    rows = []
    for f in sorted(glob.glob(R + "traces/*_V?_r?.json")):
        n = os.path.basename(f)[:-5]
        try: rows.append(analyze(n))
        except Exception as e: rows.append({"take": n, "error": repr(e)[:200]}); print("ERR", n, repr(e)[:200])
    keys = list(dict.fromkeys(k for r in rows for k in r))
    os.makedirs(R + "metrics", exist_ok=True)
    with open(R + "metrics/takes.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
    summ = {}
    num = lambda r, k: r.get(k) if isinstance(r.get(k), (int, float)) and not (isinstance(r.get(k), float) and np.isnan(r.get(k))) else None
    for var in ("V0", "V1", "V2"):
        for scope, sel in (("subset", lambda r: r["rep"] in ("1", "2") and r["turn_id"] in ("v2visit_vip_t00_greeting", "v2visit_staff_t00_greeting", "v2visit_vip_t03_dialogue", "v2visit_vip_t02_dialogue", "v2visit_staff_t02_dialogue", "v2visit_regular_t05_farewell")), ("all20", lambda r: r["rep"] == "3")):
            rs = [r for r in rows if r.get("variant") == var and "error" not in r and sel(r)]
            if not rs: continue
            S = {"n": len(rs), "falls": sum(r["fall"] for r in rs), "contract_ok": sum(r["executed_eq_contract"] for r in rs), "resumed": sum(bool(r.get("resumed")) for r in rs)}
            for k in ("still_s", "still_frac", "still_body_s", "still_body_frac", "sonic_driven_frac", "out_max_speed_meas", "out_max_jerk_cmd", "sd_snap_max_speed_meas", "in_max_speed_meas", "in_max_jerk_cmd", "delete_to_set_accepted_ms", "delete_to_sd_observed_ms", "idle_to_urs_ms", "idle_to_add_ms",
                      "onset_minus_anchor_ms", "stroke_minus_anchor_ms", "track_lag4_before_mean", "track_lag4_after_mean", "pelvis_z_min", "tilt_max_deg", "sonic_ref_max_speed", "sonic_ref_p99_jerk", "sonic_ref_still_s", "handoff_shift_vs_plan_s"):
                x = [num(r, k) for r in rs if num(r, k) is not None]
                if x: S[k] = {"median": float(np.median(x)), "mean": float(np.mean(x)), "max": float(np.max(x)), "min": float(np.min(x))}
            S["policy_counts"] = {p: sum(r["policy"] == p for r in rs) for p in ("stroke", "mid", "onset")}
            summ[f"{var}_{scope}"] = S
    json.dump(summ, open(R + "metrics/summary.json", "w"), indent=1, default=float)
    print(len(rows), "takes"); pass
