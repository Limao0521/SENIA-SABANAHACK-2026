import json, sys
import numpy as np
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "twin"))
from c12_io import load
from pathlib import Path
O = Path(__file__).resolve().parent / "out"; tag = sys.argv[1] if len(sys.argv) > 1 else "B1"
c = load(O / tag / f"{tag.lower()}.csv"); M = json.load(open(O / tag / f"{tag.lower()}.json")); BR = json.load(open(O / tag / "brain.json"))
J = [k[4:] for k in c if k.startswith("cmd_")]
CMD = np.stack([c["cmd_" + j] for j in J], 1); MEAS = np.stack([c["meas_" + j] for j in J], 1); t = c["t"]; ph = c["phase"]; turn = np.nan_to_num(c["turn"], nan=-2)
res = {}
# ---- timing
dt = np.diff(t[ph != "startup"]); idx = np.where(ph != "startup")[0]
jit = c["jitter_ms"][idx]
res["motor_loop"] = dict(n=int(len(idx)), wake_jitter_ms=dict(mean=float(np.nanmean(jit)), p99=float(np.nanpercentile(jit, 99)), max=float(np.nanmax(jit))),
                         period_ms=dict(mean=float(dt.mean() * 1000), std=float(dt.std() * 1000), max_dev=float(np.abs(dt - 0.02).max() * 1000), p99_dev=float(np.percentile(np.abs(dt - 0.02), 99) * 1000), n_over_5ms=int((np.abs(dt - 0.02) > 0.005).sum()), where_over_5ms=[(str(ph[idx[1:]][i]), int(turn[idx[1:]][i])) for i in np.where(np.abs(dt - 0.02) > 0.005)[0]]),
                         pub_ms=dict(mean=float(np.nanmean(c["pub_ms"][idx])), max=float(np.nanmax(c["pub_ms"][idx]))), underrun_ticks=int(np.nansum(c["underrun"])))
# ---- safety
pel, tilt = c["pelvis_z"], c["tilt_deg"]; inpre = np.isin(ph, ["preset_wait", "preset", "urs_wait", "fade_in", "fade_out"]) & (turn >= 0)
kinds = {int(r["idx"]): r["kind"] for r in BR["turns"]}; pset = np.array([kinds.get(int(x)) == "preset" for x in turn])
good = np.isfinite(pel) & (ph != "startup")
res["safety"] = dict(pelvis_min=float(np.nanmin(pel[good])), tilt_max_all=float(np.nanmax(tilt[good])), tilt_max_idle=float(np.nanmax(tilt[good & (ph == "idle")])),
                     tilt_max_sonic_turns=float(np.nanmax(tilt[good & ~pset & (turn >= 0)])), tilt_max_preset_turns=float(np.nanmax(tilt[good & pset])),
                     tilt_max_in_preset_play=float(np.nanmax(tilt[good & (ph == "preset")])), falls_brain=int(sum(r["fell"] for r in BR["turns"])), pelvis_min_sonic_turns=float(np.nanmin(pel[good & ~pset & (turn >= 0)])))
# ---- dynamics helpers (50 Hz rows)
def dyn(x):
    v = np.diff(x, axis=0) / 0.02; a = np.diff(v, axis=0) / 0.02; j = np.diff(a, axis=0) / 0.02
    return float(np.nanmax(np.abs(v))), float(np.nanmax(np.abs(j))), float(np.nanpercentile(np.abs(j), 99))
base_mask = np.isin(ph, ["sonic", "pre", "sonic2"]) & (turn >= 0)
# base = SONIC steady segments (after entry blend of 0.5 s), per contiguous turn
bv = []; bj = []; bp99 = []; mv = []; mjk = []; mjp = []
for tn in sorted(set(turn[turn >= 0].astype(int))):
    m = base_mask & (turn == tn); ii = np.where(m)[0]; ii = ii[25:]
    if len(ii) > 20:
        v, j, p = dyn(CMD[ii]); bv.append(v); bj.append(j); bp99.append(p); dm = dyn(MEAS[ii]); mv.append(dm[0]); mjk.append(dm[1]); mjp.append(dm[2])
res["sonic_base"] = dict(cmd_peak_speed=float(np.max(bv)), cmd_peak_jerk=float(np.max(bj)), cmd_jerk_p99=float(np.max(bp99)), meas_peak_speed=float(np.max(mv)), meas_peak_jerk=float(np.max(mjk)), meas_jerk_p99=float(np.max(mjp)), cmd_peak_speed_per_turn_mean=float(np.mean(bv)))
# base from the brain's own raw SONIC (pre-limiter)
SR = np.stack([c["sonic_" + j] for j in J], 1); sv = []; sj = []
for tn in sorted(set(turn[turn >= 0].astype(int))):
    ii = np.where(base_mask & (turn == tn))[0][25:]
    if len(ii) > 20 and np.isfinite(SR[ii]).all(): a, b_, _ = dyn(SR[ii]); sv.append(a); sj.append(b_)
res["sonic_base"].update(raw_peak_speed=float(np.max(sv)), raw_peak_jerk=float(np.max(sj)))
# ---- transitions: first row index where phase changes; window -0.5..+1.0 s
def transitions():
    out = []
    for i in range(1, len(ph)):
        if ph[i] == ph[i - 1]: continue
        a, b = ph[i - 1], ph[i]
        name = None
        if a == "idle" and b in ("sonic", "pre"): name = "idle->SONIC"
        elif a == "exit" and b == "idle": name = "SONIC->idle (exit ramp end)"
        elif a == "pre" and b == "fade_out": name = "SONIC->preset (fade-out start)"
        elif a == "fade_out" and b == "preset_wait": name = "handoff (DELETE+SD)"
        elif a == "preset" and b == "urs_wait": name = "preset->URS request"
        elif a == "urs_wait" and b == "fade_in": name = "URS re-entry (fade-in start)"
        elif a == "fade_in" and b == "sonic2": name = "fade-in end -> SONIC"
        elif a in ("sonic", "sonic2", "pre", "exit_pending") and b == "exit": name = "SONIC->exit ramp start"
        if name: out.append((name, i))
    return out
rows = {}
for name, i in transitions():
    sl = slice(max(0, i - 25), min(len(ph), i + 50)); v, j, p = dyn(CMD[sl]); mv_, mj_, mp_ = dyn(MEAS[sl])
    rows.setdefault(name, []).append(dict(cmd_peak_speed=v, cmd_peak_jerk=j, cmd_jerk_p99=p, meas_peak_speed=mv_, meas_peak_jerk=mj_, meas_jerk_p99=mp_,
                                          max_tick_step_cmd=float(np.nanmax(np.abs(np.diff(CMD[sl], axis=0)))), tilt=float(np.nanmax(tilt[sl])), pelvis=float(np.nanmin(pel[sl]))))
res["transitions"] = {k: dict(n=len(v), **{m: float(np.nanmax([x[m] for x in v])) for m in v[0]}) for k, v in rows.items()}
# ---- idle behaviour
idle = (ph == "idle") & (turn < 0); ii = np.where(idle)[0]
res["idle"] = dict(ticks=int(idle.sum()), max_cmd_step=float(np.nanmax(np.abs(np.diff(CMD[ii], axis=0)))) if len(ii) > 2 else None, cmd_dev_from_ready_max=float(np.nanmax(np.abs(CMD[ii] - np.array([0.2, 0.2, 0, -0.6, 0, 0, 0, 0.2, -0.2, 0, -0.6, 0, 0, 0])))),
                   meas_dev_from_ready_max_after_1s=float(np.nanmax(np.abs(MEAS[ii][50:] - np.array([0.2, 0.2, 0, -0.6, 0, 0, 0, 0.2, -0.2, 0, -0.6, 0, 0, 0])))))
# ---- limiter
lim = c["limited"]; res["limiter"] = dict(vmax=M["vmax"], amax=M["amax"], frac_ticks_limited_sonic=float(np.nanmean(lim[base_mask])), frac_ticks_limited_all=float(np.nanmean(lim[ph != "startup"])),
                                          max_abs_raw_minus_out_sonic=float(np.nanmax(np.abs(SR[base_mask] - CMD[base_mask]))), rmse_raw_minus_out_sonic=float(np.sqrt(np.nanmean((SR[base_mask] - CMD[base_mask]) ** 2))), lim_hits_total=M["lim_hits"])
# ---- per turn: latency, first motion
ev = {}
for e in M["events"]:
    if e["event"] == "turn_start": ev.setdefault(e["turn"], {}).update(motor_t_start=e["t"], late_s=e["late_s"])
    if e["event"] == "turn_slack": ev.setdefault(e["turn"], {}).update(slack_min_s=e["slack_min_s"], n_late=e["n_late"], lag_mean_ms=1000 * float(np.mean(e["lags"])), lag_max_ms=1000 * float(np.max(e["lags"])), n_ticks=e["n_ticks"])
READY = np.array([0.2, 0.2, 0, -0.6, 0, 0, 0, 0.2, -0.2, 0, -0.6, 0, 0, 0]); pt = []
for r in BR["turns"]:
    i = r["idx"]; m = (turn == i) & (c["t"] >= r["t_first_mono"]); ii = np.where(m)[0]
    e = dict(name=r["name"], kind=r["kind"], first_cmd_s=r["first_cmd_s"], rtf=r["rtf"], tilt_max_sim=r["tilt_max"], pelvis_min_sim=r["pelvis_min"], audio_start_after_request_s=r["time_to_audio_start_s"], lead_s=r["lead_s"])
    if len(ii):
        dv = np.abs(CMD[ii] - READY).max(1); k1 = np.argmax(dv > 0.01) if (dv > 0.01).any() else None
        mdev = np.abs(MEAS[ii] - MEAS[ii[0]]).max(1); k2 = np.argmax(mdev > 0.03) if (mdev > 0.03).any() else None
        e["first_motion_cmd_after_request_s"] = None if k1 is None else float(c["t"][ii[k1]] - r["t_req_mono"])
        e["first_visible_motion_after_request_s"] = None if k2 is None else float(c["t"][ii[k2]] - r["t_req_mono"])
        e["first_visible_motion_vs_audio_s"] = None if k2 is None else float(c["t"][ii[k2]] - r["T_a0"])
    e.update(ev.get(i, {})); pt.append(e)
res["turns"] = pt
ready_ev = [e for e in M["events"] if e["event"] in ("handoff", "urs_active", "th_search", "preset_set_reply")]
res["preset_events"] = [{k: v for k, v in e.items() if k != "t"} for e in ready_ev]
res["brain"] = dict(split_bench=BR.get("split_bench"), load=BR["load"])
json.dump(res, open(O.parent / f"metrics_{tag}.json", "w"), indent=1, default=float)
print(json.dumps({k: v for k, v in res.items() if k not in ("turns",)}, indent=1, default=float)[:7000])
for e in pt: print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in e.items()})
