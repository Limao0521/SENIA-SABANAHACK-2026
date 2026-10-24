"""Analyze realnames logs: leg reactions (tilt, pelvis_z) vs upper-body command features."""
import csv, glob, json, sys
import numpy as np
from pathlib import Path

rows_all = {}
for p in sorted(glob.glob("/home/luis/GitHub/x2_ultra-reto2/out_reto2/realnames/*_log.csv")):
    name = Path(p).stem.replace("_log", "")
    with open(p) as f:
        rows = [r for r in csv.DictReader(f)]
    rows_all[name] = rows

def f(r, k, d=np.nan):
    try:
        v = float(r[k]); return v
    except (ValueError, TypeError):
        return d

print(f"{'persona':11s} {'n':>5s} {'stand':>5s} | tilt: base mean p95 max | pelz p95dev | corr(arm_spd) corr(arm_jerk) corr(waist) | phases worst-tilt")
for name, rows in rows_all.items():
    t = np.array([f(r, 't') for r in rows])
    tilt = np.array([f(r, 'tilt_deg') for r in rows])
    pelz = np.array([f(r, 'pelvis_z') for r in rows])
    cmd = np.array([f(r, 'cmd_vel_max', 0) for r in rows])
    asp = np.array([f(r, 'arm_speed_max', 0) for r in rows])
    ajk = np.array([f(r, 'arm_jerk_max', 0) for r in rows])
    wd = np.array([f(r, 'waist_delta_max', 0) for r in rows])
    ga = np.array([f(r, 'gesture_active', 0) for r in rows])
    turn = [r['turn_id'] for r in rows]
    ok = ~np.isnan(tilt) & ~np.isnan(t)
    t, tilt, pelz, cmd, asp, ajk, wd, ga = t[ok], tilt[ok], pelz[ok], cmd[ok], asp[ok], ajk[ok], wd[ok], ga[ok]
    turn = [turn[i] for i, o in enumerate(ok) if o]
    stand = cmd < 0.05
    sl = stand & ~np.isnan(asp)
    if sl.sum() < 50:
        continue
    def corr(a, b):
        m = sl & ~np.isnan(a) & ~np.isnan(b)
        if m.sum() < 30 or np.std(a[m]) < 1e-9: return np.nan
        return float(np.corrcoef(a[m], b[m])[0, 1])
    # per-turn worst tilt while standing
    turns = sorted(set(x for x, s in zip(turn, stand) if s and x))
    worst = []
    for tn in turns:
        m = np.array([x == tn for x in turn]) & stand
        if m.sum() > 5:
            worst.append((float(np.percentile(tilt[m], 95)), tn))
    worst.sort(reverse=True)
    wz = worst[:3]
    print(f"{name:11s} {len(t):5d} {int(sl.sum()):5d} | {np.mean(tilt[sl]):.2f} {np.percentile(tilt[sl],95):.2f} {np.max(tilt[sl]):.2f} | "
          f"{np.percentile(np.abs(pelz[sl]-np.median(pelz[sl])),95)*1000:.1f}mm | "
          f"{corr(tilt,asp):+.2f} {corr(tilt,ajk):+.2f} {corr(tilt,wd):+.2f} | " +
          ", ".join(f"{tt}:{v:.1f}deg" for v, tt in wz))

# lagged correlation: does tilt rise AFTER arm-speed spikes? (Luis)
print("\n--- lagged corr tilt(t+k*0.1s) vs arm_speed, all personas pooled, standing ---")
pool_a, pool_b, pool_w = [], [], []
for name, rows in rows_all.items():
    tilt = np.array([f(r, 'tilt_deg') for r in rows])
    cmd = np.array([f(r, 'cmd_vel_max', 0) for r in rows])
    asp = np.array([f(r, 'arm_speed_max', 0) for r in rows])
    wd = np.array([f(r, 'waist_delta_max', 0) for r in rows])
    sl = (cmd < 0.05) & ~np.isnan(tilt) & ~np.isnan(asp)
    pool_a.append(asp[sl]); pool_b.append(tilt[sl]); pool_w.append(wd[sl])
A, B, W = np.concatenate(pool_a), np.concatenate(pool_b), np.concatenate(pool_w)
for k in (0, 2, 5, 10, 20):
    if len(A) > k + 100:
        c1 = np.corrcoef(A[:len(A)-k], B[k:])[0,1]
        c2 = np.corrcoef(W[:len(W)-k], B[k:])[0,1]
        print(f"lag {k*0.02:.2f}s: corr(arm_speed -> tilt) {c1:+.3f}   corr(waist_delta -> tilt) {c2:+.3f}")

# tilt spikes: distribution by gesture_active and by arm jerk decile
print("\n--- tilt p95 by arm_jerk decile (standing, pooled) ---")
jk = []
for name, rows in rows_all.items():
    tilt = np.array([f(r, 'tilt_deg') for r in rows]); cmd = np.array([f(r, 'cmd_vel_max', 0) for r in rows])
    a = np.array([f(r, 'arm_jerk_max', 0) for r in rows]); sl = (cmd < 0.05) & ~np.isnan(tilt) & ~np.isnan(a)
    jk.append((a[sl], tilt[sl]))
JA = np.concatenate([x[0] for x in jk]); JT = np.concatenate([x[1] for x in jk])
qs = np.quantile(JA, np.linspace(0, 1, 11))
for i in range(10):
    m = (JA >= qs[i]) & (JA <= qs[i+1])
    print(f"jerk decile {i+1:2d} [{qs[i]:7.1f},{qs[i+1]:7.1f}]: tilt p50 {np.percentile(JT[m],50):.2f} p95 {np.percentile(JT[m],95):.2f} max {JT[m].max():.2f}")
