"""StreamTalk ONNX fp16 arena-exact in .venv-hri vs o1b out/onnx_fp16_lean on 3 clips (replayed torch noise)."""
import sys, time, numpy as np
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/o1b_onnx"); sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from common import *
import onnxruntime as ort; ort.preload_dlls()
import onnx_st
st = onnx_st.OnnxST(variant="fp16", arena_exact=True); st.warm(); print("providers", st.active, "ort", ort.__version__)
for c in ["c001", "c004", "c010"]:
    y = np.load(O1B / f"scratch/audio/{c}.npy"); noise = np.load(O1B / f"scratch/noise/{c}_s1.npy")
    P_, T_ = [], []
    for n, nw, p, tr, i in st.stream(y, 1, noise=noise): P_.append(p); T_.append(tr)
    p = np.concatenate(P_); t = np.concatenate(T_); r = np.load(O1B / f"out/onnx_fp16_lean/{c}/{c}.npz")
    print(c, p.shape, "poses maxdiff", float(np.abs(p - r["poses"]).max()), "trans maxdiff", float(np.abs(t - r["trans"]).max()), "bitexact", bool((p == r["poses"]).all() and (t == r["trans"]).all()))
