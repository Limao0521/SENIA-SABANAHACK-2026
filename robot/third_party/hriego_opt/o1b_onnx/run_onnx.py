"""Run the torch-free ONNX StreamTalk on the 10 T2 clips. python run_onnx.py TAG [--variant fp16] [--noise replay|numpy] [--clips a,b] [--wavlm-variant fp32] [--cpu-retrieval]
Writes out/TAG/<clip>/<clip>.npz (T2 layout) + out/TAG/run.json (timings, memory, VRAM). Needs only numpy+onnxruntime (run with the Face .venv-face python for CUDA EP)."""
import argparse, json, os, subprocess, sys, threading, time
from pathlib import Path
import numpy as np
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser(); ap.add_argument("tag"); ap.add_argument("--variant", default="fp16"); ap.add_argument("--wavlm-variant", default=None); ap.add_argument("--noise", default="replay")
ap.add_argument("--clips", default=""); ap.add_argument("--cudnn", default="DEFAULT"); ap.add_argument("--seed", type=int, default=1); ap.add_argument("--cpu-retrieval", action="store_true"); ap.add_argument("--arena-exact", action="store_true"); ap.add_argument("--repeat", type=int, default=1)
args = ap.parse_args()
def smaps():
    d = {}
    for l in open("/proc/self/smaps_rollup"):
        p = l.split()
        if len(p) >= 3 and p[2] == "kB": d[p[0].rstrip(":")] = int(p[1]) / 1024
    return d
def hwm():
    for l in open("/proc/self/status"):
        if l.startswith("VmHWM"): return int(l.split()[1]) / 1024
def vram():
    try:
        for l in subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"], text=True).splitlines():
            p, m = [x.strip() for x in l.split(",")]
            if p == str(os.getpid()): return int(m)
    except Exception: pass
    return 0
class Samp(threading.Thread):
    def __init__(s): super().__init__(daemon=True); s.ev = threading.Event(); s.v = 0; s.pss = 0
    def run(s):
        while not s.ev.is_set():
            s.v = max(s.v, vram()); s.pss = max(s.pss, smaps()["Pss"]); s.ev.wait(0.15)
smp = Samp(); smp.start()
mem0 = smaps()["Pss"]
t0 = time.perf_counter()
import onnx_st
st = onnx_st.OnnxST(variant=args.variant, wavlm_variant=args.wavlm_variant, cudnn_search=args.cudnn, arena_exact=args.arena_exact, retrieval_provider="CPUExecutionProvider" if args.cpu_retrieval else "CUDAExecutionProvider")
t_sess = time.perf_counter() - t0; st.warm(); load_s = time.perf_counter() - t0
import gc, ctypes; gc.collect(); ctypes.CDLL("libc.so.6").malloc_trim(0)
sm = smaps(); load = dict(load_s=load_s, sessions_s=t_sess, wavlm_load_s=st.t_wavlm_load, pss_after_load=sm["Pss"], rss_after_load=sm["Rss"], anon_after_load=sm["Anonymous"], hwm_load=hwm(), vram_after_load=vram(), vram_peak_load=smp.v, providers=st.active,
            torch_loaded=any(m.split(".")[0] in ("torch", "transformers", "librosa", "scipy") for m in sys.modules), mods=sorted({m.split(".")[0] for m in sys.modules if m.split(".")[0] in ("torch", "transformers", "librosa", "scipy", "numba", "onnx")}))
print("load", json.dumps(load), flush=True)
clips = json.load(open(HERE.parents[0] / "t2_streamtalk_precision/clips.json")); sel = args.clips.split(",") if args.clips else list(clips)
out = HERE / "out" / args.tag; out.mkdir(parents=True, exist_ok=True); rows = {}; smp.v = 0
for rep in range(args.repeat):
  for c in sel:
    y = np.load(HERE / f"scratch/audio/{c}.npy"); noise = np.load(HERE / f"scratch/noise/{c}_s{args.seed}.npy") if args.noise == "replay" else None
    t = time.perf_counter(); P_, T_, info = [], [], []
    for n, nw, p, tr, i in st.stream(y, args.seed, noise=noise): P_.append(p); T_.append(tr); info.append(i)
    gen = time.perf_counter() - t; poses = np.concatenate(P_); trans = np.concatenate(T_)
    if rep == 0:
        (out / c).mkdir(exist_ok=True); np.savez(out / c / f"{c}.npz", poses=poses, trans=trans)
    rows[c] = dict(audio_s=len(y) / 16000, gen_s=gen, rtf=gen / (len(y) / 16000), frames=len(poses), windows=len(info), per_window_s=[i["gen_s"] + i["wavlm_s"] for i in info], wavlm_s=[i["wavlm_s"] for i in info], first_window_s=info[0]["gen_s"] + info[0]["wavlm_s"])
    print(c, f"{gen:.2f}s rtf {rows[c]['rtf']:.3f} win {np.mean(rows[c]['per_window_s']):.3f}s", flush=True)
sm = smaps(); gc.collect(); ctypes.CDLL("libc.so.6").malloc_trim(0); sm2 = smaps()
run = dict(load=load, clips=rows, steady_pss=sm2["Pss"], steady_rss=sm2["Rss"], steady_anon=sm2["Anonymous"], peak_hwm=hwm(), vram_steady=vram(), vram_peak_run=max(smp.v, vram()), pss_peak_sampled=smp.pss,
           mean_rtf=float(np.mean([r["rtf"] for r in rows.values()])), total_gen_s=float(sum(r["gen_s"] for r in rows.values())), total_audio_s=float(sum(r["audio_s"] for r in rows.values())), args=vars(args))
json.dump(run, open(out / "run.json", "w"), indent=1); smp.ev.set()
print("steady PSS %.0f RSS %.0f HWM %.0f VRAM %s/%s  RTF(total) %.3f" % (sm2["Pss"], sm2["Rss"], hwm(), run["vram_steady"], run["vram_peak_run"], run["total_gen_s"] / run["total_audio_s"]))
