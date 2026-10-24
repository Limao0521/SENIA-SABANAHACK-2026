#!/usr/bin/env python3
"""Safety/quality metrics for a motor_fix run. usage: analyze_fix.py RUN_DIR (needs motor.csv, motor.json, sender.json)."""
import csv, json, sys
from pathlib import Path
import numpy as np

RUN = Path(sys.argv[1])
TICK = 0.02
rows = list(csv.DictReader(open(RUN / "motor.csv")))
mj = json.loads((RUN / "motor.json").read_text())
sj = json.loads((RUN / "sender.json").read_text()) if (RUN / "sender.json").exists() else {"turns": []}
evs = mj["events"]

def col(name, rws=None):
    out = []
    for r in (rws if rws is not None else rows):
        v = r.get(name)
        if v not in (None, "", "None"):
            try: out.append(float(v))
            except ValueError: pass
    return np.array(out)

tilt = col("tilt_deg"); pel = col("pelvis_z"); jit = col("jitter_ms")
meas_cols = [c for c in rows[0] if c.startswith("meas_")]
cmd_cols = [c for c in rows[0] if c.startswith("cmd_")]

def segments(rws):
    """split rows into contiguous runs (no diff across gaps)."""
    out = []; cur = []
    for r in rws:
        if cur and abs(float(r["t"]) - float(cur[-1]["t"])) > 1.5 * TICK: out.append(cur); cur = []
        cur.append(r)
    if cur: out.append(cur)
    return out

def speeds(rws):
    """max per-joint measured speed (rad/s), computed within contiguous segments only."""
    sp = []
    for seg in segments(rws):
        if len(seg) < 2: continue
        M = np.array([[np.nan if r[c] in ("", "None", None) else float(r[c]) for c in meas_cols] for r in seg], float)
        sp.append(np.nanmax(np.abs(np.diff(M, axis=0)), axis=1) / TICK)
    return (np.concatenate(sp) if sp else np.array([])), None

def jerks(rws):
    jk = []
    for seg in segments(rws):
        C = np.array([[np.nan if r[c] in ("", "None", None) else float(r[c]) for c in cmd_cols] for r in seg], float)
        ok = ~np.isnan(C).any(axis=1); C = C[ok]
        if len(C) >= 3: jk.append(np.nanmax(np.abs(np.diff(C, 2, axis=0)), axis=1) / TICK**2)
    return np.concatenate(jk) if jk else np.array([])

res = {
    "rows": len(rows),
    "unsafe_flag": mj.get("unsafe", False),
    "fall_events": [e for e in evs if e["event"] == "fall_interlock"],
    "fatal_errors": [e.get("err") for e in evs if e["event"] == "fatal_error"],
    "falls": int(bool(mj.get("unsafe")) or np.any(pel < 0.60) or np.any(tilt > 25.0)),  # fall = interlock fired, pelvis collapse, or tilt beyond any vendor bow
    "tilt_gt_15_rows": int(np.sum(tilt > 15.0)),
    "tilt_max_deg": float(tilt.max()), "pelvis_min_m": float(pel.min()),
    "jitter_ms": {"p50": float(np.percentile(jit, 50)), "p95": float(np.percentile(jit, 95)),
                  "p999": float(np.percentile(jit, 99.9)), "max": float(jit.max()),
                  "pct_le_5ms": float(100 * np.mean(jit <= 5.0))},
    "wref_twist_max_rad": float(np.abs(col("wref_twist_waist")).max()) if len(col("wref_twist_waist")) else None,
    "wref_bendf_max_rad": float(np.abs(col("wref_bend_forward")).max()) if len(col("wref_bend_forward")) else None,
    "turns_done": sum(1 for t in sj.get("turns", []) if t.get("outcome") == "done"),
    "turns_refused": sum(1 for t in sj.get("turns", []) if t.get("outcome") == "refused"),
    "fell_driver": sj.get("fell", False),
}

# waist activity during preset phases (fix 2: must be hold/zero)
pre_rws = [r for r in rows if r["phase"] in ("preset_wait", "preset", "preset_settle", "urs_wait")]
if pre_rws:
    u_cols = [c for c in rows[0] if c.startswith("u_")]
    U = np.array([[np.nan if r[c] in ("", "None", None) else float(r[c]) for c in u_cols] for r in pre_rws], float)
    res["preset_phase_rows"] = len(pre_rws)
    res["preset_phase_u_nonzero_rows"] = int(np.nansum(np.nanmax(np.abs(U), axis=1) > 1e-9)) if not np.isnan(U).all() else 0
    W = np.array([[np.nan if r.get(f"wmeas_{f}") in ("", "None", None) else float(r[f"wmeas_{f}"]) for f in ("twist_waist", "bend_forward", "bend_sideways")] for r in pre_rws], float)
    res["preset_phase_wmeas_absmax_rad"] = float(np.nanmax(np.abs(W))) if len(W) else None

# per-turn + transitions
def turn_of(r):
    v = r.get("turn")
    if v in ("", "None", None): return None
    try: return int(float(v))
    except ValueError: return None
turn_ids = sorted({t for t in (turn_of(r) for r in rows) if t is not None and t >= 0})
disp = {int(e["turn"]) if "turn" in e else None: e for e in evs if e["event"] == "preset_dispatch"}
disp_by_t = {}
for e in evs:
    if e["event"] in ("preset_dispatch", "preset_gate_skip", "preset_abort", "preset_idle", "urs_active", "handoff", "handoff_done", "svc_timeout", "turn_refused"):
        disp_by_t.setdefault(e.get("audio_s") is not None, [])
skips = [e for e in evs if e["event"] in ("preset_gate_skip", "preset_abort")]
res["preset_gate_skips"] = len(skips)
res["svc_timeouts"] = [e for e in evs if e["event"] == "svc_timeout"]
hand = [e for e in evs if e["event"] == "handoff_done"]
res["handoff_svc_dur_s"] = {"max": max((e["dur_s"] for e in hand), default=None)}

# executed == contract (preset turns from sender)
contract = [t for t in sj.get("turns", []) if t.get("kind") == "preset"]
executed = 0
stroke_anchor = []
for t in contract:
    idx = t["idx"]; trows = [r for r in rows if turn_of(r) == idx]
    did_disp = any(e["event"] == "preset_dispatch" for e in evs if abs(e["t"] - (t["T_a0"] + t["anchor_start_s"])) < 60) if t.get("T_a0") else False
    # per-turn detection via events with audio clock is ambiguous; use CSV phase==preset presence
    played = any(r["phase"] == "preset" for r in trows)
    idle_seen = any(r["phase"] == "preset_settle" or r["phase"] == "urs_wait" or r["phase"] == "fade_in" for r in trows)
    if played and idle_seen:
        executed += 1
        # stroke: first t in preset phase where smoothed max joint speed > 0.5 rad/s
        prows = [r for r in trows if r["phase"] == "preset"]
        if len(prows) > 12:
            sp, _ = speeds(prows)
            sm = np.convolve(sp, np.ones(5) / 5, mode="same")
            # stroke = peak arm speed of the animation (skips the ~1.0 s preplay blend; c12 measured the dominant-joint peak)
            i0 = min(len(sm) - 1, int(1.15 / TICK))          # after preplay
            if i0 < len(sm) - 1:
                peak = float(sm[i0:].max()); thr = 0.35 * peak
                hits = np.where(sm[i0:] > thr)[0]
                i = i0 + int(hits[0]) if len(hits) else i0 + int(np.argmax(sm[i0:]))
                t_stroke = float(prows[min(i + 1, len(prows) - 1)]["t"])
                anchor_abs = t["T_a0"] + t["anchor_start_s"]
                stroke_anchor.append(1000 * (t_stroke - anchor_abs))
res["executed_eq_contract"] = f"{executed}/{len(contract)}"
if stroke_anchor:
    res["stroke_minus_anchor_ms"] = {"n": len(stroke_anchor), "median": float(np.median(stroke_anchor)),
                                     "min": float(np.min(stroke_anchor)), "max": float(np.max(stroke_anchor))}

# transition peak speed / jerk (fade_out + fade_in rows, all turns)
tr_rws = [r for r in rows if r["phase"] in ("fade_out", "fade_in")]
if len(tr_rws) > 4:
    sp, _ = speeds(tr_rws); jk = jerks(tr_rws)
    res["transition"] = {"max_measured_arm_speed_rad_s": float(sp.max()), "p95_speed": float(np.percentile(sp, 95)),
                         "max_cmd_jerk_rad_s3": float(jk.max()) if len(jk) else None}
fo = [r for r in rows if r["phase"] == "fade_out"]
if len(fo) > 4:
    spfo, _ = speeds(fo)
    res["transition"]["fade_out_max_speed_rad_s"] = float(spfo.max()) if len(spfo) else None

# per-turn tilt/pelvis
per_turn = {}
for idx in turn_ids:
    trows = [r for r in rows if turn_of(r) == idx]
    tt = col("tilt_deg", trows); pp = col("pelvis_z", trows)
    per_turn[idx] = {"rows": len(trows), "tilt_max": round(float(tt.max()), 2), "pelvis_min": round(float(pp.min()), 4)}
res["per_turn"] = per_turn

out = RUN / "metrics_fix.json"
out.write_text(json.dumps(res, indent=2, default=str))
brief = {k: v for k, v in res.items() if k not in ("per_turn", "fall_events", "svc_timeouts", "fatal_errors")}
print(json.dumps(brief, indent=1, default=str))
