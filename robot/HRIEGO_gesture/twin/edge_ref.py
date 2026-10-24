"""Edge discontinuity reference: pointing-arm commanded speed/jerk within +-0.1 s of prep start and recovery end vs the SONIC base alone (same joints, same window).
usage: edge_ref.py OUT.json  TRACE.csv:BASE_arms.npz [...]   (run from HRIEGO_gesture/twin, env laplace, PYTHONPATH=<teleop>/src)"""
import csv, json, sys
import numpy as np
from x2_ultra.robot.joints import ARM_JOINTS
TICK = 0.02; res = {}
for spec in sys.argv[2:]:
    f, base = spec.split(":"); m = json.load(open(f.replace(".csv", ".json"))); D = m["direction"]; side = "left" if D == "LEFT" else "right"
    rows = list(csv.DictReader(open(f))); g = lambda k: np.array([float(r[k]) for r in rows])
    ta = g("audio_s"); cmd = np.stack([g(f"cmd_{n}") for n in ARM_JOINTS], 1); pc = [i for i, n in enumerate(ARM_JOINTS) if n.startswith(side)]
    bc = np.load(base)["cmd"][100:]; B = np.stack([bc[min(int(round(max(t, 0) / TICK)), len(bc) - 1)] for t in ta])
    out = {}
    for tag, t in (("prep_start", m["prep_start_s"]), ("rec_end", m["recovery_end_s"])):
        w1 = (ta[1:] >= t - .1) & (ta[1:] <= t + .1); w3 = (ta[3:] >= t - .1) & (ta[3:] <= t + .1)
        sp = lambda X: float((np.abs(np.diff(X, axis=0)) / TICK)[w1][:, pc].max()); jk = lambda X: float((np.abs(np.diff(X, 3, axis=0)) / TICK ** 3)[w3][:, pc].max())
        out[tag] = {"cmd_speed": sp(cmd), "base_speed": sp(B), "cmd_jerk": jk(cmd), "base_jerk": jk(B),
                    "cmd_speed_step_max": float(np.abs(np.diff(cmd[:, pc], axis=0))[w1].max() / TICK)}
    res[f.split("/")[-1]] = out
json.dump(res, open(sys.argv[1], "w"), indent=1)
for k, v in res.items(): print(k, {t: {a: round(b, 2) for a, b in d.items()} for t, d in v.items()})
