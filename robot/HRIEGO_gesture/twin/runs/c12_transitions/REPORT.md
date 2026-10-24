# c12 - SONIC co-speech <-> SDK preset transitions inside one speaking turn (X2 twin, quantitative, no video)
Twin x2-twin (MC v1.0.0, `preplaying_tick` 500 untouched), `x2-llm` stopped, workers stopped before the twin started. Code: `twin/{phaseA_sonic_emote.py, emote_lib.py, stream_emote_turn.py, run_c12.py, analyze_c12.py, probe_c12.py, c12_probe_report.py, c12_io.py}`. Metrics: `metrics/{takes.csv,summary.json,phaseA_status.json}`; traces `traces/`; probes `probes/`. Nothing committed.

## Phase A (no twin)
20/20 emote turns got a SONIC base with the warm workers (S1-S4) + c07 `prep_arms.py` into `visits_v2/<rank>/sonic/<turn_id>/` (existing NONE/POINT outputs untouched). All GMR PASS on seed 1, 4.4-5.8 s per turn. MemAvailable 17.1 -> 10.9 GB with the three workers resident, no swap growth.

## MC probes (twin MC v1.0.0; 50 Hz logs in `probes/`)
1. **SetMcPresetMotion right after `SetMcInputSource DELETE`?** The service ACCEPTS it (code 0, state 400) 61 ms after DELETE even with the MC still in UPPERBODY_REMOTE_SPLIT, and the player runs (state 1 then 2, 1.0 s preplay as c08) - but in URS the preset does NOT move the arms (arms held at the last command for the whole 4.5 s, probe A). The arms only play the preset when the MC is in STAND_DEFAULT: `DELETE -> SetMcAction STAND_DEFAULT` (5 ms service call, mode observed STAND_DEFAULT 60-80 ms later, STABLE immediately) and the preset set can go out ~50 ms after DELETE, before the mode is even observed (probe B/D: accepted 11-61 ms after DELETE, executed correctly). So no waiting for STABLE is needed; the whole handoff is ~10-60 ms.
2. **Arms at DELETE:** they HOLD the last command (speed 0.9 -> 0.0 within 0.1 s, no jump). **The SD request is the jump:** with the arms away from the planner default D (shoulder_pitch 0.4, elbow -1.2, all else 0, = preset CSV first frame, NOT SONIC's READY 0.2/0.2/-0.6), the MC snaps them to D in ~0.1 s (wrist yaw 0.78 -> 0.07 in 0.07 s, about 10 rad/s) with 0.5 s of ringing (probe C, no preset). The MC's 1.0 s preplay blend therefore does NOT absorb the approach (the SD takeover happens first), so the planned "DELETE before reaching rest" of V2 is NOT usable; both V1 and V2 fade to D (not READY) BEFORE the DELETE. With the arms at D at the handoff the snap is only 0.5-1.2 rad/s.
3. **McBodyPose waist/head during a preset:** the waist FOLLOWS (twist/bend commands move it during area 2 and area 11 presets) once a source is (re-)ADDed in SD (ADD OK 0.3-0.6 s after the handoff); the HEAD does NOT (frozen at the value the preset leaves it, for both area 2 and area 11, and resumes 0.1-0.5 s after the preset ends). Gaze therefore cannot follow SONIC during a preset (stays forward, as requested).
4. **After the preset (player idle):** SetMcAction URS answered in 5 ms, URS active 80-100 ms later, ADD 10 ms after that: idle -> URS+ADD = 90-111 ms. The arms are at D (+-0.035 rad) at that instant, so the fade-in starts from the measured pose with no jump. (Raw: `probes/probe{A,B,C,D}*`, `c12_probe_report.py`.)

## Variants (all with the same dispatch policy and timing)
Policy per turn: preset STROKE on the CTC anchor (dispatch = anchor - lead_stroke_s, c08 medians), limited by the 1.5 s start-of-audio delay cap; fallback midpoint onset/stroke, then onset. Used: stroke 19/20 turns, midpoint 1/20 (external_t00_greeting, anchor 0.8 s vs lead_stroke 2.22 s -> stroke lands 524 ms after the anchor by construction), onset 0.
- **V0** stand still -> preset -> still (4c behaviour, SONIC absent).
- **V1** SONIC -> linear 0.8 s ramp to D -> DELETE, SD, preset (at the planned dispatch) -> URS, ADD -> linear 0.8 s from the measured pose to SONIC at the CURRENT audio time. Waist/head faded to zero with the ramp and held zero during the preset.
- **V2** SONIC -> min-jerk amplitude fade cmd = D + (1-w)(sonic - D) over 0.6 s ending at a handoff chosen at the local minimum of SONIC arm speed within +-0.3 s of the planned dispatch (the arms then rest at D under SD until the planned dispatch instant, so the preset timing is not moved); waist/head keep SONIC through McBodyPose during the preset; fade-in 0.7 s min-jerk from the measured pose at the current audio time.
When the planned handoff lies before audio start (dispatch < 0; 14/20 turns, because lead_stroke 1.6-2.2 s exceeds the anchor 0.35-2.8 s) the SONIC part before the preset is only the static start pose, so the fade is trivial there.

## Subset comparison (6 turns x 2 reps = 12 takes per variant; medians over takes, `metrics/summary.json`)
| metric | V0 | V1 | V2 |
|---|---|---|---|
| falls / pelvis z min (m) / tilt max (deg, median) | 0 / 0.693 / 4.6 | 0 / 0.693 / 5.1 | 0 / 0.693 / 5.3 |
| executed == contract | 12/12 | 12/12 | 12/12 |
| arm-still time inside the turn (v<0.05 rad/s for >0.3 s), s (max) | 2.17 (4.24) | 1.42 (2.90) | 0.00 (1.92) |
| whole-upper-body still (arms+waist+head yaw), s (max) | 2.17 (2.98) | 1.19 (1.42) | 0.00 (0.62) |
| share of the turn with SONIC driving the arms | 0 | 0.17 | 0.19 |
| turns that resume SONIC after the preset | 0 | 6/12 | 6/12 |
| fade-out: max measured arm speed near handoff, rad/s (max) | - | 0.74 (1.08) | 1.60 (2.29) |
| fade-out: max cmd jerk, rad/s^3 (max) | - | 0.17 (716) | 96 (1810) |
| SD snap speed after handoff, rad/s (max) | - | 0.61 (0.91) | 0.50 (1.26) |
| fade-in: max measured arm speed (max) | - | 2.25 (2.55) | 2.44 (2.54) |
| fade-in: max cmd jerk (max) | - | 1917 (2362) | 1438 (1991) |
| SONIC own levels (turn clips): max speed / p99 jerk | 2.31 (3.10) / 2539 (3840) | same | same |
| DELETE -> preset accepted, ms | - | 11 | 11 after the late set (the set is sent at D_a; handoff shift vs plan -0.25..+0.18 s) |
| DELETE -> SD observed, ms | - | 60-80 | 60-80 |
| preset idle -> URS active / ADD done, ms | - | 80 / 91 | 80-100 / 91-111 |
| stroke - anchor, ms (median, max) | 7 (31) | 20 (39) | 20 (182) |
| onset - anchor, ms (median) | -658 | -716 | -721 |
| SONIC lag-4 tracking before / after the preset (mean abs, rad; gate 0.015) | - | 0.006 / 0.008 (max 0.028) | 0.006 / 0.008 (max 0.027) |
The preset onset is ~0.5-1.0 s before the anchor and the stroke on it (onset values for V1/V2 are noisier because the detector sees settling/waist coupling; stroke is the aligned quantity).

## 20-turn run (V2, 1 rep, `takes.csv` rep 3)
20/20 completed, executed==contract 20/20, falls 0, pelvis z min 0.692-0.693 m, tilt max 6.1 deg, MC STABLE after every take. Stroke - anchor median 14 ms (<=39 ms in 17/20; 3 turns above: external_t00 +524 ms midpoint policy by construction; external_t05_farewell +288 ms, regular_t05_farewell +228 ms, see failures). Policy: stroke 19, midpoint 1.
**SONIC resumed after the preset in only 5/20 turns** (regular_t02 @4.22 s, staff_t00 @4.08, staff_t02 @2.95, staff_t03 @2.54, vip_t00 @3.72): in the other 15 the preset (4.5-6.5 s incl. 1.0 s preplay) lasts until the end of the speech (turns are 3.3-5.4 s), so there is no audio left to resume into; the body is SONIC -> emote -> end (arms released to D by the end of audio). On the 5 resumed turns: fade-in max speed 2.1-2.5 rad/s, max jerk 690-1340 (below SONIC's own p99 2500-3840), SONIC lag-4 tracking after 0.008-0.011 except staff_t02 0.027 (> gate 0.015; the SONIC clip is fast/large there; same value with V1). Arm-still time median 0.44 s (max 2.54 s, staff_t03: the preset hold), whole-body still <=0.32 s.

## Failures / caveats
- Only 5/20 turns have the SONIC -> emote -> SONIC shape; limited by preset length vs short speech, not by the transitions (which take ~0.1 s). A shorter preset set or the preset started later would be needed for the rest.
- V2 farewell (3031) stroke offsets of +180..+290 ms (regular_t05 +228/+181, external_t05 +288) vs V0 +6..+28 ms: cause NOT resolved (candidates: waist following SONIC coupling into the dominant-joint reading, or stroke-threshold sensitivity); V1 farewell was +26..+34 ms, so it is V2-specific. 3029/11 keeps the known head-yaw clamp flag.
- "Still" under V2 is 0 partly because McBodyPose waist motion makes the measured arm speed exceed 0.05 rad/s during preset holds (<0.34 rad/s); the whole-body metric is the fair one.
- Head cannot follow SONIC during presets (probe 3). Blocking service calls cost <=10 ms of 50 Hz stream at the handoff and ~15 ms at the resume.
- Twin only; real robot (MC v1.1.4) not tested. The SD snap-to-default and "preset ignored in URS" are twin-MC observations to re-verify on the robot.

## Recommendation
Use V2 (min-jerk fade to the planner default D, handoff at the local SONIC speed minimum, hold at D until the planned dispatch, waist follows SONIC through McBodyPose, 0.7 s min-jerk fade-in at the current audio time): same executed==contract and stability as V0/V1, near-zero idle, fade-in jerk 40% below V1. Always fade to D (not READY) before DELETE. Fix before adopting: investigate the V2 farewell stroke shift and choose presets whose motion_duration fits inside the speech (or accept that long presets end the turn).

## Commands
`phaseA_sonic_emote.py` (physicalai python, ~2.5 min); twin: `official_sim/twin.sh start --escena vacia --camaras no --sin-lidar --sin-visor --sin-audio`, `stand`, `suelta`; `run_c12.py --turns subset --variants V0,V1,V2 --reps 2`; `run_c12.py --turns all --variants V2 --reps 1 --rep0 3`; `analyze_c12.py`; probes `probe_c12.py MOTION AREA ARMS.npz OUT.csv [--sd-first] [--bp-test] [--no-preset]`.
