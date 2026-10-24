"""4e: mission-level gates + per-turn gates -> runs/4e_missions/metrics/missions.json (run analyze_4e.py 1 first, same env)."""
import csv, json, sys, os
import numpy as np
H = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, H)
from analyze_walk import walk_metrics, load
R = os.path.join(H, "runs/4e_missions"); T = list(csv.DictReader(open(f"{R}/metrics/turns.csv")))
runs = {}
for l in open(f"{R}/logs/turn_runs.jsonl"):
    d = json.loads(l); runs[d["turn"]] = d
out = {}
for rk in ("vip", "staff", "guest"):
    N = f"m_{rk}"; w = walk_metrics(f"{R}/traces", N, N, f"{N}_walk"); L = load(f"{R}/traces/{N}_log.csv")
    t0 = min(r["t_start_mono"] for r in runs.values() if r["rank"] == rk)
    pre = L["t"] < t0; i0 = np.argmin(abs(L["t"] - t0))
    rows = [r for r in T if r["rank"] == rk]
    g = {"walk1_1.5m_pm0.15": abs(w["walk1_m"] - 1.5) <= 0.15, "post_arc_walk_ge_0.8m": w["walk2_m"] >= 0.8,
         "heading_90_pm5": abs(w["heading_change_deg"] - 90) <= 5, "arc_vx_gt0_while_turning": w["arc_confirmed_vx_gt0"],
         "stable_after_stop": bool(w["stable_ok"] and L["mode"][i0] == "STAND_DEFAULT"), "pelvis_ge_0.50_mission": float(np.nanmin(L["z"])) >= 0.5}
    g["walk_gates_ok"] = all(g.values())
    tg = [r["gates_ok"] == "True" for r in rows]
    mode_seq = sorted(set(L["mode"]))
    out[rk] = {"walk": w, "walk_gates": g, "mode_at_first_turn": str(L["mode"][i0]), "pelvis_z_min_mission": float(np.nanmin(L["z"])), "tilt_max_deg_mission": float(np.nanmax(L["tilt"])),
               "falls": int(np.nanmin(L["z"]) < 0.5), "turns_gates_ok": f"{sum(tg)}/{len(tg)}", "turns_all_ok": all(tg), "mission_ok": bool(g["walk_gates_ok"] and all(tg)),
               "modes_seen": mode_seq, "time_walk_start_to_first_turn_s": float(t0 - json.load(open(f"{R}/traces/{N}_walk_events.json"))[0]["t"]),
               "turns": [{k: (None if r.get(k, "") == "" else r[k]) for k in ("turn", "type", "emote", "gates_ok", "onset_minus_anchor_ms", "stroke_minus_anchor_ms", "err_deg_mean_hold", "hold_actual_s_prog99",
                                                                          "arm_track_err_rad", "track_err_rad_outside_lag4", "pelvis_z_min", "executed_eq_contract", "mode_before")} for r in rows]}
json.dump(out, open(f"{R}/metrics/missions.json", "w"), indent=1)
for rk, d in out.items():
    w = d["walk"]; print(rk, "ok" if d["mission_ok"] else "FAIL", {k: round(v, 3) if isinstance(v, float) else v for k, v in w.items() if k in ("walk1_m", "arc_path_chord_m", "walk2_m", "heading_change_deg", "arc_duration_s", "arc_odom_vx_mean", "arc_odom_vx_min", "arc_radius_m", "pelvis_z_min", "tilt_max_dev_deg", "time_to_stable_s", "walk1_heading_drift_deg")}, d["walk_gates"], d["turns_gates_ok"], "falls", d["falls"], "z", round(d["pelvis_z_min_mission"], 3), "tilt", round(d["tilt_max_deg_mission"], 2), d["mode_at_first_turn"])
