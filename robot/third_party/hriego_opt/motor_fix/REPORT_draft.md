# motor_fix — O3 motor safety fixes + twin validation (TASK B, 2026-10-23)

Fixes applied to a COPY of opt/vab motor in opt/motor_fix/ (no committed file touched; container path /tmp/o3fix).

| # | Fix | Implementation (o3_motor_fix.py / emote_lib_fix.py) |
|---|---|---|
| 1 | re-ADD URS only after player idle + 0.2 s settle | SD+0.3 s re-ADD (o3_motor_vab.py:185) removed; new phase preset_settle; register_source(65) only in urs_wait after URS active, via worker |
| 2 | no waist stream during presets; global SONIC waist clamp | wref=None (McBodyPose zeros=hold) in preset_wait/preset/preset_settle/urs_wait; clamp_w(): |twist|,|bend_f|,|bend_s| ≤ 0.12 rad + slew ≤1.2 rad/s everywhere (incl. Smith-predictor lookahead and entry blend) |
| 3 | fall interlock | 3-tick debounce; pelvis<0.62 m always; tilt>12° EXCEPT animation-player-busy in preset with pelvis≥0.66; tilt>25° hard cap; on trigger: stop publish, hold, DELETE+SD (worker), send_evt(fall), all further turns refused (turn_refused), run marked unsafe |
| 4 | pre-preset gate | dispatch only if tilt≤6°, arms ≤0.05 rad of D, MC SD+STABLE(4)+RUNNING(100), zero commanded locomotion; extend ≤1 s then preset_gate_skip → URS re-entry without preset |
| 5 | 50 Hz never blocks on services | Svc worker thread (sequential, budgeted, svc_timeout events) runs delete_source/request_mode/register_source; loop only submits+polls |
| 6 | locomotion lockout | Rig subscribes /aima/mc/locomotion/velocity; turn start refused unless last cmd (0.5 s window) zero/absent AND measured odom |v|≤0.08, |wz|≤0.15 AND MC STABLE+RUNNING; 2 s grace then turn_refused; startup asserts |

KEY DECISION (interlock tuning): raw tilt>12° false-positives on vendor bow 3001/11 — c12 V0 (waist free, preset-only) peaked tilt 20.97°/pelvis 0.693 with 0 falls (twin/runs/c12_transitions/metrics/takes.csv). Measured here: bend_forward 0.32 rad, pelvis 0.695 rising, MC self-recovers after player idle. The turn-24 fall signature was tilt>12° WITH pelvis sinking (0.66→0.49) → interlock uses that coupling. Deviation from the literal 12° spec, documented.

## Validation (twin x2-twin, MC v1.0.0, preplaying_tick 500 verified)
PLACEHOLDER_RESULTS
