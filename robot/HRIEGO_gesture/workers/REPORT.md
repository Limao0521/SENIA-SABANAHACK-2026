# Persistent workers: feasibility of an online StreamTalk -> GMR -> SONIC deploy

Machine: 22 GB RAM (about 17 GB MemAvailable incl. cache, twin and x2-llm stopped), 12 GB VRAM, 2 GB swap. Everything here was run with the twin and x2-llm off.
Code: `common.py` (JSON-lines protocol), `st_worker.py` (env physicalai), `gmr_worker.py` (env gmr), `sonic_worker.py` (env physicalai), `wlib.py` (worker wrapper, MemAvailable guard, samplers), `bench.py` (warm / pipe / cold / compare).
Originals are untouched: the workers import and call `x2_retarget.retarget_one`, `x2_sonic.convert_one/rollout_one`, `streamtalk_cached_entry` (retrieval-cache patch) and `streamtalk_runner` classes; only the StreamTalk generation loop is copied (same code, per-request state rebuilt). Protocol: one JSON request per line on stdin, one reply per line on stdout (ready line first, then per-request wall, RSS/HWM, stage timings).

## Result in one paragraph
Persistent workers cut the chain from about 31 s cold per clip to about 6.3 s warm (p50, 4-10 s clips, RTF about 0.8), outputs are bit-identical to cold for the same seed, and pipelining the stages gives 2.1x throughput. The three workers together hold about 7.9 GB RSS steady (about 10 GB at transient peaks) and 2.3 GB VRAM. They fit next to x2-llm only with a thin margin (not with the 4 GB safety margin at peaks) and do NOT fit next to x2-llm plus the twin on this 22 GB machine.

## Per-worker resources (sampled 1 s RSS, 0.5 s nvidia-smi; `bench/samples_*.csv`)
| worker | load (s) | RSS after load | steady RSS (median while serving) | peak RSS (HWM) | VRAM steady/peak | notes |
|---|---|---|---|---|---|---|
| StreamTalk | 20.4 | 2.5 GB | 2.6 GB | 4.26 GB (during load only) | 2.29 GB / 2.29 GB | per-request torch peak alloc about 2.0 GB; models + retrieval DB resident |
| GMR (X2 IK) | 2.5 | 2.5 GB | 2.7-2.9 GB | 4.2 GB (per-request transient) | 0 | CPU only, 2 threads |
| SONIC convert+rollout | 0.5 | 0.33 GB | 1.9-3.1 GB (creeps about 30 MB/req) | 3.4 GB (transient) | 0 | CPU only; `malloc_trim` after each request is needed, otherwise RSS stays at 2.7-3.0 GB and creeps |
| sum of 3 | 23.4 | 5.4 GB | 7.9 GB (8.6 GB under pipelining) | about 10.0 GB (pipelined peak) | 2.3 GB | MemAvailable min during runs: 9.4 GB (serial), 8.3 GB (pipelined), starting from 16.6-17.1 GB |

Cold per-stage peaks (reference, `/usr/bin/time -v`): StreamTalk 4.26 GB RSS / 2.30 GB VRAM, GMR 3.9 GB, convert 2.05 GB, rollout 2.2 GB.

## Cold vs warm latency (per clip, same code, same seed)
Cold (2 clips measured, workers stopped; each stage in a fresh process): S1 StreamTalk 21.4-22.3 s, S2 GMR 5.1 s, S3 convert 1.2 s, S4 rollout 2.8 s, total 30.5-31.5 s (mean 31.0 s).
Warm (19 commercial clips, serial through the workers; requests including JSON/disk overhead):

| stage | p50 s | p95 s | mean s | notes |
|---|---|---|---|---|
| StreamTalk | 1.55 | 2.44 | 1.55 | WavLM about 0.07 s, diffusion about 0.27 s per 1.7 s segment; sums over seed retries |
| GMR retarget | 2.17 | 3.81 | 2.29 | sums over seed retries |
| SONIC convert | 0.64 | 0.66 | 0.65 | cold 1.2 s |
| SONIC rollout (no record) | 1.78 | 2.00 | 1.74 | simulates the whole clip faster than real time |
| end-to-end WAV -> trace | 6.31 | 8.51 | 6.32 | 17 single-attempt clips: p50 6.28, p95 6.68; RTF p50 0.82 |

Speedup about 4.9x (31.0 s -> 6.3 s). Of the warm 6.3 s, the rollout (about 2.4 s with convert) is validation/trace only; on a live twin/robot the playback replaces it, so generate-to-play is about 3.7-4.3 s (StreamTalk + GMR + convert) for a typical 8 s clip.

Warm vs cold equality (clips 003 and 008, seed 1): SMPL-X poses/trans/expressions, GMR qpos, SONIC pkl dof and the trace CSV all have max abs diff 0.0 and the trace CSVs are byte-identical (`bench/compare_results.json`). This needs the CPU RNG state captured after the cold-order model construction (cold consumes torch CPU RNG while initialising models). That is exact only for the load seed (1). Retry seeds 2-4 are valid but not bit-identical to a cold run of the same seed.

## Pipelined (12 clips, 3 workers in parallel threads; `bench/pipe_results.json`)
| mode | makespan | latency p50 / p95 | note |
|---|---|---|---|
| serial warm (same 12 clips) | 80.9 s | 6.3 / - | baseline |
| pipelined, all arrive at t=0 | 38.7 s | 25.6 / 36.2 (queueing) | throughput 2.1x, bottleneck = SONIC stage 2.8 s/clip |
| pipelined, arrival every 3.26 s (1.15 x bottleneck) | 42.5 s | 7.4 / 7.7 | steady stream: latency about 17% above solo (CPU contention between GMR, SONIC, ORT and StreamTalk, 32 logical cores) |
Pipelining helps back-to-back throughput, not single-turn latency. Stage walls under pipelining: StreamTalk 1.8, GMR 2.5, SONIC 2.8 s.

## Fit / no-fit on this machine (budgets from the task: x2-llm +5.7 GB RAM / 3.9 GB VRAM, twin +3.1 GB RAM; MemAvailable about 17 GB at start, rule: keep 4 GB free)
| configuration | RAM steady / peak | VRAM | verdict |
|---|---|---|---|
| 3 workers alone | 7.9-8.6 / about 10.0 GB | 2.3 GB | fits (min MemAvailable observed 8.3 GB) |
| 3 workers + x2-llm | 13.6-14.3 / about 15.7 GB | 6.2 GB of 12 | fits VRAM; RAM leaves about 1.8 GB at peak (below the 4 GB margin): marginal, only if browsers/page cache are small |
| 3 workers + twin | 11.0-11.7 / about 13.1 GB | 2.3 GB | fits with about 4.4 GB margin at peak |
| 3 workers + x2-llm + twin | 16.7-17.4 / about 18.8 GB | 6.2 GB | NO FIT (needs about 19 GB; swap is 2 GB) |
| StreamTalk worker only warm + cold GMR/SONIC per turn | 2.6 + (cold peaks 3.9 / 2.2, one at a time) + x2-llm + twin = about 14.4 GB peak | 2.3 GB | fits, per-turn about 1.6 + 5.1 + 1.2 + 2.8 = about 10.7 s (or about 7.9 s without rollout) |
| StreamTalk + GMR workers, convert/rollout cold or on the twin | 5.3-7.5 GB resident + x2-llm + twin = about 14.1-16.3 GB | 2.3 GB | borderline; drop the rollout (twin plays the motion) and convert is 0.6 s warm and small |

## Expected per-turn latency of an online deploy (after TTS audio exists)
- All three warm: about 6.3 s p50 for the full WAV -> SONIC trace; about 4.3 s to a playable SONIC pkl (no rollout, rollout is replaced by the twin/robot).
- Add LLM and TTS time on top; the persistent workers remove about 21 s (StreamTalk load) + 3 s (GMR) + 1 s each for the other stages.
- Back-to-back turns: pipelining keeps latency about 7.4 s at a 3.3 s arrival interval.

## Can StreamTalk stream/chunk? (from code, not built)
Yes, feasible with moderate work. `streamtalk_runner.py` already generates per 2 s WavLM window (60 frames, 8-frame overlap; about 52 new frames = 1.73 s of motion per segment) in a sequential loop whose only state is `pose_prompt`/`trans_prompt` (last 8 frames) and the retrieval injection. So: (1) WavLM runs per window (0.07 s for all windows of a clip), (2) each segment costs about 0.27 s warm, (3) segment i can be emitted once segment i+1 has produced its first 8 frames (the overlap blend, 0.27 s lookahead) -> latency to first motion about 0.3-0.6 s after the first 2 s of audio exist. Blockers: the loop must be turned into a generator that accepts audio incrementally (the final partial window is zero-padded and sized from `output_frame_counts`, known only at end of audio); downstream GMR needs a chunked mode (`retarget_frames` canonicalisation uses clip height and `ground_track` is temporal, so state must be carried); SONIC convert adds a start ramp (hold+blend 2.75 s) per clip, which would be per-stream, not per-chunk. IndexTTS currently emits a whole WAV, so true streaming also needs chunked TTS.

## Recommendations
1. Use persistent workers: 31 s -> 6.3 s with bit-identical outputs. Keep them as processes with a thin queue; the JSON-lines protocol is already enough.
2. On this 22 GB machine run `3 workers + twin` or `3 workers + x2-llm`, not all four. For the full stack either (a) move the SONIC convert/rollout worker off the box (it is CPU only, 0.33 GB idle but 3.4 GB transient), (b) skip rollout in production (the twin is the player) and keep only convert (0.64 s), (c) use only GMR+StreamTalk workers, or (d) add RAM/swap. Do not rely on the 2 GB swap.
3. Cap transient peaks: SONIC and GMR spike +1.2-1.5 GB per request; `malloc_trim` after each request is already in `common.py`; recycle the SONIC worker every few hundred requests (RSS creeps about 30 MB/request).
4. Spend effort next on the bottlenecks: GMR (2.2 s, CPU IK, 2 threads), then the SONIC rollout (1.8 s, drop it online), then StreamTalk diffusion (1.3 s). Streaming StreamTalk+GMR in chunks would cut time-to-first-motion to about 1-2 s.
5. The seed-1 GMR `self_collision` failure rate was 2/19 here (007, 011); the retry with seed 2 passed both. Budget one extra StreamTalk+GMR pass (about 3.7 s) on about 10% of turns.

## Commercial workload (19 IndexTTS WAVs, NO prep_arms), outputs in `voice/experiments/x2_commercial_show_indextts_corregido_20261022/motion/<clip>/` (smplx/, x2/retarget/, x2/inputs/, sonic/, traces/full_<clip>.csv, logs/, status.json with the GMR QC)
All 19 clips: GMR PASS, SONIC convert PASS (rollout traces written; max tilt 8.0-14.0 deg, min pelvis z 0.63 m on all 19, so no falls). Two clips (007, 011) failed GMR QC `self_collision` on seed 1 and passed on seed 2 (the failed seed-1 SMPL-X/retarget outputs were overwritten; their QC is kept in each `status.json` -> `all_attempts`).
| clip | audio s | seed (attempts) | GMR QC | SONIC | e2e warm s |
|---|---|---|---|---|---|
| 001_ULTRA_bienvenida | 10.136 | 1 (1) | PASS | PASS | 8.28 |
| 002_X2_original_bienvenida | 8.139 | 1 (1) | PASS | PASS | 6.32 |
| 003_ULTRA_bienvenida | 7.338 | 1 (1) | PASS | PASS | 6.03 |
| 004_X2_original_serie_x | 8.742 | 1 (1) | PASS | PASS | 6.48 |
| 005_ULTRA_serie_x | 8.777 | 1 (1) | PASS | PASS | 6.5 |
| 006_X2_original_serie_x | 5.224 | 1 (1) | PASS | PASS | 4.85 |
| 007_ULTRA_serie_d | 7.779 | 2 (2) | PASS (seed 1: self_collision) | PASS | 10.0 |
| 008_X2_original_serie_d | 7.906 | 1 (1) | PASS | PASS | 6.28 |
| 009_ULTRA_serie_d | 8.023 | 1 (1) | PASS | PASS | 6.31 |
| 010_X2_original_serie_c | 5.503 | 1 (1) | PASS | PASS | 5.1 |
| 011_ULTRA_serie_c | 6.467 | 2 (2) | PASS (seed 1: self_collision) | PASS | 8.51 |
| 012_X2_original_serie_c | 7.419 | 1 (1) | PASS | PASS | 6.25 |
| 013_ULTRA_serie_g | 8.696 | 1 (1) | PASS | PASS | 6.59 |
| 014_X2_original_serie_g | 8.58 | 1 (1) | PASS | PASS | 6.68 |
| 015_ULTRA_serie_g | 8.139 | 1 (1) | PASS | PASS | 6.34 |
| 016_ULTRA_despedida | 6.699 | 1 (1) | PASS | PASS | 5.64 |
| 017_X2_original_despedida | 5.027 | 1 (1) | PASS | PASS | 4.76 |
| 018_ULTRA_despedida | 3.785 | 1 (1) | PASS | PASS | 4.49 |
| 019_X2_original_despedida | 4.342 | 1 (1) | PASS | PASS | 4.65 |

The 13 HRIEGO visit/4aa clips were NOT run (not needed for latency samples: 19 commercial + 12 pipelined requests already give them).

## Files
- `workers/{common,wlib,st_worker,gmr_worker,sonic_worker,bench}.py`
- `workers/bench/resource_latency.{json,csv}` (summary), `warm_results.json`, `pipe_results.json`, `cold_results.json`, `compare_results.json`, `samples_warm.csv`, `samples_pipe.csv`, `*_worker.log`, `warm.log`, `pipe.log`, `cold/<clip>/`, `pipelined/{burst,staggered}/<clip>/`
- Re-run: `python bench.py warm | pipe 12 | cold <clips> | compare <clips>` with the physicalai python.
