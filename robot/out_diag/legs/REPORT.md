# TASK D1 — Leg "reactions" while standing: diagnosis (2026-10-23)

## Question
Why do the legs move/step/jitter in Reto 2 videos while the robot should stand still?
Candidates: SMPL-X (StreamTalk) → GMR retarget → SONIC → show-engine application of upper-body cmds.

## 1) Log evidence (out_reto2/realnames/*_log.csv, 6 personas, standing rows cmd<0.05)
| persona | tilt mean/p95/max ° | pelvis-z p95 dev | corr(tilt,arm_speed) | corr(tilt,arm_jerk) | corr(tilt,waist_delta) | worst turns |
|---|---|---|---|---|---|---|
| Desconocido | 1.14/3.36/4.67 | 13.0mm | +0.04 | +0.10 | **+0.68** | t01_guidance 4.2° |
| Josaed | 1.31/2.99/4.80 | 10.5mm | +0.00 | +0.13 | **+0.47** | t04_dialogue 4.4° |
| Laura | 1.67/3.96/5.50 | 20.1mm | +0.11 | +0.21 | **+0.63** | t00_greeting 5.0° |
| Luis | 1.18/2.51/4.26 | 6.3mm | +0.13 | +0.17 | **+0.43** | t02_dialogue 3.5° |
| Mario | 1.28/2.88/4.28 | 8.9mm | +0.00 | +0.01 | **+0.34** | t01_guidance 4.1° |
| Sebastian | 1.50/3.73/5.67 | 9.2mm | +0.06 | +0.10 | **+0.49** | t01_guidance 4.3° |

Lag-sweep (pooled): corr(waist_delta→tilt) = +0.53 and flat over lags 0–0.4 s (sustained postural
coupling, not transient); corr(arm_speed→tilt) ≈ +0.06→−0.01. Arm-jerk deciles: tilt p50 rises
0.59°→1.3° (arms ≈ 2× the baseline lean; waist dominates the variance). Reactions concentrate in
POINT/guidance and expressive-dialogue turns (arm+waist heavy), never in idle phases.

## 2) Attribution replays (show engine ShowEngine(CLUB), headless, turn rn_luis_t02_dialogue, 6.3 s stream + 2 s preroll, guest disabled)
Script: out_diag/legs/replay_attr.py; results: out_diag/legs/replay_rn_luis_t02_dialogue.json
| cond | legs qvel RMS | tilt mean° | tilt max° | xy drift | pelvis-z std |
|---|---|---|---|---|---|
| g all frozen (baseline) | 0.0001 | 0.47 | 0.48 | 0.0 m | 0.0 mm |
| d waist delta = 0 | **0.026** | **0.72** | 1.07 | **0.015 m** | 1.1 mm |
| f arms frozen at rest | 0.129 | 2.06 | 4.36 | 0.039 m | 5.5 mm |
| b arms = GMR desired | 0.248 | 2.45 | 4.90 | 0.085 m | 7.4 mm |
| c arms low-pass 1 Hz + 1.0 rad/s | 0.288 | 2.18 | 4.39 | 0.152 m | 7.4 mm |
| a as-is (SONIC) | 0.297 | 2.07 | 4.23 | 0.199 m | 7.4 mm |
| e head frozen | 0.299 | 2.10 | 4.13 | 0.241 m | 7.5 mm |
| h waist = GMR instead of SONIC | **0.449** | **2.83** | **6.06** | **0.562 m** | 11.4 mm |

Reads:
- Baseline (g) is perfectly still → all leg reactions come from the upper-body streams, not policy noise.
- Removing waist delta (d): legs −91%, tilt −65%, drift −93% → **waist delta is the dominant cause**.
- Freezing arms (f): legs −57% but tilt unchanged (2.06°) → tilt is waist-driven; arms add stepping/drift
  via reaction momentum the policy must catch.
- Head (e): no effect. Arm filtering (c) or GMR-desired arms (b): no improvement (c ≈ a; b worse tilt) →
  arm reactions are momentum/observation-driven, not jerk-driven; smoothing the arm targets does not help.
- GMR waist (h) is strictly worse than SONIC waist → SONIC's smoothing is already helping; the raw
  retarget is the roughest signal in the chain.

## 3) Upstream smoothness (out_diag/legs/upstream_smooth.py)
| signal | vel max rad/s | jerk max | frame-jumps (>5× median step) |
|---|---|---|---|
| SMPL-X poses (30 fps) | 4.93 | 141 | 0 |
| GMR desired arms | **8.49** | **162** | **10 (f137-141, f174, f182-185)** |
| SONIC cmd arms (50 Hz, shipped) | 2.94 | 92.7 | 0 |
| SONIC waist | 1.15 | 67.0 | 8 (f212-217, f229-230) |
| GMR waist | 0.74 | 22.5 | 5 (f140-144) |

- SMPL-X is smooth; the **GMR retarget introduces discontinuities** (window seams); SONIC re-smooths arms
  but its waist stream still carries jump frames (f212-217) → step changes of the waist target that the
  engine applies instantly (`d.ctrl[act_waist] += waist_delta`, no ramp) → visible leg catch-steps.
- SONIC's own rollout trace (traces/full_*.csv) shows tilt mean 4.49°/max 8.24° and leg *desired* motion
  (vel RMS 0.32 rad/s, range up to 1.18 rad) → even upstream, gesturing on this body requires leg motion
  for balance; part of what we see is genuine CoM physics, not a bug.

## 4) Cause ranking
1. **Waist delta path (engine + SONIC waist)** — ±0.35 rad yaw / ±0.15 pitch-roll added directly on the
   policy's waist targets while standing, un-ramped, with jerk spikes (67 rad/s³) and frame jumps. The
   arms-free policy observes waist pos/vel + projected gravity, so torso twist = "disturbance" → it steps
   to catch balance (tilt 2.1°, drift 20 cm per 6 s turn). ~65-93% of the reaction.
2. **SONIC arm cmd (co-speech)** — fast arm swings (up to 2.9 rad/s) create reaction momentum; the policy
   (which observes arms) compensates with small steps and slow base drift. ~57% of leg qvel, most drift.
   Filtering/rate-limiting does NOT fix it (c, b).
3. **GMR retarget** — roughest link (jumps at seams) but SONIC masks it; only matters if GMR signals are
   used directly (h, b: worse).
4. **SMPL-X / StreamTalk** — smooth; not a contributor.
5. Head — no measurable effect.

## 5) Proposed fixes (concrete)
- Waist: slew-rate-limit + low-pass the waist_delta stream in the engine (e.g. ≤0.4 rad/s, fc≈1.5 Hz),
  and/or reduce waist_delta_lim yaw 0.35→0.15–0.20; add 0.3–0.5 s ramp-in/out at turn boundaries.
  Keep SONIC waist (never GMR raw). Optionally re-center yaw slowly toward 0 during long stands.
- Arms: don't bother with extra smoothing; instead (a) train/domain-randomize the arms-free policy with
  moving-arm disturbances (arm q/qvel are already in obs — it should learn to hold still), and (b) cap
  gesture amplitude for standing turns; accept ~4 cm drift or add slow xy re-centering via cmd.
- Pipeline: fix GMR window-seam discontinuities (f137-141/182-185 pattern) and the SONIC waist jumps
  (median/SG filter at 50 Hz) before export to arms.npz.
