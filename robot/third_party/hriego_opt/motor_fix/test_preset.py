#!/usr/bin/env python3
"""ISOLATED preset safety test (runs inside x2-twin, from /tmp/o3fix; uses the motor_fix emote_lib).
STAND_DEFAULT + STABLE -> SetMcPresetMotion MOTION/AREA x REPS (no SONIC, no input source, no waist streaming) -> stay in STAND_DEFAULT.
50 Hz logging; fall interlock tilt > 12 deg or pelvis < 0.62 m (3-tick debounce) -> abort, request SD, mark unsafe.
Pre-dispatch gate per rep: MC STABLE+RUNNING in SD, tilt <= 6 deg, zero commanded locomotion.
usage: python3 test_preset.py MOTION AREA REPS OUT.csv"""
import json, os, sys, time
import numpy as np
from emote_lib import Rig, SD, TICK
from x2_ultra.real.control import ACTION_RUNNING, FSM_STABLE

MOTION = int(sys.argv[1]); AREA = int(sys.argv[2]); REPS = int(sys.argv[3]); OUT = sys.argv[4]
TILT_FALL, PELVIS_FALL, FALL_DEB = 12.0, 0.62, 3
TILT_GATE, GATE_T = 6.0, 4.0

R = Rig("preset_iso"); L = R.link; ev = R.ev
unsafe = False
try:
    assert L.wait_stable(10.0), "MC not STABLE"
    assert L.observed(graph=False).mode == SD, "mode is not STAND_DEFAULT"
    assert R.loc_zero(), f"locomotion commanded at startup: {R.loc_cmd()}"
    for rep in range(REPS):
        # gate: wait up to GATE_T s for tilt <= 6 and MC STABLE/RUNNING/SD
        t0 = time.monotonic(); tilt = R.pose["tilt"]; obs = L.observed(graph=False)
        while time.monotonic() - t0 < GATE_T:
            obs = L.observed(graph=False); tilt = R.pose["tilt"]
            if obs.mode == SD and obs.fsm == FSM_STABLE and obs.action_status == ACTION_RUNNING and tilt is not None and tilt <= TILT_GATE and R.loc_zero():
                break
            time.sleep(TICK)
        else:
            ev("gate_fail", rep=rep, mode=obs.mode, fsm=obs.fsm, status=obs.action_status, tilt=tilt); unsafe = True; break
        R.preset_set(MOTION, AREA); t_disp = time.monotonic(); ev("dispatch", rep=rep, motion=MOTION, area=AREA)
        seen_busy = False; idle_deb = 0; fall_deb = 0
        while True:
            time.sleep(TICK)
            obs = L.observed(graph=False); tilt = R.pose["tilt"]; pel = R.pose["z"]
            R.log("preset_iso", extra={"rep": rep})
            if (tilt is not None and tilt > TILT_FALL) or (pel is not None and pel < PELVIS_FALL): fall_deb += 1
            else: fall_deb = 0
            if fall_deb >= FALL_DEB:
                unsafe = True; ev("fall_interlock", rep=rep, tilt=tilt, pelvis=pel); break
            if obs.player_state not in (None, 0): seen_busy = True
            idle_deb = idle_deb + 1 if (seen_busy and obs.player_state == 0) else 0
            if idle_deb >= 2: ev("idle", rep=rep, dur_s=time.monotonic() - t_disp, tilt=tilt, pelvis=pel); break
            if time.monotonic() - t_disp > 25.0: ev("busy_timeout", rep=rep, seen_busy=seen_busy); unsafe = True; break
        if unsafe: break
        t0 = time.monotonic()   # 2 s settle logging
        while time.monotonic() - t0 < 2.0:
            time.sleep(TICK); R.log("settle", extra={"rep": rep})
            tilt = R.pose["tilt"]; pel = R.pose["z"]
            if (tilt is not None and tilt > TILT_FALL) or (pel is not None and pel < PELVIS_FALL):
                unsafe = True; ev("fall_interlock_settle", rep=rep, tilt=tilt, pelvis=pel); break
        if unsafe: break
        ev("rep_done", rep=rep, tilt=R.pose["tilt"], pelvis=R.pose["z"])
finally:
    for _ in range(10): R.send_body_pose(np.zeros(4)); time.sleep(TICK)
    err = None
    try:
        if L.observed(graph=False).mode != SD: L.request_mode(SD, busy_retry_s=8.0)
    except Exception as e: err = str(e); ev("final_sd_err", err=err)
    ok = L.wait_stable(10.0)
    ev("final", stable=bool(ok), unsafe=bool(unsafe), mode=L.observed(graph=False).mode, tilt=R.pose["tilt"], pelvis=R.pose["z"])
    R.dump(OUT); json.dump(dict(events=R.events, unsafe=bool(unsafe), motion=MOTION, area=AREA, reps=REPS), open(OUT.replace(".csv", ".json"), "w"), default=str)
    print("preset_iso done reps", REPS, "unsafe", unsafe, "stable", ok, "tilt", R.pose["tilt"], "pelvis", R.pose["z"], flush=True)
    try: R.close()
    except Exception: pass
    os._exit(0)
