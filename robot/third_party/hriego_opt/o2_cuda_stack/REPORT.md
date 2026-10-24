# O2 - one common CUDA stack (ORT-GPU 1.31, CUDA 13) for UniFace + StreamTalk (+SONIC), 2026-10-22
Single runs, 8 stills / 11 ST windows (c001+c004). Nothing committed; no existing env touched. Scripts: `common.py worker.py sep.py brain.py st_validate.py uf_dump.py uf_compare.py`; raw: `logs/`; freeze: `requirements/hri-brain.txt` (+ `base.txt` = hand-written install list).

## 1. Venv `.venv-hri` (python 3.12)
onnxruntime-gpu 1.31.0 + the CUDA-13 nvidia wheels (cublas, cudnn-cu13, cuda-runtime, nvrtc, cufft, curand, nvjitlink; **no torch**), numpy 2.5.4, scipy 1.18.1, opencv-python-headless 5.0.0.93, scikit-image 0.26.0, requests, tqdm, plus mujoco 3.13.0 + daqp 0.10.3 + mink 1.3.0 (+ qpsolvers, glfw, etc.). 37 packages. UniFace itself is not pip-installed: `Face/thirdparty/uniface` (v4.1.0) on PYTHONPATH exactly as the Face README does.
Disk: **3.1 GB** (nvidia libs 2.2 GB, onnxruntime 0.34 GB, cv2+libs 0.15 GB, scipy 0.14 GB); mujoco+daqp+mink stack added only ~0.1 GB -> cheap, no bloat. (Face .venv-face = 6.1 GB, mostly torch+triton.) `ort.get_available_providers()` = TensorRT, CUDA, CPU; opencv-headless is enough (no imshow in the brain).

## 2. Validation
- StreamTalk ONNX fp16, arena kSameAsRequested, replayed noise, c001/c004/c010 vs o1b `out/onnx_fp16_lean`: poses and trans max diff **0.0 -> bit-exact** (3/3; `st_validate.py`).
- UniFace (LiveRecognizer, 8 stills, 45 faces) .venv-hri vs .venv-face (`uf_compare.py`): embedding cosine min **1.0000000** (0.9999999999999998) / mean 1.0; box max diff **0.0 px**; decisions with MIN_COSINE 0.45 / MIN_MARGIN 0.08 unchanged: **45/45 identical** (top1 id + status), max top1-score diff 0.0. Fully bit-identical, despite headless cv2 and no torch libs.
- Harmless noise: ORT logs "Conv ... running in Fallback mode" warnings when the WavLM/step convs are first created (cuDNN conv fallback path); the speed/outputs are the same as the o1b numbers, so not investigated.

## 3. Memory (steady, after warm-up; PSS/USS in MB, VRAM per PID from nvidia-smi)
| config | host PSS | anon | file | VRAM | unified GB |
|---|---|---|---|---|---|
| M1 baseline UniFace (.venv-face) | 1650 | | | 500 | 2.15 |
| UniFace in .venv-face, this worker (re-measured) | 1681 | 1106 | 565 | 496 | 2.18 |
| M1 baseline StreamTalk torch lean | 2186 | | | 1348 | 3.54 |
| M1/O1b StreamTalk ONNX (.venv-face) | 1266 | 925 | | 1482 | 2.75 |
| **UniFace alone, .venv-hri** | 1245 | 867 | 368 | 496 | **1.74** |
| **StreamTalk ONNX alone, .venv-hri** | 1252 | 923 | 319 | 1482 | **2.73** |
| (a) two processes (alive together) sum of PSS | **2260** (RSS 2529; 270 MB file pages shared, USS sum 2015) | 1796 | 445 | 1970 | **4.23** |
| (b) ONE process, both loaded + warm, steady | **1758** (HWM 1855) | 1360 | 388 | **1852** (peak = steady) | **3.61** |
| (b) + SONIC ONNX CPU EP (2 threads) | 1816 (+58) | | | 1852 (+0) | 3.67 |
| (b) + SONIC ONNX on CUDA EP instead | 1836 (+78) | | | 2032 (+180) | 3.87 |
Findings:
- The slim venv alone saves 0.44 GB for UniFace (no torch libs pulled in by `preload_dlls`): 2.18 -> 1.74 GB.
- Separate processes in the new venv share ~237 MB of file-backed lib pages (PSS sum 2260 vs 2497 for two isolated runs) but each still pays its anonymous ~0.9 GB (CUDA/cuDNN/cuBLAS runtime init, ORT) and its own CUDA context (~120 MB VRAM). Sum = 4.23 GB.
- Same process: host -0.50 GB (2260 -> 1758), VRAM -0.12 GB (1970 -> 1852): **saves 0.62 GB (15%) vs two processes**, and 1.0 GB vs the M1 baselines summed (2.15 + 2.75 = 4.90 -> 3.61, -26%). The marginal cost of adding UniFace to a process that already holds ST ORT-CUDA is 0.51 GB host + 0.37 GB VRAM = 0.88 GB (vs 1.74 alone); adding ST to UniFace's process is 1.87 GB.
- Peak = steady (VmHWM 1855 MB, 100 MB over steady PSS); no load-time spike.
- SONIC ONNX (obs 1670 -> actor): CPU EP +58 MB host, 1.05 ms/inference (isolated); CUDA EP 0.19 ms but +180 MB VRAM +78 MB host: CPU EP is the better choice (control tick 20 ms).

## 4. Latency interference inside one process (UniFace ~18 ms/frame alone, ST 0.093 s/window alone in the combined process; o1b ST alone 0.104)
| scenario | face ms/frame | ST s/window |
|---|---|---|
| each alone (same process) | 18.0 | 0.093 |
| single-thread interleaved (3 faces after every window) | 20.4 (+13%) | 0.108 (+17%) - includes tiny GPU/cache cooling effects |
| face thread running flat-out while ST runs | 28.3 (+57%, 35.6 fps total) | 0.156 (+68%) |
| SONIC CPU loop (50 Hz thread) with ST running | SONIC 1.7 ms mean, p99 2.6, max 2.65 (vs 1.05 alone) | 0.116 |
Even the worst case (face saturating the GPU/CPU concurrently) keeps ST at RTF 0.078 (0.156 s per 2 s window) and UniFace at 35 fps: both far inside real time. The interference is GPU/CPU contention on the shared stream queue, not a lock (separate sessions; ORT releases the GIL). A face loop gated to ~10-15 fps (typical for HRI) lands between the interleaved and threaded rows.

## 5. Recommendation for the "brain" process
- Host UniFace + StreamTalk ONNX (+ SONIC ONNX, CPU EP) in **one process on .venv-hri** (ORT 1.31 CUDA13, no torch): 3.67 GB unified with SONIC vs 4.90 GB for the M1 layout (2 procs, Face venv) -> -1.2 GB, and 0.62 GB better than two procs in the same venv. A single CUDA context also avoids duplicated cuDNN/cuBLAS handles.
- Run face recognition on its own thread (throttle to the camera rate) and StreamTalk windows on another; ORT sessions are independent. Keep SONIC in a tight thread with its CPU session (2 threads, spinning off): its jitter (<3 ms) is fine for a 20 ms tick.
- Not in the brain: anything needing torch (physicalai, GMR/mink with its python 3.10 env, TTS) - GMR/SONIC rollouts with mujoco 3.13/daqp/mink can be added (venv already holds them, +0.1 GB disk) but GMR itself needs python 3.10 and was not tested here. Trade-off of one process: a crash in either component takes down both, and the CUDA context is shared (no isolation of GPU OOM).
- Caveats: single runs; ST measured on 2 clips (11 windows); face on 8 stills (mostly multi-face, 18 ms/frame); no camera/tracker-heavy load; mujoco not run in-process.
