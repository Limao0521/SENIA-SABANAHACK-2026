"""4aa metrics. usage: analyze.py TAG [TAG...]  (TAG = READY | SONIC); writes metrics/pointing_summary.{json,csv}
FK: c04 angular error (wrist_roll_link - shoulder_pitch_link in the pelvis frame vs RIGHT[0,-1,0]/LEFT[0,1,0]/FRONT[1,0,0]) on the recorded twin
qpos (rep 1) and, for every rep, on the measured arm + waist joints (upright pelvis)."""
import csv, glob, json, sys
import numpy as np
from point_common import *
from x2_ultra.robot.joints import ARM_JOINTS
R = "runs/4aa_pointing/"; TICK = 0.02
READY = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])
LO = HI = None
def limits():
    global LO, HI
    _R = {"shoulder_pitch": (-3.0805, 2.0333), "shoulder_yaw": (-2.5569, 2.5569), "elbow": (-2.3562, 0.0), "wrist_yaw": (-2.5569, 2.5569), "wrist_pitch": (-0.5236, 0.5236)}
    lo, hi = [], []
    for n in ARM_JOINTS:
        s = n.split("_")[0]; p = n[len(s) + 1:-6]
        if p == "shoulder_roll": l, h = (-0.0611, 3.0456) if s == "left" else (-3.0456, 0.0611)
        elif p == "wrist_roll": l, h = (-1.5097, 0.7243) if s == "left" else (-0.7243, 1.5097)
        else: l, h = _R[p]
        lo.append(l + .05); hi.append(h - .05)
    LO, HI = np.array(lo), np.array(hi)
limits()
fk = FK()
def quintic(s):
    s = np.clip(s, 0, 1); return s ** 3 * (10 - 15 * s + 6 * s * s)
def first_at(mask, t):
    i = np.where(mask)[0]; return float(t[i[0]]) if len(i) else None
def longest_run(mask, t):
    best = 0; cur = 0
    for m in mask:
        cur = cur + 1 if m else 0; best = max(best, cur)
    return best * TICK

def analyze(csvf, base_name, base_npz=None):
    rows = list(csv.DictReader(open(csvf))); m = json.load(open(csvf.replace(".csv", ".json")))
    D = m["direction"]; side = SIDE[D]; tg = json.load(open("point_targets.json"))[D]
    g = lambda k: np.array([float(r[k]) if r[k] not in ("", "None") else np.nan for r in rows])
    ta = g("audio_s"); w = g("w"); tp = g("t_pub"); ts_ = g("t_sched")
    cmd = np.stack([g(f"cmd_{n}") for n in ARM_JOINTS], 1); meas = np.stack([g(f"meas_{n}") for n in ARM_JOINTS], 1)
    wm = np.stack([g(f"wmeas_{k}") for k in ("twist_waist", "bend_forward", "bend_sideways", "turn_head")], 1)
    jn = [f"{side}_{p}_joint" for p in PARTS]; pc = np.array([ARM_JOINTS.index(n) for n in jn]); tgt = np.array([tg["joints"][n] for n in jn])
    a0 = m["anchor_start_s"]; ts = m["stroke_s"]; hold = m["hold_s"]; tp0 = m["prep_start_s"]; tr1 = m["recovery_end_s"]
    # base
    if m["base"] == "READY": base = lambda t: READY
    else:
        z = np.load(base_npz or R + f"motion/P_{D}_arms.npz"); bc = z["cmd"][100:]
        base = lambda t: bc[min(int(round(max(t, 0) / TICK)), len(bc) - 1)]
    B = np.stack([base(t) for t in ta])
    prog = ((meas[:, pc] - B[:, pc]) @ (tgt - B[:, pc].mean(0)) ) if False else None
    # progress toward the target: per tick, projection of (meas-base) on (target-base) of the pointing arm
    dv = tgt[None] - B[:, pc]; prog = np.einsum("ij,ij->i", meas[:, pc] - B[:, pc], dv) / np.maximum(np.einsum("ij,ij->i", dv, dv), 1e-9)
    # FK error from measured joints (+ waist) every tick
    errm = np.zeros(len(rows)); ext = np.zeros(len(rows))
    for i in range(len(rows)):
        arm = dict(zip(ARM_JOINTS, meas[i])); arm.update({"waist_yaw_joint": wm[i, 0], "waist_pitch_joint": wm[i, 1], "waist_roll_joint": wm[i, 2]})
        errm[i], ext[i] = fk.err_deg(fk.qpos_with(arm), D)
    qf = glob.glob(csvf.replace("traces", "video").replace(".csv", "_qpos.npz")); errq = None
    if qf:
        Q = np.load(qf[0])["qpos"]; errq = np.array([fk.err_deg(q, D)[0] for q in Q])
    E = errq if errq is not None else errm
    at = lambda arr, t: float(arr[np.argmin(np.abs(ta - t))])
    win = (ta >= ts) & (ta <= ts + hold); win_a = (ta >= a0) & (ta <= ts + hold)
    t95 = first_at((prog >= 0.95) & (ta > tp0), ta); t15 = first_at((E <= 15) & (ta > tp0), ta)
    hold_prog = longest_run((prog >= 0.95), ta); hold_15 = longest_run(E <= 15, ta)
    # jitter / timing
    jit = (tp - ts_) * 1e3; dtp = np.diff(tp) * 1e3
    # kinematics of the pointing arm (measured, commanded)
    vm = np.abs(np.diff(meas[:, pc], axis=0)) / TICK; am = np.abs(np.diff(meas[:, pc], 2, axis=0)) / TICK ** 2
    vc = np.abs(np.diff(cmd[:, pc], axis=0)) / TICK
    # clamp / limiter fractions (ideal command from the plan: base + w (target - base))
    ideal = B.copy(); ideal[:, pc] = B[:, pc] + w[:, None] * (tgt - B[:, pc]); clipped = np.clip(ideal, LO, HI)
    act = w > 0
    clamp_frac = float((np.abs(clipped - ideal)[act][:, pc] > 1e-6).any(1).mean()) if act.any() else 0.0
    lim_frac = float((np.abs(cmd - clipped)[:, pc] > 1e-6).any(1)[(ta > tp0) & (ta < tr1 + 3)].mean())
    # 0.35 rad/s real-robot limiter applied to the clipped ideal (offline)
    x = B[0, pc].copy(); sim = []
    for i in range(len(ta)):
        x = x + np.clip(clipped[i, pc] - x, -0.35 * TICK, 0.35 * TICK); sim.append(x.copy())
    sim = np.array(sim); p35 = np.einsum("ij,ij->i", sim - B[:, pc], dv) / np.maximum(np.einsum("ij,ij->i", dv, dv), 1e-9)
    t95_35 = first_at((p35 >= 0.95) & (ta > tp0), ta)
    ok35 = (ta >= ts) & (ta <= ts + hold)
    # return to base
    late = ta >= tr1 + 0.6
    ret_err = float(np.abs(meas[late] - B[late]).max()) if late.any() else None
    outside = ((ta < tp0 - 0.2) | (ta > tr1 + 0.6)) & (ta >= 0); inside = (ta >= tp0) & (ta <= tr1)
    trk_out = float(np.nanmean(np.abs(meas - cmd)[outside])); trk_in = float(np.nanmean(np.abs(meas - cmd)[inside]))
    ii = np.where(outside[:-4])[0]; trk_out_lag4 = float(np.nanmean(np.abs(meas[ii + 4] - cmd[ii]))); trk_out_lag4_max = float(np.nanmax(np.abs(meas[ii + 4] - cmd[ii])))
    trk_out_nopt = float(np.nanmean(np.abs(meas - cmd)[:, [i for i in range(14) if i not in pc]][outside]))
    # discontinuity at ramp edges: max commanded speed within +-0.1 s of prep start / recovery end vs max speed of the base alone in the same windows
    def edge(t):
        s = (ta[1:] >= t - 0.1) & (ta[1:] <= t + 0.1); return float(vc[s][:, :].max()) if s.any() else None
    bv = np.abs(np.diff(B, axis=0)) / TICK
    edges = {"cmd_speed_prep_start": edge(tp0), "cmd_speed_rec_end": edge(tr1)}
    jerk_edge = lambda t: float((np.abs(np.diff(cmd, 3, axis=0)) / TICK ** 3)[(ta[3:] >= t - .1) & (ta[3:] <= t + .1)][:, pc].max())
    stroke_t = t95
    out = {"file": csvf.split("/")[-1], "direction": D, "base": m["base"], "hold_s_cmd": hold, "anchor_start_s": a0, "stroke_planned_s": ts, "T0_phase_ms": 1e3 * m["phase_s"],
           "err_deg_at_stroke_planned": at(E, ts), "err_deg_at_anchor": at(E, a0), "err_deg_mean_hold": float(np.nanmean(E[win])), "err_deg_mean_anchor_to_hold_end": float(np.nanmean(E[win_a])),
           "err_deg_min": float(np.nanmin(E[(ta > tp0) & (ta < tr1)])), "err_source": "recorded_qpos" if errq is not None else "measured_joints",
           "err_deg_meas_joints_mean_hold": float(np.nanmean(errm[win])), "extension_mean_hold": float(np.nanmean(ext[win])),
           "t95_s": t95, "t95_minus_anchor_ms": None if t95 is None else 1e3 * (t95 - a0), "w1_minus_anchor_ms": 1e3 * (ts - a0), "t_err15_minus_anchor_ms": None if t15 is None else 1e3 * (t15 - a0),
           "hold_actual_s_prog95": hold_prog, "hold_actual_s_err15": hold_15,
           "jitter_ms_max_abs": float(np.abs(jit).max()), "pub_latency_ms_max": float(((g("t_pub_done") - tp) * 1e3).max()), "jitter_ms_std": float(jit.std()), "tick_dt_ms_max_dev": float(np.abs(dtp - 20).max()),
           "vmax_meas_rad_s": float(vm.max()), "amax_meas_rad_s2": float(am.max()), "vmax_cmd_rad_s": float(vc.max()),
           "clamp_frac": clamp_frac, "limiter_2rad_frac": lim_frac,
           "real035_t95_minus_anchor_ms": None if t95_35 is None else 1e3 * (t95_35 - a0), "real035_prog_at_stroke": float(p35[np.argmin(np.abs(ta - ts))]), "real035_prog_at_anchor": float(p35[np.argmin(np.abs(ta - a0))]),
           "real035_prog_max_in_hold": float(p35[ok35].max()), "real035_hold_prog95_s": longest_run(p35 >= 0.95, ta),
           "pelvis_z_min": float(np.nanmin(g("pelvis_z"))), "tilt_max_deg": float(np.nanmax(g("tilt_deg"))), "mode_all_urs": all(r["mode"] == "UPPERBODY_REMOTE_SPLIT" for r in rows),
           "return_to_base_err_rad": ret_err, "track_err_rad_outside": trk_out, "track_err_rad_inside": trk_in, "track_err_rad_outside_lag4": trk_out_lag4, "track_err_rad_outside_lag4_max": trk_out_lag4_max, "track_err_rad_nonpointing_arm_outside": trk_out_nopt,
           "cmd_speed_prep_start": edges["cmd_speed_prep_start"], "cmd_speed_rec_end": edges["cmd_speed_rec_end"], "cmd_jerk_prep_start": jerk_edge(tp0), "cmd_jerk_rec_end": jerk_edge(tr1),
           "base_speed_max": float(bv[:, :].max())}
    g1 = lambda c: bool(c)
    out["gate_err_hold"] = out["err_deg_mean_hold"] <= 15 and out["err_deg_at_anchor"] <= 15
    out["gate_stroke_timing"] = t95 is not None and abs(t95 - a0) <= 0.1
    out["gate_jitter"] = out["jitter_ms_max_abs"] <= 5
    out["hold_actual_s_prog99"] = longest_run(prog >= 0.99, ta); out["t95_minus_stroke_planned_ms"] = None if t95 is None else 1e3 * (t95 - ts)
    out["gate_stroke_timing_vs_planned_stroke"] = t95 is not None and abs(t95 - ts) <= 0.1
    out["gate_hold"] = 1.0 <= out["hold_actual_s_prog99"] <= 2.0 and 1.0 <= hold <= 2.0
    out["gate_pelvis"] = out["pelvis_z_min"] >= 0.5
    out["gate_return"] = ret_err is not None and ret_err < 0.05
    out["gate_urs"] = out["mode_all_urs"]; out["gate_track_outside"] = trk_out_lag4 <= 0.015
    return out

if __name__ == "__main__":
    res = []
    for tag in sys.argv[1:]:
        for f in sorted(glob.glob(R + f"traces/{tag}_*_r[0-9].csv")):
            try: res.append(analyze(f, tag))
            except Exception as e: print("FAIL", f, repr(e))
            else: print(res[-1]["file"], {k: (round(v, 3) if isinstance(v, float) else v) for k, v in res[-1].items() if k.startswith(("err_deg_at", "err_deg_mean_hold", "t95_minus", "hold_actual", "jitter_ms_max", "gate", "return"))})
    out = R + "metrics/pointing_summary"
    if len(sys.argv) > 1 and not sys.argv[1].startswith("test"):
        prev = json.load(open(out + ".json")) if glob.glob(out + ".json") else []
        keep = [p for p in prev if p["file"].split("_")[0] not in sys.argv[1:]]
        res = keep + res
    json.dump(res, open(out + ".json", "w"), indent=1)
    keys = sorted({k for r in res for k in r}, key=lambda k: list(res[0]).index(k) if k in res[0] else 999)
    with open(out + ".csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=keys); wr.writeheader(); wr.writerows(res)
