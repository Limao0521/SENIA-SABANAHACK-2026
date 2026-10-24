# 4e - 3 egocentric missions on the X2 twin (VIP, STAFF, GUEST)
Each mission: walk 1.5 m -> ARC ~90 deg while still walking (vx>0 + wz) -> walk ~1 m -> stop -> STABLE -> 3 s settle -> the 6 visit turns of that visitor (4c execution: presets via c10 synced_playback, POINT via stream_point.py with the SONIC base, NONE via c07 streamer --start-frame 100, waist/head reset after URS turns). Twin x2-twin, preplaying_tick 500 untouched, left in STAND_DEFAULT/STABLE. One run per mission. Metrics: `metrics/missions.json`, `metrics/turns.csv`, `metrics/summary.json`.

## Arc calibration (runs: `calib/c1..c5`)
Walking through AgiBot's MC (`/aima/mc/locomotion/velocity`), closed loop on odometry, one continuous velocity stream (no zero between phases): walk vx 0.4 until 1.5 m, arc vx 0.35 + wz 0.8 until heading change (relative to heading at arc start) reaches 90 - lead, walk vx 0.4 until 1.0 - 0.2 m, stop.
- wz 0.8 rad/s with vx 0.35 turns at once (no in-place stepping; the MC walks the arc): odometry during the arc vx 0.43-0.45 m/s mean (min 0.33-0.39, never < 0), wz ~0.95-1.05 rad/s, radius ~0.45 m, arc ~1.5 s, chord ~0.63 m. No fallback needed.
- The MC keeps turning ~2-5 deg after wz is zeroed, spread 1.8-5.5 deg. Lead 8 deg gave 87.6, lead 5 gave 87.4 / 90.4, lead 4 gave 91.8. Chosen lead 4.5 deg. Heading change at the arc end is 85-86 deg, final 88.7 / 91.7 / 93.4 in the missions.
- Straight-walk heading drift before the arc (wz=0 is not corrected by the MC): -7.9 to +1.9 deg (calib), -2.8 / -6.9 / -0.4 in the missions. Heading change is measured relative to the heading at arc start.
- pelvis z 0.676-0.716 m, tilt deviation max 3.6-4.4 deg during walk+arc, time stop->STABLE 1.96-2.16 s (modes LOCOMOTION_DEFAULT -> STAND_DEFAULT).
Residual spread of the heading after stopping (about +-3 deg) means 90+-5 is met but not guaranteed on every run (calibration 5 runs: 87.4 .. 91.8 with leads 4-5; mission 3 of 3 inside).

## Mission gates (walk part)
| | VIP | STAFF | GUEST | gate |
|---|---|---|---|---|
| walk 1 (m) | 1.507 | 1.509 | 1.507 | 1.5 +-0.15 |
| arc heading change (deg) | 88.7 | 91.7 | 93.4 | 90 +-5 |
| arc duration / chord (s / m) | 1.68 / 0.63 | 1.52 / 0.62 | 1.48 / 0.62 | - |
| arc odom vx mean / min (m/s), radius (m) | 0.43 / 0.33, 0.45 | 0.44 / 0.38, 0.45 | 0.45 / 0.39, 0.44 | vx>0 while turning |
| walk after arc (m) | 1.007 | 0.963 | 1.003 | >= 0.8 |
| stop -> STABLE (s) / mode at first turn | 2.16 / STAND_DEFAULT | 1.96 / STAND_DEFAULT | 2.06 / STAND_DEFAULT | STABLE |
| pelvis z min whole mission (m) | 0.680 | 0.676 | 0.685 | >= 0.50 |
| falls | 0 | 0 | 0 | 0 |
| walk gates | PASS | PASS | PASS | |

## Per-turn gates (4c definitions; 18/18 turns PASS: VIP 6/6, STAFF 6/6, GUEST 6/6)
- Presets (12): executed == contract (motion_id/area, set_code 0, completed, MC motion seen) 12/12; dispatch jitter 0.0 ms; |onset - CTC anchor| <= 100 ms: max 30.8 ms (STAFF t03 CONFIRMATION), others <= 18 ms; first turn after the walk (VIP GREETING +18 ms, STAFF -12 ms, GUEST -18 ms) shows no onset artifact (3 s settle + fresh baseline in synced_playback).
- POINT (3): FK error mean over hold 6.4 / 6.9 / 5.8 deg (gate 15), hold (prog99) 1.82 / 1.66 / 1.56 s (1.0-2.0), jitter <= 0.0 ms. Raw |meas-cmd| outside the gesture 0.011 (lag-4, gate 0.015) in all three; the 4c caveat on lag-free raw error (0.018-0.027) still applies.
- NONE (3): arm tracking lag-4 0.009 / 0.010 / 0.009 rad (gate 0.015).
- pelvis z >= 0.685 m in all turns, falls 0, MC mode returns to STAND_DEFAULT between turns.

## Videos (`video/`, 1280x720, 30 fps, audio = the 6 WAVs at their measured audio-start times)
`m_vip_mission.mp4` (98 s), `m_staff_mission.mp4` (91 s), `m_guest_mission.mp4` (97 s); `m_*_video.json` has the audio-start times. Side/chase camera while walking (smoothed heading) with a top-view odometry inset, transition to a frontal upper-body camera for the interaction. Overlays: phase (WALK / ARC / STOP / SETTLE / TURN k/6 + state), MC mode, odometry vx/wz, pelvis z, gesture (preset name / pointing direction / NONE -> SONIC co-speech), speech text, amber CTC-anchor banner and tick on the progress bar. Inspected frames: walk, arc inset, stop, greeting, pointing right with anchor banner.

## Deviations / notes
- Not a fallback: real arc (vx>0 + wz) used in all missions.
- Heading change after the arc has a +-3 deg spread from MC stopping; 4.5 deg lead tuned on 5 calibration runs, so a mission can miss 90+-5 occasionally.
- VIP GREETING_VIP (3001/11) tilts the pelvis up to 18.3 deg (same preset in 4c: 21-23 deg); not a walking effect.
- The walk script skips `wait_ready` (no battery topic in the twin). Timeline walk start -> first turn ~15 s.
- Audio start for NONE turns = streamer t0 + ramp ticks (c07 start-frame 100 = clip start); POINT = stream_point T0; presets = synced_playback audio_start_monotonic. All on the host/container shared CLOCK_MONOTONIC.
- The twin has no audio output, the WAVs are placed in the MP4 from the logged times.

## Commands
```
export PATH=$X2_TWIN_SHIM_DIR:$PATH; cd HRIEGO_gesture/twin
./calib.sh c5 --turn-lead-deg 4                      # calibration (walk_arc.py + mission_log.py in the container)
python3 mission_4e.py vip|staff|guest                # mission (default lead 4.5, settle 3 s)
PYTHONPATH=<teleop>/src python analyze_4e.py 1; python mission_metrics.py        # env laplace
X2_URDF_ROOT=~/robots/agibot_x2_urdf PYTHONPATH=<teleop>/src MUJOCO_GL=egl python render_4e_video.py vip|staff|guest
```
Files: `mission_4e.py`, `walk_arc.py`, `mission_log.py`, `stream_arms_waist_4e.py` (c07 streamer + t0 json), `analyze_walk.py`, `analyze_4e.py`, `mission_metrics.py`, `render_4e_video.py`.
