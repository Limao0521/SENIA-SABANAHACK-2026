"""Torch fp16 lean reference (opt/m2_motion_server/st_stream.py) on the 10 clips, same audio arrays. python ref_torch.py TAG"""
import json, sys, time, numpy as np
from pathlib import Path
O1B = Path(__file__).resolve().parent
sys.path.insert(0, str(O1B.parents[0] / "m2_motion_server"))
from m2common import *   # noqa
import st_stream as ST
tag = sys.argv[1]; mem = MemSampler(0.15); mem.start()
load_s = ST.load(); import torch
sm = smaps(); trim(); sm = smaps()
load = dict(load_s=load_s, pss_after_load=sm["Pss"], rss_after_load=sm["Rss"], hwm=status_mb()["VmHWM"], vram_after_load=vram_mb())
print("load", json.dumps(load), flush=True)
out = O1B / "out" / tag; out.mkdir(parents=True, exist_ok=True); rows = {}; vpk = 0
for c in json.load(open(CLIPS_JSON)):
    y = np.load(O1B / f"scratch/audio/{c}.npy"); t = time.perf_counter(); P_, T_, info = [], [], []
    for n, nw, p, tr, i in ST.stream(y, 1): P_.append(p); T_.append(tr); info.append(i); vpk = max(vpk, vram_mb())
    gen = time.perf_counter() - t; poses = np.concatenate(P_); trans = np.concatenate(T_)
    (out / c).mkdir(exist_ok=True); np.savez(out / c / f"{c}.npz", poses=poses, trans=trans)
    rows[c] = dict(audio_s=len(y) / 16000, gen_s=gen, rtf=gen / (len(y) / 16000), frames=len(poses), windows=len(info), per_window_s=[i["gen_s"] + i["wavlm_s"] for i in info], wavlm_s=[i["wavlm_s"] for i in info])
    print(c, f"{gen:.2f}s rtf {rows[c]['rtf']:.3f}", flush=True)
trim(); sm = smaps()
run = dict(load=load, clips=rows, steady_pss=sm["Pss"], steady_rss=sm["Rss"], peak_hwm=status_mb()["VmHWM"], vram_steady=vram_mb(), vram_peak_run=max(vpk, vram_mb()), pss_peak_sampled=mem.peak()[0],
           total_gen_s=sum(r["gen_s"] for r in rows.values()), total_audio_s=sum(r["audio_s"] for r in rows.values()), torch_modules=len([m for m in sys.modules if m.startswith("torch")]))
json.dump(run, open(out / "run.json", "w"), indent=1); mem.stop(); print("steady PSS %.0f RSS %.0f HWM %.0f VRAM %s/%s RTF %.3f" % (sm["Pss"], sm["Rss"], run["peak_hwm"], run["vram_steady"], run["vram_peak_run"], run["total_gen_s"] / run["total_audio_s"]))
