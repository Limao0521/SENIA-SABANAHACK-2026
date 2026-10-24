# O3 - brain/motor split on the X2 twin (x86, 2026-10-22)
Single runs (B1 = split 12, B2 = split 14, both 13 turns). Code: `brain_server.py` `brain_run.py` (host, physicalai), `motor.py` `o3common.py` (container), `partA_run.py` `analyze_A.py` `analyze_B.py`, `runA.sh` `runB.sh` `start_motor.sh` `probe_mode.sh`.
Metrics: `metrics_A.json`, `metrics_B1.json`, `metrics_B2.json`; raw: `out/A_*`, `out/B1`, `out/B2` (motor CSV 50 Hz rows + events JSON, brain.json, per-turn npz). Nothing committed. Twin left in STAND_DEFAULT/STABLE (probe: STAND_DEFAULT, stable); x2-llm off, nothing else running.

## Part A - V2 hull models at the X2 level: PASS
M2 serial, native_qp 15 Hz, fast prefix, armed, 10 clips. V1 = `ik_v1/sonic_v1` mjb, V2 = `ik_v2/sonic_v2` mjb (3.11). V1 run twice = bit-identical (noise floor 0.0).
| V2 vs V1 | value | gate |
|---|---|---|
| arm RMSE (actual SONIC, motion part) mean / worst clip | **0.0072 / 0.0164 rad** (max abs single tick 0.18) | <= 0.02 (0.07 online-vs-offline, 0.20 seed) |
| waist / head RMSE | 0.0025 / 0.0021 rad | |
| arm jerk ratio V2/V1 | 1.002 (0.98-1.03) | |
| tilt max / mean-of-max (deg) V1 -> V2 | 11.94 / 8.75 -> 11.79 / 8.67 | |
| pelvis min | 0.626 -> 0.626 m | |
| falls; GMR QC (finite, vel, selfcol, unexpected limits) | 0/10 both; 10/10 pass both, selfcol max 0.074 both | |
| GMR qpos | mean arm RMSE 0.0027 rad; max single-frame 0.13 rad (c004) | |
| first command | 0.529 (V1) vs 0.520 s (V2) | |
Verdict: V2 accepted for the brain (used in Part B). Caveat: c005/c018/staff clips sit at 0.014-0.016 rad (chaos amplification of the 1e-5 contact differences), 1 of 10 clips (guest_t01) identical.
SONIC base dynamics (arms, 10 clips, 50 Hz diffs): speed p99 / p99.9 / max = 1.86 / 2.82 / 4.52 rad/s; accel 23 / 40 / 111 rad/s^2; jerk p99 1.3e3, max 5.2e3 rad/s^3. Offline limiter test: 2 rad/s limits 8.8 % of ticks (rmse vs raw 0.0054, max dev 0.32 rad); **0.35 rad/s limits 99 % of ticks, rmse 0.138 rad (max 0.20 per clip, 1.2 rad peak deviation): incompatible with SONIC gestures** (only usable as a safe-mode ramp).

## Part B - architecture
- Twin container has `--network none`: no TCP. Channel = **AF_UNIX stream socket on the bind-mounted `/tmp/.X11-unix/o3_motor.sock`** (same path host/container, 0777), framed (type, len, payload): JSON controls + binary tick chunks (`<turn,k0,n,t_send>` + n x 19 float32 = arms14, waist3, head2 actual SONIC joints). Host and container share CLOCK_MONOTONIC, so every stamp is absolute: T_a0 (audio sample 0), t_send, arrival.
- BRAIN (host, 1 process, physicalai, V2 models, serial pull chain + first-block split): sink callback ships every batch of new ticks immediately (one chunk per GMR solve, ~78 chunks/turn). When the first tick exists it sends `turn` {prefix_n=27, T_a0 = t_first_tick + lead + guard 0.15 s, preset params}, then chunks, then `turn_end`. Waits for motor `turn_done`, +1.0 s idle gap, next turn.
- MOTOR (container, numpy only): thread 1 = socket receiver (per-turn buffers, arrival stamps); thread 2 = single 50 Hz controller (sleep + busy-wait last 2 ms) owning rclpy: URS arms (source 65, via limiter), McBodyPose waist/head (domain 77, c07/c12 Smith-predictor, reference looked ahead LAG ticks on the audio clock), c10 preset helper subprocess, logger (jitter, publish time, limiter flags, underruns). URS stays active from startup (min-jerk to READY) through all 13 turns; between turns it streams READY and waist/head 0 (no mode change between SONIC turns). SONIC turn = tick 0 at T_a0 - 27*20 ms, 0.3 s min-jerk blend READY->stream, SONIC ticks at audio-clock index 27 + t/20 ms (linear interp), at the end a min-jerk exit to READY (T = max(0.8 s, 1.875*d/1.2)). Limiter: per joint, v <= 2 rad/s AND a <= 40 rad/s^2 (about the SONIC p99.9), braking-profile follower. Preset turns = c12 V2 path (fade to D 0.6 s, handoff at local speed minimum +-0.3 s, DELETE+SD, preset, URS re-entry, fade-in 0.7 s from the measured pose at the current audio time), reusing the c12 logic (re-implemented in one controller, streaming buffer instead of a precomputed array). Preset turn audio start is delayed by the fade lead (1.9-2.2 s, c12 settle rule).
- Memory: motor ~ tens of MB; brain unchanged (V2 models: IK 12 MB / SONIC 17 MB mjb instead of 227+232).

## Part B - results (B2, 13 turns: 10 SONIC + bow 3001/11, heart 3004/11, applause 3017/11)
Latency (all per turn, mean / max):
| item | value |
|---|---|
| first command (request -> first tick), no split / split 12 / **split 14** (15 runs each, interleaved, 4 s audio) | 0.559+-0.021 / 0.561 (no gain: first command needs 13 source frames, 12 is one short) / **0.421+-0.013 s (-0.138 s)**; 13: 0.423, 16: 0.429 |
| same on the 13 real turns | B1 (split 12) mean 0.58 s; B2 **0.442 s (0.422-0.477)** |
| frame conversion cost measured | 3.4 ms/frame (12 fr 45 ms, 52 fr 172 ms) |
| first motion command at the motor after request (guard 0.15 s + entry) | 0.68 s (0.64-0.75) |
| first visible arm motion (measured joints > 0.03 rad) after request | **0.79 s (0.74-0.87)**; precedes audio start by 0.36 s (SONIC's own entry ramp) for SONIC turns, 1.7-1.95 s for preset turns |
| audio start after request (T_a0) | SONIC 1.12-1.2 s (first_cmd + 0.54 prefix + 0.15 guard); preset turns 2.6-2.8 s (fade lead) |
| brain->motor lag (send -> arrival, socket) | mean 0.47 ms, max 4.3 ms |
| tick slack (arrival before due time) | min 0.023 s (mean-of-min 0.4 s), **0 late ticks, 0 underruns** in 13 turns (guard 0.15 s is enough; B1 min 0.148) |
| brain RTF | 0.59-0.65 |
Safety (twin): **0 falls**, pelvis min 0.6926 m (all phases), tilt max 6.10 deg overall (SONIC turns 5.5, idle 5.9, preset turns 6.1) - inside the 4c/4e ranges (<= 6-8 deg, >= 0.50 m). Brain-sim: 0 falls, tilt max 11.8 deg, pelvis 0.626 (the sim, not the twin).
Motor 50 Hz: wake jitter mean 1 us, p99 3 us, max 0.2 ms; publish ~0.24 ms (max 2.4 ms); period std 0.52 ms, p99 |dev| 1.3 ms. **22-23 ticks (of 9049) deviate > 5 ms (max 11 ms), all inside the preset transitions** (blocking DELETE/request_mode/URS calls, SD->URS re-entry); SONIC turns and idle: none > 5 ms. So the 5 ms gate is met outside preset hand-offs and fails at them (max 11 ms for a 1-2 tick pair).
Transitions (max over occurrences, window -0.5..+1 s; speeds rad/s, jerk rad/s^3; SONIC base = steady SONIC segments, same quantity):
| transition | n | cmd peak speed | cmd jerk p99 | meas peak speed | meas jerk p99 | tilt / pelvis |
|---|---|---|---|---|---|---|
| SONIC base (reference) | 10+ | 2.00 (limiter; raw 4.39) | 1394 | 3.77 | 2099 | |
| idle -> SONIC | 13 | 2.00 | 1453 | 2.44 | 1718 | 4.9 / 0.69 |
| SONIC -> exit ramp start | 11 | 2.00 | 1994* | 3.79 | 4315* | 5.3 / 0.69 |
| exit end -> idle | 13 | 1.18 | 32 | 3.79* | 4315* | 5.9 / 0.69 |
| SONIC -> preset (fade-out) | 3 | 1.80 | 1787 | 2.11 | 1682 | 4.7 / 0.69 |
| handoff DELETE+SD | 3 | 1.80 | 190 | 2.11 | 1682 | 4.7 / 0.69 |
| preset -> URS request | 3 | 2.00 | 1299 | 2.85 | 2163 | 5.3 / 0.69 |
| URS re-entry fade-in start | 3 | 2.00 | 1304 | 2.39 | 2163 | 5.3 / 0.69 |
| fade-in end -> SONIC | 1 | 2.00 | 1259 | 2.39 | 2163 | 4.7 / 0.69 |
(*) windows that straddle the last SONIC ticks, so they contain base motion (a bit above base p99); the ramp part itself has cmd jerk p99 32 rad/s^3 and speed 1.18. All other transitions are <= SONIC base in speed and jerk. Peak jerk of commands hits 4000 = the limiter's accel bang-bang (2*A/dt), vs SONIC raw 4315 (peak), i.e. not above base.
READY idle: command step between idle ticks exactly 0.0 (3640 idle ticks); measured arms stay within 0.025 rad of READY after 1 s; idle->SONIC max command step per tick 0.04 rad; **the continuous READY stream removes the entry snap: no mode change, no D-pose snap between SONIC turns** (c12-style SD/URS cycling occurs only in the 3 preset turns).
Limiter on the twin: 16.7 % of SONIC ticks limited (v 2 rad/s), rmse raw-vs-out 0.032 rad, max 0.32 rad (SONIC reference peaks 4.4 rad/s; the 2 rad/s limit is conservative and changes the gesture visibly in fast strokes). 0.35 rad/s: see Part A (not viable for gestures).
Preset events: services accepted (code 0, state 400, RTT 1 ms); dispatch jitter vs plan 5-15 ms (tick-quantised, +1 tick); handoff DELETE 5 ms + SD 5 ms; URS re-entry 80 ms after preset idle; p0 deviation from D 0.024-0.036 rad (no snap at re-entry); local-speed-minimum handoff found for all 3 (t_h = D_a-0.26/-0.26/-0.26 s).

## Failures / limits / caveats
1. First-block split only helps with >= 13 source frames (needed for the 0.9 s SONIC lookahead at 15 Hz): split 8 and 12 give no gain (B1 run used 12 and shows no improvement); 14 gives -0.138 s as predicted. Not retried with a different window-0 ST length.
2. >5 ms motor period excursions (max 11 ms) at preset hand-offs (blocking service calls inside the 50 Hz thread). Fix: move DELETE/request_mode/URS into a worker thread. Not done.
3. Peak measured joint speed/jerk during SONIC itself (3.8 rad/s, jerk p99 2.1e3) is the SONIC base; the twin's measured arm state is noisy so jerk comparisons are indicative only.
4. Limiter amax = 40 rad/s^2 and vmax = 2 rad/s were chosen from SONIC p99.9; 17 % of SONIC ticks are clipped - the visual cost of 2 rad/s was not judged (no video).
5. Audio clock is virtual (no TTS/audio on the twin); the guard 0.15 s assumes the brain stays ahead (RTF 0.6, slack min 23 ms on the slowest turn: thin on a slower CPU/Orin). Waist/head via McBodyPose are direction commands with 0.06-0.45 s lag (unchanged c07 controller; waist tracking error not re-evaluated here).
6. Single runs, no repeat variance. Preset turns use the SONIC chain on the real turn audio (not the stored offline arms of c12), so they are not numerically comparable to c12 tables.
7. The socket lives in `/tmp/.X11-unix` (the only RW bind mount not touching repos); removed after the run. motor.py is copied into the container (`/tmp/o3`) by hand (`podman cp`), as with the other twin scripts.
