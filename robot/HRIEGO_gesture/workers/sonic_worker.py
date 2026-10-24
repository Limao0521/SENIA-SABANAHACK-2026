#!/usr/bin/env python3
"""Persistent SONIC X2 worker (env physicalai). Pre-imports mujoco/onnxruntime/eval_x2_mujoco_onnx, caches the ONNX session across requests and
runs the unchanged c03 x2_sonic.convert_one / rollout_one (--no-record, trace CSV). Request {"id","name","retarget_dir","sonic_dir","log_dir","trace_dir"}."""
import os, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from common import serve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # HRIEGO_gesture root -> paths.py
import paths as P
MOTION = P.MOTION_DIR  # vendored x2_sonic.py
S = {}

def load():
    os.environ.setdefault("MUJOCO_GL", "egl")
    sys.path.insert(0, str(MOTION)); import x2_sonic as X
    sys.path.insert(0, str(X.SONIC_SCRIPTS)); sys.path.insert(0, str(X.RETARGETER))
    import eval_x2_mujoco_onnx as runner
    onnx = str(X.SONIC_ROOT / "models/x2_sonic_14000_g1.onnx")
    actor = runner.OnnxActor(onnx)                      # created once
    class Cached:                                       # runner.main() calls OnnxActor(path): hand back the shared session
        def __new__(cls, *a, **k): return actor
    runner.OnnxActor = Cached
    S.update(X=X, runner=runner, orig_rec=runner.VideoRecorder)
    return {"onnx": onnx}

def handle(req):
    X, name, runner = S["X"], req["name"], S["runner"]
    X.RETARGET = Path(req["retarget_dir"]); X.SONIC_OUT = Path(req["sonic_dir"]); X.LOGS = Path(req["log_dir"]); X.LOGS.mkdir(parents=True, exist_ok=True)
    os.environ["X2_TRACE_DIR"] = req["trace_dir"]; os.environ["X2_TRACE_TAG"] = name
    t = time.time(); c = X.convert_one(name, True); conv = time.time() - t; t = time.time()
    runner.VideoRecorder = S["orig_rec"]
    r = X.rollout_one(name, True, None, record=False); roll = time.time() - t
    return dict(convert_s=round(conv, 3), rollout_s=round(roll, 3), sim_s=round(r["simulated_seconds"], 2), sonic_status=c["status"], trace=str(Path(req["trace_dir"]) / f"full_{name}.csv"))

if __name__ == "__main__": serve("sonic", load, handle)
