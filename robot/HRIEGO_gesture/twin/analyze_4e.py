"""4e per-turn metrics (copy of analyze_visits.py, 4e traces; ranks vip staff guest).
usage: analyze_visits.py [REP=1]"""
import sys as _sys, pathlib as _pl; _sys.path.insert(0, str(_pl.Path(__file__).resolve().parents[1]))
from paths import laplace_root as _lr, semantic_corporea_root as _sc, py_physicalai as _pyp, conda_exe as _conda; _LR = _lr(); _SC = _sc()
import csv, json, sys, os
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); os.chdir(H); sys.path.insert(0, H)
import analyze as A          # 4aa metrics (pointing)
from x2_ultra.robot.joints import ARM_JOINTS
R = "runs/4e_missions/"; V = os.path.join(os.path.dirname(H), "visits_v2")
C8 = str(_LR / "experiments/RCP-01/runs/20261021_rcp01c08_preset_latency_twin")
sys.path.insert(0, C8 + "/scripts")
from csv_timeline import stroke_index
TL = json.load(open(C8 + "/metrics/preset_csv_timeline.json"))
JOINTS = [f"{s}_{j}_joint" for s in ("left", "right") for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")] + ["waist_yaw", "waist_pitch", "waist_roll", "head_yaw", "head_pitch"]
REP = sys.argv[1] if len(sys.argv) > 1 else "1"
runs = {}
for l in open(R + "logs/turn_runs.jsonl"):
    d = json.loads(l)
    if str(d["rep"]) == REP: runs[d["turn"]] = d       # last record wins
F = lambda x: None if x is None else float(x)

def preset(row, t, run, name):
    ev = json.load(open(R + f"traces/{name}.json")); plan = json.load(open(V + f"/{t['rank']}/anchor_plan/plans/{t['turn_id']}.plan.json"))
    evt = json.load(open(V + f"/{t['rank']}/anchor_plan/events/{t['turn_id']}.event.json")); fs = evt["audio"]["sample_rate_hz"]
    a0 = evt["anchor"]["start_sample"] / fs; bk = f"{t['motion_id']}/{t['area']}"
    rows = list(csv.DictReader(open(R + f"traces/{name}.csv")))
    tt = np.array([float(r["t"]) for r in rows]); Q = np.array([[float(r[j]) if r[j] != "" else np.nan for j in JOINTS] for r in rows])
    z = np.array([float(r["pelvis_z"]) for r in rows]); tilt = np.array([float(r["tilt_deg"]) for r in rows])
    ps = np.array([int(r["player_state"] or 0) for r in rows]); mot = {r["motion"] for r, p in zip(rows, ps) if p in (1, 2)}
    row.update(emote_motion=bk, anchor_start_s=a0, delay_s=ev.get("start_delay_s"), dispatched=ev["dispatched"], completed=ev.get("completed"),
               set_code=ev.get("set_code"), set_state=ev.get("set_state"), mc_motion_seen=",".join(sorted(mot)), service_rtt_ms=F(ev.get("service_rtt_s")) and 1e3 * ev["service_rtt_s"],
               wav_sha_ok=ev["wav_sha256"] == plan["audio"]["sha256"], mode_before=ev.get("mode_before"), mode_after=ev.get("mode_after"), stable_after=ev.get("stable_after"),
               mode_all_sd=all(r["mode"] == "STAND_DEFAULT" for r in rows))
    row["executed_eq_contract"] = bool(ev["dispatched"] and ev["binding_key"] == bk and ev["motion_id"] == t["motion_id"] and ev["area"] == t["area"] and ev.get("set_code") == 0
                                       and mot == {str(t["motion_id"])} and ev.get("completed") and ev["plan_status"] == "pending")
    row["mode_transitions_ok"] = bool(ev.get("mode_before") == "STAND_DEFAULT" and row["mode_all_sd"] and ev.get("mode_after") == "STAND_DEFAULT" and ev.get("stable_after"))
    if ev["dispatched"]:
        td, au = ev["t_dispatch"], ev["audio_start_monotonic"]; pre = tt < td; post = tt >= td
        qb = np.nanmedian(Q[pre], 0); Dm = np.nanmax(np.abs(Q - qb), 1); k = np.where(post & (Dm > 0.05))[0]; onset = float(tt[k[0]] - au) if len(k) else None
        tl = TL[bk]; jd = tl["dominant_joint"]; jc = JOINTS.index(jd.replace("_joint", "") if jd.startswith(("waist", "head")) else jd)
        d = np.abs(Q[:, jc] - tl["rest_q"][jd]); ip = int(np.where(post & (ps == 2))[0][0]); i = ip + stroke_index(d[ip:]); stroke = float(tt[i] - au)
        row.update(dispatch_jitter_ms=ev["jitter_ms"], onset_audio_s=onset, stroke_audio_s=stroke, onset_minus_anchor_ms=None if onset is None else 1e3 * (onset - a0),
                   stroke_minus_anchor_ms=1e3 * (stroke - a0), dom_disp_ratio_vs_c08=float(np.nanmax(d) / max(tl["max_disp_rad"], 1e-9)),
                   clamp_flag=bk == "3029/11", pelvis_z_min=float(z.min()), tilt_max_deg=float(tilt.max()))
        row["gate_jitter"] = abs(ev["jitter_ms"]) <= 5; row["gate_onset100"] = onset is not None and abs(onset - a0) <= 0.100
        row["gate_onset150"] = onset is not None and abs(onset - a0) <= 0.150
    row["contract_ok"] = row["executed_eq_contract"]
    row["gates_ok"] = bool(row["contract_ok"] and row.get("gate_jitter") and row.get("gate_onset100") and row.get("pelvis_z_min", 0) >= 0.5 and row["mode_transitions_ok"])

def point(row, t, run, name):
    m = A.analyze(R + f"traces/{name}.csv", "SONIC", base_npz=f"{R}plans/{t['turn_id']}_arms.npz")
    row.update({k: m[k] for k in ("direction", "hold_s_cmd", "anchor_start_s", "err_deg_at_stroke_planned", "err_deg_at_anchor", "err_deg_mean_hold", "err_deg_mean_anchor_to_hold_end", "err_source",
                                  "t95_minus_anchor_ms", "w1_minus_anchor_ms", "hold_actual_s_prog95", "hold_actual_s_prog99", "hold_actual_s_err15", "jitter_ms_max_abs", "pelvis_z_min", "tilt_max_deg",
                                  "mode_all_urs", "return_to_base_err_rad", "track_err_rad_outside", "track_err_rad_outside_lag4", "track_err_rad_outside_lag4_max", "track_err_rad_inside", "cmd_speed_prep_start", "cmd_speed_rec_end", "cmd_jerk_prep_start", "cmd_jerk_rec_end", "base_speed_max", "vmax_cmd_rad_s")})
    row["dispatch_jitter_ms"] = m["jitter_ms_max_abs"]
    row["executed_eq_contract"] = bool(m["err_deg_mean_hold"] <= 15); row["contract_ok"] = row["executed_eq_contract"]
    row["gate_hold"] = bool(m["gate_hold"]); row["gate_jitter"] = m["gate_jitter"]; row["gate_track_outside"] = m["track_err_rad_outside_lag4"] <= 0.015
    row["gate_return"] = m["gate_return"]; row["gate_urs"] = m["gate_urs"]; row["gate_err_anchor15"] = m["err_deg_at_anchor"] <= 15
    row["mode_transitions_ok"] = bool(m["mode_all_urs"] and "delete_source_error=None" in run["stdout_tail"])
    row["gates_ok"] = bool(row["contract_ok"] and row["gate_hold"] and row["gate_jitter"] and row["gate_track_outside"] and row["gate_return"] and m["pelvis_z_min"] >= 0.5 and row["mode_transitions_ok"])

def none(row, t, run, name):
    rows = list(csv.DictReader(open(R + f"traces/{name}.csv"))); ph = [r["phase"] for r in rows]; i0 = ph.index("sonic"); ns = ph.count("sonic")
    g = lambda k: np.array([float(r[k]) if r[k] not in ("", "None") else np.nan for r in rows])
    C = np.stack([g(f"cmd_{n}") for n in ARM_JOINTS], 1)[i0:i0 + ns]; M = np.stack([g(f"meas_{n}") for n in ARM_JOINTS], 1)
    Mm = M[i0 + 4:i0 + ns + 4]; n = min(len(C), len(Mm))
    row.update(sonic_ticks=ns, arm_track_err_rad=float(np.nanmean(np.abs(Mm[:n] - C[:n]))), arm_track_err_max_rad=float(np.nanmax(np.abs(Mm[:n] - C[:n]))),
               pelvis_z_min=float(np.nanmin(g("pelvis_z"))), tilt_max_deg=float(np.nanmax(g("tilt_deg"))), mode_all_urs=all(r["mode"] == "UPPERBODY_REMOTE_SPLIT" for r in rows),
               ramp_in_ticks=ph.count("ramp"))
    row["mode_transitions_ok"] = bool(row["mode_all_urs"] and "delete_source_error=None" in run["stdout_tail"])
    row["gate_track"] = row["arm_track_err_rad"] <= 0.015; row["contract_ok"] = True
    row["gates_ok"] = bool(row["gate_track"] and row["pelvis_z_min"] >= 0.5 and row["mode_transitions_ok"])

out = []
for rk in "vip staff guest".split():
    for l in open(f"{V}/{rk}/tts_manifest.jsonl"):
        t = json.loads(l); t["rank"] = rk; tid = t["turn_id"]; kind = "NONE" if t["emote"] == "NONE" else "POINT" if t["emote"].startswith("POINT") else "PRESET"
        row = {"rank": rk, "turn": tid.split("_", 2)[2], "turn_id": tid, "type": kind, "emote": t["emote"], "rep": REP, "audio_s": t["duration_s"]}
        run = runs.get(tid)
        if run is None or not run["csv_ok"]:
            row.update(completed_turn=False, gates_ok=False, error="no csv" if run else "not run"); out.append(row); continue
        name = f"{tid}_r{REP}"
        try:
            {"PRESET": preset, "POINT": point, "NONE": none}[kind](row, t, run, name); row["completed_turn"] = True
        except Exception as e:  # noqa: BLE001
            row.update(completed_turn=False, gates_ok=False, error=repr(e)[:200])
        row["turn_wall_incl_reset_s"] = run["wall_turn_incl_reset_s"]; out.append(row)
# pass the next-turn handoff check: a URS turn is OK only if the following turn started in STAND_DEFAULT (preset mode_before)
for i, r in enumerate(out[:-1]):
    if r["type"] != "PRESET" and r.get("mode_transitions_ok") and out[i + 1]["rank"] == r["rank"] and out[i + 1]["type"] == "PRESET":
        nm = out[i + 1].get("mode_before"); r["next_turn_mode_before"] = nm
        if nm != "STAND_DEFAULT": r["mode_transitions_ok"] = False; r["gates_ok"] = False
keys = []
for r in out:
    for k in r:
        if k not in keys: keys.append(k)
os.makedirs(R + "metrics", exist_ok=True)
with open(R + f"metrics/turns.csv" if REP == "1" else R + f"metrics/turns_r{REP}.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(out)
vis = {}
for rk in "vip staff guest".split():
    rs = [r for r in out if r["rank"] == rk]
    vis[rk] = {"turns": len(rs), "completed": sum(bool(r.get("completed_turn")) for r in rs), "gates_ok": sum(bool(r.get("gates_ok")) for r in rs),
               "falls": sum(1 for r in rs if r.get("pelvis_z_min") is not None and r["pelvis_z_min"] < 0.5), "wall_s": sum(r.get("turn_wall_incl_reset_s", 0) for r in rs),
               "all_completed": all(r.get("completed_turn") for r in rs), "failed_turns": [r["turn"] for r in rs if not r.get("gates_ok")]}
pr = [r for r in out if r["type"] == "PRESET" and r.get("completed_turn")]; po = [r for r in out if r["type"] == "POINT" and r.get("completed_turn")]; no = [r for r in out if r["type"] == "NONE" and r.get("completed_turn")]
mx = lambda rs, k, f=max: None if not rs else float(f(abs(r[k]) for r in rs if r.get(k) is not None))
summ = {"rep": REP, "turns": len(out), "completed": sum(bool(r.get("completed_turn")) for r in out), "gates_ok": sum(bool(r.get("gates_ok")) for r in out),
        "falls": sum(v["falls"] for v in vis.values()), "visits": vis,
        "preset": {"n": len(pr), "executed_eq_contract": sum(r["executed_eq_contract"] for r in pr), "max_abs_jitter_ms": mx(pr, "dispatch_jitter_ms"),
                   "onset_err_ms_abs_max": mx(pr, "onset_minus_anchor_ms"), "onset_err_ms_abs_median": float(np.median([abs(r["onset_minus_anchor_ms"]) for r in pr if r.get("onset_minus_anchor_ms") is not None])) if pr else None,
                   "gate_onset100": sum(bool(r.get("gate_onset100")) for r in pr), "gate_onset150": sum(bool(r.get("gate_onset150")) for r in pr),
                   "stroke_err_ms_abs_median": float(np.median([abs(r["stroke_minus_anchor_ms"]) for r in pr if r.get("stroke_minus_anchor_ms") is not None])) if pr else None,
                   "pelvis_z_min": min((r["pelvis_z_min"] for r in pr if r.get("pelvis_z_min")), default=None)},
        "point": {"n": len(po), "executed_eq_contract": sum(r["executed_eq_contract"] for r in po), "err_deg_mean_hold_max": mx(po, "err_deg_mean_hold"),
                  "err_deg_at_anchor_max": mx(po, "err_deg_at_anchor"), "hold_prog99_range": [min(r["hold_actual_s_prog99"] for r in po), max(r["hold_actual_s_prog99"] for r in po)] if po else None,
                  "track_err_outside_lag4_max": mx(po, "track_err_rad_outside_lag4"), "jitter_ms_max": mx(po, "dispatch_jitter_ms"), "return_err_max": mx(po, "return_to_base_err_rad"),
                  "pelvis_z_min": min((r["pelvis_z_min"] for r in po), default=None)},
        "none": {"n": len(no), "arm_track_err_max": mx(no, "arm_track_err_rad"), "pelvis_z_min": min((r["pelvis_z_min"] for r in no), default=None)},
        "turn_wall_s": {"mean": float(np.mean([r["turn_wall_incl_reset_s"] for r in out if "turn_wall_incl_reset_s" in r])), "max": float(max(r["turn_wall_incl_reset_s"] for r in out if "turn_wall_incl_reset_s" in r))}}
json.dump(summ, open(R + f"metrics/summary{'' if REP == '1' else '_r' + REP}.json", "w"), indent=1)
print(json.dumps(summ, indent=1)); print()
for r in out:
    print(r["rank"], r["turn"], r["type"], r["emote"], "OK" if r.get("gates_ok") else "FAIL", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k in ("onset_minus_anchor_ms", "stroke_minus_anchor_ms", "dispatch_jitter_ms", "err_deg_mean_hold", "hold_actual_s_prog99", "arm_track_err_rad", "track_err_rad_outside", "pelvis_z_min", "error")})
