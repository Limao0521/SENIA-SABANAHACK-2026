# O1b - StreamTalk neural parts on ONNX Runtime (CUDA EP, fp16), torch-free (x86, 2026-10-22)

Single runs, no variance except where stated. Reference = torch fp16 lean (`opt/m2_motion_server/st_stream.py`, `.half()`), seed 1, same 16 kHz audio arrays and the same noise stream
(the torch CPU RNG stream for seed 1 is dumped by `prep_inputs.py` and replayed). Nothing installed into existing envs; nothing committed.

## 0. Verdict
- Export OK: WavLM-large, SimpleSpeechModel (both speaker conditions), the CFG velocity net (one call = cond+uncond pair + CFG combine) and an L1 retrieval graph. fp32 and fp16 ONNX. ODE loop, rotation maths, SMPL-X FK, blending: numpy.
- Quality: ONNX fp16 vs torch fp16 = 6.1 deg mean error (p95 26.7), below the fp32 seed-to-seed reference (12.3 deg). ONNX fp32 vs fp32_s1 = 2.8 deg. Jerk/velocity inside the seed spread; sync marginal on BAS (see 3).
- Torch-free: YES. The runtime imports only numpy + onnxruntime (verified via `sys.modules`: no torch/transformers/librosa/scipy/numba/onnx).
- Resources (steady, whole process): 1.27 GB PSS + 1.48 GB VRAM = 2.75 GB unified vs torch fp16 lean 2.19 + 1.35 = 3.54 GB. Load 2.2 s vs 6.0 s. RTF 0.064 vs 0.182 (2.8x faster; 0.104 s vs 0.272 s per 2 s window).
- BLOCKER for physicalai: its `onnxruntime` is the CPU 1.30 wheel that overwrote the `onnxruntime-gpu` 1.26 pybind module (providers = Azure, CPU only; the CUDA .so files are present but unused). Runs here used the Face venv
  (`/home/luis/GitHub/x2_ultra/Face/.venv-face/bin/python`: ORT 1.31 gpu, CUDA 13, numpy 2.5.4, scipy) with `onnxruntime.preload_dlls()`. To run in physicalai: uninstall the CPU wheel and reinstall onnxruntime-gpu (an env change I did not make; the CTC aligner asserts CPUExecutionProvider on its own session, so it is unaffected as long as it asks for CPU explicitly). Export/convert/test of the pieces needing torch ran in physicalai.

## 1. What was exported (models/, sha256 in `models/SHA256SUMS.txt`)
WavLM consumes only `last_hidden_state` of the 24-layer encoder (after the final stable-layer-norm), always on exactly 32000 samples (2 s window, zero-padded), 99 frames x 1024, then linear interpolation (align_corners) to 60 frames.
The interpolation (a 60x99 matrix), audio normalisation (zero mean/unit var, eps 1e-7: bit-identical to Wav2Vec2Processor) and fp16 rounding are done in numpy. The ONNX time axis is dynamic (min 1600 samples).
Step net inputs: x (1,60,333), t (1), poses_prompt (1,60,330), prompt_mask, trans_prompt (1,60,3), trans_mask, hubert (1,60,1024), expressions, expressions_uncond; pid=1 and cfg=3 are baked in; output = pred_x1 with CFG combined. 15 calls per window (10 + 5). Export wrapper equals `forward_with_cfg` exactly (maxdiff 0.0).
Exporter: torch 2.11 dynamo (opset 18). fp16: `onnxruntime.transformers.float16.convert_float_to_float16(keep_io_types=True)` (onnxconverter-common not installed, not needed). Variant `fp16ln` additionally keeps LayerNormalization/Softmax/ReduceMean in fp32 (autocast-like); it was not better (below), so `fp16` is the production variant.

| file (+ .data) | size MB | note |
|---|---|---|
| wavlm_fp32 / fp16 / fp16ln | 1204 / 602 / 1204 | fp16 = default variant |
| step_fp32 / fp16 / fp16ln | 273 / 136 / 272 | |
| simple_fp32 / fp16 / fp16ln | 51 / 26 / 51 | outputs (expr, expr_uncond) |
| retrieval (fp16 table baked in) | 51 | L1 over 160173x165, 16 unrolled chunks |
Total 3.9 GB: **not for commit** (`.gitignore` in this folder excludes `*.data`, fp32). `assets/st_assets.npz` (47 kB: body-part indices, READY prompt incl. 6D, SMPL-X rest joints for the READY betas, parents) is small and committable. The mmapped poses table (211 MB) and joints table come from `m1_memory/out/kd_cache` (not committed either).

## 2. Component checks (ONNX on CUDA EP; `logs/component_check.json`, input = real c001 windows)
| component | vs torch fp16 (production style): max / mean abs, cosine | vs torch fp32 | reference: torch fp16 vs fp32 |
|---|---|---|---|
| WavLM fp32 ONNX | 0.113 / 0.0111, 0.99891 | 0.085 / 0.0030, 0.99988 | 0.116 / 0.0111, 0.99891 |
| WavLM fp16 ONNX | 0.171 / 0.0148, 0.99804 | 0.162 / 0.0114, 0.99879 | |
| WavLM fp16ln | 0.188 / 0.0168, 0.99748 | 0.162 / 0.0142, 0.99820 | |
| Simple fp16 (expr, vs fp32) | max 0.0052, mean 0.0010, cos 0.999999 | | torch autocast vs fp32: max 0.0020 |
| Step fp32 ONNX (t=0/.5/.9) | mean 0.0069/0.0047/0.0078, cos >=0.999995 | mean 0.0028/0.0021/0.0029 | 0.0074/0.0043/0.0071 |
| Step fp16 ONNX | max 0.115/0.078/0.109, mean 0.0065/0.0062/0.0099, cos 0.999991-0.999998 | mean 0.0066/0.0050/0.0078 | |
| Step fp16ln ONNX | mean 0.0069/0.0060/0.0100 | | no better than fp16 |
The ONNX fp16 error to torch fp16 is the same size as torch fp16's own error to fp32 (WavLM mean 0.015 vs 0.011; step mean ~0.007 vs 0.007). ONNX WavLM fp32 differs from torch fp32 by 1.5% relative (mean 0.003); likely TF32 in the CUDA EP, not investigated.
Numpy ports (`logs/numpy_ports_check.json`): SMPL-X FK vs `smplx` torch model max 3e-7 m (fp16 query identical in 99.97% of entries); 6D -> matrix -> axis-angle chain max 9.5e-4 rad on random data (mean 1.4e-6; the exact torch acos-clamp algorithm is ported); ONNX retrieval picks the same row as the torch loop in 112/112 queries (max distance diff 0.002).

## 3. Full pipeline on the 10 clips (poses; `metrics_o1b.py` = T2 `metrics.py` with a base switch; `metrics_vs_*.json/csv`)
Error = geodesic joint angle, 55 joints, mean / p95 over all frames (deg). Seed-to-seed reference: fp32_s2 vs fp32_s1 = 12.33 / 36.1.
| variant | vs torch_fp16 mean / p95 | vs fp32_s1 mean / p95 | first 2 s | last 2 s (vs fp32_s1) | trans MAE m |
|---|---|---|---|---|---|
| torch_fp16 lean (this run) | - | 3.26 / 17.9 | 0.53 | 8.55 | 0.009 |
| T2 fp16_s1 (production) | 4.63 / 22.7 | 4.43 / 22.9 | 0.45 | 5.89 | 0.010 |
| **onnx_fp16** | **6.11 / 26.7** | **5.78 / 25.7** | 0.64 | 8.21 | 0.021 |
| onnx_fp32 | 4.82 / 23.4 | 2.79 / 15.9 | 0.10 | 5.38 | 0.007 |
| onnx_fp16ln | 7.54 / 30.3 | 7.01 / 30.6 | 0.95 | 9.48 | 0.014 |
| onnx_fp16, numpy RNG (different noise) | 13.85 / 41.6 | 13.87 / 41.6 | 6.55 | 9.55 | 0.034 |
| fp32_s2 (seed ref) | 12.37 / 36.4 | 12.33 / 36.1 | 5.80 | 8.46 | 0.041 |
Per-clip (vs fp32_s1) onnx_fp16: c001 11.9, c004 5.5, c005 10.5, c010 0.2, c016 6.2, c018 3.0, guest_t00 0.2, guest_t01 2.6, regular_t02 5.1, staff_t05 0.4 (bimodal/chaotic as in T2: keypose retrieval flips; torch_fp16 itself gives c001 11.4, c016 6.8). Same-noise runs stay near the baseline in the first 2 s (0.64 deg) and drift later, like T2's fp16. Different noise (numpy RNG) lands at the seed-to-seed level (13.9), as expected.

Motion/sync stats (pooled; seed spread = fp32_s1 vs fp32_s2; torch fp16 shown for context):
| stat | fp32_s1 | fp32_s2 | torch_fp16 | onnx_fp16 | onnx_fp32 |
|---|---|---|---|---|---|
| arm jerk mean / p95 | 217 / 630 | 212 / 624 | 242 / 658 | 247 / 683 | 220 / 633 |
| wrist jerk mean / p95 | 64.3 / 171 | 55.1 / 145 | 69.7 / 178 | 70.1 / 172 | 66.0 / 176 |
| arm vel mean / p95 | 0.680 / 2.14 | 0.612 / 1.99 | 0.684 / 2.20 | 0.685 / 2.16 | 0.681 / 2.10 |
| wrist speed mean | 0.379 | 0.305 | 0.374 | 0.373 | 0.382 |
| corr RMS (lag 0) / best lag | 0.136 / 0.200 | 0.150 / 0.209 | 0.134 / 0.167 | 0.121 / 0.270 | 0.116 / 0.177 |
| corr onset (lag 0) | 0.102 | 0.068 | 0.113 | 0.117 | 0.087 |
| BAS | 0.530 | 0.564 | 0.546 | 0.508 | 0.534 |
| FGD vs fp32_s1 (seed ref 1.85) | 0 | 1.85 | 0.52 | 0.69 | 0.12 |
Acceptance (as in T2): error 6.1 / 5.8 deg < 12.3 PASS; velocity, wrist jerk, FGD inside the spread PASS; arm jerk +14% over fp32_s1 (torch fp16 is +12%, same fp16 effect, not worse than production) -> same verdict as T2 fp16 ("small, acceptable");
sync: lag-0 correlations inside the spread; BAS 0.508 is 0.02 below the lowest reference (0.530) and best-lag RMS corr 0.27 above the highest (0.209) - both are single-seed, 10-clip statistics that already move by +-0.03 between references (nprng variant: BAS 0.520, corr best-lag 0.151). Flag as marginal/noise-level, not evidence of degradation. ONNX fp32 is closest to fp32_s1 everywhere if a safer option is wanted (VRAM 3.2 GB, RTF 0.088).
Trans MAE 0.021 m vs fp32_s1 (torch fp16 0.009; seed ref 0.041) - below the seed reference, larger than torch fp16; not investigated.
Determinism: two ONNX fp16 runs (and a 3-pass repeat) give bit-identical poses (max diff 0.0).

## 4. Torch-free feasibility
Needs torch/transformers at runtime today: NOTHING in `onnx_st.py`. Ported to numpy (verified in section 2): NormalizeRot6d, 6D->matrix, matrix->6D, global->local, matrix->axis-angle (exact torch algorithm incl. clamp), Rodrigues, SMPL-X FK of the 55 joints (J_rest = J_regressor @ (v_template + shapedirs@betas), precomputed once for the READY betas into `assets/st_assets.npz`; a different betas vector needs a re-run of `build_assets.py`, which needs torch/smplx at build time only), retrieval L1 (ONNX graph, GPU), poses-table row fetch (mmap), 8-frame blending, wav normalisation, 99->60 interpolation.
Pre-computed at build time (torch/scipy): pose-prompt 6D of the READY clip, body-part index arrays. The audio loader is `audio_io.py` (scipy.io.wavfile + resample_poly; bit-identical to librosa on the 10 clips, all 16 kHz mono PCM; other rates would differ slightly from librosa's soxr).
Still torch-dependent only at build time: `export_onnx.py` (torch 2.11, dynamo exporter), `convert_fp16.py` (onnx + ORT float16 tool), `build_assets.py`, `prep_inputs.py`, `ref_*.py`. Noise: torch-free runs use `numpy.random.RandomState(seed)`; results are valid but not comparable to the torch stream at the frame level (see nprng row). Remaining gap for drop-in use: the server's `st_stream.py` interface (same generator tuple) is matched by `OnnxST.stream`, but the whole of `server.py` was not re-run on it.

## 5. Resources and speed (10 clips, 38 windows, steady-state, per-process; VRAM via nvidia-smi per PID; machine otherwise idle but for the other agent)
| process | load s | PSS after load / steady MB | RSS steady | VmHWM MB | VRAM MB | unified GB | per window (mean / p95 / max) | RTF (all clips) | WavLM / window |
|---|---|---|---|---|---|---|---|---|---|
| torch fp16 lean (st_stream) | 6.0 | 2203 / 2186 | 2211 | 2400 | 1348 | 3.54 (M1: 3.63, load 8 s) | 0.272 / 0.299 / 0.300 s | 0.182 | 15 ms |
| **onnx fp16, arena kSameAsRequested** | **2.2** | **1256 / 1266** | 1270 | **1343** | **1482** | **2.75** | **0.104 / 0.115 / 0.115 s** | **0.064** | 8 ms |
| onnx fp16, default arena | 2.8 | 1267 / 1272 | 1282 | 1355 | 1896 | 3.17 | 0.130 / 0.145 / 0.149 s | 0.081 | 9 ms |
| onnx fp32 (default arena) | 3.0 | 1191 / 1195 | 1205 | 1277 | 3172 | 4.37 | 0.142 / 0.157 / 0.160 s | 0.088 | 11 ms |
`cudnn_conv_algo_search=HEURISTIC` changed nothing material (PSS 1230, VRAM 1492). Per-clip gen time 0.22-0.62 s (e.g. 10.1 s clip: ~0.65 s) vs 0.63-1.62 s torch. Peak host memory equals steady (HWM only 80 MB above): the 0.9-1.0 GB torch peak-over-steady and the torch/transformers import (0.86 GB) are gone; the remaining 1.27 GB is mostly the CUDA/cuDNN/cuBLAS runtime tax plus ORT (anon 925 MB). The CUDA tax is paid once per process, so if the ONNX generator lives in a process that already holds ORT-GPU (e.g. Face), the marginal host cost is lower - not measured.
Per-step note: ONNX step net with CFG pair runs 15x per window; the host<->device copies of the small inputs were not optimised (no IOBinding) and are not the bottleneck at this speed.

## 6. Blockers / open items
1. physicalai ORT is CPU-only (see section 0); decide: reinstall onnxruntime-gpu there, or run this generator in the Face venv (works now, but numpy 2.5.4/CUDA 13 differ from physicalai and the retrieval/poses-table paths are absolute to the M1 cache).
2. Single seed, 10 clips; BAS and best-lag correlation are marginal vs the seed spread (statistical noise level). Trans MAE 2x torch fp16. A second ONNX seed would require a second torch noise dump (`prep_inputs.py` takes seed list; only seed 1 dumped).
3. WavLM ONNX fp32 vs torch fp32 1.5% relative difference not explained (TF32?). Dynamic time axis is exported but only 32000-sample windows were tested.
4. Not done: server-level integration test, ORT CPU-EP fallback timing, TensorRT EP, IOBinding. Model files total 3.9 GB (variants); the production set (wavlm_fp16, step_fp16, simple_fp16, retrieval) is 0.82 GB.

## 7. Files and how to rerun
- Export: `export_onnx.py all`, `convert_fp16.py {step,simple,wavlm} {fp16,fp16ln}`, `build_assets.py` (physicalai python, `CUDA_VISIBLE_DEVICES=` for CPU export).
- Inputs: `prep_inputs.py` (audio + torch noise dump to `scratch/`).
- Runtime: `onnx_st.py` (class `OnnxST(variant="fp16", arena_exact=True)`), `audio_io.py`, `run_onnx.py TAG [--arena-exact] [--noise replay|numpy]`, run with the Face venv python.
- Reference/metrics: `ref_torch.py TAG`, `ref_components.py` + `check_components.py`, `test_numpy_ports.py`, `metrics_o1b.py` (`O1B_BASE=fp32_s1|torch_fp16 O1B_SFX=...`, PYTHONPATH=HRIEGO_gesture), results in `out/*/run.json`, `logs/`, `metrics_*`.
