# motor_fix — O3 motor safety fixes + twin validation (TASK B, 2026-10-23)

Fixes applied to a COPY of opt/vab motor in opt/motor_fix/ (no committed file touched; container path /tmp/o3fix).
Diagnosis source: ../diag_fall/REPORT.md (turn-24 fall, preset 1002/2 SM_wave_right_hand, SONIC waist twist 0.237 rad).
Validation: twin x2-twin (MC v1.0.0, preplaying_tick 500). Metrics: analyze_fix.py (CPU-only) over out/{t24_fix,full_fix}; iso from out/iso_preset/iso1.json.

| # | Fix | Implementation (o3_motor_fix.py / emote_lib_fix.py) |
|---|---|---|
| 1 | re-ADD URS only after player idle + 0.2 s settle | SD+0.3 s re-ADD (o3_motor_vab.py:185) removed; new phase preset_settle; register_source(65) only in urs_wait after URS active, via worker |
| 2 | no waist stream during presets; global SONIC waist clamp | wref=None (McBodyPose zeros=hold) in preset_wait/preset/preset_settle/urs_wait; clamp_w(): \|twist\|,\|bend_f\|,\|bend_s\| ≤ 0.12 rad + slew ≤1.2 rad/s everywhere (incl. Smith-predictor lookahead and entry blend) |
| 3 | fall interlock | 3-tick debounce; pelvis<0.62 m always; tilt>12° EXCEPT animation-player-busy in preset with pelvis≥0.66; tilt>25° hard cap; on trigger: stop publish, hold, DELETE+SD (worker), send_evt(fall), all further turns refused (turn_refused), run marked unsafe |
| 4 | pre-preset gate | dispatch only if tilt≤6°, arms ≤0.05 rad of D, MC SD+STABLE(4)+RUNNING(100), zero commanded locomotion; extend ≤1 s then preset_gate_skip → URS re-entry without preset |
| 5 | 50 Hz never blocks on services | Svc worker thread (sequential, budgeted, svc_timeout events) runs delete_source/request_mode/register_source; loop only submits+polls |
| 6 | locomotion lockout | Rig subscribes /aima/mc/locomotion/velocity; turn start refused unless last cmd (0.5 s window) zero/absent AND measured odom \|v\|≤0.08, \|wz\|≤0.15 AND MC STABLE+RUNNING; 2 s grace then turn_refused; startup asserts |

KEY DECISION (interlock tuning): raw tilt>12° false-positives on vendor bow 3001/11 — c12 V0 (waist free, preset-only) peaked tilt 20.97°/pelvis 0.693 with 0 falls. The turn-24 fall signature was tilt>12° WITH pelvis sinking (0.66→0.49) → interlock uses that coupling (exception: player busy in preset AND pelvis≥0.66; hard cap 25°). Deviation from the literal 12° spec, documented. First full-replay attempt (out/full_fix_motor.{csv,json}, 02:13, pre-tuning) tripped the interlock on the turn-0 bow (tilt 14.2°/pelvis 0.695, no real fall) and aborted fail-safe exactly as designed (stop publish → hold → DELETE+SD → stable, no further turns); after the exception clause (o3_motor_fix.py 02:22) the rerun passed with 0 triggers.

## Results

### A. Isolated preset 1002/2 ×3 from stand (out/iso_preset)
| rep | dur (dispatch→idle) | tilt at idle | pelvis at idle | final |
|---|---|---|---|---|
| 0 | 5.408 s | 2.11° | 0.6889 m | stable, STAND_DEFAULT |
| 1 | 5.385 s | 2.11° | 0.6889 m | stable, STAND_DEFAULT |
| 2 | 5.385 s | 2.11° | 0.6889 m | stable, unsafe=false, tilt 1.98°, pelvis 0.6887 m |
→ 1002/2 alone is benign; the turn-24 fall needs the v2 SONIC waist stream context (matches diagnosis cause #1).

### B. Turn-24 replay, the exact fallen turn ×3 (out/t24_fix, B_r1_external_front_t00_greeting)
| metric | value | Codex v2_o3 turn 24 |
|---|---|---|
| falls / unsafe | 0 / false | fell (pelvis 0.489→0.043 m) |
| tilt max / pelvis min | 4.61° / 0.6926 m | 72.3° / 0.064 m (97.3° later) |
| turns done / refused | 3 / 0 | run continued 36 s on the floor |
| executed == contract | 3/3 | n/a |
| u_ nonzero rows in preset phases | 0 / 891 (waist held) | streamed, u_bend bang-bang ±0.3 |
| wref twist/bend max | 0.0003 / 0.0045 rad | 0.237 rad twist |
| jitter p50/p95/max, %≤5 ms | 0.84 µs / 1.34 µs / 1.72 ms, 100% | gaps 81/50/132 ms on blocking svcs |
| transition peak arm speed / cmd jerk | 2.05 rad/s / 15.6 rad/s³ | — |
| handoff svc max (worker) / svc timeouts | 26.2 ms / 0 | 132 ms blocking in 50 Hz thread |
| interlock triggers (true/false) | 0 / 0 | no interlock existed |
| stroke−anchor | median −967 ms (−976..−962) | — |

### C. Full replay, 5 visits × 6 turns = 30 turns, 19 presets (out/full_fix)
| metric | value |
|---|---|
| turns done / refused / fell | 30 / 0 / false; unsafe=false, aborted=false |
| falls (interlock ∨ pelvis<0.60 ∨ tilt>25°) | 0 |
| tilt max / pelvis min (all 30 turns) | 20.29° (turn-0 vendor bow only) / 0.6906 m |
| tilt>15° rows | 100 (2 s, turn-0 bow, pelvis≥0.69 → covered by exception) |
| interlock triggers | 0 true, 0 false |
| jitter p50/p95/p99.9/max, %≤5 ms | 0.57 µs / 1.12 µs / 13.8 µs / 2.18 ms, 100% |
| executed == contract (presets) | 19/19 (dispatch/idle/urs_active/handoff_done 19 each) |
| preset gate skips / aborts / svc timeouts | 0 / 0 / 0 |
| handoff svc dur max (worker thread) | 15.9 ms (loop never blocked) |
| waist during preset phases | u_ nonzero 0/5349 rows; wmeas absmax 0.323 rad = vendor animation's own waist (as diag), not streamed |
| wref twist / bend max (SONIC, clamped) | 0.120 / 0.120 rad (= clamp ceiling; raw v2 stream asked 0.237) |
| transition peak arm speed (p95) / cmd jerk max | 2.24 rad/s (2.00) / 40 rad/s³; limiter hits 937/12428 ticks (vmax 2.0) |
| stroke−anchor | median −606 ms, range −1024..−285 (n=19) |
| sender timing | lag_max 2.9 ms, n_late 0, slack_min 0.247 s |
| final state | stable=true, tilt 4.07°, pelvis 0.6925 m; exit mode URS; post-run probe: MC STAND_DEFAULT, status 100 |

### D. vs Codex failed run (../vab/out/v2_motor/v2_o3_new/summary.json)
| metric | Codex (unfixed) | motor_fix |
|---|---|---|
| outcome | partial_failed_safety: fall at turn 24, turns 25-30 replayed on the floor, 3001/11 rejected | 30/30 turns valid, 0 falls |
| pelvis min / tilt max | 0.0435 m / 97.34° | 0.6906 m / 20.29° (vendor bow) |
| MC "Detect Falling" | 62 warns, no safe latch | never reached (interlock upstream) |
| schedule jitter p50/p95/max, %≤5 ms | 1.9 µs / 7.2 µs / 161.5 ms, 98.85% | 0.57 µs / 1.12 µs / 2.18 ms, 100% |
| limiter hits before fall | 778/5144 active (15.1%) | 937/12428 rows (7.5%), non-causal |
| waist in presets | streamed (wmeas bend ±0.33 fighting wref +0.04) | held (wref=None), SONIC clamped ≤0.12 rad |
| blocking svc calls in 50 Hz | 81/50/132/59 ms gaps | worker thread; max observed 26 ms off-loop |

## Remaining risks
1. Bow margin: vendor bow 3001/11 peaks 20.3-21° tilt, ~4-5° from the 25° hard cap; the tilt>12° exception depends on player_state busy + pelvis≥0.66. A false-positive abort during a bow remains possible (fail-safe direction, demonstrated 02:13); a real fall during a bow with pelvis≥0.66 delays detection until pelvis<0.62 or tilt>25° (3-tick debounce).
2. stroke−anchor is negative (median −606 ms, −1024..−285 full; −967 t24): the gesture stroke lands before the audio anchor (dispatch lead D_a=anchor−0.92 s + preplay blend); analyzer notes clock-mapping ambiguity — anchor alignment intent should be confirmed with the contract owner.
3. Waist clamp 0.12 rad/slew 1.2 is a heuristic from the c12 envelope (0.113 rad); it clips expressive v2 SONIC streams (0.237 requested) — data-side audit (diag fix #6) still pending so generation stays inside the envelope.
4. MC vendor issues unfixed: SM_wave_right_hand.csv load overruns the 2 ms task by 24.9 ms; "Detect Falling" only warns (no damping/latch). Report to vendor pending (diag fix #5).
5. Transition dynamics at the validated ceiling: cmd jerk hits 40 rad/s³, measured arm speed 2.24 rad/s vs vmax 2.0 limiter (937 hit ticks); acceptable in sim, verify on real hardware.
6. Single rep of the full 30-turn replay, twin-only (MC v1.0.0 sim); interlock abort path exercised once (02:13 attempt) but full fall-recovery (re-stand + resume) not tested.
7. Fixes live in opt/motor_fix (container /tmp/o3fix) — merge into opt/vab and deploy scripts is a pending decision; nothing committed to git.

Twin container x2-twin stopped after the run to free RAM (post-run probe: STAND_DEFAULT/100).
