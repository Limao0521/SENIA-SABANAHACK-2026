import sys, time, numpy as np, os
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap, trim
import onnxruntime as ort; snap("a_import_ort")
P = "/home/luis/GitHub/Laplace/voice/experimental/hri_temporal_alignment_20261020/E4_COMPACT_CTC/models/quartznet15x5_es/model.onnx"
print("onnx MB", os.path.getsize(P) >> 20, ort.__version__)
o = ort.SessionOptions(); o.intra_op_num_threads = 2; s = ort.InferenceSession(P, o, providers=["CPUExecutionProvider"]); snap("b_session")
ins = s.get_inputs(); print([(i.name, i.shape, i.type) for i in ins])
feed = {}
for i in ins:
    sh = [d if isinstance(d, int) else (1 if j == 0 else 300) for j, d in enumerate(i.shape)]
    feed[i.name] = (np.random.randn(*sh).astype(np.float32) if "float" in i.type else np.array([sh[-1]] * sh[0], dtype=np.int64))
for k in range(3): t = time.time(); s.run(None, feed); snap(f"c_run{k}", extra=dict(s=round(time.time() - t, 3)))
trim(); snap("d_trim")
