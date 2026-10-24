"""4e: walk-arc metrics from <dir>/<name>_log.csv + _events.json + _cmd.csv. usage: analyze_walk.py DIR NAME -> prints/returns dict."""
import csv, json, math, sys
import numpy as np
def load(p):
    r = list(csv.DictReader(open(p))); return {k: np.array([float(x) if x not in ("", "None") else np.nan for x in [q[k] for q in r]]) if k not in ("mode", "phase") else np.array([q[k] for q in r]) for k in r[0]}
def walk_metrics(D, N, LN=None, WN=None):
    LN = LN or N; WN = WN or N
    L = load(f"{D}/{LN}_log.csv"); ev = json.load(open(f"{D}/{WN}_events.json")); C = load(f"{D}/{WN}_cmd.csv")
    E = {e["event"] + (":" + e["name"] if e["event"] == "phase" else ""): e for e in ev}
    t0, ta, tb, tc = E["phase:walk1"]["t"], E["phase:arc"]["t"], E["phase:walk2"]["t"], E["phase:stop"]["t"]
    F = E["final"]; m = lambda a, b: (L["t"] >= a) & (L["t"] < b)
    arc = m(ta, tb + 0.4); yaw = np.unwrap(L["yaw"]); ya = yaw[np.argmin(abs(L["t"] - ta))]
    cm = C["phase"] == "arc"
    # vx during turn: odometry body-frame vx while |wz|>0.3
    turning = m(ta + 0.5, tb) & (abs(L["wz"]) > 0.3)
    # path curvature = wz / v over the arc (odometry): radius from displacement
    wz_arc = float(np.nanmean(L["wz"][turning])); vx_arc = float(np.nanmean(L["vx"][turning])); vxmin = float(np.nanmin(L["vx"][turning]))
    R = vx_arc / wz_arc if wz_arc else None
    during = m(t0, tc + 3); rest = float(np.nanmedian(L["tilt"][m(t0 - 1, t0)]))
    st = L["mode"][m(tc, tc + 4)]
    d = {"name": N, "walk1_m": F["d1"], "arc_path_chord_m": F["d_arc"], "walk2_m": F["d2"], "walk2_to_stop_cmd_m": F["d2_to_stop"],
         "heading_change_deg": F["turn_deg_final"], "heading_change_at_arc_end_deg": F["turn_deg_arc_end"], "walk1_heading_drift_deg": F["straight_dev_walk1_deg"],
         "arc_duration_s": tb - ta, "arc_cmd_vx": float(np.nanmean(C["cmd_vx"][cm])), "arc_cmd_wz": float(np.nanmean(C["cmd_wz"][cm])),
         "arc_odom_vx_mean": vx_arc, "arc_odom_vx_min": vxmin, "arc_odom_wz_mean": wz_arc, "arc_radius_m": R, "arc_confirmed_vx_gt0": bool(vx_arc > 0.1 and vxmin > 0.0),
         "arc_net_forward_chord_m": F["d_arc"], "pelvis_z_min": float(np.nanmin(L["z"][during])), "pelvis_z_max": float(np.nanmax(L["z"][during])),
         "tilt_max_dev_deg": float(np.nanmax(abs(L["tilt"][during] - rest))), "time_to_stable_s": E["stable"]["time_to_stable_s"], "stable_ok": E["stable"]["ok"],
         "final_mode": str(L["mode"][-1]), "stop_to_stable_modes": sorted(set(st))}
    return d
if __name__ == "__main__":
    print(json.dumps(walk_metrics(sys.argv[1], sys.argv[2]), indent=1))
