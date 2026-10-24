"""c12: one speaking turn = SONIC co-speech -> transition -> SDK preset -> transition -> SONIC co-speech (resume at the CURRENT audio time).
Runs inside x2-twin. usage: stream_emote_turn.py V0|V1|V2 TURN.json ARMS.npz OUT.csv
 TURN.json (written by run_c12.py): turn_id, motion_id, area, anchor_s, dur_s, lead_onset_s, lead_stroke_s, policy_dispatch_s{stroke,mid,onset}, max_initial_delay_s.
 Audio clock: T0 = stream time of audio sample 0 (random 0-20 ms sub-tick phase, as c10). The twin has no audio; playback is virtual.
 D = MC planner default arm pose (SD snap target, preset first frame): [0.4,0,0,-1.2,0,0,0] x2 (probe: SetMcAction SD snaps arms to D in ~0.1 s, so the fade targets D, not SONIC's READY).
 V0: stand still (STAND_DEFAULT) -> preset -> still.   V1: linear 0.8 s ramp SONIC->D, DELETE URS + SD + preset, ADD URS, linear 0.8 s ramp back.
 V2: min-jerk amplitude fade 0.6 s (cmd = D + (1-w)(sonic-D)), handoff at a local minimum of SONIC arm speed within +-0.3 s of the planned dispatch, waist/head keep SONIC
     via McBodyPose during the preset, fade-in 0.7 s min-jerk from the measured pose at the current audio time."""
import json, sys, time, random
import numpy as np
from emote_lib import *

variant, turn_f, arms_f, out = sys.argv[1:5]
T = json.load(open(turn_f)); z = np.load(arms_f); assert list(z["names"]) == list(ARM_JOINTS)
cmdc = z["cmd"][100:]; whc = np.asarray(z["waist_head"])[100:, :4]
D = np.array([0.4, 0, 0, -1.2, 0, 0, 0] * 2, float)
def interp(a, ts):
    f = np.clip(ts / TICK, 0, len(a) - 1); i = int(f); j = min(i + 1, len(a) - 1); r = f - i; return a[i] * (1 - r) + a[j] * r
sonic_at = lambda ts: interp(cmdc, ts); sonic_wh = lambda ts: interp(whc, ts)
dur = T["dur_s"]; anchor = T["anchor_s"]; cap = T["max_initial_delay_s"]
# ---- dispatch policy: stroke at anchor, else midpoint onset/stroke at anchor, else onset (plan) -- all limited by the 1.5 s start-of-audio delay cap
pol = None
for name in ("stroke", "mid", "onset"):
    d_a = T["policy_dispatch_s"][name]
    if d_a >= -cap: pol = name; break
D_a = T["policy_dispatch_s"][pol]
L_OUT, L_IN = (0.8, 0.8) if variant == "V1" else (0.6, 0.7)
shape = (lambda s: float(np.clip(s, 0, 1))) if variant == "V1" else (lambda s: float(minjerk(s)))
# V2: handoff instant at the local minimum of SONIC arm speed within +-0.3 s of the planned dispatch
t_h = D_a
if variant == "V2":
    ts = np.arange(D_a - 0.3, D_a + 0.3 + 1e-9, TICK); v = np.array([np.abs(sonic_at(t + 0.02) - sonic_at(t - 0.02)).max() / 0.04 for t in ts])
    v = np.convolve(v, np.ones(5) / 5, mode="same"); v[:2] = v[-2:] = np.inf if len(v) > 5 else v[:2]
    t_h = float(ts[int(np.argmin(v))])
t_f0 = t_h - L_OUT
R = Rig("emote_" + variant); L = R.link; ev = R.ev
meta = {**T, "variant": variant, "policy": pol, "D_a": D_a, "t_handoff_planned": t_h, "t_fade0": t_f0, "L_out": L_OUT, "L_in": L_IN}
def wh_ref(ts, g=1.0):
    return sonic_wh(ts) * g
rows_ok = False
try:
    assert L.wait_stable(10.0), "MC not STABLE"; obs0 = L.observed(graph=False); assert obs0.mode == SD, f"mode {obs0.mode}"
    PHASE_S = random.uniform(0, TICK)
    anchor_arm = R.arms_now()
    if variant == "V0":
        T0 = time.monotonic() + 1.0 + max(0.0, -D_a) + PHASE_S    # still-stand lead-in, T0 such that dispatch >= now+1 s
    else:
        L.request_mode(URS); assert L.wait_mode(URS, 8.0), "URS not reached"
        t_end = time.monotonic() + 2.0
        while L.node.count_subscribers("/mc/upper_body_command") == 0 and time.monotonic() < t_end: time.sleep(0.05)
        L.register_source(65); R.hand_init(); anchor_arm = R.arms_now()
        b0 = sonic_at(min(0.0, t_f0)); ramp_n = int(max(2.0, np.abs(b0 - anchor_arm).max() / 0.5) / TICK)
        settle = 0.5 + max(0.0, -t_f0)
        T0 = time.monotonic() + 0.2 + (ramp_n * TICK) + settle + PHASE_S
        t_start = time.monotonic() + 0.2
    meta.update(T0=T0, phase_s=PHASE_S)
    st = {"phase": "init", "k": 0}
    def tick_wait(nxt):
        time.sleep(max(0.0, nxt - time.monotonic() - 0.002))
        while time.monotonic() < nxt: pass
    # ---- URS ramp-in
    if variant != "V0":
        for i in range(ramp_n):
            w = 0.5 - 0.5 * np.cos(np.pi * i / (ramp_n - 1)); c = anchor_arm + w * (b0 - anchor_arm); tick_wait(t_start + i * TICK)
            R.publish_arms(c); R.log("ramp", cmd=c)
        t_loop0 = t_start + ramp_n * TICK
    else:
        t_loop0 = time.monotonic() + 0.1
    # ---- main loop (50 Hz) as a state machine
    phase = "pre" if variant != "V0" else "v0_wait"
    k = 0; t_disp = t_delete = t_sd = None; set_resp = None; seen_busy = False; t_idle = None; t_urs = None; t_in0 = None; p0 = None; res_state = None
    h_start_obs = None; reg_src_t = None; idle_deb = 0; task_time = None; wh_g_state = {"g": 1.0}
    end_audio = dur + 0.4; ended = False; t_end_fade0 = None; end_p = None
    max_t = T0 + max(dur, D_a + T["preset_dur_s"] + 3.0) + 8.0
    while True:
        nxt = t_loop0 + k * TICK; tick_wait(nxt); now = time.monotonic(); au = now - T0
        obs = L.observed(graph=False)
        if obs.mc_age_s is None or obs.mc_age_s > 0.5: raise RuntimeError(f"MC state stale at {au:.2f}")
        cmd = None; sonic = None; g = 1.0
        if variant != "V0" and phase in ("pre", "fade_out", "fade_in", "sonic2", "tail", "end_fade", "ramp_hold"):
            sonic = sonic_at(au)
        # --- transitions
        if phase == "pre" and au >= t_f0: phase = "fade_out"
        if phase == "fade_out" and au >= t_h:
            # handoff: DELETE URS -> SD -> preset (all within ~0.02 s)
            c_last = R.last_cmd.copy(); t_delete = time.monotonic(); err = L.delete_source(); t_del_done = time.monotonic()
            L.request_mode(SD, busy_retry_s=8.0); t_sd = time.monotonic()
            ev("handoff", audio_s=au, delete_err=err, delete_dur_s=t_del_done - t_delete, sd_dur_s=t_sd - t_del_done, last_cmd_dev_from_D=float(np.abs(c_last - D).max()))
            phase = "preset_wait"
        if phase == "preset_wait" and au >= D_a:      # V1: immediately; V2 handoff earlier than D_a: arms rest at D (SD) until the planned dispatch
            R.preset_set(T["motion_id"], T["area"]); t_disp = time.monotonic(); phase = "preset"
        if phase == "v0_wait" and au >= D_a:
            t_disp = time.monotonic(); R.preset_set(T["motion_id"], T["area"]); phase = "preset"; ev("v0_dispatch", audio_s=au)
        # --- arms
        if phase == "pre": cmd = sonic
        elif phase == "fade_out":
            w = shape((au - t_f0) / L_OUT); cmd = D + (1 - w) * (sonic - D); g = 1 - w
        elif phase == "fade_in":
            w = shape((au - t_in0) / L_IN); cmd = p0 + w * (sonic - p0); g = w
            if w >= 1.0: phase = "sonic2"
        elif phase == "sonic2": cmd = sonic
        if cmd is not None: R.publish_arms(cmd)
        # --- preset phase bookkeeping
        for rp in R.poll_replies():
            if rp["op"] == "set": set_resp = rp; meta.update(set_code=rp["code"], set_state=rp["state"], t_call_helper=rp["t_call"], t_resp=rp["t_resp"])
        if phase in ("preset_wait", "preset") and variant != "V0" and reg_src_t is None and t_sd is not None and now - t_sd >= 0.3:
            try: L.register_source(65); ev("re_add_source", ok=True, audio_s=au)
            except Exception as e: ev("re_add_source", ok=False, err=str(e))
            reg_src_t = now; R.reset_inflight()
        if phase == "preset":
            if obs.player_state not in (None, 0): seen_busy = True
            if seen_busy and obs.player_state == 0:
                idle_deb += 1
            else: idle_deb = 0
            if idle_deb >= 2 and t_idle is None:
                t_idle = now; ev("preset_idle", audio_s=au)
                if variant == "V0": phase = "post_v0"; t_post0 = now
                elif au >= dur - 0.3: phase = "no_resume"; ev("no_resume_audio_over", audio_s=au)
                else:
                    L.request_mode(URS); t_req = time.monotonic(); phase = "urs_wait"; ev("urs_request", audio_s=au)
        if phase == "urs_wait" and obs.mode == URS and obs.action_status == __import__("x2_ultra.real.control", fromlist=["x"]).ACTION_RUNNING:
            t_urs = time.monotonic(); L.register_source(65); t_add = time.monotonic(); R.hand_init(); p0 = R.arms_now(); t_in0 = (time.monotonic() - T0) + 0.02
            ev("urs_active", audio_s=au, dt_idle_to_urs_s=t_urs - t_idle, dt_idle_to_add_s=t_add - t_idle, p0_dev_from_D=float(np.abs(p0 - D).max())); phase = "fade_in"
            if variant == "V1": R.reset_inflight()
        if phase == "post_v0" and now - t_post0 >= 0.3 and au > dur: ended = True
        # --- waist/head reference (V1: scaled by g and zero in preset; V2: SONIC throughout; V0: none)
        wref = u = q_wh = None
        if variant != "V0":
            gg = g if variant == "V1" else 1.0
            if variant == "V1" and phase in ("preset_wait", "preset", "urs_wait", "no_resume"): gg = 0.0
            if phase not in ("ramp",) and (phase not in ("preset", "preset_wait") or reg_src_t is not None) and phase != "no_resume":
                q_wh, u, wref = R.body_pose_ref(lambda ts: wh_ref(ts, gg), au)
        # --- end handling: at audio end + 0.4 s ramp to D, release
        if variant != "V0" and phase in ("sonic2", "pre") and au >= dur + 0.2 and not ended:
            phase = "end_fade"; t_ef0 = au; p_ef = R.last_cmd.copy()
        if phase == "end_fade":
            w = float(minjerk((au - t_ef0) / 0.8)); cmd = p_ef + w * (D - p_ef); R.publish_arms(cmd)
            if w >= 1.0: ended = True
        if variant == "V0" and phase == "post_v0" and au > dur + 0.3: ended = True
        R.log(phase, audio_s=au, cmd=cmd, sonic=sonic, wref=wref, u=u, extra={"fade_g": g})
        k += 1
        if ended or time.monotonic() > max_t: break
        if phase == "no_resume" and au > dur + 0.3: break
    rows_ok = True
    meta.update(t_disp=t_disp, t_delete=t_delete, t_sd=t_sd, t_idle=t_idle, t_urs=t_urs, t_in0_audio=t_in0, final_phase=phase, completed=t_idle is not None,
                jitter_ms=None if t_disp is None else 1000 * (t_disp - (T0 + D_a)))
finally:
    for _ in range(25): R.send_body_pose(np.zeros(4)); time.sleep(TICK)
    err = L.delete_source() if variant != "V0" else None
    try:
        if L.observed(graph=False).mode != SD: L.request_mode(SD, busy_retry_s=12.0)
    except Exception as e: meta["sd_final_error"] = str(e)
    ok = L.wait_stable(15.0); meta.update(stable_after=bool(ok), mode_after=L.observed(graph=False).mode, delete_error_final=err, events=R.events)
    R.dump(out); json.dump(meta, open(out.replace(".csv", ".json"), "w"), default=str)
    print("done", variant, T["turn_id"], "rows", len(R.rows), "policy", pol, "stable", ok); R.close()
