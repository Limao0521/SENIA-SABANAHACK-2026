"""(b) ONE process hosting UniFace (LiveRecognizer) + StreamTalk ONNX (+ optional SONIC CPU session). python brain.py"""
import sys, time, os, threading, json, gc, ctypes
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/o1b_onnx"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
import numpy as np
import onnxruntime as ort; ort.preload_dlls()
setup_face(); import cv2
from live.live_recognizer import LiveRecognizer; from live.frame_buffer import Frame
import onnx_st
R = {}; pk = dict(v=0, pss=0); stop = threading.Event()
def sampler():
    while not stop.is_set(): pk["v"] = max(pk["v"], vram()); pk["pss"] = max(pk["pss"], smaps()["Pss"]); stop.wait(0.15)
threading.Thread(target=sampler, daemon=True).start()
def hwm(): return [int(l.split()[1]) / 1024 for l in open("/proc/self/status") if l.startswith("VmHWM")][0]
def trim(): gc.collect(); ctypes.CDLL("libc.so.6").malloc_trim(0)
R["m0"] = mem("start")
t = time.perf_counter(); rec = LiveRecognizer(registry_path=REG); R["load_uf_s"] = time.perf_counter() - t; R["m1_uf_built"] = mem("uf_built")
t = time.perf_counter(); st = onnx_st.OnnxST(variant="fp16", arena_exact=True); st.warm(); R["load_st_s"] = time.perf_counter() - t; trim(); R["m2_both_loaded"] = mem("both_loaded")
ims = [cv2.imread(p) for p in IMGS]; fid = [0]
def face(im):
    fid[0] += 1; t = time.perf_counter(); rec.process(Frame(frame_id=fid[0], image=im, received_at=time.monotonic(), decode_seconds=0.0)); return time.perf_counter() - t
def clip(c): return np.load(O1B / f"scratch/audio/{c}.npy"), np.load(O1B / f"scratch/noise/{c}_s1.npy")
CL = ["c001", "c004"]
def win_iter(c):
    y, n = clip(c); return st.stream(y, 1, noise=n)
# warm face
for r in range(2):
    for im in ims: face(im)
# 1. alone (in the combined process)
fa = [face(im) for r in range(3) for im in ims]; R["face_alone_ms"] = 1e3 * float(np.mean(fa))
wa = []
for c in CL:
    for _ in win_iter(c) if False else []: pass
    g = win_iter(c)
    while True:
        t = time.perf_counter()
        try: next(g)
        except StopIteration: break
        wa.append(time.perf_counter() - t)
R["st_alone_s_per_window"] = float(np.mean(wa)); R["st_windows"] = len(wa)
trim(); R["m3_steady_alone"] = mem("steady_after_alone")
# 2. interleaved single thread: after every window, K=3 face frames
fi, wi = [], []; K = 3; j = 0
for c in CL:
    g = win_iter(c)
    while True:
        t = time.perf_counter()
        try: next(g)
        except StopIteration: break
        wi.append(time.perf_counter() - t)
        for _ in range(K): fi.append(face(ims[j % len(ims)])); j += 1
R["interleaved"] = dict(face_ms=1e3 * float(np.mean(fi)), st_s_per_window=float(np.mean(wi)), windows=len(wi), faces=len(fi))
# 3. face thread running continuously during ST windows
ft = []; fstop = threading.Event()
def loop():
    k = 0
    while not fstop.is_set(): ft.append((time.perf_counter(), face(ims[k % len(ims)]))); k += 1
th = threading.Thread(target=loop); th.start(); wt = []; t0 = time.perf_counter()
for c in CL:
    g = win_iter(c)
    while True:
        t = time.perf_counter()
        try: next(g)
        except StopIteration: break
        wt.append(time.perf_counter() - t)
el = time.perf_counter() - t0; fstop.set(); th.join()
R["threaded"] = dict(face_ms=1e3 * float(np.mean([x[1] for x in ft])), face_fps=len(ft) / el, st_s_per_window=float(np.mean(wt)), windows=len(wt), faces=len(ft))
trim(); R["m4_steady_after_mix"] = mem("steady_after_mix"); R["hwm_mb"] = hwm(); R["vram_peak"] = pk["v"]; R["pss_peak_sampled"] = pk["pss"]
# 4. SONIC CPU session increment
o = ort.SessionOptions(); o.intra_op_num_threads = 2; o.inter_op_num_threads = 1; o.add_session_config_entry("session.intra_op.allow_spinning", "0")
s = ort.InferenceSession("/home/luis/GitHub/Laplace/mimo/X2/vendor/x2_external_evidence/sonic_x2_c9959443/models/x2_sonic_14000_g1.onnx", o, providers=["CPUExecutionProvider"])
feeds = {i.name: np.random.rand(1, *[d if isinstance(d, int) else 1 for d in i.shape[1:]]).astype(np.float32) for i in s.get_inputs()}
R["sonic_inputs"] = {i.name: i.shape for i in s.get_inputs()}
ts = []
for _ in range(300): t = time.perf_counter(); s.run(None, feeds); ts.append(time.perf_counter() - t)
trim(); R["sonic_cpu_ms"] = 1e3 * float(np.mean(ts[50:])); R["m5_plus_sonic_cpu"] = mem("plus_sonic_cpu")
# SONIC while a ST window is running (50 Hz-ish control loop interference)
fstop.clear(); st_t = []; so = []
def sloop():
    while not fstop.is_set(): t = time.perf_counter(); s.run(None, feeds); so.append(time.perf_counter() - t); time.sleep(0.02)
th = threading.Thread(target=sloop); th.start()
for c in CL:
    g = win_iter(c)
    while True:
        t = time.perf_counter()
        try: next(g)
        except StopIteration: break
        st_t.append(time.perf_counter() - t)
fstop.set(); th.join(); R["sonic_with_st"] = dict(sonic_ms_mean=1e3 * float(np.mean(so)), sonic_ms_p99=1e3 * float(np.percentile(so, 99)), sonic_ms_max=1e3 * max(so), st_s_per_window=float(np.mean(st_t)))
# SONIC on CUDA EP (try)
try:
    s2 = ort.InferenceSession("/home/luis/GitHub/Laplace/mimo/X2/vendor/x2_external_evidence/sonic_x2_c9959443/models/x2_sonic_14000_g1.onnx", providers=["CUDAExecutionProvider"]); ts = []
    for _ in range(300): t = time.perf_counter(); s2.run(None, feeds); ts.append(time.perf_counter() - t)
    trim(); R["sonic_cuda_ms"] = 1e3 * float(np.mean(ts[50:])); R["m6_plus_sonic_cuda"] = mem("plus_sonic_cuda")
except Exception as e: R["sonic_cuda_err"] = str(e)[:200]
stop.set(); json.dump(R, open(HERE / "logs/brain.json", "w"), indent=1); print(json.dumps(R, indent=1))
