"""c12 MC probes (inside x2-twin). One take: URS+SONIC arms -> DELETE URS mid-motion -> SetMcPresetMotion right after (retry until accepted) ->
(ADD source + McBodyPose waist/head test during the preset) -> preset idle -> SetMcAction URS + ADD -> fade back to SONIC. Logs 50 Hz rows + events.
usage: probe_c12.py MOTION AREA ARMS.npz OUT.csv [--del-at 2.5] [--no-sd-request] [--bp-test]"""
import argparse, json, time, sys
import numpy as np
from emote_lib import *
ap = argparse.ArgumentParser(); ap.add_argument("motion", type=int); ap.add_argument("area", type=int); ap.add_argument("arms"); ap.add_argument("out")
ap.add_argument("--del-at", type=float, default=2.5); ap.add_argument("--bp-test", action="store_true"); ap.add_argument("--sd-after", type=float, default=1.0)
ap.add_argument("--pre-s", type=float, default=0.05); ap.add_argument("--sd-first", action="store_true"); ap.add_argument("--no-preset", action="store_true")
a = ap.parse_args()
z = np.load(a.arms); cmdc = z["cmd"][100:]
def sonic_at(ts):
    f = np.clip(ts / TICK, 0, len(cmdc) - 1); i = int(f); j = min(i + 1, len(cmdc) - 1); r = f - i; return cmdc[i] * (1 - r) + cmdc[j] * r
R = Rig("probe_c12"); L = R.link; ev = R.ev
def tick(nxt, phase, **kw):
    time.sleep(max(0.0, nxt - time.monotonic() - 0.002))
    while time.monotonic() < nxt: pass
    return R.log(phase, **kw)
try:
    assert L.wait_stable(10.0); L.request_mode(URS); assert L.wait_mode(URS, 8.0)
    t_end = time.monotonic() + 2.0
    while L.node.count_subscribers("/mc/upper_body_command") == 0 and time.monotonic() < t_end: time.sleep(0.05)
    L.register_source(65); R.hand_init(); anchor = R.arms_now(); b0 = sonic_at(0.0)
    ramp_n = int(max(2.0, np.abs(b0 - anchor).max() / 0.5) / TICK); t0 = time.monotonic() + 0.2; k = 0
    for i in range(ramp_n):
        w = 0.5 - 0.5 * np.cos(np.pi * i / (ramp_n - 1)); c = anchor + w * (b0 - anchor); R.publish_arms(c); tick(t0 + k * TICK, "ramp", cmd=c); k += 1
    n_del = int(a.del_at / TICK); body = np.zeros((10 ** 5, 4))
    for i in range(n_del):
        c = sonic_at(i * TICK); R.publish_arms(c); tick(t0 + k * TICK, "sonic", cmd=c, sonic=c, audio_s=i * TICK); k += 1
    c_last = R.last_cmd.copy(); sp = np.abs(sonic_at(a.del_at) - sonic_at(a.del_at - 0.04)).max() / 0.04
    ev("delete_urs_call", last_cmd_speed_rad_s=float(sp)); td0 = time.monotonic(); err = L.delete_source(); td1 = time.monotonic()
    ev("delete_urs_done", err=err, dur_s=td1 - td0)
    if a.sd_first:
        ts0 = time.monotonic()
        try: L.request_mode(SD, busy_retry_s=8.0); ev("sd_first_request_ok", dur_s=time.monotonic() - ts0)
        except Exception as e: ev("sd_first_request_fail", err=str(e))
        ok = L.wait_stable(10.0); ev("sd_first_stable", ok=ok, dur_s=time.monotonic() - ts0, mode=L.observed(graph=False).mode); td1 = time.monotonic() - a.pre_s
    t_pre = td1 + a.pre_s; set_t = None; set_resp = []; sd_req_t = None; task = None; busy_seen = False; idle_t = None; bp_started = None; accepted_t = None
    nk = 0; t_del_ref = td1
    last_try = 0
    while True:
        now = time.monotonic(); rel = now - td1
        if (not a.no_preset) and task is None and rel >= a.pre_s and now - last_try >= 0.25:
            R.preset_set(a.motion, a.area); last_try = now; ev("set_sent", rel_s=rel, mode=L.observed(graph=False).mode)
        for rp in R.poll_replies():
            if rp["op"] == "set":
                set_resp.append({"rel_s": rp["t_resp"] - td1, "code": rp["code"], "state": rp["state"], "task_id": rp["task_id"]}); ev("set_reply", **set_resp[-1])
                if rp["code"] == 0 and task is None: task = rp["task_id"] or -1; accepted_t = now
        if (not a.no_preset) and task is None and sd_req_t is None and rel >= a.sd_after:
            sd_req_t = time.monotonic(); ev("sd_request_start", rel_s=rel)
            try: L.request_mode(SD, busy_retry_s=8.0); ev("sd_request_ok", dur_s=time.monotonic() - sd_req_t)
            except Exception as e: ev("sd_request_fail", err=str(e))
            ok = L.wait_stable(10.0); ev("sd_stable", ok=ok, rel_s=time.monotonic() - td1)
        obs = L.observed(graph=False)
        if accepted_t and bp_started is None and a.bp_test and now - accepted_t >= 0.6:
            try: L.register_source(65); ev("re_add_source_during_preset", ok=True)
            except Exception as e: ev("re_add_source_during_preset", ok=False, err=str(e))
            bp_started = time.monotonic(); R.reset_inflight()
        wref = None; u = None
        if bp_started:
            tb = time.monotonic() - bp_started
            ref = np.array([0.25, 0.0, 0.0, 0.4]) if 0.0 <= tb < 2.0 else np.zeros(4)
            q = R.wh_now(); u = np.zeros(4)
            for f in range(4):
                e = ref[f] - q[f]; u[f] = np.sign(e) * 0.3 if abs(e) > DEADBAND[f] else 0.0
            R.send_body_pose(u); wref = ref
        if obs.player_state not in (None, 0): busy_seen = True
        if busy_seen and obs.player_state == 0 and idle_t is None: idle_t = now; ev("preset_idle", rel_s=rel)
        R.log("post_delete", cmd=None, wref=wref, u=u, extra={"task": task})
        nk += 1
        if (idle_t and now - idle_t > 0.5) or rel > 14 or (a.no_preset and rel > 2.5): break
        time.sleep(max(0.0, td1 + nk * TICK - time.monotonic()))
    if bp_started: R.send_body_pose(np.zeros(4))
    # ---- resume: SD -> URS, ADD, fade from the measured pose back to SONIC
    t_a = time.monotonic(); ev("resume_start", mode=L.observed(graph=False).mode, fsm=L.observed(graph=False).fsm)
    L.wait_stable(10.0); ev("resume_stable", dt=time.monotonic() - t_a)
    L.request_mode(URS); ev("resume_urs_req_done", dt=time.monotonic() - t_a); ok = L.wait_mode(URS, 8.0); ev("resume_urs_active", ok=ok, dt=time.monotonic() - t_a)
    L.register_source(65); ev("resume_added", dt=time.monotonic() - t_a)
    R.hand_init(); p0 = R.arms_now(); t1 = time.monotonic() + 0.1; ev("resume_p0", p0=p0.tolist())
    for i in range(int(2.0 / TICK)):
        w = float(minjerk(i * TICK / 0.8)); s = sonic_at(i * TICK + 1.0); c = p0 + w * (s - p0); R.publish_arms(c); tick(t1 + i * TICK, "resume", cmd=c, sonic=s)
finally:
    for _ in range(25): R.send_body_pose(np.zeros(4)); time.sleep(TICK)
    err = L.delete_source()
    try: L.request_mode(SD, busy_retry_s=12.0)
    except Exception as e: print("SD request failed", e)
    R.dump(a.out); json.dump({"events": R.events, "args": vars(a)}, open(a.out.replace(".csv", ".json"), "w"), default=str)
    print("done", len(R.rows), "rows; delete err", err, "final mode", L.observed(graph=False).mode); R.close()
