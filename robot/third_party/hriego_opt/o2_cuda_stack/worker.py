"""Separate-process worker: python worker.py {uf|st|sonic} READY_FILE DONE_FILE. Reaches steady state, prints MEM, touches READY, waits for DONE."""
import sys, time, os
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/o1b_onnx"); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import *
mode, ready, done = sys.argv[1:4]
import numpy as np, gc, ctypes
import onnxruntime as ort; ort.preload_dlls()
mem("start")
if mode == "uf":
    setup_face(); import cv2
    from live.live_recognizer import LiveRecognizer; from live.frame_buffer import Frame
    rec = LiveRecognizer(registry_path=REG); mem("built"); ims = [cv2.imread(p) for p in IMGS]; tt = []
    for rnd in range(3):
        for i, im in enumerate(ims):
            t = time.perf_counter(); rec.process(Frame(frame_id=rnd * 100 + i + 1, image=im, received_at=time.monotonic(), decode_seconds=0.0)); tt.append(time.perf_counter() - t)
    extra = dict(face_ms_mean_last_round=1e3 * float(np.mean(tt[-8:])))
elif mode == "st":
    import onnx_st
    st = onnx_st.OnnxST(variant="fp16", arena_exact=True); st.warm(); mem("loaded"); ws = []
    for c in ["c001", "c004"]:
        y = np.load(O1B / f"scratch/audio/{c}.npy"); noise = np.load(O1B / f"scratch/noise/{c}_s1.npy")
        for n, nw, p, tr, i in st.stream(y, 1, noise=noise): ws.append(i["gen_s"] + i["wavlm_s"])
    extra = dict(window_s_mean=float(np.mean(ws)), windows=len(ws))
elif mode == "sonic":
    o = ort.SessionOptions(); o.intra_op_num_threads = 2; o.inter_op_num_threads = 1; o.add_session_config_entry("session.intra_op.allow_spinning", "0")
    s = ort.InferenceSession("/home/luis/GitHub/Laplace/mimo/X2/vendor/x2_external_evidence/sonic_x2_c9959443/models/x2_sonic_14000_g1.onnx", o, providers=["CPUExecutionProvider"])
    feeds = {i.name: np.random.rand(1, *[d if isinstance(d, int) else 1 for d in i.shape[1:]]).astype(np.float32) for i in s.get_inputs()}
    for _ in range(50): s.run(None, feeds)
    extra = {}
gc.collect(); ctypes.CDLL("libc.so.6").malloc_trim(0)
r = mem("steady", mode=mode, **extra); open(ready, "w").write("1")
while not os.path.exists(done): time.sleep(0.2)
