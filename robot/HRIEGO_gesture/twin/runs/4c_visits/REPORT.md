# 4c - 5 simulated visits on the X2 twin (quantitative, no video)
Twin (x2-twin, preplaying_tick 500 untouched), `run_visits.py` (host) + `analyze_visits.py`. 5 visits x 6 turns = 30 turns, rep 1 (metrics/turns.csv, summary.json) and rep 2 (turns_r2.csv, summary_r2.json).
Presets: c10 `synced_playback.py` unmodified (plan.json with required start delay, fail-closed on WAV sha) on the audio clock. POINT: `stream_point.py` with the turn's SONIC base, hold 1.5 s, CTC anchor. NONE: c07 `stream_arms_waist.py --start-frame 100`.
After URS turns: waist/head zero reset. regular_t01 SONIC re-run with seed 2 (SONIC_SEED env in sonic_batch.py): GMR PASS (seed 1: selfcol 0.65 FAIL, kept in `regular/sonic/_seed1_GMR_FAIL_*`).

| Visit | rep1 gates ok / 6 | rep2 gates ok / 6 | all completed | falls | wall incl. reset (rep1, s) |
|---|---|---|---|---|---|
| vip | 5 (t00 onset artifact) | 6 | yes | 0 | 65 |
| regular | 6 | 6 | yes | 0 | 69 |
| guest | 6 | 6 | yes | 0 | 68 |
| staff | 6 | 6 | yes | 0 | 62 |
| external | 6 | 6 | yes | 0 | 65 |

Aggregates (rep1 / rep2): presets executed == contract 20/20 both; dispatch jitter max 0.0002 ms (<=5); onset-anchor median abs 7.0 / 9.4 ms, max 1158 (vip_t00 rep1 only) / 40 ms; |onset err| <=100 ms: 19/20, 20/20; stroke-anchor median 693 / 700 ms (stroke is later than the anchor by the preset's own onset->apex time, as in c10 G22, not gated here); pelvis z min 0.684 / 0.693 m. POINT: FK error mean hold max 8.1 deg (gate 15), hold (prog99) 1.5-1.82 s, SONIC tracking outside gesture (lag-4 definition as c07) max 0.0103 rad, return error max 0.019 rad, jitter max 0.11 / 3.4 ms, pelvis >=0.69. NONE: arm tracking error 0.009-0.010 rad (gate 0.015). Turn wall incl. reset mean 10.9 s, max 16.2 s.

Failures/caveats: (1) vip_t00 rep1 onset -1158 ms: first turn after twin start, pre-dispatch baseline not settled (stroke 562 ms matches c10 544 ms; rep2 onset +14.7 ms). (2) Error at the anchor instant for FRONT turns is 14-20 deg (external 20.0/20.3, guest 15.9) because the SONIC base starts further from the target; the mean over the hold is 5-8 deg. (3) Early arrival (-53 to -127 ms for RIGHT/LEFT; FRONT +61..+67) accepted per user, not gated. (4) Staff t03 CONFIRMATION onset +54/+40 ms (within gate). (5) 3029/11 (staff_t02 NEGATION_NORMAL) has the known twin head-yaw clamp: stroke definition unreliable (flagged `clamp_flag`). (6) Raw |meas-cmd| without lag compensation for POINT turns is 0.018-0.027 rad (SONIC base moves fast); lag-4 gives 0.010, the c07 definition. (7) SONIC around presets inside a turn (preset + SONIC blend) is future work c12.
