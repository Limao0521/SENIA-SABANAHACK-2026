"""UniFace stack measure-only (unmodified): LiveRecognizer on still images from Laplace/Face/data/evaluation_osfr02 (no camera). cwd=Face."""
import sys, time, glob, os
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap
LOG = "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory/logs/uniface.jsonl"; open(LOG, "w").close()
snap("a_start", LOG); import cv2, numpy as np; snap("b_cv2_numpy", LOG)
from live.live_recognizer import LiveRecognizer; snap("c_import_live_recognizer(uniface,onnxruntime)", LOG)
from live.frame_buffer import Frame
t = time.time(); rec = LiveRecognizer(registry_path="/home/luis/GitHub/x2_ultra/Face/data/institutional_identity.sqlite3"); snap("d_recognizer_built", LOG, extra=dict(s=round(time.time() - t, 2), providers=str(rec.runtime.get("active_providers"))))
imgs = sorted(glob.glob("/home/luis/GitHub/Laplace/Face/data/evaluation_osfr02/W0*.jpg"))
for rnd in range(3):
    tt = []; nf = 0
    for i, p in enumerate(imgs):
        im = cv2.imread(p); t = time.time(); r = rec.process(Frame(frame_id=rnd * 100 + i + 1, image=im, received_at=time.monotonic(), decode_seconds=0.0)); tt.append(time.time() - t); nf += len(r.get("observations", []))
    snap(f"e_infer_round{rnd}", LOG, extra=dict(imgs=len(imgs), faces=nf, mean_s=round(float(np.mean(tt)), 3), shape=str(im.shape)))
print("providers", rec.runtime, file=sys.stderr)
print("HWM", [l.strip() for l in open("/proc/self/status") if l.startswith("VmHWM")], file=sys.stderr)
