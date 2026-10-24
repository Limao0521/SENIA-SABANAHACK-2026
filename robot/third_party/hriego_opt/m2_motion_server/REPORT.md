# M2 - Prototype of the ONLINE motion server (x86, 2026-10-22)

One persistent `physicalai` process: StreamTalk (lean, `.half()`, mmap retrieval) -> GMR (lean `.mjb`, mink) -> SONIC (lean `.mjb`, ONNX), streaming; output = 50 Hz stream of the
*actual* SONIC arm(14) + waist(3) + head(2) joints (what `prep_arms` extracts), no full-clip rollout, no CSV/pkl traces.
All runs: `systemd-run --user --scope -p MemoryMax=5G -p MemorySwapMax=0 taskset -c 0-3` (works here; no OOM kill), x2-llm and twin off, one process at a time, 10 clips of `t2_streamtalk_precision/clips.json`
(3.4-10.1 s, 55 s total), 1 warm-up request (3 s) before the timed ones, single run per variant (no variance estimate). Raw: `out/<variant>/`, `metrics.json|csv`, `metrics_extra.json`, `gmr_bench.json`, `logs/`.
Hardware is the x86 dev PC (4 pinned cores + RTX); Orin figures are NOT measured.

## 1. Design (files)
| file | what |
|---|---|
| `st_stream.py` | `load()` = M1 lean loader (fp16 `.half()` generator+WavLM, mmap 6D pose table, no cKDTree, trim, RNG state of production). `stream(audio)` = `workers/st_worker.gen` as an **incremental generator**: WavLM per 2 s window on demand, window n emitted right after it is generated. Window = 2.0 s audio, stride 1.733 s (52 frames @30 fps); the last 8 frames of each window are held back and blended with the first 8 of the next (exactly the batch rule), so a window emits 52 frames. Bit-identical to `gen()` (`test_st_equivalence.py`: max diff 0.0) provided the CPU RNG position of the batch code is reproduced (WavLM draws 24 CPU randoms per call; `batch_rng=True` skips them; live use does not need bit-equality). **Structural lookahead: the first frame needs 2.0 s of audio.** |
| `gmr_stream.py` | GMR inside physicalai (`site_gmr/` symlinks to gmr-env `mink` + patched `smplx`; IK model from `ik_lean_3.11.0.mjb`; `mj_setCacheCapacity(0)`). `GmrStream` = one retargeter per request, fed SMPL-X blocks, yields qpos **frame by frame** (canonical SMPL-X FK on the block, `get_gvhmr_data_offline_fast`, warm start, per-frame velocity clamp, stateful across blocks). Variants: `solver` native_qp / analytic_gmr; `rate` 30 (production), 15 / 10 (decimated source frames), 50 (human frames nlerp-interpolated to 50 Hz). No ground tracking (SONIC does not observe root z; offline uses it only for init z). "Armed" mode: object + collision limit + 20-iteration warm start on the READY pose are prepared while idle; at request time 3 iterations on frame 0 (instead of 20). |
| `sonic_stream.py` | `Resampler` (rate -> 50 Hz, linear + slerp, same grid/clamp as `x2_sonic.resample`, incremental) and `SonicOnline`: port of `eval_x2_mujoco_onnx.step_once` (bigrun deploy tuning, ONNX, 4 x 5 ms MuJoCo substeps) + `convert_one` entry prefix (hold + smoothstep blend), stepping 50 Hz as soon as the reference has the 0.9 s lookahead the tokenizer reads (10 future frames). Reference kept in a ring (~1 s), vectorised tokenizer (rotation matrices precomputed per frame). Prefix `exact` = 25 hold + 1.5 s blend (== offline), `fast` = 2 hold + 0.5 s blend (0.54 s). |
| `server.py` | `MotionServer.load/arm/run` + benchmark CLI. `--pipeline serial` (pull chain of generators in one thread) or `threads` (StreamTalk and GMR stages in their own threads with bounded queues, SONIC in the main thread). `--audio-rtf r` gates window availability at `r x audio time` (0 = whole WAV available). |
| `offline_ref.py`, `analyze.py`, `gmr_bench.py`, `run.sh`, `sweep3.sh` | reference chain (current production workers on the SAME StreamTalk output), tables, GMR micro-benchmark, Orin-like launcher, run list |

Command stream timing definitions (wall clock since the WAV/first window is available): `first_cmd` = first 50 Hz command emitted; `D = max_k(wall_k - 0.02 k)` = how long the whole stream
must be delayed so every tick is ready on its 50 Hz slot (steady-state lag vs the clock is then <= 0; it only grows if a stage is slower than real time); `audio_start = D + prefix` = earliest moment the audio can start
playing in sync with the gesture (the entry ramp precedes audio time 0). With the whole WAV available, "from WAV" == "from first audio window" (window 0 = first 2.0 s).

## 2. Latency (10 clips; mean / max over clips; times in s; armed; prefix fast unless noted)
| variant | first cmd | D (lead) | audio start (D+prefix) | RTF total (mean/max) | per-window compute (ST+GMR+SONIC, serial) mean/max vs 1.733 s | windows over real time |
|---|---|---|---|---|---|---|
| native 30 Hz, serial, prefix exact (== offline entry) | 0.49 / 0.51 | 0.49 / 0.51 | 2.49 / 2.51 | 0.78 / 0.82 | 1.28 / 1.54 | 0% |
| **native 30 Hz, serial** | 0.58 / 0.61 | 0.58 / 0.61 | **1.12 / 1.15** | 0.75 / 0.79 | 0.24 + 0.75 + 0.22 = 1.22 / 1.52 | 0% |
| native 30 Hz, threads | 0.63 / 0.66 | 0.63 / 0.66 | 1.17 / 1.20 | 0.68 / 0.74 | (stage sums inflated by contention: 2.1) | n/a |
| **native 15 Hz, serial** | 0.53 / 0.54 | 0.53 / 0.54 | **1.07 / 1.08** | 0.59 / 0.61 | 0.24 + 0.49 + 0.21 = 0.93 / 1.04 | 0% |
| native 15 Hz, threads | 0.57 / 0.60 | 0.57 / 0.60 | 1.11 / 1.14 | 0.50 / 0.52 | - | n/a |
| native 50 Hz, serial | 0.64 / 0.66 | 0.71 / 1.03 | 1.25 / 1.57 | 0.96 / 1.06 | 0.24 + 1.10 + 0.23 = 1.57 / 2.11 | 11% |
| native 50 Hz, threads | 0.70 / 0.71 | 0.71 / 0.78 | 1.25 / 1.32 | 0.91 / 1.02 | - | n/a |
| analytic 15 Hz, serial | 0.97 / 1.04 | 1.17 / 1.78 | 1.71 / 2.32 | 1.08 / 1.17 | 0.24 + 1.33 + 0.22 = 1.79 / 2.28 | 57% |
| analytic 15 Hz, threads | 1.12 / 1.22 | 1.23 / 1.59 | 1.77 / 2.13 | 0.96 / 1.06 | - | n/a |
| analytic 30 Hz, serial | 1.22 / 1.35 | 4.53 / 8.44 | 5.07 / 8.98 | 1.68 / 1.83 | 0.24 + 2.37 + 0.25 = 2.85 / 3.80 | 100% |
| analytic 50 Hz, serial | 1.58 / 1.81 | 9.49 / 17.6 | 10.0 / 18.1 | 2.48 / 2.71 | 0.24 + 3.74 + 0.25 = 4.23 / 5.72 | 100% |
| native 30 Hz, threads, audio arriving in real time (4 clips) | 2.59 (= 2.0 s of audio + 0.59) | 2.85 | 3.39 | n/a (audio-bound) | 1.30 / 1.44 | 0% |

- Time to first motion command after the first 2 s window of audio exists: **0.53-0.64 s** for native (0.49 with the long exact prefix because the first ticks need no real reference). Of that: StreamTalk window 0 = 0.25 s (WavLM ~0.03 + two diffusion passes), SMPL-X FK + frame conversion of the 52-frame block ~0.18 s (3.5 ms/frame; splitting the first block would save ~0.12 s - not done), 20->3 warm-start + first IK solves + SONIC start the rest. Without "armed" (retargeter built at request time, 20 warm iterations) the first command was ~1.2 s (first debug run), so the arming step is worth ~0.65 s.
- Per-window compute is stable across clips: ST 0.24 s (fp16, no growth with clip length), GMR native 0.75 s (14.5 ms/frame incl. 3.5 ms FK/conversion), SONIC 0.22 s (2.6 ms/tick, 87 ticks) => 70% of real time on this x86; 4-core pin did not hurt (GMR is ~1 core).
- Threads help total RTF slightly (0.75 -> 0.68, 0.59 -> 0.50) but delay the first command by ~0.05 s and add ~100 MB; on x86 the serial pull chain is already faster than real time. Command streams are identical between serial and threads (deterministic).
- Steady lag vs audio clock: for every real-time-capable variant D is set by tick 0 and the stream only gets *ahead* (mean last-tick lag -2 s). Variants with RTF > 1 (analytic 30/50 Hz) fall behind without bound (D 4.5-9.5 s mean, 8-18 s on the longest clip).
- Audio arriving in real time (`--audio-rtf 1`) shows the structural cost: motion cannot start before 2.0 s of audio. This is only a problem if TTS is not faster than real time (Supertonic RTF ~0.15 -> 2 s of audio in ~0.3 s).

## 3. Memory (physicalai process, x86; PSS from smaps_rollup sampled at 4 Hz; VRAM from nvidia-smi per PID)
| variant | PSS after load (MB) | PSS steady after 10 clips | PSS peak during runs | VmHWM (RSS peak) | VRAM (MB) | torch alloc peak | unified steady = PSS + VRAM |
|---|---|---|---|---|---|---|---|
| serial (all solvers/rates, 8 runs) | 3010-3035 | 3148-3182 | 3423-3542 | 3725-3748 | 1348 | 1560 | **~4.5 GB** (4.5-4.55) |
| threads (4 runs) | 3017-3037 | 3175-3295 | 3459-3597 | 3770-3882 | 1364 | 1560 | ~4.6 GB |
M1's merged number (2725 MB RSS + 1348 VRAM = 4.07 GB) was taken right after loading; here the same process holds +~0.3 GB after serving (ONNX/MuJoCo data, retargeter objects, glibc arenas) and +~0.15 GB during a request. Load time 7.5-8.3 s with warm page cache (StreamTalk 5.3 s, GMR 0.7 s, SONIC 0.7 s). No swap used, cgroup limit 5 GB never hit (peak RSS 3.9 GB includes file-backed mmap pages). The torch allocator peak (1.56 GB) vs 1.35 GB nvidia-smi is the same memory counted two ways; the retrieval table is mmapped (no 211 MB VRAM copy).

## 4. Quality vs the current offline chain
Reference: the production chain (GMR native_qp worker in the gmr env -> SONIC convert+rollout worker -> `prep_arms`) run on the **same StreamTalk output** the server produced (`offline_ref.py`; GMR QC 10/10 PASS; mean GMR 1.78 s/clip; tilt max 11.8 deg (mean of per-clip max 8.8), pelvis min 0.627 m, arm jerk 204 rad/s^3). RMSE on SONIC *actual* trajectories over the motion part (online motion frame `prefix` vs offline frame 100). Seed-to-seed reference of T3: fp32 arm RMSE 0.20 rad, waist 0.043.
| variant | arm RMSE (rad) mean / worst clip | waist RMSE | head RMSE | arm jerk vs ref | seam jerk ratio (window boundaries / elsewhere) | tilt max / mean-of-max (deg) | pelvis min (m) | falls | QC GMR (gmr_bench, 3 clips, after ground_track) |
|---|---|---|---|---|---|---|---|---|---|
| native 30, prefix exact | **0.031 / 0.081** | 0.011 | 0.019 | 1.03 | 0.92 | 13.3 / 9.3 | 0.624 | 0 | pass |
| native 30, prefix fast | 0.070 / 0.147 | 0.016 | 0.052 | 1.07 | 1.12 | 14.0 / 9.1 | 0.630 | 0 | pass |
| native 15, prefix fast | 0.073 / 0.143 | 0.015 | 0.050 | 0.95 | 1.00 | 11.9 / 8.7 | 0.626 | 0 | pass |
| native 50, prefix fast | 0.072 / 0.107 | 0.015 | 0.048 | 1.30 | 1.13 | 11.8 / 8.7 | 0.621 | 0 | pass |
| analytic 30 / 15 / 50, fast | 0.290 / 0.291 / 0.293 | 0.05 | 0.079 | 1.49 / 1.34 / 1.68 | 0.96 / 1.00 / 0.95 | 9.0 / 8.7 / 9.5 | 0.63 | 0 | pass |
| native 30 fast, audio at real time (4 clips) | 0.093 / 0.147 | 0.021 | 0.066 | 1.11 | 1.20 | 9.8 / 8.4 | 0.630 | 0 | - |
- Fidelity of the port: with the exact prefix the online chain matches the offline one to 0.031 rad (first 4 s: <= 0.008 rad); the residual is chaos amplification of the daqp 0.8.5 (physicalai) vs 0.9.1 (gmr env) QP difference and of dropped ground tracking. Everything is far below the 0.20 rad seed spread, as is the fast prefix (0.07).
- The shorter entry ramp (0.54 s vs 2.0 s) costs 0.04 rad of arm RMSE (the policy has less time to settle before the motion) and a slightly larger first-seconds transient; no falls, tilt max 14.0 deg (offline 11.8). Window seams show no discontinuity (ratio 0.92-1.2; the 8-frame blend works; 1.2 on the 4-clip real-time run is noise on 4 clips).
- Online vs the *stored* T3 fp16_s1 arms (different StreamTalk because lean `.half()` flips a retrieval key pose in some clips): 0.090 rad arm / 0.023 waist (per clip 0.01-0.26), i.e. within the seed spread.
- Analytic differs from native by 0.29 rad (same value as T3, 0.263), +34-68% jerk, but lowest tilt (9 deg). This is distance to native, not to ground truth.
- QC in `gmr_bench.py` (qc_checks after `ground_track`, as offline; native 30/15/50, analytic 30/15 on c010, c018, v_guest_t01): all pass, no self-collision. The server itself does not run QC online (needs the whole clip); a cheap online guard (finite + velocity clamp already present) is possible.

## 5. GMR solver verdict (micro-benchmark `gmr_bench.json`, 3 clips, 1 pinned process, IK only)
| config | ms per IK solve | ms per 30 fps source frame | arm qpos diff vs offline native 30 Hz | note |
|---|---|---|---|---|
| native 30 Hz (production) | 10.0 | 10.0 | 0.002 (same solver; online == offline) | + 3.5 ms FK/conversion per frame |
| native 15 Hz (+ linear interp to 50) | 11.4 | **5.7** | 0.035 | per-solve cost grows (more QP iterations on bigger steps); halves cost per frame; smoother (jerk 0.95) |
| native 50 Hz | 9.4 | 15.6 | 0.018 | +30% jerk, no accuracy benefit; RTF ~0.95 |
| analytic 30 Hz | 44.5 | 44.5 | 0.40 | 4.5x native (T3 offline measured 2.4x) |
| analytic 15 Hz | 51.4 | 25.7 | 0.40 | still 2.6x native 30; RTF 1.08 serial |
| native 30 Hz, leg/foot/pelvis frame tasks zeroed ("arms+waist only") | 9.9 | 9.9 | arm 0.168 / **waist 0.43** | no speed-up (the QP is the same 31-DoF problem with limits; cost is not in the tasks) and the waist is wrong => not feasible by task weights; would need a reduced-DoF model (arms+waist+head only, ~12 DoF) built in the IK .mjb, not tried |
**Verdict: native_qp, solved at 15 Hz (every 2nd 30 fps frame) and interpolated to 50 Hz, is the online choice** (RTF 0.59 vs 0.75 at 30 Hz; arm RMSE vs offline 0.073 vs 0.070, jerk 0.95 vs 1.07, tilt 11.9 vs 14.0; first command 0.53 s). native 50 Hz costs +30% jerk and 0.95 RTF without benefit; analytic is real-time only at 15 Hz with threads (RTF ~1.0), 1.0-1.2 s later to first command, 0.29 rad away from native with +34% jerk and no QC advantage - keep analytic out of the online path (only upside: lower tilt, 9 vs 12-14 deg). 15 Hz is the lever that gives margin on a slower CPU (GMR ~half of the per-window compute).

## 6. Projection: one SONIC turn end to end (x86 numbers; Orin will be slower)
| step | time | source |
|---|---|---|
| LLM B first sentence | 0.40 s | given |
| TTS: first >= 2 s of audio (or the whole first sentence if shorter; the generator pads a partial window) | ~0.30-0.40 s | Supertonic RTF ~0.15 (M1: 0.95 s for 6.4 s) + call overhead, estimated |
| motion server: first motion command after window-0 audio exists | +0.53-0.58 s | this report (15/30 Hz native, armed, serial) |
| motion server: sync-start (D + 0.54 s entry ramp) | +1.07-1.12 s | this report |
=> first motion command ~**1.35-1.4 s** after the turn starts; audio+gesture playback in sync can start at ~**1.8-1.9 s** (fast ramp). With the offline-identical 2.0 s ramp it would be ~3.2 s; speech without waiting for the gesture can start as soon as TTS has audio (~0.8 s) with the arms joining ~1 s later. The gesture generator is per sentence-chunk: later windows are ahead of real time (RTF 0.6-0.75), so only the first window pays.
Levers not done: split the first block (frame conversion 0.18 s) -> ~-0.12 s; start the ramp earlier / persistent sim that keeps the robot at READY between turns (no entry ramp at all -> audio start ~0.6 s after window 0); feed StreamTalk a 1.0 s first window (changes the model's context; untested).

## 7. Blockers / risks
1. **Orin CPU/GPU speed is unmeasured.** Serial compute is 1.2 s per 1.733 s window at 30 Hz on x86 (70%); an ARM A78 core is plausibly 2-3x slower for GMR/SONIC (python + QP + MuJoCo) and the Orin GPU 3-5x slower for StreamTalk (0.24 s/window): that would exceed real time. 15 Hz GMR + threads is the only configuration with margin; GMR is single-core, so 4+ cores are needed to run it concurrently with StreamTalk/SONIC.
2. GIL: in `threads` mode stage times inflate 2-3x (contention) although RTF improves; a multi-process split (GMR separate) would break the single-process memory goal.
3. Python-level per-frame costs dominate (FK/frame dict conversion 3.5 ms, 55 scipy Rotations per frame; IK 10 ms): an optimised FK (numpy) and a reduced-DoF IK model are the real speed-ups; not attempted.
4. Entry ramp: the offline-identical 2.0 s prefix hides latency but adds 2 s before audio; the 0.54 s ramp is a sim-initialisation artefact (sim restarts from the default stand each turn). A persistent SONIC sim between turns (return-to-READY ramp at the end of a turn) removes it but was not implemented/tested.
5. Real-time audio gives 2.0 s structural lookahead (StreamTalk window); TTS must be faster than real time. The last-window tail rule needs the audio end to be known (end-of-stream flag).
6. Version pins: `.mjb` files are MuJoCo-version specific (3.11 in physicalai), daqp 0.8.5 here vs 0.9.1 in the gmr env (qpos differs ~0.03-0.05 rad; no QC effect); `site_gmr/` symlinks mink + patched smplx from the gmr env (to vendor).
7. Not covered: Orin memory with real unified allocator/JetPack CUDA, long-duration drift/leaks (only 10 clips + warm-up), variance across repeated runs, online GMR QC, GMR first-frame warm-up with a very different first pose, concurrent Qwen/UniFace load on the same cores.
8. `os._exit(0)` at the end of the benchmark: one run hung in interpreter shutdown (CUDA/ORT teardown); not a serving issue but note for supervisors.
