# P4 - X2 commercial show on the twin (two robots, SONIC co-speech + c12-V2 emotes), 1 rep
Twin x2-twin (MC v1.0.0, preplaying_tick 500 untouched), x2-llm stopped, nothing committed. Code: `twin/{p4_manifest.py, p4_timeline.py, p4_robot_run.py, p4_commercial.py, analyze_p4.py, render_p4_panel.py, p4_mux.py}`. Video: `video/commercial_split.mp4` (1920x1080 = 2x960x1080, 30 fps, 147.6 s, AAC audio = the 19 WAVs at t_k).
Data: `timeline_{ultra,original}.json`, `conversation_clock.json`, `plans/` (anchor manifest + CTC events, ref_*.npz), `traces/{robot}_r1.{csv,json}` + `_qpos.npz`, `metrics/{events.csv,summary.json}`. Each robot = one twin run of the whole conversation (162 s wall) on the same conversation clock; the two are combined only in the video.

## Conversation clock
Clock c = 0 at the start of each run's streaming (after 3 s settle + URS ramp to READY). Line 1 starts at c = 3.5 s (pre-roll so the Hola bow can be dispatched 1.98 s after clock 0). t_k = end(k-1) + 0.30 s, end = start + actual WAV duration (full WAV including its own edge silence; WAV durations differ slightly from the CSV `duracion_s`). t_k: 001 3.50 | 002 13.94 | 003 22.37 | 004 30.01 | 005 39.05 | 006 48.13 | 007 53.66 | 008 61.73 | 009 69.94 | 010 78.26 | 011 84.07 | 012 90.83 | 013 98.55 | 014 107.55 | 015 116.43 | 016 124.87 | 017 131.87 | 018 137.19 | 019 141.28 (ends 145.62; run ends C_END = 147.62).
Ultra speaks 001,003,005,007,009,011,013,015,016,018 (75.8 s); Original 002,004,...,017,019 (60.9 s).

## Timeline per robot
- Arms/waist/head reference (50 Hz on the clock, `plans/ref_*.npz`): speaking = SONIC clip from frame 100 (arms via URS, waist yaw/pitch/roll + head yaw via McBodyPose); 0.6 s min-jerk entry fade READY->SONIC(0) before each line (overlaps the other robot's tail/pause); exit fade 0.9 s to READY (clips end mid-gesture, up to 1.46 rad from READY; 0.6 s gave 4.5 rad/s); consecutive own lines (015->016, gap 0.3 s) cross-faded 1.0 s on the two running clips; listening = READY arms, waist 0. Original head reference = 0 always (measured |head yaw| <= 0.0033 rad). 
- Ultra head: +0.35 rad (toward Original) with 0.5 s min-jerk transitions during Original lines 002,004,006,008,010,012,014,017 (not 019 = farewell); the return to 0 is moved earlier when an Ultra preset follows (head is frozen by the MC during presets): in 004 Ultra looks at Original 30.0-36.6 s, then faces the audience before the heart. Transition time 0.5 s instead of 0.4 s because head speed 1.3 rad/s.
- Emotes (dispatch = CTC anchor - lead_stroke median of c08, handoff at dispatch-0.06 s after the 0.6 s fade to D; all three on the stroke policy, no midpoint needed): 
  | line | anchor (CTC start) | Ultra | Original | dispatch (both, s) |
  |---|---|---|---|---|
  | 001 "Hola" | 3.70 s | BOW 3001/11 | BOW 3001/11 | 1.98 |
  | 005 "nosotros somos un ejemplo" | 39.31 s | HEART 3004/11 (37.23) | APPLAUSE 3017/11 (37.43) | |
  | 019 "despedirnos" | 142.90 s | FAREWELL 3031/11 | FAREWELL 3031/11 | 141.04 |
  Manifest guard speech[start:end]==trigger_text asserted in `p4_manifest.py`; CTC starts: Hola 0.20 s, nosotros 0.26 s, despedirnos 1.62 s into the line.

## Emote results (clock s; `metrics/events.csv`)
| robot | emote | executed==plan | dispatch err ms | stroke - anchor ms | onset - anchor ms |
|---|---|---|---|---|---|
| Ultra | bow 3001/11 | yes | +1 | +1 | see csv |
| Original | bow 3001/11 | yes | +15 | +15 | |
| Ultra | heart 3004/11 | yes | +7 | +7 | |
| Original | applause 3017/11 | yes | +1 | +1 | |
| Ultra | farewell 3031/11 | yes | +4 | +3 | |
| Original | farewell 3031/11 | yes | +19 | +18 | |
Inter-robot sync error (stroke_Ultra - stroke_Original, from two separate runs on the same clock): bow -14 ms, farewell -14 ms, heart vs applause +6 ms (different motions, both on the anchor). set_code 0 in 6/6, player completed 6/6, only the planned motion id seen by the MC in each preset window.

## Gates (per robot; `metrics/summary.json`)
| gate | Ultra | Original |
|---|---|---|
| SONIC lag-4 mean abs, per speaking line (gate 0.015; excluded: windows around presets) | 0.0079-0.0133 (all pass) | 0.0078-0.0115 (all pass; line 019 fully inside the preset window) |
| still arms in speaking lines (v<0.05 rad/s >0.3 s) | 2.2 s of 75.8 (2.9 %) | 0.6 s of 60.9 (0.9 %) |
| whole-upper-body still | 0 s | 0 s |
| pelvis z min | 0.693 m | 0.691 m |
| tilt max | 6.0 deg | 6.0 deg |
| falls | 0 | 0 |
| completed / STABLE after | yes / yes | yes / yes |
| head | tracking mean abs 0.035 rad, max 0.154 (lag 8 ticks), range -0.43..+0.38 | 0 (|yaw| <= 0.003) |
Handoffs (6 presets): idle -> URS 80 ms, fade-in starts <=0.04 rad from D; fade-out max measured arm speed 1.9 rad/s and SD snap 0.4 rad/s in 4/6; fade-in max measured speed 1.6-2.4 rad/s, cmd jerk 68-2724 rad/s^3 (Ultra bow/heart fade-in 2724/1624 because the target reference is SONIC speech motion; c12 V2 had up to 1990). Exceptions: Ultra farewell fade-out/SD snap 4.6 rad/s and Original applause 3.6 rad/s (see deviations).

## Video checks (frames viewed)
t=3.7 s both robots at the bow stroke at the same instant, amber CTC marker; t=40.2 s Ultra heart over head vs Original applause (hands together), Ultra head forward; t=143.0 s both raising the farewell hand together, Original highlighted as speaker; t=33 s (004) Ultra head yaw +0.34 rad toward Original (overlay "head yaw"). Panels rendered one at a time (4429 frames each), then hstack + amix mux (WAV at t_k, 48 kHz).

## Deviations / caveats
1. The pinned stroke times force co-speech truncation by the fade-out: Original applause dispatch (37.43) is 1.3 s before the end of its own line 004 (SONIC arms leave 004 about 2 s early, waist keeps following); Ultra farewell fade starts 0.6 s before the end of its line 018 (140.38 vs 140.98); Original's farewell preset overruns its line 019 by 0.7 s. In these two cases (Ultra farewell, Original applause) the arms still lag the command at the handoff (the SONIC tail is 1+ rad from D) so the SD snap reaches 3.6-4.6 rad/s (no fall, still within the stable envelope, but above c12's 1.3 rad/s; c12 picked a speed minimum, not possible when the dispatch is pinned). A longer fade (0.9 s) for these two or a later handoff check would fix it; not re-run (time box).
2. Ultra head at the farewell start is frozen at -0.34 rad (SONIC head yaw of line 018 at that instant, head frozen during presets): Ultra does not face straight to the audience for the farewell; the head resumes after the preset. Same mechanism: heart start +0.08, bow -0.02. Fix: zero the SONIC head contribution ahead of any Ultra preset (not done).
3. Two separate runs: sync error is measured on the common clock (plus per-run jitter), not on a single clock; both runs used T0 phase random 0-20 ms.
4. Head look-at transition 0.5 s (not 0.4 s); exit fade 0.9 s.
5. Video: single robot model in each panel, no second twin in the same scene; no real TTS audio in the twin (audio muxed at t_k); twin only. The README's 0.18 s edge silence is not trimmed: the full WAV is used and 0.30 s pauses added.
6. 1 rep (no second rep).
