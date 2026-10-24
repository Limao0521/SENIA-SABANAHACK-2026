"""physicalai: dump 16 kHz audio (librosa, as production) and the exact torch CPU noise stream (seed 1 = restored load RNG, WavLM 24-draw skip) per clip -> scratch/audio, scratch/noise.
The noise does not depend on the model, so ONNX runs can replay it for bit-comparable (up to fp rounding) output."""
import json, sys, numpy as np, torch, librosa
from common_paths import *
sys.path.insert(0, str(HRIEGO / "opt/m2_motion_server"))
import random
(HERE / "scratch/audio").mkdir(exist_ok=True); (HERE / "scratch/noise").mkdir(exist_ok=True)
SR = 16000; WIN = SR * 2; OVL = 8; STRIDE = WIN - int(SR / 30 * OVL)
def plan(n):
    starts = list(range(0, n - WIN + 1, STRIDE))
    if starts:
        clips = [(s, s + WIN) for s in starts]; counts = [60] + [60 - OVL] * (len(starts) - 1); le = starts[-1] + WIN
        if n > le:
            ps = starts[-1] + STRIDE; clips.append((ps, min(ps + WIN, n))); counts.append(min(60 - OVL, int(np.ceil(max(0, n - le) / STRIDE * (60 - OVL)))))
    else: clips = [(0, n)]; counts = [max(1, min(60, int(np.ceil(n / WIN * 60))))]
    return clips, counts
rng_load = torch.load(RNG_PT)
for c, wav in json.load(open(CLIPS_JSON)).items():
    y = librosa.load(wav, sr=SR)[0].astype(np.float32); np.save(HERE / f"scratch/audio/{c}.npy", y)
    nw = len(plan(len(y))[0])
    for seed in (1,):
        torch.manual_seed(seed); np.random.seed(seed); random.seed(seed)
        if seed == 1: torch.set_rng_state(rng_load)
        torch.rand(24 * nw); noise = np.stack([torch.randn((1, 60, 333)).numpy()[0] for _ in range(nw)])
        np.save(HERE / f"scratch/noise/{c}_s{seed}.npy", noise)
    print(c, len(y) / SR, "s", nw, "windows", noise.shape)
