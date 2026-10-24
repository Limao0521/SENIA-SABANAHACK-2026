#!/usr/bin/env python3
"""O3 MOTOR (runs inside x2-twin; ROS/AimDK overlays sourced; numpy only, no CUDA).
Threads: (1) receiver  - AF_UNIX server on a bind-mounted socket; parses tick chunks / JSON controls from the brain into per-turn buffers (stamps arrival).
         (2) controller - ONE 50 Hz loop (busy-wait last 2 ms) that owns ROS: publishes URS arms (source 65) + McBodyPose waist/head (domain 77, c07/c12 Smith-predictor
             sign control), SetMcPresetMotion via the c10 helper subprocess (inside emote_lib.Rig), the state machine and the per-tick logger.
Between turns the controller keeps URS and streams READY (arms) / 0 (waist, head): no mode change between SONIC turns.
Per-joint velocity AND acceleration limiter on every arm command (V_MAX / A_MAX).  Preset turns follow the c12 V2 path (min-jerk fade to pose D, handoff at a local
minimum of SONIC arm speed, preset in STAND_DEFAULT, URS re-entry, fade-in from the measured pose at the CURRENT audio time).
usage: motor.py OUT.csv [--vmax 2.0] [--amax 40]"""
import argparse, json, os, socket, sys, threading, time
import numpy as np
from emote_lib import *          # Rig, URS, SD, TICK, READY, LO, HI, minjerk, ARM_JOINTS
from o3common import *
from x2_ultra.real.control import ACTION_RUNNING

ap = argparse.ArgumentParser(); ap.add_argument("out"); ap.add_argument("--vmax", type=float, default=2.0); ap.add_argument("--amax", type=float, default=40.0)
ap.add_argument("--entry-s", type=float, default=0.3); ap.add_argument("--max-run-s", type=float, default=900.0)
A = ap.parse_args()
D = np.array([0.4, 0, 0, -1.2, 0, 0, 0] * 2, float)
BUFN = 4000


class Limiter:
    """Velocity + acceleration limited follower: v_des = clip(e/dt, +-vmax, +-sqrt(2*0.9*amax*|e|)); v += clip(v_des - v, +-amax*dt)."""
    def __init__(s, vmax, amax): s.vmax, s.amax = vmax, amax; s.p = None; s.v = np.zeros(14); s.nlim = 0
    def reset(s, q): s.p = np.array(q, float); s.v = np.zeros(14)
    def step(s, tgt):
        e = tgt - s.p; vd = np.sign(e) * np.minimum(np.minimum(np.abs(e) / TICK, s.vmax), np.sqrt(2 * 0.9 * s.amax * np.abs(e)))
        dv = np.clip(vd - s.v, -s.amax * TICK, s.amax * TICK); s.v = s.v + dv; s.p = s.p + s.v * TICK
        hit = bool(np.any(np.abs(vd - s.v) > 1e-9) or np.any(np.abs(e / TICK) > s.vmax + 1e-9)); s.nlim += hit
        return s.p.copy(), hit


class TurnBuf:
    def __init__(s, meta):
        s.meta = meta; s.arr = np.zeros((BUFN, 19), np.float32); s.n = 0; s.recv_t = np.full(BUFN, np.nan); s.n_ticks = None; s.lags = []; s.nchunks = 0
    def at(s, f):
        """linear interpolation at fractional tick f; (vector, underrun)"""
        f = max(f, 0.0); n = s.n
        if n == 0: return None, True
        if f > n - 1: return s.arr[n - 1].astype(float), (s.n_ticks is None or f < s.n_ticks - 1)
        i = int(f); j = min(i + 1, n - 1); r = f - i; return s.arr[i] * (1 - r) + s.arr[j] * r, False


turns = {}; pending = []; ctrl_events = []; lock = threading.Lock(); quit_flag = [False]; conn_ok = threading.Event(); client = [None]


def receiver():
    if os.path.exists(SOCK): os.unlink(SOCK)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(SOCK); os.chmod(SOCK, 0o777); srv.listen(1)
    while not quit_flag[0]:
        c, _ = srv.accept(); client[0] = c; conn_ok.set()
        try:
            while True:
                t, p = recv_frame(c); tr = time.monotonic()
                if t == 1:
                    with lock:
                        if p["op"] == "turn": turns[p["turn"]] = TurnBuf(p); pending.append(p["turn"]); p["t_recv"] = tr
                        elif p["op"] == "turn_end": turns[p["turn"]].n_ticks = p["n_ticks"]
                        elif p["op"] == "quit": quit_flag[0] = True
                else:
                    turn, k0, a, ts = p; b = turns[turn]
                    b.arr[k0:k0 + len(a)] = a; b.recv_t[k0:k0 + len(a)] = tr; b.lags.append(tr - ts); b.nchunks += 1; b.n = max(b.n, k0 + len(a))
        except (ConnectionError, OSError): pass
        if quit_flag[0]: break


threading.Thread(target=receiver, daemon=True).start()
R = Rig("o3_motor"); L = R.link; ev = R.ev
lim = Limiter(A.vmax, A.amax)
tick_log = []        # (sched, actual_before_publish, publish_done)


def tick_wait(nxt):
    time.sleep(max(0.0, nxt - time.monotonic() - 0.002))
    while time.monotonic() < nxt: pass


def send_evt(**kw):
    try:
        if client[0] is not None: send_json(client[0], kw)
    except Exception: pass


try:
    assert L.wait_stable(10.0), "MC not STABLE"; assert L.observed(graph=False).mode == SD, "mode"
    L.request_mode(URS); assert L.wait_mode(URS, 8.0), "URS not reached"
    t_end = time.monotonic() + 2.0
    while L.node.count_subscribers("/mc/upper_body_command") == 0 and time.monotonic() < t_end: time.sleep(0.05)
    L.register_source(65); R.hand_init(); anchor = R.arms_now()
    # ---- startup: min-jerk (>= 2 s, <= 0.5 rad/s) from the measured pose to READY; then the continuous READY stream begins
    rn = int(max(2.0, np.abs(READY - anchor).max() / 0.5) / TICK); t_s = time.monotonic() + 0.1
    for i in range(rn):
        c = anchor + minjerk(i / (rn - 1)) * (READY - anchor); tick_wait(t_s + i * TICK); R.publish_arms(c); R.log("startup", cmd=c)
    lim.reset(R.last_cmd)
    ev("ready_stream_start"); send_evt(op="ready"); t_loop0 = time.monotonic() + 0.05
    k = 0; phase = "idle"; turn = None; T0 = None; out_prev = READY.copy(); wh_prev = np.zeros(4)
    prefix_n = 0; t_enter = None; enter_from = READY.copy(); enter_wh = np.zeros(4)
    t_run0 = time.monotonic()
    # preset (V2) state
    P = {}
    while True:
        nxt = t_loop0 + k * TICK; tick_wait(nxt); now = time.monotonic(); jit = now - nxt
        obs = L.observed(graph=False)
        if obs.mc_age_s is None or obs.mc_age_s > 0.5: raise RuntimeError(f"MC state stale k={k}")
        if now - t_run0 > A.max_run_s: raise RuntimeError("max run time")
        # ---- activate a pending turn at its start time
        if turn is None and phase == "idle":
            with lock: nxt_turn = pending[0] if pending else None
            if nxt_turn is not None:
                tb = turns[nxt_turn]; m = tb.meta; prefix_n = m["prefix_n"]; T0 = m["T_a0"]
                pre = m.get("preset"); t_start = T0 + start_offset(prefix_n, pre)
                if now >= t_start:
                    pending.pop(0); turn = nxt_turn; tbuf = tb; phase = "sonic"; t_enter = now; enter_from = out_prev.copy(); enter_wh = wh_prev.copy()
                    ev("turn_start", turn=turn, kind=m["kind"], late_s=now - t_start, t_recv_ctrl=m["t_recv"], T_a0=T0, t_first_tick_recv=float(tbuf.recv_t[0]) if tb.n else None)
                    P = {}
                    if pre is not None:
                        pol, D_a = preset_policy(pre); P = dict(pol=pol, D_a=D_a, t_h=D_a, th_done=False, t_f0=D_a - L_OUT, set_resp=None, seen_busy=False, t_idle=None, idle_deb=0,
                                                              reg_src_t=None, t_sd=None, t_disp=None, p0=None, t_in0=None, resume=True)
        au = (now - T0) if T0 is not None and turn is not None else now
        # ---- references for this tick
        cmd = None; wref = None; sonic_raw = None; underrun = False; wsrc = None; we_e = 1.0
        def sonic_vec(ts):
            v, ur = tbuf.at(prefix_n + ts / TICK); return v, ur
        if turn is not None:
            sv, underrun = sonic_vec(au); sonic_arm, sonic_wh = sv[:14], sv[14:18]
            n_ticks = tbuf.n_ticks
            if phase in ("sonic", "pre", "fade_out", "fade_in", "sonic2", "preset_wait", "preset", "urs_wait") and P:
                pass
            # ---- preset V2 state machine (only when the turn has a preset)
            if P:
                pre = tbuf.meta["preset"]; Dp = P["D_a"]
                if phase == "sonic": phase = "pre"
                # local minimum of SONIC arm speed within +-0.3 s of the planned dispatch (needs ticks up to Dp+0.32 s)
                if phase == "pre" and not P["th_done"] and au >= Dp - 1.2:
                    need = prefix_n + (Dp + 0.32) / TICK + 2
                    if tbuf.n >= need or au >= Dp - 0.95:
                        if tbuf.n >= need:
                            ts_ = np.arange(Dp - 0.3, Dp + 0.3 + 1e-9, TICK)
                            vv = np.array([np.abs(sonic_vec(t + 0.02)[0][:14] - sonic_vec(t - 0.02)[0][:14]).max() / 0.04 for t in ts_]); vv = np.convolve(vv, np.ones(5) / 5, mode="same")
                            vv[:2] = vv[-2:] = np.inf; P["t_h"] = float(ts_[int(np.argmin(vv))]); ev("th_search", ok=True, t_h=P["t_h"], D_a=Dp)
                        else: ev("th_search", ok=False, t_h=Dp, D_a=Dp)
                        P["th_done"] = True; P["t_f0"] = P["t_h"] - L_OUT
                if phase == "pre" and P["th_done"] and au >= P["t_f0"]: phase = "fade_out"
                if phase == "fade_out" and au >= P["t_h"]:
                    c_last = R.last_cmd.copy(); t_del = time.monotonic(); err = L.delete_source(); t_dd = time.monotonic()
                    L.request_mode(SD, busy_retry_s=8.0); P["t_sd"] = time.monotonic()
                    ev("handoff", audio_s=au, delete_err=err, delete_dur_s=t_dd - t_del, sd_dur_s=P["t_sd"] - t_dd, last_cmd_dev_from_D=float(np.abs(c_last - D).max())); phase = "preset_wait"
                if phase == "preset_wait" and au >= Dp:
                    R.preset_set(pre["motion"], pre["area"]); P["t_disp"] = time.monotonic(); phase = "preset"
                for rp in R.poll_replies():
                    if rp["op"] == "set": P["set_resp"] = rp; ev("preset_set_reply", code=rp["code"], state=rp["state"], rtt_s=rp["t_resp"] - rp["t_call"], jitter_ms=1000 * (P["t_disp"] - (T0 + Dp)) if P["t_disp"] else None)
                if phase in ("preset_wait", "preset") and P["reg_src_t"] is None and P["t_sd"] is not None and now - P["t_sd"] >= 0.3:
                    try: L.register_source(65); ev("re_add_source", ok=True, audio_s=au)
                    except Exception as e: ev("re_add_source", ok=False, err=str(e))
                    P["reg_src_t"] = now; R.reset_inflight()
                if phase == "preset":
                    if obs.player_state not in (None, 0): P["seen_busy"] = True
                    P["idle_deb"] = P["idle_deb"] + 1 if (P["seen_busy"] and obs.player_state == 0) else 0
                    if P["idle_deb"] >= 2 and P["t_idle"] is None:
                        P["t_idle"] = now; ev("preset_idle", audio_s=au)
                        done_audio = n_ticks is not None and (prefix_n + au / TICK) >= n_ticks - 1 - 0.3 / TICK
                        P["resume"] = not done_audio
                        L.request_mode(URS); phase = "urs_wait"; ev("urs_request", audio_s=au, resume=P["resume"])
                if phase == "urs_wait" and obs.mode == URS and obs.action_status == ACTION_RUNNING:
                    t_urs = time.monotonic(); L.register_source(65); R.hand_init(); P["p0"] = R.arms_now(); P["t_in0"] = (time.monotonic() - T0) + 0.02; P["wh0"] = wh_prev.copy()
                    lim.reset(P["p0"]); ev("urs_active", audio_s=au, dt_idle_to_urs_s=t_urs - P["t_idle"], p0_dev_from_D=float(np.abs(P["p0"] - D).max())); phase = "fade_in"
                if phase == "fade_out": w = float(minjerk((au - P["t_f0"]) / L_OUT)); cmd = D + (1 - w) * (sonic_arm - D)
                elif phase == "fade_in":
                    w = float(minjerk((au - P["t_in0"]) / L_IN))
                    tgt = sonic_arm if P["resume"] else READY
                    cmd = P["p0"] + w * (tgt - P["p0"])
                    if w >= 1.0: phase = "sonic2" if P["resume"] else "exit_pending"
                elif phase in ("pre", "sonic2"): cmd = sonic_arm
                elif phase in ("preset_wait", "preset", "urs_wait"): cmd = None
                if phase in ("sonic2", "pre", "fade_out", "fade_in") or (phase in ("preset_wait", "preset") and P["reg_src_t"] is not None) or phase == "urs_wait":
                    wsrc = bool(P["resume"] or phase != "fade_in"); wref = sonic_wh if wsrc else np.zeros(4)
                if phase == "exit_pending":
                    cmd = READY.copy(); wref = np.zeros(4)
            else:
                cmd = sonic_arm; wref = sonic_wh; wsrc = True
            # ---- entry blend from the idle (READY) stream into the SONIC stream; for SONIC turns only
            if phase in ("sonic", "pre") and now - t_enter < A.entry_s and cmd is not None:
                we = float(minjerk((now - t_enter) / A.entry_s)); we_e = we; cmd = enter_from + we * (cmd - enter_from); wref = enter_wh + we * (wref - enter_wh)
            # ---- turn end: all ticks played -> min-jerk exit to READY (arms) / 0 (waist, head)
            fpos = prefix_n + au / TICK
            if phase in ("sonic", "pre", "sonic2", "exit_pending") and n_ticks is not None and fpos >= n_ticks - 1:
                phase = "exit"; t_x0 = now; p_x = (R.last_cmd.copy() if R.last_cmd is not None else sonic_arm); wh_x = wh_prev.copy()
                Tx = max(0.8, 1.875 * float(np.abs(READY - p_x).max()) / 1.2); ev("turn_exit_start", turn=turn, audio_s=au, T_exit=Tx)
            if phase == "exit":
                w = float(minjerk((now - t_x0) / Tx)); cmd = p_x + w * (READY - p_x); wref = wh_x * (1 - w)
                if w >= 1.0:
                    ev("turn_done", turn=turn, n_chunks=tbuf.nchunks)
                    sl = (tbuf.recv_t[:tbuf.n] - (T0 + (np.arange(tbuf.n) - prefix_n) * TICK)) * -1.0       # slack = due time - arrival time (>0: arrived early)
                    send_evt(op="turn_done", turn=turn, slack_min_s=float(np.nanmin(sl)), slack_mean_s=float(np.nanmean(sl)), n_late=int((sl < 0).sum()), lag_mean_s=float(np.mean(tbuf.lags)), lag_max_s=float(np.max(tbuf.lags)))
                    ev("turn_slack", turn=turn, slack_min_s=float(np.nanmin(sl)), n_late=int((sl < 0).sum()), lags=[float(x) for x in tbuf.lags], n_ticks=tbuf.n_ticks)
                    turn = None; T0 = None; phase = "idle"; cmd = READY.copy(); wref = np.zeros(4); out_prev = READY.copy()
        else:
            cmd = READY.copy(); wref = np.zeros(4)
            if quit_flag[0] and not pending: break
        # ---- publish (arms through the limiter) and McBodyPose
        in_sd = phase in ("preset_wait", "preset", "urs_wait") and turn is not None
        hit = False
        if cmd is not None and not in_sd:
            sonic_raw = cmd.copy(); outc, hit = lim.step(np.clip(cmd, LO, HI)); t_pub0 = time.monotonic(); R.publish_arms(outc); out_prev = outc.copy()
        else: t_pub0 = time.monotonic(); outc = None
        q_wh = u = None
        if wref is not None:
            wh_prev = np.array(wref, float)
            _w = np.array(wref, float); fn = (lambda ts, e=we_e, ew=enter_wh.copy(): (1 - e) * ew + e * sonic_vec(ts)[0][14:18]) if wsrc else (lambda ts, _w=_w: _w)
            q_wh, u, _ = R.body_pose_ref(fn, au)      # reference evaluated LAG ticks ahead on the audio clock (Smith predictor as c12)
        else: R.send_body_pose(np.zeros(4))
        t_pub1 = time.monotonic()
        R.log(phase if turn is not None else "idle", audio_s=(au if turn is not None else None), cmd=outc, sonic=sonic_raw, wref=wref, u=u,
              extra={"turn": -1 if turn is None else turn, "jitter_ms": 1000 * jit, "pub_ms": 1000 * (t_pub1 - t_pub0), "limited": int(hit), "underrun": int(underrun), "sched": nxt})
        k += 1
    ev("quit_clean")
finally:
    for _ in range(25): R.send_body_pose(np.zeros(4)); time.sleep(TICK)
    try:
        if R.last_cmd is not None and L.observed(graph=False).mode == URS:
            for _ in range(10): L.publish_upper_body(R.head, R.last_cmd.tolist(), R.hand_mode, R.hand_pos, "cero"); time.sleep(TICK)
    except Exception: pass
    err = None
    try:
        err = L.delete_source()
        if L.observed(graph=False).mode != SD: L.request_mode(SD, busy_retry_s=12.0)
    except Exception as e: ev("final_sd_error", err=str(e))
    ok = L.wait_stable(15.0)
    ev("final", stable=bool(ok), mode=L.observed(graph=False).mode, delete_err=err)
    R.dump(A.out); json.dump(dict(events=R.events, vmax=A.vmax, amax=A.amax, lim_hits=lim.nlim), open(A.out.replace(".csv", ".json"), "w"), default=str)
    print("motor done rows", len(R.rows), "stable", ok, flush=True)
    send_evt(op="bye")
    try: R.close()
    except Exception: pass
    os._exit(0)
