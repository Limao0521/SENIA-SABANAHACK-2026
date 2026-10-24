"""P4: run one robot's full-conversation timeline on the twin (inside x2-twin). usage: p4_robot_run.py TIMELINE.json REF.npz OUT.csv QPOS.npz
Arms (URS, 50 Hz) = reference ref_arms(c) (SONIC co-speech / READY listening). Each preset = c12 V2 transition: min-jerk 0.6 s fade of the reference to the planner default D, DELETE URS -> SD,
preset set at the planned dispatch (anchor - lead_stroke), re-ADD source 0.3 s later (waist/head keep following ref_wh via McBodyPose), after the player idles URS+ADD and 0.7 s min-jerk fade-in
from the measured pose onto ref_arms at the CURRENT clock. Waist/head via McBodyPose all the time (head frozen by the MC during presets)."""
import json, sys, time, random
import numpy as np
from emote_lib import *
from x2_ultra.real.control import ACTION_RUNNING
tl_f, ref_f, out, qout = sys.argv[1:5]
TL = json.load(open(tl_f)); Z = np.load(ref_f); ARMS, WH = Z["arms"], Z["wh"]; P = TL["presets"]; C_END = TL["C_END"]
D = np.array([0.4, 0, 0, -1.2, 0, 0, 0] * 2, float)
def interp(a, c):
    f = np.clip(c / TICK, 0, len(a) - 1); i = int(f); j = min(i + 1, len(a) - 1); r = f - i; return a[i] * (1 - r) + a[j] * r
from x2_ultra.twin.model import build_twin_model
from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState
shared = SharedState(build_twin_model("vacia").nq, name=TWIN_SHM_NAME)
R = Rig("p4_" + TL["robot"]); L = R.link; ev = R.ev
meta = dict(robot=TL["robot"], presets=[dict(p) for p in P]); qlog = []; clog = []
try:
    assert L.wait_stable(10.0), "MC not STABLE"; assert L.observed(graph=False).mode == SD
    time.sleep(3.0)                                              # fresh baseline settle before the first preset
    L.request_mode(URS); assert L.wait_mode(URS, 8.0), "URS not reached"
    t_end = time.monotonic() + 2.0
    while L.node.count_subscribers("/mc/upper_body_command") == 0 and time.monotonic() < t_end: time.sleep(0.05)
    L.register_source(65); R.hand_init(); a0 = R.arms_now(); b0 = ARMS[0]
    ramp_n = int(max(2.0, np.abs(b0 - a0).max() / 0.5) / TICK); t_start = time.monotonic() + 0.2
    def tick_wait(nxt):
        time.sleep(max(0.0, nxt - time.monotonic() - 0.002))
        while time.monotonic() < nxt: pass
    for i in range(ramp_n):
        w = 0.5 - 0.5 * np.cos(np.pi * i / (ramp_n - 1)); c = a0 + w * (b0 - a0); tick_wait(t_start + i * TICK); R.publish_arms(c); R.log("ramp", cmd=c)
    PHASE_S = random.uniform(0, TICK); t_loop0 = t_start + ramp_n * TICK + 0.5; T0 = t_loop0 + PHASE_S; meta.update(T0=T0, phase_s=PHASE_S)
    for i in range(25): tick_wait(t_start + (ramp_n + i) * TICK); R.publish_arms(b0); R.log("hold", cmd=b0)
    pi = 0; stage = "wait"; k = 0; reg_src_t = None; seen_busy = False; idle_deb = 0; t_in0 = p0 = None; t_sd = None; max_t = T0 + C_END + 40
    while True:
        tick_wait(t_loop0 + k * TICK); now = time.monotonic(); au = now - T0
        obs = L.observed(graph=False)
        if obs.mc_age_s is None or obs.mc_age_s > 0.5: raise RuntimeError(f"MC state stale at {au:.2f}")
        cur = P[pi] if pi < len(P) else None; ref = interp(ARMS, au); cmd = None
        if cur is not None:
            if stage == "wait" and au >= cur["t_fade0"]: stage = "fade_out"
            if stage == "fade_out" and au >= cur["t_handoff"]:
                c_last = R.last_cmd.copy(); t_del = time.monotonic(); err = L.delete_source(); t_dd = time.monotonic(); L.request_mode(SD, busy_retry_s=8.0); t_sd = time.monotonic()
                cur.update(t_delete=t_del, t_sd=t_sd); ev("handoff", pid=cur["id"], audio_s=au, delete_err=err, delete_dur_s=t_dd - t_del, sd_dur_s=t_sd - t_dd, last_cmd_dev_from_D=float(np.abs(c_last - D).max()))
                stage = "preset_wait"; reg_src_t = None; seen_busy = False; idle_deb = 0
            if stage == "preset_wait" and au >= cur["dispatch"]:
                R.preset_set(cur["motion"], cur["area"]); cur["t_disp"] = time.monotonic(); stage = "preset"
        if stage == "fade_out": w = float(minjerk((au - cur["t_fade0"]) / 0.6)); cmd = D + (1 - w) * (ref - D)
        elif stage == "fade_in":
            w = float(minjerk((au - t_in0) / 0.7)); cmd = p0 + w * (ref - p0)
            if w >= 1.0: cur.update(t_fadein_done=au); stage = "wait"; pi += 1; cur = P[pi] if pi < len(P) else None
        elif stage in ("wait",): cmd = ref
        if cmd is not None: R.publish_arms(cmd)
        for rp in R.poll_replies():
            if rp["op"] == "set": cur.update(set_code=rp["code"], set_state=rp["state"], t_call_helper=rp["t_call"], t_resp=rp["t_resp"])
        if stage in ("preset_wait", "preset") and reg_src_t is None and t_sd is not None and now - t_sd >= 0.3:
            try: L.register_source(65); ev("re_add_source", ok=True, audio_s=au)
            except Exception as e: ev("re_add_source", ok=False, err=str(e))
            reg_src_t = now; R.reset_inflight()
        if stage == "preset":
            if obs.player_state not in (None, 0): seen_busy = True
            idle_deb = idle_deb + 1 if (seen_busy and obs.player_state == 0) else 0
            if idle_deb >= 2:
                cur["t_idle"] = now; cur["idle_audio_s"] = au; L.request_mode(URS); stage = "urs_wait"; ev("urs_request", audio_s=au)
        if stage == "urs_wait" and obs.mode == URS and obs.action_status == ACTION_RUNNING:
            t_urs = time.monotonic(); L.register_source(65); t_add = time.monotonic(); R.hand_init(); p0 = R.arms_now(); t_in0 = (time.monotonic() - T0) + 0.02
            cur.update(dt_idle_to_urs_s=t_urs - cur["t_idle"], dt_idle_to_add_s=t_add - cur["t_idle"], p0_dev_from_D=float(np.abs(p0 - D).max()), t_in0=t_in0); stage = "fade_in"
        wref = u = None
        if not (stage in ("preset_wait", "preset") and reg_src_t is None):
            _, u, wref = R.body_pose_ref(lambda ts: interp(WH, ts), au)
        snap = shared.read(); qlog.append(snap[0].copy() if snap is not None else (qlog[-1] if qlog else np.zeros(1))); clog.append(au)
        R.log(stage if cur is not None or stage != "wait" else "ref", audio_s=au, cmd=cmd, sonic=ref, wref=wref, u=u, extra={"pidx": pi})
        k += 1
        if au > C_END and pi >= len(P) and stage == "wait": break
        if time.monotonic() > max_t: meta["timeout"] = True; break
    meta["completed"] = pi >= len(P)
finally:
    for _ in range(25): R.send_body_pose(np.zeros(4)); time.sleep(TICK)
    err = L.delete_source()
    try:
        if L.observed(graph=False).mode != SD: L.request_mode(SD, busy_retry_s=12.0)
    except Exception as e: meta["sd_final_error"] = str(e)
    ok = L.wait_stable(15.0); meta.update(stable_after=bool(ok), mode_after=L.observed(graph=False).mode, delete_error_final=err, events=R.events)
    meta["presets"] = P; R.dump(out); json.dump(meta, open(out.replace(".csv", ".json"), "w"), default=str)
    if qlog: np.savez(qout, qpos=np.array(qlog), c=np.array(clog), tick_s=TICK)
    print("done", TL["robot"], "rows", len(R.rows), "stable", ok); R.close()
