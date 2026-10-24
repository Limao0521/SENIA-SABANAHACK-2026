#!/usr/bin/env python3
"""Aggregate the server runs: latency / memory / quality tables (vs offline_ref = production chain on the same StreamTalk output)."""
import json, sys
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent; OUT = HERE / "out"
WIN_S = 52 / 30.0
SIDES, PARTS = ("left", "right"), ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll")


def jerk(q): return float(np.abs(np.diff(q, 3, axis=0)).mean() * 50 ** 3)


def load_ref(c):
    f = OUT / "offline_ref" / c / "arms.npz"
    if not f.exists(): return None
    z = np.load(f); return z["sonic_actual"], z["waist_head"], z["tilt"], z["pelvis_z"]


def quality(tag, rows):
    q = dict(arm_rmse=[], waist_rmse=[], head_rmse=[], jerk_ratio=[], bound_ratio=[], tilt_max=[], pelvis_min=[], fell=0, vel_peak=[], rows={})
    for r in rows:
        c = r["name"]; d = np.load(OUT / tag / f"{c}.npz"); ref = load_ref(c); pn = r["prefix_n"]
        a, wh = d["arms"], d["wh"]
        q["tilt_max"].append(float(d["tilt"].max())); q["pelvis_min"].append(float(d["pelvis_z"].min())); q["fell"] += int(r["fell"]); q["vel_peak"].append(float(np.abs(np.diff(a, axis=0)).max() * 50))
        # window-boundary discontinuity: 3rd-difference energy around the ST window seams (motion frame 52*i @30 fps) vs elsewhere
        j3 = np.abs(np.diff(a[pn:], 3, axis=0)).mean(1); seams = [int(round(52 * i / 30 * 50)) for i in range(1, 20) if 52 * i / 30 * 50 < len(j3) - 4]
        mask = np.zeros(len(j3), bool)
        for s in seams: mask[max(0, s - 3):s + 3] = True
        if seams and (~mask).any(): q["bound_ratio"].append(float(j3[mask].mean() / j3[~mask].mean()))
        if ref is not None:
            ra, rw = ref[0], ref[1]; L = min(len(a) - pn, len(ra) - 100)
            da = a[pn:pn + L] - ra[100:100 + L]; dw = wh[pn:pn + L, :3] - rw[100:100 + L, :3]; dh = wh[pn:pn + L, 3:] - rw[100:100 + L, 3:]
            q["arm_rmse"].append(float(np.sqrt((da ** 2).mean()))); q["waist_rmse"].append(float(np.sqrt((dw ** 2).mean()))); q["head_rmse"].append(float(np.sqrt((dh ** 2).mean())))
            q["jerk_ratio"].append(jerk(a[pn:pn + L]) / jerk(ra[100:100 + L]))
            q["rows"][c] = dict(arm_rmse=q["arm_rmse"][-1], waist_rmse=q["waist_rmse"][-1], jerk=jerk(a[pn:pn + L]), jerk_ref=jerk(ra[100:100 + L]))
    return q


def main():
    tags = sys.argv[1:] or sorted(p.parent.name for p in OUT.glob("*/summary.json"))
    table = {}
    for tag in tags:
        s = json.load(open(OUT / tag / "summary.json")); rows = s["rows"]
        win = [w for r in rows for w in r["windows"]]
        comp = np.array([w["gen_s"] + w["gmr_s"] + w.get("sonic_s", 0) for w in win]); full = [w for w in win if w["frames"] == 52]
        comp_full = np.array([w["gen_s"] + w["gmr_s"] + w.get("sonic_s", 0) for w in full])
        st = np.array([w["gen_s"] for w in full]); gm = np.array([w["gmr_s"] for w in full]); so = np.array([w.get("sonic_s", 0) for w in full])
        g = lambda k: np.array([r[k] for r in rows])
        t = dict(variant=tag, clips=len(rows), first_cmd_mean=g("first_cmd_s").mean(), first_cmd_max=g("first_cmd_s").max(), lead_D_mean=g("lead_D_s").mean(), lead_D_max=g("lead_D_s").max(),
                 audio_start_mean=g("audio_start_s").mean(), audio_start_max=g("audio_start_s").max(), first_window_ready_mean=g("t_first_window_ready").mean(),
                 rtf_mean=g("rtf").mean(), rtf_max=g("rtf").max(), lag_last_mean=g("lag_last_s").mean(),
                 win_compute_sum_mean=comp_full.mean(), win_compute_sum_max=comp_full.max(), win_over_real=float((comp_full > WIN_S).mean()),
                 st_win_s=st.mean(), gmr_win_s=gm.mean(), sonic_win_s=so.mean(),
                 pss_load=s["mem_load"]["after_sonic"]["pss"], pss_steady=float(np.mean([r["pss_mb"] for r in rows[-3:]])), pss_peak_run=s["pss_peak_run_mb"], vmhwm=s["vmhwm_mb"],
                 vram_mb=rows[-1]["vram_mb"], torch_peak_mb=max(r["torch_peak_mb"] for r in rows), load_total_s=s["load_s"]["total"])
        qq = quality(tag, rows)
        t.update(arm_rmse=float(np.mean(qq["arm_rmse"])) if qq["arm_rmse"] else None, arm_rmse_max=float(np.max(qq["arm_rmse"])) if qq["arm_rmse"] else None,
                 waist_rmse=float(np.mean(qq["waist_rmse"])) if qq["waist_rmse"] else None, head_rmse=float(np.mean(qq["head_rmse"])) if qq["head_rmse"] else None,
                 jerk_ratio=float(np.mean(qq["jerk_ratio"])) if qq["jerk_ratio"] else None, bound_ratio=float(np.mean(qq["bound_ratio"])) if qq["bound_ratio"] else None,
                 tilt_max=float(np.max(qq["tilt_max"])), tilt_mean_of_max=float(np.mean(qq["tilt_max"])), pelvis_min=float(np.min(qq["pelvis_min"])), fell=qq["fell"], vel_peak=float(np.max(qq["vel_peak"])))
        table[tag] = t; table[tag]["per_clip_quality"] = qq["rows"]
    json.dump(table, open(HERE / "metrics.json", "w"), indent=1)
    import csv
    keys = [k for k in next(iter(table.values())) if k != "per_clip_quality"]
    with open(HERE / "metrics.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(keys)
        for t in table.values(): w.writerow([round(t[k], 4) if isinstance(t[k], float) else t[k] for k in keys])
    for t in table.values(): print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in t.items() if k != "per_clip_quality"})


if __name__ == "__main__":
    main()
