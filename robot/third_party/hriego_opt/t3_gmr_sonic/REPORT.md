# T2b / T3 - X2-level precision study and GMR solver comparison

Pipeline per clip, persistent workers (warm): StreamTalk SMPL-X npz (t2 `out/<variant>/<clip>`) -> GMR (`native_qp`) -> SONIC convert + rollout -> `prep_arms.py`.
Serial, one heavy process at a time, x2-llm/twin off. Scripts: `driver_gmr.py`, `gmr_worker_t3.py` (copy of the worker with `GMR_SOLVER` env), `driver_sonic.py` (production sonic_worker + prep_arms), `analyze.py`. Raw: `out/` (97 MB, not for commit).
All X2 metrics use SONIC *actual* trajectories (what is streamed to the twin): 14 arm joints (rad) and waist yaw/pitch/roll. Reference = fp32_s1, same clip, truncated to common length. Jerk = mean |d3 q/dt3| of the 14 arm joints at 50 Hz (rad/s^3).

## Part 1 - variants (X2 level)
| variant | clips | arm RMSE vs fp32_s1 (rad) [seed ref] | waist RMSE (rad) [seed ref] | arm p95/joint mean (max) [seed ref mean] | arm vel (rad/s) | arm jerk (ratio vs s1) | GMR QC | SONIC tilt max (deg) / pelvis min (m) | clamped | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| fp32_s1 (base) | 10 | - | - | - | 0.252 | 174 (1.00) | 10/10 | 14.5 / 0.624 | 12.0% | baseline |
| fp32_s2 (seed ref) | 10 | 0.203 | 0.043 | 0.376 (0.756) | 0.188 | 137 (0.79) | 10/10 | 10.8 / 0.625 | 12.7% | reference |
| fp16_s1 (production) | 10 | 0.120 | 0.025 | 0.221 (0.543) | 0.251 | 176 (1.01) | 10/10 | 11.6 / 0.627 | 12.1% | PASS |
| bf16_s1 | 10 | 0.144 | 0.030 | 0.268 (0.779) | 0.241 | 178 (1.02) | 10/10 | 11.2 / 0.626 | 12.1% | PASS |
| i8sim_gen_s1 | 10 | 0.123 | 0.029 | 0.246 (0.557) | 0.258 | 179 (1.03) | 10/10 | 10.9 / 0.623 | 11.7% | PASS |
| i8sim_all_s1 | 10 | 0.104 | 0.027 | 0.204 (0.413) | 0.252 | 179 (1.03) | 10/10 | 11.9 / 0.604 | 11.8% | PASS |
| i8dyn_gen_s1 | 4 | 0.117 [0.171] | 0.031 [0.043] | 0.231 (0.616) [0.336] | 0.239 | 273 (1.60; base 171) | 4/4 | 7.9 / 0.626 | 11.3% | FAIL (jerk) |
| i8dyn_all_s1 | 4 | 0.178 [0.171] | 0.033 [0.043] | 0.328 (0.850) [0.336] | 0.227 | 250 (1.46) | 4/4 | 8.6 / 0.642 | 12.0% | FAIL (arm RMSE > seed, jerk) |

Acceptance rule used: arm RMSE and waist RMSE <= fp32_s2 reference on the same clips; arm jerk ratio within max(25%, seed-ref deviation) of fp32_s1; GMR QC pass rate >= baseline; tilt max <= 1.25x max(base, seed); clamp fraction <= 1.5x max(base, seed). Last two variants are scored on their 4 clips (baseline/seed recomputed on the same 4).
Findings:
- All 68 clips pass GMR QC (no self_collision) and SONIC (no falls; pelvis min >= 0.60 m). Tilt max and clamp fraction (~12%, clamped by prep_arms limits, identical in all variants incl. baseline) do not separate variants.
- At X2 level fp16, bf16, i8sim_gen and i8sim_all are all well inside the seed spread (arm error 0.10-0.14 rad vs 0.20 seed; waist 0.025-0.030 vs 0.043). Note the SMPL-X-level T2 verdict on bf16 (jerk x3, FGD 9.6) does NOT carry through: the SONIC policy low-passes it (arm jerk +2%). bf16 is the worst of the passing ones (p95 max 0.78 rad ~ seed level), still within tolerance.
- i8dyn (real int8 on CPU) fails: arm jerk +46..60% (SONIC attenuates the T2 x11 jerk but not enough); i8dyn_all also exceeds the seed error. Confirms T2: not acceptable.
- Caveats: one seed per variant, 10 clips, per-clip error is chaotic (keypose retrieval); fp32_s2's lower jerk/velocity (0.79x / 0.75x) shows seed spread is large, so the jerk band is loose for the passing variants. The 4-clip i8dyn numbers are noisy. Error vs fp32_s1 mainly measures divergence of retrieval choices, not quality.

## Part 2 - GMR solver (fp32_s1, 10 clips, warm persistent worker, V2M_CPU_LIMIT=2)
| clip | frames | native s | analytic s | native CPU s | analytic CPU s | QC native/analytic | GMR arm qpos RMSE (rad) | SONIC arm RMSE (rad) |
|---|---|---|---|---|---|---|---|---|
| c001 | 305 | 2.734 | 7.016 | 3.1 | 7.37 | PASS/PASS | 0.412 | 0.321 |
| c004 | 263 | 2.361 | 6.203 | 2.68 | 6.6 | PASS/PASS | 0.431 | 0.273 |
| c005 | 264 | 2.381 | 5.846 | 2.8 | 6.16 | PASS/PASS | 0.427 | 0.295 |
| c010 | 166 | 1.742 | 4.216 | 2.09 | 4.55 | PASS/PASS | 0.397 | 0.240 |
| c016 | 201 | 2.029 | 4.925 | 2.36 | 5.3 | PASS/PASS | 0.400 | 0.247 |
| c018 | 114 | 1.471 | 3.295 | 1.82 | 3.64 | PASS/PASS | 0.393 | 0.219 |
| v_guest_t00_greeting_EMOTE | 101 | 1.468 | 2.746 | 1.81 | 3.09 | PASS/PASS | 0.408 | 0.247 |
| v_guest_t01_guidance_POINT | 120 | 1.467 | 2.748 | 1.87 | 3.13 | PASS/PASS | 0.418 | 0.241 |
| v_regular_t02_dialogue_NONE | 157 | 1.717 | 4.736 | 2.14 | 5.09 | PASS/PASS | 0.447 | 0.170 |
| v_staff_t05_farewell_NONE | 159 | 1.732 | 3.727 | 2.13 | 4.13 | PASS/PASS | 0.381 | 0.267 |

Mean wall: native 1.91 s, analytic 4.55 s (x2.38); per frame ~10.9 vs 25.1 ms.

| metric | native_qp | analytic_gmr |
|---|---|---|
| worker load (spawn to ready) | 3.8 s, RSS 2512 MB | 2.6 s, RSS 2529 MB |
| first clip (c001, 305 fr) | 2.7 s | 7.0 s (same as warm: no extra cold-start cost beyond load) |
| RSS steady (after clips) | ~2.6-2.8 GB | ~2.6-2.7 GB |
| VmHWM peak | ~3.9-4.1 GB | ~3.9-4.1 GB |
| threads / CPU | 34 threads in process, affinity pinned to 2 cores (V2M_CPU_LIMIT=2), CPU time/wall = 1.1-1.2 (about 1 core busy) | same, CPU/wall = 1.05 |
| GMR QC pass | 10/10 | 10/10 |
| SONIC tilt max / mean of per-clip max (deg) | 14.5 / 9.2 | 9.1 / 7.6 |
| SONIC pelvis min (m) | 0.624 | 0.634 |
| arm jerk (rad/s^3) / peak arm vel (rad/s) | 174 / 3.3 | 238 / 4.4 |
| clamped samples | 12.0% | 13.1% |
| SONIC tracking MAE (actual vs GMR desired, rad) | 0.223 | 0.310 |

Native vs analytic difference: SONIC arm RMSE 0.263 rad (per-clip 0.17-0.32; p95/joint mean 0.42, max 0.83), waist RMSE 0.047 rad, GMR-level arm qpos RMSE 0.41 rad. This is a large difference (greater than the fp32_s1 vs fp32_s2 seed spread of 0.20 rad), i.e. the solvers produce visibly different arm motion. analytic_gmr ran without errors (upstream `GeneralMotionRetargeting` swapped into `batch._worker["GMR"]`, same path as `x2_retarget.py --solver-mode analytic_gmr`; wired by `gmr_worker_t3.py` since the stock worker has no solver option).
Not determined: which solver is closer to the human motion (no ground-truth metric computed here); analytic gives +37% jerk, +30% peak velocity and worse SONIC tracking.

## Recommendations
1. fp16 (production) stays; fp32 gives no X2-level benefit (fp16 is within 0.12 rad of fp32, below the 0.20 rad seed noise). bf16, i8sim_gen and i8sim_all also pass at X2 level, so precision is not a risk for the arms; only gain would come from real GPU int8 kernels (not available), so no change.
2. Drop i8dyn (CPU dynamic int8): fails jerk at X2 level too and is not real-time.
3. Keep native_qp: analytic_gmr is ~2.3x slower (about 4.2 vs 1.9 s mean per clip warm), equal RAM, 10/10 QC, but produces different, jerkier arm motion (0.26 rad RMSE from native) with worse SONIC tracking; its only upside is lower SONIC tilt max (9.1 vs 14.5 deg, single-seed, 10 clips). Consider it only if a visual review prefers its poses.
4. GMR does not benefit from threads (about 1 core busy); the 2-core pin is enough, and the worker costs ~2.6 GB steady / 4 GB peak.
