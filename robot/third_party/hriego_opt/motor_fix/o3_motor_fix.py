#!/usr/bin/env python3
"""O3 MOTOR (runs inside x2-twin; ROS/AimDK overlays sourced; numpy only, no CUDA).
Threads: (1) receiver  - AF_UNIX server on a bind-mounted socket; parses tick chunks / JSON controls from the brain into per-turn buffers (stamps arrival).
         (2) controller - ONE 50 Hz loop (busy-wait last 2 ms) that owns ROS: publishes URS arms (source 65) + McBodyPose waist/head (domain 77, c07/c12 Smith-predictor
             sign control), SetMcPresetMotion via the c10 helper subprocess (inside emote_lib.Rig), the state machine and the per-tick logger.
Between turns the controller keeps URS and streams READY (arms) / 0 (waist, head): no mode change between SONIC turns.
Per-joint velocity AND acceleration limiter on every arm command (V_MAX / A_MAX).  Preset turns follow the c12 V2 path (min-jerk fade to pose D, handoff at a local
minimum of SONIC arm speed, preset in STAND_DEFAULT, URS re-entry, fade-in from the measured pose at the CURRENT audio time).
SAFETY FIXES (motor_fix, diag_fall 2026-10-23):
 (1) URS re-ADD only AFTER the MC player reports idle + SETTLE_S settle (never during the animation; the SD+0.3 s re-ADD of o3_motor_vab:185 is removed).
 (2) NO waist streaming during preset phases (wref=None -> McBodyPose zeros = hold); global SONIC waist clamp |twist|,|bend| <= 0.12 rad, rate-limited (W_RATE).
 (3) Fall interlock: tilt > 12 deg or pelvis < 0.62 m (3-tick debounce) -> stop streaming, hold, DELETE+SD via worker, mark run unsafe, refuse further turns until reset.
 (4) Pre-preset gate: tilt <= 6 deg, arms within 0.05 rad of D, MC STABLE+RUNNING in SD, zero commanded locomotion; extend preset_wait up to 1 s then skip the preset.
 (5) ALL blocking MC service calls (delete_source/request_mode/register_source) run on the Svc worker thread with budgets; the 50 Hz loop only submits and polls.
 (6) Locomotion lockout: a turn refuses to start (up to 2 s, then turn_refused) unless commanded locomotion velocity is zero / absent and the MC is STABLE+RUNNING.
usage: motor.py OUT.csv [--vmax 2.0] [--amax 40]"""
import argparse, json, os, queue, socket, sys, threading, time
import numpy as np
from pathlib import Path
from emote_lib import *          # Rig, URS, SD, TICK, READY, LO, HI, minjerk, ARM_JOINTS
from o3common import *
from x2_ultra.real.control import ACTION_RUNNING, FSM_STABLE

POINT_TARGETS = json.loads((Path(__file__).resolve().parent / "point_targets.json").read_text())
HEAD_SIGN = {"LEFT": 1.0, "RIGHT": -1.0, "FRONT": 0.0}

# ---- fix 2: global SONIC waist/head reference clamp (twist, bend_forward, bend_sideways; head unrestricted) + slew rate
W_AMP = np.array([0.12, 0.12, 0.12, 1e9]); W_RATE = np.array([1.2, 1.2, 1.2, 2.5])
# ---- fix 3: fall interlock thresholds; fix 4: pre-preset gate; fix 1: post-idle settle; fix 6: start-gate window
TILT_FALL, PELVIS_FALL, FALL_DEB = 12.0, 0.62, 3
TILT_HARD = 25.0     # absolute tilt cap (vendor bow 3001/11 peaks at 21 deg per c12 V0; a fall blows through this)
TILT_GATE, ARM_GATE, GATE_EXT_S = 6.0, 0.05, 1.0
SETTLE_S = 0.2; START_GATE_S = 2.0; URS_TIMEOUT_S = 8.0


def clamp_w(w, prev):
    """fix 2: amplitude clamp (W_AMP) + per-tick slew limit (W_RATE rad/s) of a 4-vector waist/head reference against the last published one."""
    w = np.clip(np.asarray(w, float), -W_AMP, W_AMP)
    return prev + np.clip(w - prev, -W_RATE * TICK, W_RATE * TICK)


class Svc:
    """fix 5: one worker thread executes the blocking MC service calls in submission order; the 50 Hz loop only submits and polls.
    Each key carries a budget; the loop logs svc_timeout once when a call exceeds it (the worker keeps its own sequence)."""
    def __init__(s, ev):
        s.ev = ev; s.q = queue.Queue(); s.lk = threading.Lock(); s.st = {}; s.t_sub = {}; s.to_logged = set()
        threading.Thread(target=s._run, daemon=True).start()
    def _run(s):
        while True:
            key, fn = s.q.get()
            t0 = time.monotonic(); err = None
            try: fn()
            except Exception as e: err = str(e)
            with s.lk: s.st[key] = {"dur_s": time.monotonic() - t0, "err": err}
    def submit(s, key, fn, budget=6.0):
        """True if newly submitted (False if already submitted/done)."""
        with s.lk:
            if key in s.st or key in s.t_sub: return False
            s.t_sub[key] = (time.monotonic(), budget)
        s.q.put((key, fn)); return True
    def get(s, key):
        with s.lk: return s.st.get(key)
    def tick(s, now):
        with s.lk: items = [(k, v) for k, v in s.t_sub.items() if k not in s.st and k not in s.to_logged]
        for k, (t0, budget) in items:
            if now - t0 > budget:
                s.to_logged.add(k); s.ev("svc_timeout", key=k, elapsed_s=now - t0, budget_s=budget)

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
svc = Svc(ev)        # fix 5
aborted = False; unsafe = False; fall_deb = 0; G0 = {}     # fix 3/6 defaults (visible to the finally block even on early failure)
lim = Limiter(A.vmax, A.amax)
tick_log = []        # (sched, actual_before_publish, publish_done)


def tick_wait(nxt):
    time.sleep(max(0.0, nxt - time.monotonic() - 0.002))
    while time.monotonic() < nxt: pass


def point_weight(audio_s, meta):
    """0.8 s quintic prep, 1.5 s hold, 1.65 s recovery around the CTC word."""
    stroke = float(meta["anchor_start_s"]) - float(meta.get("lead_s", 0.12))
    prep = float(meta.get("prep_s", 0.8)); hold = float(np.clip(meta.get("hold_s", 1.5), 1.0, 2.0))
    recovery = float(meta.get("recovery_s", 1.65)); p0 = stroke - prep; h1 = stroke + hold; r1 = h1 + recovery
    if audio_s < p0 or audio_s >= r1: return 0.0
    if audio_s < stroke:
        x = np.clip((audio_s - p0) / prep, 0.0, 1.0); return float(x ** 3 * (10 - 15 * x + 6 * x * x))
    if audio_s <= h1: return 1.0
    x = np.clip((audio_s - h1) / recovery, 0.0, 1.0); return float(1 - x ** 3 * (10 - 15 * x + 6 * x * x))


def send_evt(**kw):
    try:
        if client[0] is not None: send_json(client[0], kw)
    except Exception: pass


try:
    assert L.wait_stable(10.0), "MC not STABLE"; assert L.observed(graph=False).mode == SD, "mode"
    assert R.loc_zero(), f"fix 6: locomotion velocity commanded at startup: {R.loc_cmd()}"   # assert/log locomotion lockout
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
    # fix 3/6 state: fall interlock + per-turn start-gate wait
    aborted = False; unsafe = False; fall_deb = 0; G0 = {}
    # preset (V2) state
    P = {}; Q = {}
    while True:
        nxt = t_loop0 + k * TICK; tick_wait(nxt); now = time.monotonic(); jit = now - nxt
        obs = L.observed(graph=False)
        if obs.mc_age_s is None or obs.mc_age_s > 0.5: raise RuntimeError(f"MC state stale k={k}")
        if now - t_run0 > A.max_run_s: raise RuntimeError("max run time")
        svc.tick(now)
        # ---- fix 3: fall interlock (3-tick debounce) -> stop streaming, hold, refuse everything until reset.
        # Fall SIGNATURE, validated on c12 data: the vendor bow 3001/11 legitimately reaches tilt 21 deg with pelvis 0.693 (c12 V0, 0 falls),
        # so raw tilt>12 would false-positive on the animation; the turn-24 fall had tilt>12 WITH pelvis sinking below 0.66.
        # Rules: pelvis < 0.62 always; tilt > 12 unless the animation player is busy in the preset phase with pelvis >= 0.66; tilt > 25 always.
        tilt_i, pel_i = R.pose["tilt"], R.pose["z"]
        busy_anim = turn is not None and phase in ("preset", "preset_wait") and obs.player_state in (1, 2)
        pel_hi = pel_i is not None and pel_i >= 0.66
        fall_now = ((pel_i is not None and pel_i < PELVIS_FALL)
                    or (tilt_i is not None and tilt_i > TILT_HARD)
                    or (tilt_i is not None and tilt_i > TILT_FALL and not (busy_anim and pel_hi)))
        if not aborted and fall_now: fall_deb += 1
        else: fall_deb = 0
        if fall_deb >= FALL_DEB and not aborted:
            aborted = True; unsafe = True
            ev("fall_interlock", tilt=tilt_i, pelvis=pel_i, phase=phase, turn=turn, k=k)
            send_evt(op="fall", turn=(-1 if turn is None else turn), phase=phase, tilt=tilt_i, pelvis=pel_i)
            turn = None; T0 = None; phase = "aborted"; P = {}; Q = {}
            svc.submit("fall_sd", lambda: (L.delete_source(), L.request_mode(SD, busy_retry_s=8.0)))
        if aborted:
            with lock: pr = list(pending); pending.clear()
            for t_ref in pr:
                ev("turn_refused", turn=t_ref, reason="fall_interlock"); send_evt(op="turn_refused", turn=t_ref, reason="fall_interlock")
            if quit_flag[0]: break
        # ---- activate a pending turn at its start time (fix 6: only with MC STABLE+RUNNING and zero commanded locomotion velocity)
        if turn is None and phase == "idle" and not aborted:
            with lock: nxt_turn = pending[0] if pending else None
            if nxt_turn is not None:
                tb = turns[nxt_turn]; m = tb.meta; prefix_n = m["prefix_n"]; T0 = m["T_a0"]
                pre = m.get("preset"); t_start = T0 + start_offset(prefix_n, pre)
                if now >= t_start:
                    gate_bad = None
                    if obs.fsm != FSM_STABLE or obs.action_status != ACTION_RUNNING: gate_bad = f"mc_not_stable(fsm={obs.fsm},status={obs.action_status})"
                    elif not R.loc_zero(): gate_bad = f"locomotion_cmd{tuple(round(x, 4) for x in R.loc_cmd())}"
                    else:
                        odm = L.odometry()
                        if odm is not None and max(abs(odm[0]), abs(odm[1])) > 0.08 or (odm is not None and abs(odm[2]) > 0.15): gate_bad = f"locomotion_measured{tuple(round(x, 3) for x in odm)}"
                    if gate_bad is not None:
                        g0 = G0.setdefault(nxt_turn, now)
                        ev("turn_start_gate_wait", turn=nxt_turn, reason=gate_bad, waited_s=now - g0) if int((now - g0) * 50) % 25 == 0 else None
                        if now - g0 > START_GATE_S:
                            with lock:
                                if pending and pending[0] == nxt_turn: pending.pop(0)
                            G0.pop(nxt_turn, None); T0 = None
                            ev("turn_refused", turn=nxt_turn, reason=gate_bad); send_evt(op="turn_refused", turn=nxt_turn, reason=gate_bad)
                    else:
                        G0.pop(nxt_turn, None)
                        pending.pop(0); turn = nxt_turn; tbuf = tb; phase = "sonic"; t_enter = now; enter_from = out_prev.copy(); enter_wh = wh_prev.copy()
                        ev("turn_start", turn=turn, kind=m["kind"], late_s=now - t_start, t_recv_ctrl=m["t_recv"], T_a0=T0, t_first_tick_recv=float(tbuf.recv_t[0]) if tb.n else None,
                           gate="ok", fsm=obs.fsm, status=obs.action_status, tilt=tilt_i, pelvis=pel_i)
                        P = {}; Q = {}
                    if m.get("point"):
                        pm = m["point"]
                        direction = str(pm["direction"]).upper()
                        pt = POINT_TARGETS[direction]
                        side = pt["side"]
                        Q = {"meta": pm, "direction": direction,
                             "parm": np.array([n.startswith(side + "_") for n in ARM_JOINTS]),
                             "target": np.array([pt["joints"].get(n, np.nan) for n in ARM_JOINTS], dtype=float)}
                    if pre is not None:
                        pol, D_a = preset_policy(pre); P = dict(pol=pol, D_a=D_a, t_h=D_a, th_done=False, t_f0=D_a - L_OUT, set_resp=None, seen_busy=False, t_idle=None, idle_deb=0,
                                                              reg_src_t=None, t_sd=None, t_disp=None, p0=None, t_in0=None, resume=True)
        au = (now - T0) if T0 is not None and turn is not None else now
        # ---- references for this tick
        cmd = None; wref = None; sonic_raw = None; underrun = False; wsrc = None; we_e = 1.0
        if aborted: cmd = None; wref = None       # fix 3: interlocked -> no arms, waist held at zero rate
        def sonic_vec(ts):
            v, ur = tbuf.at(prefix_n + ts / TICK); return v, ur
        def point_vec(ts):
            v, ur = sonic_vec(ts)
            if v is None or not Q: return v, ur
            w = point_weight(ts, Q["meta"])
            arms = np.asarray(v[:14], dtype=float).copy(); wh = np.asarray(v[14:18], dtype=float).copy()
            m = Q["parm"]; arms[m] += w * (Q["target"][m] - arms[m])
            wh[3] += HEAD_SIGN[Q["direction"]] * float(Q["meta"].get("head_amp", 0.30)) * w
            return np.concatenate([arms, wh, np.asarray(v[18:19])]), ur
        if aborted:
            pass                                  # fix 3: hold; cmd/wref stay None (publish section sends McBodyPose zeros, no arms)
        elif turn is not None:
            sv, underrun = point_vec(au); sonic_arm, sonic_wh = sv[:14], sv[14:18]
            n_ticks = tbuf.n_ticks
            if phase in ("sonic", "pre", "fade_out", "fade_in", "sonic2", "preset_wait", "preset", "preset_settle", "urs_wait") and P:
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
                    c_last = R.last_cmd.copy()
                    svc.submit(f"handoff_{turn}", lambda: (L.delete_source(), L.request_mode(SD, busy_retry_s=8.0)))   # fix 5: DELETE+SD on the worker, loop never blocks
                    ev("handoff", audio_s=au, async_svc=True, last_cmd_dev_from_D=float(np.abs(c_last - D).max())); phase = "preset_wait"; P["ho_log"] = False
                if phase in ("preset_wait", "preset", "preset_settle", "urs_wait", "fade_in") and P.get("ho_log") is False:
                    ho = svc.get(f"handoff_{turn}")
                    if ho is not None: P["ho_log"] = True; ev("handoff_done", delete_sd_err=ho["err"], dur_s=ho["dur_s"], audio_s=au)
                if phase == "preset_wait" and au >= Dp:
                    # fix 4: pre-preset gate (tilt <= 6 deg, arms within 0.05 rad of D, MC STABLE+RUNNING in SD, zero commanded locomotion); extend up to 1 s, then skip the preset
                    meas = R.arms_now(); tilt_g = R.pose["tilt"]; loc_ok = R.loc_zero()
                    dev = float(np.abs(np.asarray(meas, float) - D).max()) if meas is not None else None   # arms_now() is already the 14-vector in ARM_JOINTS order
                    gate_ok = (dev is not None and dev <= ARM_GATE and tilt_g is not None and tilt_g <= TILT_GATE
                               and obs.mode == SD and obs.fsm == FSM_STABLE and obs.action_status == ACTION_RUNNING and loc_ok)
                    if gate_ok:
                        R.preset_set(pre["motion"], pre["area"]); P["t_disp"] = time.monotonic(); phase = "preset"
                        ev("preset_dispatch", audio_s=au, delay_vs_plan_s=now - (T0 + Dp), arm_dev_from_D=dev, tilt=tilt_g)
                    elif au >= Dp + GATE_EXT_S:
                        ev("preset_gate_skip", audio_s=au, arm_dev_from_D=dev, tilt=tilt_g, mode=obs.mode, fsm=obs.fsm, status=obs.action_status, loc_zero=loc_ok)
                        done_audio = n_ticks is not None and (prefix_n + au / TICK) >= n_ticks - 1 - 0.3 / TICK
                        P["t_idle"] = now; P["resume"] = not done_audio; P["skipped"] = True; phase = "preset_settle"
                for rp in R.poll_replies():
                    if rp["op"] == "set": P["set_resp"] = rp; ev("preset_set_reply", code=rp["code"], state=rp["state"], rtt_s=rp["t_resp"] - rp["t_call"], jitter_ms=1000 * (P["t_disp"] - (T0 + Dp)) if P["t_disp"] else None)
                # fix 1: the SD+0.3 s re-ADD of o3_motor_vab:185 is GONE; the source is re-ADDed only after player idle + settle, in urs_wait below
                if phase == "preset":
                    if obs.player_state not in (None, 0): P["seen_busy"] = True
                    P["idle_deb"] = P["idle_deb"] + 1 if (P["seen_busy"] and obs.player_state == 0) else 0
                    sr = P.get("set_resp"); rej = sr is not None and sr.get("code") not in (0, None)
                    no_busy = P["t_disp"] is not None and now - P["t_disp"] > 2.0 and not P["seen_busy"]
                    if (P["idle_deb"] >= 2 or rej or no_busy) and P["t_idle"] is None:
                        P["t_idle"] = now
                        if rej or no_busy:
                            P["skipped"] = True; ev("preset_abort", audio_s=au, rejected=bool(rej), code=(sr or {}).get("code"), no_busy_timeout=bool(no_busy))
                        else: ev("preset_idle", audio_s=au)
                        done_audio = n_ticks is not None and (prefix_n + au / TICK) >= n_ticks - 1 - 0.3 / TICK
                        P["resume"] = not done_audio; phase = "preset_settle"
                # fix 1: URS is requested only SETTLE_S after the player went idle (request_mode on the worker, fix 5)
                if phase == "preset_settle" and now - P["t_idle"] >= SETTLE_S:
                    svc.submit(f"urs_{turn}", lambda: L.request_mode(URS)); phase = "urs_wait"
                    ev("urs_request", audio_s=au, resume=P["resume"], settle_s=now - P["t_idle"], skipped=bool(P.get("skipped")))
                if phase == "urs_wait" and obs.mode == URS and obs.action_status == ACTION_RUNNING:
                    svc.submit(f"add_{turn}", lambda: L.register_source(65))       # fix 1/5: re-ADD after idle+settle, async
                    ra = svc.get(f"add_{turn}")
                    if ra is None and P.get("t_add0") is None: P["t_add0"] = now
                    if ra is not None or (P.get("t_add0") is not None and now - P["t_add0"] > 0.5):
                        t_urs = time.monotonic(); R.hand_init(); R.reset_inflight()
                        P["p0"] = R.arms_now(); P["p0"] = D.copy() if P["p0"] is None else P["p0"]
                        P["t_in0"] = (time.monotonic() - T0) + 0.02; P["wh0"] = wh_prev.copy()
                        lim.reset(P["p0"]); ev("urs_active", audio_s=au, dt_idle_to_urs_s=t_urs - P["t_idle"], add_err=(ra or {}).get("err"), add_dur_s=(ra or {}).get("dur_s"),
                                               p0_dev_from_D=float(np.abs(P["p0"] - D).max())); phase = "fade_in"
                if phase == "urs_wait" and now - P["t_idle"] > URS_TIMEOUT_S:
                    ev("urs_timeout", audio_s=au, mode=obs.mode, status=obs.action_status)   # bounded degraded path: end the turn towards READY
                    P["resume"] = False; R.hand_init(); R.reset_inflight()
                    P["p0"] = R.arms_now(); P["p0"] = D.copy() if P["p0"] is None else P["p0"]
                    P["t_in0"] = (time.monotonic() - T0) + 0.02; lim.reset(P["p0"]); phase = "fade_in"
                if phase == "fade_out": w = float(minjerk((au - P["t_f0"]) / L_OUT)); cmd = D + (1 - w) * (sonic_arm - D)
                elif phase == "fade_in":
                    w = float(minjerk((au - P["t_in0"]) / L_IN))
                    tgt = sonic_arm if P["resume"] else READY
                    cmd = P["p0"] + w * (tgt - P["p0"])
                    if w >= 1.0: phase = "sonic2" if P["resume"] else "exit_pending"
                elif phase in ("pre", "sonic2"): cmd = sonic_arm
                elif phase in ("preset_wait", "preset", "preset_settle", "urs_wait"): cmd = None
                # fix 2: waist streams ONLY in the SONIC arm phases (never during preset_wait/preset/preset_settle/urs_wait: wref None -> McBodyPose zeros, hold, no fight
                # with the animation), and always clamped (|twist|,|bend| <= 0.12 rad) + slew-limited
                if phase in ("sonic2", "pre", "fade_out", "fade_in"):
                    wsrc = bool(P["resume"] or phase != "fade_in"); wref = clamp_w(sonic_wh, wh_prev) if wsrc else np.zeros(4)
                if phase == "exit_pending":
                    cmd = READY.copy(); wref = np.zeros(4)
            else:
                cmd = sonic_arm; wref = clamp_w(sonic_wh, wh_prev); wsrc = True     # fix 2: global clamp
            # ---- entry blend from the idle (READY) stream into the SONIC stream; for SONIC turns only
            if phase in ("sonic", "pre") and now - t_enter < A.entry_s and cmd is not None:
                we = float(minjerk((now - t_enter) / A.entry_s)); we_e = we; cmd = enter_from + we * (cmd - enter_from); wref = clamp_w(enter_wh + we * (wref - enter_wh), wh_prev)
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
        in_sd = phase in ("preset_wait", "preset", "preset_settle", "urs_wait") and turn is not None
        hit = False
        if cmd is not None and not in_sd:
            sonic_raw = cmd.copy(); outc, hit = lim.step(np.clip(cmd, LO, HI)); t_pub0 = time.monotonic(); R.publish_arms(outc); out_prev = outc.copy()
        else: t_pub0 = time.monotonic(); outc = None
        q_wh = u = None
        if wref is not None:
            wh_prev = np.array(wref, float)
            _w = np.array(wref, float); fn = (lambda ts, e=we_e, ew=enter_wh.copy(): (1 - e) * ew + e * np.clip(point_vec(ts)[0][14:18], -W_AMP, W_AMP)) if wsrc else (lambda ts, _w=_w: _w)
            q_wh, u, _ = R.body_pose_ref(fn, au)      # reference evaluated LAG ticks ahead on the audio clock (Smith predictor as c12); fix 2: lookahead clamped too
        else: R.send_body_pose(np.zeros(4))
        t_pub1 = time.monotonic()
        R.log(phase if (turn is not None or aborted) else "idle", audio_s=(au if turn is not None else None), cmd=outc, sonic=sonic_raw, wref=wref, u=u,
              extra={"turn": -1 if turn is None else turn, "jitter_ms": 1000 * jit, "pub_ms": 1000 * (t_pub1 - t_pub0), "limited": int(hit), "underrun": int(underrun), "sched": nxt})
        k += 1
    ev("quit_clean")
finally:
    _exc = sys.exc_info()[1]
    if _exc is not None:
        import traceback; _tb = traceback.format_exc()
        try: ev("fatal_error", err=str(_exc), tb=_tb[-3000:])
        except Exception: pass
        print("FATAL", _tb, flush=True)
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
    ev("final", stable=bool(ok), mode=L.observed(graph=False).mode, delete_err=err, unsafe=bool(unsafe), aborted=bool(aborted), tilt=R.pose["tilt"], pelvis=R.pose["z"])
    R.dump(A.out); json.dump(dict(events=R.events, vmax=A.vmax, amax=A.amax, lim_hits=lim.nlim, unsafe=bool(unsafe), aborted=bool(aborted)), open(A.out.replace(".csv", ".json"), "w"), default=str)
    print("motor done rows", len(R.rows), "stable", ok, "unsafe", unsafe, flush=True)
    send_evt(op="bye")
    try: R.close()
    except Exception: pass
    os._exit(0)
