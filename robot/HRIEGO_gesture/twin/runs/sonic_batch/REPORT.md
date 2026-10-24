# SONIC base batch report (4aa pointing + visits NONE/POINT)

Pipeline: S1 StreamTalk (c07 READY prompt, native retrieval) -> S2 GMR retarget -> S3 SONIC convert -> S4 SONIC rollout --no-record -> S5 prep_arms. Driver: `twin/sonic_batch.py`. Sequential, one process at a time.

## Per-clip status

| clip | emote | audio s | GMR | tilt max deg | pelvis z min/end m | arms npz rows (dur s) |
|---|---|---|---|---|---|---|
| P_RIGHT | POINT_RIGHT | 2.926 | PASS | 6.2 | 0.643/0.667 | 246 (4.92) |
| P_LEFT | POINT_LEFT | 3.344 | PASS | 7.4 | 0.639/0.666 | 268 (5.36) |
| P_FRONT | POINT_FRONT | 2.647 | PASS | 8.1 | 0.643/0.666 | 233 (4.64) |
| v2visit_vip_t01_guidance | POINT_RIGHT | 2.926 | PASS | 7.0 | 0.643/0.668 | 246 (4.92) |
| v2visit_vip_t04_dialogue | NONE | 4.319 | PASS | 8.9 | 0.643/0.666 | 316 (6.32) |
| v2visit_regular_t01_guidance | POINT_LEFT | 3.204 | FAIL | 8.2 | 0.643/0.67 | 261 (5.22) |
| v2visit_regular_t04_dialogue | NONE | 2.856 | PASS | 5.4 | 0.635/0.665 | 243 (4.86) |
| v2visit_guest_t01_guidance | POINT_FRONT | 3.971 | PASS | 7.6 | 0.643/0.667 | 299 (5.98) |
| v2visit_guest_t04_dialogue | NONE | 3.413 | PASS | 7.0 | 0.643/0.666 | 271 (5.42) |
| v2visit_staff_t01_guidance | POINT_LEFT | 3.204 | PASS | 7.7 | 0.643/0.67 | 261 (5.22) |
| v2visit_staff_t04_dialogue | NONE | 2.995 | PASS | 6.8 | 0.643/0.67 | 249 (4.98) |
| v2visit_external_t01_guidance | POINT_FRONT | 3.971 | PASS | 10.0 | 0.636/0.669 | 299 (5.98) |
| v2visit_external_t04_dialogue | NONE | 3.413 | PASS | 7.1 | 0.643/0.669 | 271 (5.42) |

## Per clip x stage resources

| clip | stage | wall s | audio s | RTF | CPU s | peak RSS MB | peak VRAM MB (minus base) | GPU util mean/peak % |
|---|---|---|---|---|---|---|---|---|
| P_FRONT | S1_streamtalk | 20.6 | 2.647 | 7.79 | 31.9 | 4259 | 2296 | 2.5/44 |
| P_FRONT | S2_gmr | 4.7 | 2.647 | 1.77 | 9.6 | 3804 | 0 | 0.0/0 |
| P_FRONT | S3_convert | 1.7 | 2.647 | 0.62 | 8.4 | 2031 | 0 | 0.0/0 |
| P_FRONT | S4_rollout | 2.4 | 2.647 | 0.92 | 9.3 | 2239 | 0 | 0.0/0 |
| P_FRONT | S5_prep_arms | 0.8 | 2.647 | 0.29 | 4.2 | 79 | 0 | 0.0/0 |
| P_LEFT | S1_streamtalk | 21.5 | 3.344 | 6.44 | 32.4 | 4258 | 2296 | 1.0/24 |
| P_LEFT | S2_gmr | 4.9 | 3.344 | 1.46 | 9.7 | 3873 | 0 | 0.0/0 |
| P_LEFT | S3_convert | 1.7 | 3.344 | 0.5 | 8.5 | 2047 | 0 | 0.0/0 |
| P_LEFT | S4_rollout | 2.5 | 3.344 | 0.74 | 9.5 | 2225 | 0 | 0.0/0 |
| P_LEFT | S5_prep_arms | 0.8 | 3.344 | 0.23 | 4.2 | 80 | 0 | 0.0/0 |
| P_RIGHT | S1_streamtalk | 25.4 | 2.926 | 8.69 | 34.3 | 4253 | 2296 | 1.0/19 |
| P_RIGHT | S2_gmr | 6.5 | 2.926 | 2.21 | 10.3 | 3879 | 0 | 0.0/0 |
| P_RIGHT | S3_convert | 1.8 | 2.926 | 0.62 | 8.4 | 2034 | 0 | 0.0/0 |
| P_RIGHT | S4_rollout | 2.6 | 2.926 | 0.89 | 9.7 | 2227 | 0 | 0.0/0 |
| P_RIGHT | S5_prep_arms | 0.8 | 2.926 | 0.28 | 4.2 | 80 | 0 | 0.0/0 |
| v2visit_external_t01_guidance | S1_streamtalk | 20.2 | 3.971 | 5.09 | 31.9 | 4257 | 2296 | 2.7/44 |
| v2visit_external_t01_guidance | S2_gmr | 4.9 | 3.971 | 1.24 | 9.8 | 3839 | 0 | 0.7/7 |
| v2visit_external_t01_guidance | S3_convert | 1.7 | 3.971 | 0.43 | 8.7 | 2022 | 0 | 0.0/0 |
| v2visit_external_t01_guidance | S4_rollout | 2.6 | 3.971 | 0.66 | 9.8 | 2264 | 0 | 0.0/0 |
| v2visit_external_t01_guidance | S5_prep_arms | 0.8 | 3.971 | 0.2 | 4.2 | 80 | 0 | 0.0/0 |
| v2visit_external_t04_dialogue | S1_streamtalk | 20.0 | 3.413 | 5.85 | 31.7 | 4257 | 2296 | 2.3/43 |
| v2visit_external_t04_dialogue | S2_gmr | 4.8 | 3.413 | 1.4 | 9.7 | 3876 | 0 | 0.7/7 |
| v2visit_external_t04_dialogue | S3_convert | 1.7 | 3.413 | 0.5 | 8.8 | 2046 | 0 | 0.0/0 |
| v2visit_external_t04_dialogue | S4_rollout | 2.6 | 3.413 | 0.77 | 10.1 | 2218 | 0 | 0.0/0 |
| v2visit_external_t04_dialogue | S5_prep_arms | 0.7 | 3.413 | 0.21 | 2.7 | 79 | 0 | 0.0/0 |
| v2visit_guest_t01_guidance | S1_streamtalk | 20.6 | 3.971 | 5.18 | 32.2 | 4258 | 2296 | 2.0/43 |
| v2visit_guest_t01_guidance | S2_gmr | 5.0 | 3.971 | 1.26 | 9.9 | 3909 | 0 | 0.7/7 |
| v2visit_guest_t01_guidance | S3_convert | 1.8 | 3.971 | 0.44 | 9.0 | 2048 | 0 | 0.0/0 |
| v2visit_guest_t01_guidance | S4_rollout | 2.7 | 3.971 | 0.69 | 10.3 | 2237 | 0 | 0.0/0 |
| v2visit_guest_t01_guidance | S5_prep_arms | 0.8 | 3.971 | 0.19 | 2.9 | 79 | 0 | 0.0/0 |
| v2visit_guest_t04_dialogue | S1_streamtalk | 20.6 | 3.413 | 6.03 | 32.6 | 4259 | 2296 | 1.4/24 |
| v2visit_guest_t04_dialogue | S2_gmr | 5.0 | 3.413 | 1.48 | 10.1 | 3877 | 0 | 0.0/0 |
| v2visit_guest_t04_dialogue | S3_convert | 1.9 | 3.413 | 0.57 | 10.0 | 2028 | 0 | 0.0/0 |
| v2visit_guest_t04_dialogue | S4_rollout | 3.2 | 3.413 | 0.95 | 12.1 | 2214 | 0 | 0.0/0 |
| v2visit_guest_t04_dialogue | S5_prep_arms | 0.8 | 3.413 | 0.23 | 3.1 | 80 | 0 | 0.0/0 |
| v2visit_regular_t01_guidance | S1_streamtalk | 20.0 | 3.204 | 6.26 | 31.8 | 4259 | 2296 | 1.6/25 |
| v2visit_regular_t01_guidance | S2_gmr | 4.8 | 3.204 | 1.51 | 9.8 | 3872 | 0 | 0.0/0 |
| v2visit_regular_t01_guidance | S3_convert | 1.7 | 3.204 | 0.53 | 8.6 | 2032 | 0 | 0.0/0 |
| v2visit_regular_t01_guidance | S4_rollout | 2.7 | 3.204 | 0.85 | 10.0 | 2193 | 0 | 0.0/0 |
| v2visit_regular_t01_guidance | S5_prep_arms | 0.8 | 3.204 | 0.26 | 3.1 | 77 | 0 | 0.0/0 |
| v2visit_regular_t04_dialogue | S1_streamtalk | 21.2 | 2.856 | 7.41 | 32.9 | 4258 | 2296 | 2.3/45 |
| v2visit_regular_t04_dialogue | S2_gmr | 4.8 | 2.856 | 1.68 | 9.7 | 3803 | 0 | 0.0/0 |
| v2visit_regular_t04_dialogue | S3_convert | 1.7 | 2.856 | 0.61 | 8.8 | 2033 | 0 | 0.0/0 |
| v2visit_regular_t04_dialogue | S4_rollout | 2.5 | 2.856 | 0.88 | 9.9 | 2224 | 0 | 0.0/0 |
| v2visit_regular_t04_dialogue | S5_prep_arms | 0.8 | 2.856 | 0.26 | 2.9 | 79 | 0 | 0.0/0 |
| v2visit_staff_t01_guidance | S1_streamtalk | 22.0 | 3.204 | 6.86 | 34.3 | 4257 | 2296 | 2.3/56 |
| v2visit_staff_t01_guidance | S2_gmr | 4.9 | 3.204 | 1.52 | 9.8 | 3860 | 0 | 0.0/0 |
| v2visit_staff_t01_guidance | S3_convert | 1.7 | 3.204 | 0.54 | 8.9 | 2048 | 0 | 0.0/0 |
| v2visit_staff_t01_guidance | S4_rollout | 2.6 | 3.204 | 0.81 | 10.0 | 2238 | 0 | 0.0/0 |
| v2visit_staff_t01_guidance | S5_prep_arms | 0.7 | 3.204 | 0.23 | 2.8 | 80 | 0 | 0.0/0 |
| v2visit_staff_t04_dialogue | S1_streamtalk | 20.1 | 2.995 | 6.72 | 31.9 | 4259 | 2296 | 2.2/56 |
| v2visit_staff_t04_dialogue | S2_gmr | 5.0 | 2.995 | 1.65 | 9.8 | 3788 | 0 | 0.0/0 |
| v2visit_staff_t04_dialogue | S3_convert | 1.8 | 2.995 | 0.59 | 8.8 | 2044 | 0 | 0.0/0 |
| v2visit_staff_t04_dialogue | S4_rollout | 2.6 | 2.995 | 0.86 | 10.0 | 2215 | 0 | 0.0/0 |
| v2visit_staff_t04_dialogue | S5_prep_arms | 0.8 | 2.995 | 0.26 | 2.9 | 79 | 0 | 0.0/0 |
| v2visit_vip_t01_guidance | S1_streamtalk | 21.0 | 2.926 | 7.16 | 31.9 | 4255 | 2296 | 1.8/34 |
| v2visit_vip_t01_guidance | S2_gmr | 4.7 | 2.926 | 1.62 | 9.7 | 3872 | 0 | 0.0/0 |
| v2visit_vip_t01_guidance | S3_convert | 1.7 | 2.926 | 0.58 | 8.6 | 2045 | 0 | 0.0/0 |
| v2visit_vip_t01_guidance | S4_rollout | 2.5 | 2.926 | 0.85 | 9.5 | 2211 | 0 | 0.0/0 |
| v2visit_vip_t01_guidance | S5_prep_arms | 0.7 | 2.926 | 0.25 | 2.7 | 79 | 0 | 0.0/0 |
| v2visit_vip_t04_dialogue | S1_streamtalk | 20.2 | 4.319 | 4.68 | 31.7 | 4259 | 2296 | 2.4/43 |
| v2visit_vip_t04_dialogue | S2_gmr | 5.0 | 4.319 | 1.16 | 9.9 | 3842 | 0 | 0.0/0 |
| v2visit_vip_t04_dialogue | S3_convert | 1.7 | 4.319 | 0.4 | 8.8 | 2025 | 0 | 0.0/0 |
| v2visit_vip_t04_dialogue | S4_rollout | 2.7 | 4.319 | 0.64 | 10.7 | 2255 | 0 | 0.0/0 |
| v2visit_vip_t04_dialogue | S5_prep_arms | 0.7 | 4.319 | 0.17 | 3.1 | 79 | 0 | 0.0/0 |

## Stage medians / peaks

| stage | n | median wall s | median RTF | max RTF | median RSS MB | max RSS MB | median VRAM MB | max VRAM MB | median GPU util mean % | max GPU util % |
|---|---|---|---|---|---|---|---|---|---|---|
| S1_streamtalk | 13 | 20.6 | 6.44 | 8.69 | 4258 | 4259 | 2296 | 2296 | 2.2 | 56 |
| S2_gmr | 13 | 4.9 | 1.51 | 2.21 | 3872 | 3909 | 0 | 0 | 0.0 | 7 |
| S3_convert | 13 | 1.7 | 0.54 | 0.62 | 2034 | 2048 | 0 | 0 | 0.0 | 0 |
| S4_rollout | 13 | 2.6 | 0.85 | 0.95 | 2225 | 2264 | 0 | 0 | 0.0 | 0 |
| S5_prep_arms | 13 | 0.8 | 0.23 | 0.29 | 79 | 80 | 0 | 0 | 0.0 | 0 |

Max VRAM any stage: 2296 MB (above baseline 15 MB); max RSS any stage: 4259 MB.

## Notes
- Failure: v2visit_regular_t01_guidance (POINT_LEFT) GMR status FAIL (qc fail = self_collision, selfcol_frac 0.65). SONIC rollout still upright (tilt max 8.2 deg), so the arms npz exists but flag it; v2visit_staff_t01_guidance (same text) passed GMR. Regenerate with another seed if a strict PASS is needed.
- prep_arms npz durations are audio + about 2 s lead-in (READY prompt); stream with `--start-frame 100` (audio t0).
- S3/S4 (SONIC convert/rollout, MuJoCo CPU) use no VRAM. StreamTalk is the only GPU stage: peak 2296 MB VRAM, 4.26 GB RSS. Mem baseline VRAM 15 MB (x2-llm off).
- 4aa earlier crash (OOM) did not reproduce with only this process running; P_RIGHT first run figures are merged from the first attempt (p1.jsonl.keep).
