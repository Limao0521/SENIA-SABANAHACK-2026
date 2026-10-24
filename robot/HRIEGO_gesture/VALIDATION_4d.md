# 4d - End-to-end validation of the gesture pipeline (HRIEGO_gesture)

Scope: LLM contract -> validator/FSM gesture manager -> Supertonic WAV -> QuartzNet CTC anchor -> c09 plan (c08 latencies) -> execution on the X2 twin (4c, 5 visits x 6 turns x 2 reps = 60 executed turns).
Method: read-only re-verification of the stored artifacts plus offline injection tests. No git commit, no twin / x2-llm / StreamTalk / GPU job was started; all scripts are CPU-only (peak < 0.5 GB).
Scripts and raw JSON: `HRIEGO_gesture/validation/` (`trace_check.py`, `failclosed.py` + `failclosed_anchor.py`, `latency_ctc.py`, `latency_budget.py`; outputs `trace_check.json`, `trace_check_table.csv`, `failclosed.json`, `latency_ctc.json`, `latency_budget.json`).
Repro: `cd $X2_ULTRA_ROOT; export PYTHONPATH=$PWD; Face/.venv-face/bin/python HRIEGO_gesture/validation/trace_check.py` and `.../failclosed.py` (spawns the physicalai python for the anchor suite); `physicalai python .../latency_ctc.py; python3 .../latency_budget.py`.

## Verdict
- Traceability: 1635 checks over 30 turns (60 executed turn-reps), **0 mismatches**.
- Fail-closed: **39/39 required injections PASS**; 0 extra probes. The gap found in this run (G1, trigger_text vs span) is now a required case and a guard in `anchor_plan.py` (status `rejected_trigger_span`), added after the measurement; `validation/failclosed.json` was regenerated with it (section 2).
- Gates: all safety gates pass (0 falls in 60 turns, 0 spurious gestures, 0 rank leaks, hashes fail closed). Open items are quality gaps, not safety: contract recall for SOCIAL_HEART/APPLAUSE, POINT error at the anchor instant (6/10 turn-reps > 15 deg), early arrival, SONIC not real time.
- Real time today: only preset turns (~1.1 s from Qwen reply to audio start). NONE and POINT need the offline SONIC chain (~31 s per clip), so they are not real time.

## 1. Traceability per executed turn

Chain checked for every turn, from stored artifacts, with the real code (`contract/schema.py`: `normalize`, `validate`, `VisitGestureManager` replayed in turn order; `find_span`):
1. LLM raw JSON parses; `normalize(raw)` == stored parsed; raw == last attempt; validator replay gives no errors == stored verdict.
2. Replayed manager decision (requested / effective / suppressed / trigger text and char span) == stored decision; effective in the per-state enum; GREETING == rank emote; FAREWELL == VIP SOCIAL_HEART (FSM-mandated) or FAREWELL_WAVE; POINT == scenario direction.
3. Manifest row: emote == effective, motion/area == catalog, speech == parsed speech, `speech[start:end]` == trigger on word boundaries, **sha256(file on disk) == manifest == visit_log**, sample rate == file.
4. CTC anchor: status `aligned_ctc_not_human_validated`, text/span == trigger, sha and sample_count == WAV, 0 <= start < end <= duration, start_sample == round(start_s*fs), summary == anchor.json == visit_log.
5. Plan (presets): status pending, audio sha == WAV, binding == manifest, latency table sha == c08 `preset_latency.json`, **dispatch_sample == anchor_start_sample - round(lead_onset_median*fs)** (independent recomputation), start delay == max(0,-dispatch) <= 1.5 s, audio unmodified. POINT: `anchor_only_no_preset`, no plan file. NONE: `none_sonic`, no anchor/event/plan file (zero dispatch).
6. Executed trace, per rep: PRESET: trace JSON wav sha == manifest, motion/area dispatched == manifest, binding == plan, start_delay and dispatch_sample == plan, dispatched+completed, MC `motion` column shows only the manifest motion id, MC STAND_DEFAULT/fsm 4 throughout with player_state 2 reached, jitter <= 5 ms. POINT: direction == effective, anchor_start == CTC anchor, base == turn SONIC npz, forward-kinematics error to the commanded direction (mean over hold, measured joints) <= 15 deg, streamed in URS (no MC preset columns). NONE: SONIC arms streamed (kind NONE, no preset JSON/columns), arm tracking <= 0.015 rad.

Result: **1635 / 1635 checks OK, 0 mismatches** (per-check counts in `trace_check.json`).

| check family | checks | mismatches |
|---|---|---|
| LLM raw -> parsed -> verdict -> manager decision -> enum (30 turns) | 345 | 0 |
| manifest + WAV sha256 on disk (30 WAVs) | 325 | 0 |
| CTC anchor (25 gesture turns) | 200 | 0 |
| plan / dispatch status (20 preset plans, 5 POINT anchor-only, 5 NONE) | 175 | 0 |
| executed trace (40 preset + 10 POINT + 10 NONE turn-reps) | 590 | 0 |

Counts of effective gestures: 20 presets (incl. 5 FSM-mandated greetings, 1 FSM-mandated VIP farewell SOCIAL_HEART), 5 POINT, 5 NONE; no turn was suppressed by cooldown/cap in these visits (requested == effective everywhere), so cooldown/cap behaviour is covered only by the injection tests below.

Informational finding F1 (metadata only, not a mismatch): `manifest.duration_s` is 3-59 ms *shorter* than the WAV file duration for all 30 WAVs (mean 36 ms; the TTS runner apparently reports the duration before trailing padding). Anchors/plans use the real file sample count (verified), so no timing consequence.

Per-turn table (rep1 / rep2 executed; onset_err = visible onset minus CTC anchor start):

| turn | state | effective (requested; FSM-mandated) | trigger anchor (CTC s) | wav sha256[:12] | plan | executed r1 / r2 |
|---|---|---|---|---|---|---|
| vip_t00_greeting | GREE | GREETING_VIP (FSM) | 0.80-1.40 | f1eea8f7115a | disp=-16758 delay=0.38s | 3001/11, onset_err=-1158 ms / 3001/11, onset_err=+15 ms |
| vip_t01_guidance | GUID | POINT_RIGHT | 1.74-2.26 | f6fb6020ad00 | anchor_only | POINT RIGHT; hold 6.5 deg, anchor=5.5 / POINT RIGHT; hold 6.4 deg, anchor=4.7 |
| vip_t02_dialogue | DIAL | APPLAUSE | 0.68-1.28 | ce2659c29e3f | disp=-22050 delay=0.50s | 3017/11, onset_err=+1 ms / 3017/11, onset_err=-3 ms |
| vip_t03_dialogue | DIAL | SOCIAL_HEART | 0.88-1.24 | 3a67d5e49928 | disp=-14112 delay=0.32s | 3004/11, onset_err=+8 ms / 3004/11, onset_err=+8 ms |
| vip_t04_dialogue | DIAL | NONE | - | c575e2fceed1 | none_sonic | SONIC arm_err=0.0092 rad / SONIC arm_err=0.0095 rad |
| vip_t05_farewell | FARE | SOCIAL_HEART (FSM) | 2.42-3.00 | 19f845bd8475 | disp=53802 delay=0.00s | 3004/11, onset_err=+5 ms / 3004/11, onset_err=+24 ms |
| regular_t00_greeting | GREE | GREETING_REGULAR (FSM) | 0.80-1.36 | ee08046da7e3 | disp=-17643 delay=0.40s | 1003/2, onset_err=-3 ms / 1003/2, onset_err=+4 ms |
| regular_t01_guidance | GUID | POINT_LEFT | 2.00-2.60 | 8da80396d47c | anchor_only | POINT LEFT; hold 4.6 deg, anchor=9.1 / POINT LEFT; hold 4.8 deg, anchor=9.9 |
| regular_t02_dialogue | DIAL | NEGATION_STRONG | 0.82-1.70 | c99f50eda1df | disp=-15874 delay=0.36s | 3009/11, onset_err=+4 ms / 3009/11, onset_err=+3 ms |
| regular_t03_dialogue | DIAL | THUMBS_UP | 3.28-3.82 | 13cb990d9b68 | disp=91730 delay=0.00s | 1005/2, onset_err=+7 ms / 1005/2, onset_err=+9 ms |
| regular_t04_dialogue | DIAL | NONE | - | 46bc1abbf040 | none_sonic | SONIC arm_err=0.0090 rad / SONIC arm_err=0.0088 rad |
| regular_t05_farewell | FARE | FAREWELL_WAVE | 2.60-3.12 | ba1a249639a9 | disp=60857 delay=0.00s | 3031/11, onset_err=-7 ms / 3031/11, onset_err=-9 ms |
| guest_t00_greeting | GREE | GREETING_GUEST (FSM) | 0.68-1.18 | 4650c3c7f350 | disp=-24688 delay=0.56s | 1011/2, onset_err=-5 ms / 1011/2, onset_err=+13 ms |
| guest_t01_guidance | GUID | POINT_FRONT | 2.86-3.36 | 1cacb6b2897c | anchor_only | POINT FRONT; hold 5.9 deg, anchor=15.9 / POINT FRONT; hold 5.7 deg, anchor=15.2 |
| guest_t02_dialogue | DIAL | SOCIAL_HEART | 1.38-1.80 | f1a9e41fc523 | disp=7938 delay=0.00s | 3004/11, onset_err=+9 ms / 3004/11, onset_err=+9 ms |
| guest_t03_dialogue | DIAL | APPLAUSE | 0.78-1.48 | 162d0a7bcb03 | disp=-17640 delay=0.40s | 3017/11, onset_err=+5 ms / 3017/11, onset_err=+4 ms |
| guest_t04_dialogue | DIAL | NONE | - | f1f8580b3044 | none_sonic | SONIC arm_err=0.0089 rad / SONIC arm_err=0.0091 rad |
| guest_t05_farewell | FARE | FAREWELL_WAVE | 2.40-3.00 | bf09aa048b34 | disp=52037 delay=0.00s | 3031/11, onset_err=-16 ms / 3031/11, onset_err=-13 ms |
| staff_t00_greeting | GREE | GREETING_STAFF (FSM) | 0.82-1.62 | a2b57e52b91c | disp=-18521 delay=0.42s | 1008/2, onset_err=+12 ms / 1008/2, onset_err=+6 ms |
| staff_t01_guidance | GUID | POINT_LEFT | 1.96-2.56 | 899afc7081e5 | anchor_only | POINT LEFT; hold 7.0 deg, anchor=15.4 / POINT LEFT; hold 6.9 deg, anchor=15.4 |
| staff_t02_dialogue | DIAL | NEGATION_NORMAL | 0.86-1.24 | 2089f10bb0c7 | disp=-23814 delay=0.54s | 3029/11, onset_err=+5 ms / 3029/11, onset_err=+0 ms |
| staff_t03_dialogue | DIAL | CONFIRMATION | 0.86-0.92 | 38f9d2f2f607 | disp=-19404 delay=0.44s | 3028/11, onset_err=+54 ms / 3028/11, onset_err=+40 ms |
| staff_t04_dialogue | DIAL | NONE | - | 1ffc6c9c1257 | none_sonic | SONIC arm_err=0.0098 rad / SONIC arm_err=0.0097 rad |
| staff_t05_farewell | FARE | FAREWELL_WAVE | 2.38-3.00 | 2b8ffa367aa3 | disp=51155 delay=0.00s | 3031/11, onset_err=-18 ms / 3031/11, onset_err=-21 ms |
| external_t00_greeting | GREE | GREETING_EXTERNAL (FSM) | 0.66-1.14 | 9dbaa68a2e8b | disp=-23813 delay=0.54s | 1002/2, onset_err=+2 ms / 1002/2, onset_err=-10 ms |
| external_t01_guidance | GUID | POINT_FRONT | 2.90-3.38 | 07e748fec251 | anchor_only | POINT FRONT; hold 8.1 deg, anchor=20.0 / POINT FRONT; hold 8.2 deg, anchor=20.3 |
| external_t02_dialogue | DIAL | SOCIAL_HEART | 0.74-1.18 | 6c4133821367 | disp=-20286 delay=0.46s | 3004/11, onset_err=+7 ms / 3004/11, onset_err=+10 ms |
| external_t03_dialogue | DIAL | NEGATION_STRONG | 1.84-2.64 | daf836c5d5e9 | disp=29108 delay=0.00s | 3009/11, onset_err=-5 ms / 3009/11, onset_err=-20 ms |
| external_t04_dialogue | DIAL | NONE | - | ee865891b587 | none_sonic | SONIC arm_err=0.0093 rad / SONIC arm_err=0.0093 rad |
| external_t05_farewell | FARE | FAREWELL_WAVE | 2.50-3.04 | 7276e54ee1ea | disp=56447 delay=0.00s | 3031/11, onset_err=-14 ms / 3031/11, onset_err=-1 ms |

(vip_t00 rep1 onset -1158 ms is the known first-turn-after-twin-start artifact; the same plan gives +15 ms in rep2 and its stroke error 562 ms matches c10's 544 ms.)

## 2. Fail-closed injection tests (offline)

Real code paths: `contract/schema.py` (`normalize`, `validate`, `VisitGestureManager.decide`) under `Face/.venv-face/bin/python`; the real `align/anchor_plan.py` CLI (QuartzNet CPU) under the physicalai python, on mutated *copies* of manifest rows / latency table in a temp dir. "Zero dispatch" = effective gesture NONE (contract layer) or no pending plan / no dispatch entries (anchor layer). Full evidence per case: `validation/failclosed.json`.

**39/39 required cases PASS** (cases 37-39 were added after the first measurement, see the note on G1 in section 2).

| # | layer | injection | observed (status / effective gesture) | dispatch | expected | result |
|---|---|---|---|---|---|---|
| 1 | contract | trigger_not_in_speech | NONE (suppressed: validation_failed); err: trigger_text must be a literal substring of speech | none | NONE / err 'literal substring' | **PASS** |
| 2 | contract | trigger_mid_word | NONE (suppressed: validation_failed); err: trigger_text must start and end on whole words of speech (no cut words | none | NONE / err 'whole words' | **PASS** |
| 3 | contract | emote_not_allowed_GREETING_THUMBS_UP | NONE (suppressed: validation_failed); err: type 'THUMBS_UP' not allowed in GREETING (enum ['GREETING_VIP']) | none | NONE / err 'not allowed in GREETING' | **PASS** |
| 4 | contract | emote_not_allowed_GREETING_other_rank | NONE (suppressed: validation_failed); err: type 'GREETING_VIP' not allowed in GREETING (enum ['GREETING_REGULAR'] | none | NONE / err 'not allowed in GREETING' | **PASS** |
| 5 | contract | emote_not_allowed_GUIDANCE_APPLAUSE | NONE (suppressed: validation_failed); err: type 'APPLAUSE' not allowed in GUIDANCE (enum ['POINT_RIGHT', 'POINT_L | none | NONE / err 'not allowed in GUIDANCE' | **PASS** |
| 6 | contract | emote_not_allowed_FAREWELL_VIP_wave | NONE (suppressed: validation_failed); err: type 'FAREWELL_WAVE' not allowed in FAREWELL (enum ['SOCIAL_HEART']) | none | NONE / err 'not allowed in FAREWELL' | **PASS** |
| 7 | contract | emote_not_allowed_FAREWELL_staff_heart | NONE (suppressed: validation_failed); err: type 'SOCIAL_HEART' not allowed in FAREWELL (enum ['FAREWELL_WAVE']) | none | NONE / err 'not allowed in FAREWELL' | **PASS** |
| 8 | contract | rank_leak_in_speech | NONE (suppressed: validation_failed); err: speech must not mention rank names/codes or 'agente' (found 'VIP') | none | NONE / err 'rank names' | **PASS** |
| 9 | contract | rank_leak_agente | NONE (suppressed: validation_failed); err: speech must not mention rank names/codes or 'agente' (found 'agente') | none | NONE / err 'rank names' | **PASS** |
| 10 | contract | third_person_direction | NONE (suppressed: validation_failed); err: speech uses visitor-relative/third-person directions; use first person | none | NONE / err 'visitor-relative' | **PASS** |
| 11 | contract | third_person_direction_tu_izquierda | NONE (suppressed: validation_failed); err: speech uses visitor-relative/third-person directions; use first person | none | NONE / err 'visitor-relative' | **PASS** |
| 12 | contract | NONE_with_stray_trigger_DIALOGUE | NONE | none | NONE | **PASS** |
| 13 | contract | NONE_in_GUIDANCE_rejected | NONE (suppressed: validation_failed); err: state GUIDANCE/VIP requires one motor intent | none | NONE / err 'requires one motor intent' | **PASS** |
| 14 | contract | NONE_in_VIP_FAREWELL_rejected | NONE (suppressed: validation_failed); err: state FAREWELL/VIP requires one motor intent | none | NONE / err 'requires one motor intent' | **PASS** |
| 15 | contract | cooldown_repeat_in_DIALOGUE | NONE (suppressed: cooldown_same_emote_consecutive) | none | NONE | **PASS** |
| 16 | contract | cap_APPLAUSE_third_in_visit | NONE (suppressed: cap_APPLAUSE_2_per_visit) | none | NONE | **PASS** |
| 17 | contract | VIP_farewell_exempt_from_cooldown_and_cap | SOCIAL_HEART | yes (allowed) | SOCIAL_HEART | **PASS** |
| 18 | contract | POINT_without_direction_cue | NONE (suppressed: validation_failed); err: trigger_text must contain an anchor cue of POINT_RIGHT: ['a mi derecha | none | NONE / err 'anchor cue' | **PASS** |
| 19 | contract | POINT_wrong_direction_vs_scenario | NONE (suppressed: validation_failed); err: type POINT_LEFT contradicts the scenario direction right | none | NONE / err 'contradicts the scenario' | **PASS** |
| 20 | contract | POINT_target_vector_flipped | NONE (suppressed: validation_failed); err: target_vector contradicts the X2_base_local convention | none | NONE / err 'target_vector' | **PASS** |
| 21 | contract | negated_trigger_suppressed | NONE (suppressed: trigger_negated) | none | NONE | **PASS** |
| 22 | contract | speech_too_short | NONE (suppressed: validation_failed); err: speech must have at least 6 words | none | NONE / err 'at least 6 words' | **PASS** |
| 23 | contract | STAFF_greeting_missing_phrase | NONE (suppressed: validation_failed); err: trigger_text must contain an anchor cue of GREETING_STAFF: ['dame esos | none | NONE / err 'dame esos cinco' | **PASS** |
| 24 | anchor_plan | wrong_wav_hash | rejected_hash (manifest wav_sha256 != file sha256) | none | rejected_hash | **PASS** |
| 25 | anchor_plan | wav_file_tampered_byte | rejected_hash (manifest wav_sha256 != file sha256) | none | rejected_hash | **PASS** |
| 26 | anchor_plan | wav_missing | rejected_hash (wav missing) | none | rejected_hash | **PASS** |
| 27 | anchor_plan | manifest_sample_rate_mismatch | rejected_hash (manifest sample_rate != file sample rate) | none | rejected_hash | **PASS** |
| 28 | anchor_plan | missing_latency_entry | rejected_resources (no usable lead_onset_s/duration_s median for 3004/11) | none | rejected_resources | **PASS** |
| 29 | anchor_plan | latency_entry_without_duration | rejected_resources (no usable lead_onset_s/duration_s median for 3004/11) | none | rejected_resources | **PASS** |
| 30 | anchor_plan | latency_table_unreadable | rejected_resources (latency table: JSONDecodeError: Expecting property name) | none | rejected_resources | **PASS** |
| 31 | anchor_plan | start_delay_over_cap_stroke_policy | rejected_horizon (dispatch -68795 needs 1.560s start delay > 1.5s) | none | rejected_horizon | **PASS** |
| 32 | anchor_plan | start_delay_over_cap_inflated_lead | rejected_horizon (dispatch -93492 needs 2.120s start delay > 1.5s) | none | rejected_horizon | **PASS** |
| 33 | anchor_plan | trigger_span_empty | alignment_error (missing trigger span) | none | alignment_error | **PASS** |
| 34 | anchor_plan | trigger_span_beyond_speech | alignment_anchor_word_mapping_mismatch | none | != planned | **PASS** |
| 35 | anchor_plan | POINT_no_preset_no_dispatch | anchor_only_no_preset | none | anchor_only_no_preset | **PASS** |
| 36 | anchor_plan | NONE_with_stray_trigger_fields | none_sonic | none | none_sonic | **PASS** |
| 37 | anchor_plan | G1_trigger_text_vs_span_mismatch | rejected_trigger_span | no | rejected_trigger_span | **PASS** (added after measurement; was the failing extra probe) |
| 38 | anchor_plan | G1_trigger_text_missing | rejected_trigger_span | no | rejected_trigger_span | **PASS** (added after measurement) |
| 39 | anchor_plan | auto_policy_falls_back_when_stroke_over_cap | planned | YES | planned (midpoint or onset, not reject) | **PASS** (added after measurement) |

Notes on specific cases:
- NONE with a stray trigger: the contract normalizes `type=NONE` to `motor_intents=[]` and drops the trigger (valid, no dispatch). A stray trigger on the manifest's NONE row is ignored by `anchor_plan` (`none_sonic`, nothing aligned).
- VIP farewell: with cooldown state (`last == SOCIAL_HEART`) and the cap already reached (2), the FAREWELL SOCIAL_HEART is still effective and `fsm_mandated=True`. Non-mandated DIALOGUE repeats are suppressed (`cooldown_same_emote_consecutive`, `cap_APPLAUSE_2_per_visit`).
- Late anchor / start-delay cap: with the onset policy the c08 leads (<= 1.40 s) can never exceed the 1.5 s cap, so the horizon check was exercised with the stroke policy on a real WAV (1.56 s needed) and with an inflated lead (2.12 s). Both -> `rejected_horizon`, plan has no dispatches.
- **G1 (former extra probe, now closed):** `probe_trigger_text_vs_span_mismatch` showed that `anchor_plan.py` planned on `speech[start:end]` without checking that it equals `trigger_text`. In the real chain the span is derived from the validated trigger by `VisitGestureManager`, and `trace_check.py` verifies that equality on all 25 rows, so no real turn was affected, but a corrupted manifest would not have been caught at this stage. **Fix applied after the measurement:** `anchor_plan.py` rejects the row with status `rejected_trigger_span` (reason `speech[start:end] != trigger_text`; also when `trigger_text` is null with a span), with no anchor, event or plan files. The traceability numbers (1635/1635) were not recomputed for this change; the twin runs they come from are not in this repository.

## 3. Latency budget (from logs; n = 30 turns, 48 LLM requests)

| stage | p50 s | p95 s | max s | source / note |
|---|---|---|---|---|
| LLM in container (llama-server, first attempt) | 1.44 | 1.59 | 1.60 | `llm.attempts[0].metrics.total_s` (Qwen3-4B Q4_K_M, ~71 tok/s, ~97 completion tokens) |
| LLM with docker exec (wall) | 1.58 | 1.71 | 1.72 | `wall_s`; docker exec overhead about +0.13 s |
| LLM per turn incl. validator retry / demo resampling | 1.51 | 5.86 | 9.79 | 4/30 turns used the validator retry (+~1.4 s each); 9 extra resample turns in 3 dialogue turns are demo-only (intended-emote loop), not part of a production path |
| TTS synth (Supertonic M1) | 0.66 | 0.87 | 1.01 | audio p50 4.15 s, RTF p50 0.16 |
| CTC align (QuartzNet CPU, 2 threads, cold WAV) | 0.086 | 0.116 | 0.125 | `latency_ctc.json`; model load once 0.6 s; resolution 20 ms |
| plan_event (c09 scheduler) | 0.0004 | 0.0005 | 0.0006 | |
| required start-of-audio delay, presets (c08 MC pre-roll 1.0 s + onset lead ~1.2 s - anchor) | 0.37 | 0.54 | 0.56 | 20 preset turns; 0 s when the anchor is late (VIP farewell) |
| same, hypothetical zero pre-roll | 0.00 | 0.00 | 0.00 | `plan_preroll0`; c11b showed pre-roll cannot be removed (tick 0 falls) |
| MC service RTT (4c) | 0.0009 | 0.0016 | 0.0033 | |
| SONIC offline: StreamTalk | 20.6 | 23.4 | 25.4 | n=13 clips, RTF 4.7-8.7 |
| SONIC offline: GMR retarget | 4.9 | 5.6 | 6.5 | RTF 1.2-2.2 |
| SONIC offline: convert + rollout + prep_arms | 1.7 + 2.6 + 0.8 | 1.8 + 2.9 + 0.8 | 1.9 + 3.2 + 0.8 | RTF < 1 |
| SONIC offline total per clip | 30.6 | 34.0 | 37.1 | sequential |

End-to-end "Qwen reply complete -> audio start" (sequential estimate = TTS + CTC + plan + required start delay):

| turn type | p50 | p95 | what is real time today |
|---|---|---|---|
| PRESET (20/30 turns) | **1.12 s** (of which 0.37 s start delay) | 1.35 s | everything; audio is held back by the MC pre-roll lead. With zero pre-roll it would be 0.83 s |
| NONE, audio only | 0.66 s | 0.87 s | audio can start after TTS, but there is no co-speech gesture |
| NONE with SONIC co-speech | ~31 s | ~35 s | **offline**: StreamTalk + GMR + convert + rollout + prep run after the WAV exists; the 4c run replays precomputed arms |
| POINT | ~31 s | ~35 s | **offline** SONIC base (same chain); only the anchor-timed pointing overlay (`stream_point.py`) is real time |

Add the LLM reply time (1.4-1.6 s) for "user utterance -> audio start". Nothing in the StreamTalk/GMR/SONIC chain is real time (RTF 4.7-8.7 for StreamTalk alone); SONIC streaming around presets inside one turn is future work (c12).

## 4. Resources and safe concurrency

Machine: 22 GB RAM (about 8-9 GB already used by the desktop and other agents), 12 GB VRAM, 2 GB swap. A previous freeze happened with twin + x2-llm + StreamTalk + eval running together.

| stage | RAM (peak RSS) | VRAM | CPU/GPU load | wall | source |
|---|---|---|---|---|---|
| x2-llm container (llama-server Qwen3-4B Q4_K_M) | 5.3 GiB idle, 5.7 GiB peak | 3.9 GiB resident | GPU util mean 65 %, peak 99 % while generating; 1 CPU core peak | 1.4 s/request | visits_v2/resource_profile.json |
| Supertonic TTS (CPU worker) | 0.5 GiB | 0 | 1-2 cores | 0.66 s/utterance | same |
| QuartzNet CTC (ONNX, CPU, 2 threads) | 0.28 GiB measured here (c09 guard: 2 GiB preflight / 1.5 GiB stop) | 0 | 2 cores | 0.09 s/utterance | latency_ctc.json |
| twin container (MuJoCo + MC) | ~3.1 GB | 0 (no render in 4c) | not profiled | real time | task brief |
| StreamTalk (S1) | 4.26 GB | 2.3 GB (+15 MiB base) | GPU util mean 2 %, peak 56 % | 20.6 s/clip | sonic_batch |
| GMR (S2) | 3.9 GB | 0 | CPU | 4.9 s | sonic_batch |
| SONIC convert (S3) / rollout (S4, MuJoCo CPU) / prep_arms (S5) | 2.0 / 2.2 / 0.08 GB | 0 | CPU | 1.7 / 2.6 / 0.8 s | sonic_batch |
| 4c replay scripts (`run_visits.py`, `stream_point.py`) | not profiled (small) | 0 | | per turn 10.9 s mean incl. reset | 4c |
| eval (`run_eval`, Face venv + Supertonic) | ~0.5-1 GB (not separately profiled) | 0 | | | |

Sums: real-time loop (x2-llm + Supertonic + CTC + twin) ~ 9.6 GB RAM, 3.9 GB VRAM -> safe. Offline SONIC chain alone ~ 4.3 GB / 2.3 GB VRAM -> safe. The freeze set (twin 3.1 + x2-llm 5.7 + StreamTalk 4.3 + eval ~1 = ~14 GB on top of 8-9 GB baseline) exceeds 22 GB and the 2 GB swap cannot absorb it. VRAM is not the constraint (3.9 + 2.3 = 6.2 of 12 GB); system RAM is.

Rules:
1. At most one heavy offline stage at a time (StreamTalk, GMR, convert, rollout run strictly sequential, as `sonic_batch.py` does).
2. StreamTalk/GMR only with x2-llm **or** the twin stopped (never both up); preferably both down. Re-start the LLM after the SONIC batch.
3. Before any stage check `MemAvailable >= stage peak + 4 GB` (StreamTalk needs >= 8 GB free).
4. Eval / visit generation (LLM + TTS + CTC): x2-llm up, twin and StreamTalk down. 4c replay (twin): x2-llm may stay up (it is idle) but no StreamTalk/GMR.
5. Do not run two GPU consumers that are both large at once; keep QuartzNet at 2 CPU threads and its memory guard.
6. The earlier 4aa OOM did not reproduce with a single process; treat RAM as a hard limit (swap is only 2 GB).

## 5. Consolidated gates

Gates marked "doc" come from the c08/c10 reports or the 4aa/4c analysis code; gates marked "4d" are thresholds I applied where no formal gate exists.

| area | gate | value | result |
|---|---|---|---|
| Contract | JSON valid (eval v2.1, 104 runs) | 100 % | PASS (4d) |
| Contract | contract valid, final / first attempt | 86.5 % / 73.1 % (retry recovered 14) | PASS final (4d >= 85 %); first attempt weak |
| Contract | NONE false-positive (spurious gesture) | 0 spurious gestures; 3/16 NONE cases came back INVALID (18.8 % miss, precision 1.0) | PASS (safety); recall 0.81 |
| Contract | per-emote precision / recall | NEG_NORMAL 1/1, NEG_STRONG 1/1, CONFIRMATION 1/1, THUMBS_UP 1/0.875, APPLAUSE 1/0.75, SOCIAL_HEART 1/0.50 | precision PASS; recall FAIL for SOCIAL_HEART (0.50) and APPLAUSE (0.75) vs 4d >= 0.85 (misses are rejections, i.e. no gesture) |
| Contract | rank leak in final speech; STAFF phrase; trigger literal | 0; 100 %; 95.6-96.7 % | PASS |
| Contract | POINT direction accuracy | 91.7 % (R 100, L 100, FRONT 75, the miss is a 3-word speech rejected) | PASS (no wrong direction emitted) |
| Contract | VIP farewell SOCIAL_HEART valid rate | 2/4 (other 2 INVALID, fail closed) | FAIL (4d); mandated gesture lost in 50 % of eval runs before retry |
| Traceability | raw -> verdict -> gesture -> sha -> anchor -> plan -> trace | 1635/1635, 0 mismatches | PASS |
| Fail-closed | 39 required injections (incl. G1 and the auto-policy fallback, added after measurement) | 39/39 | PASS |
| Fail-closed | G1 trigger_text vs span at anchor_plan (former extra probe) | rejected_trigger_span | PASS after the guard was added; the original run planned on the span (gap G1, no real turn affected) |
| Anchors | CTC alignment rate (5 visits) | 25/25 aligned (100 %); 0 alignment errors | PASS; not human validated, 20 ms resolution |
| Anchors | start delay <= 1.5 s cap | max 0.56 s | PASS |
| Presets c08 | G16 accepted/completed, G17 pelvis z >= 0.50, G18 onset sd <= 0.04 s | 12/12, 0.693 m, <= 0.019 s | PASS |
| Presets c08 | G19 stroke vs CSV <= 0.15 s | 11/12 bindings; 3029/11 NEGATION_NORMAL -0.76 s (head clamp) | FAIL for 3029/11 (used in staff_t02, flagged `clamp_flag`) |
| Presets c10 | G20 jitter <= 5 ms; G21 onset <= 0.10 s med / 0.15 s max; G22 stroke policy; G23 pelvis z | 0.0002 ms; med 5 ms, max 14 ms; med 5, max 15 ms (3 cases only); 0.6925 m | PASS x4 |
| Presets 4c | executed == contract | 20/20 and 20/20 | PASS |
| Presets 4c | onset vs anchor within 100 ms | 19/20 (r1), 20/20 (r2); median 7.0 / 9.4 ms; max 1158 ms (vip_t00 r1) / 40 ms | PASS (doc: vip_t00 r1 artifact) |
| Presets 4c | dispatch jitter <= 5 ms | 0.0002 ms | PASS |
| Presets 4c | stroke vs anchor (not gated) | median 693 / 700 ms after anchor | info (onset policy; stroke follows by the preset's own onset->apex time) |
| Pointing 4aa READY base (9 runs) | mean err over hold <= 15 deg; err at anchor <= 15 deg | 2.2-4.4 deg; 5.5-12.5 deg | PASS |
| Pointing 4aa READY | hold 1-2 s; pelvis z >= 0.5; return < 0.05 rad; URS; jitter | 1.78-2.04 s; 0.69 m; 0.011-0.014; all URS; <= 0.19 ms | PASS |
| Pointing 4aa READY | arm reaches 95 % within +-0.1 s of anchor | FRONT +38..+51 ms OK; RIGHT/LEFT -170..-185 ms (early) | FAIL strict for L/R, early arrival accepted by user |
| Pointing 4aa SONIC base (9 runs) | mean err over hold <= 15 deg | 5.3-6.3 deg | PASS |
| Pointing 4aa SONIC | err at anchor <= 15 deg | RIGHT 6.3-6.4, LEFT 10.8-11.4, FRONT 15.6 / 15.1 / 14.1 | FAIL for FRONT r1, r2 (`gate_err_hold` false) |
| Pointing 4aa SONIC | hold, tracking outside (lag-4, <= 0.015 rad), return, pelvis, URS | 1.76-1.94 s; 0.0054-0.0092; 0.012-0.018; 0.692 m; yes | PASS |
| Pointing 4aa SONIC | arrival vs anchor +-0.1 s | FRONT +39..+54 ms, LEFT -70..-93 ms OK; RIGHT -106..-117 ms | FAIL strict for RIGHT (early, accepted) |
| Pointing edges (4aa) | speed/jerk at prep start vs SONIC base; recovery end | cmd jerk 756-1600 vs base 1114-1833 (not worse than the base); end speed 0.06-0.10 rad/s | PASS (qualitative, no formal gate) |
| Pointing 4c (10 turn-reps) | mean err over hold <= 15 deg | max 8.1 / 8.2 deg | PASS |
| Pointing 4c | err at the anchor instant <= 15 deg | guest FRONT 15.9/15.2, staff LEFT 15.4/15.4, external FRONT 20.0/20.3; others 4.7-9.9 | **FAIL 6/10 turn-reps** (see F2) |
| Pointing 4c | hold (prog99) 1-2 s; SONIC tracking outside gesture; return; jitter; pelvis | 1.5-1.82 s; <= 0.0104 rad; <= 0.019 rad; <= 3.4 ms; >= 0.69 m | PASS |
| NONE / SONIC | arm tracking error <= 0.015 rad, 10 turn-reps | 0.0088-0.0097 rad | PASS |
| Stability | falls | 0 / 60 turns (both reps) | PASS |
| Stability | pelvis z min | 0.684 m (r1) / 0.693 m (r2) (gate >= 0.50) | PASS |
| Stability | 4c per-turn gates | 29/30 (r1), 30/30 (r2) | PASS (r1 fail = vip_t00 onset artifact) |
| SONIC batch | GMR QC | 12/13 PASS; regular_t01 seed 1 self-collision 0.65, seed 2 PASS (kept in `_seed1_GMR_FAIL_*`) | PASS after re-seed |
| Real time | NONE/POINT Qwen reply -> audio start | ~31-35 s offline | FAIL for real time (preset path 1.1 s PASS) |

### Additional findings
- F2: 4c REPORT lists the FRONT error at the anchor instant (14-20 deg) as the caveat; the CSV also shows staff_t01 LEFT at 15.4 deg in both reps, and the aggregate `gates_ok` does not include `gate_err_anchor15`, so 30/30 hides 6 turn-reps above 15 deg at the anchor (mean over hold is always <= 8.2 deg).
- F3: the sharper quantity for POINT is time-to-95 % arrival: -53 to -127 ms for RIGHT/LEFT (early), +61 to +67 ms for FRONT.
- F4: the 3001 bow (vip_t00) tilts the pelvis to 23.0 / 20.9 deg (c10 measured 18.3 deg); still no fall, z 0.692 m.
- F5: demo visits use an intended-emote resample loop (up to 4 extra turns); external_t02 intended APPLAUSE ended as SOCIAL_HEART after 4 resamples. This loop is not part of a production path.
- F6 (LLM content, not contract): the NONE replies are not reliable: guest and external answer by echoing the visitor question ("Cual es la hora de cierre del laboratorio hoy?"), vip and staff state invented facts ("El laboratorio cierra a las 18:00 horas", "El clima esta despejado y soleado"). The contract validates gestures and form, not factual content.

### Known caveats carried forward
- vip_t00 rep1 onset error (-1158 ms) is an artifact of the first turn after twin start (pre-dispatch baseline not settled); rep2 +15 ms.
- FRONT error at the anchor instant is high because the SONIC base starts farther from the target; mean over the hold is 5-8 deg.
- Early arrival of POINT (-53..-127 ms RIGHT/LEFT) is accepted, not gated.
- Presets use the onset policy: the preset stroke lands ~0.7 s after the anchor (median 693-700 ms; c10 +0.54..+1.02 s) because of the preset's own onset->apex time.
- LLM hallucinated facts (F6); small simulated utterance sets, per-emote recall optimistic.
- Twin MC v1.0.0 (1.0 s pre-roll, overlay workaround for `SetMcPresetMotion`), not the robot MC v1.1.4: latencies, pre-roll and the plans must be re-measured and regenerated on the robot.
- The real robot arm limit is 0.35 rad/s (the twin pointing overlay moves at up to 2 rad/s; the `real035_*` metrics show only ~25-36 % progress at the anchor at that speed), so pointing timing will not transfer without re-planning.
- SONIC around presets within one turn (preset + SONIC blend) is future work c12; today a turn is either a preset, or a SONIC clip (NONE / POINT).
- `preplaying_tick` must stay 500 (c11b: tick 0 makes 3001 fall 3/4 takes and snaps arms at 22-46 rad/s; 100/250 untested).
- 3029/11 head clamp on the twin makes its stroke timing unreliable (flagged).
- CTC anchors are not human validated (20 ms resolution); playback in 4c is virtual (no audio device latency or clock drift).

## Files
- `HRIEGO_gesture/VALIDATION_4d.md` (this report)
- `HRIEGO_gesture/validation/{trace_check.py,trace_check.json,trace_check_table.csv,failclosed.py,failclosed_anchor.py,failclosed.json,latency_ctc.py,latency_ctc.json,latency_budget.py,latency_budget.json}`
