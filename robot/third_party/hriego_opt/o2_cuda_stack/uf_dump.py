"""python uf_dump.py OUT.json -- UniFace via LiveRecognizer on the 8 stills: embeddings, boxes, decisions (MIN_COSINE .45 / MIN_MARGIN .08 unchanged)."""
import sys, time, json
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
from common import *
setup_face()
import cv2, numpy as np
from live.live_recognizer import LiveRecognizer
from live.frame_buffer import Frame
from src.uniface_runner import analyze_timed
rec = LiveRecognizer(registry_path=REG); out = dict(providers=str(rec.runtime.get("active_providers")), imgs={})
for p in IMGS:
    im = cv2.imread(p); faces, _ = analyze_timed(rec.analyzer, im)
    r = rec.process(Frame(frame_id=rec.frame_id + 1, image=im, received_at=time.monotonic(), decode_seconds=0.0))
    out["imgs"][Path(p).name] = dict(boxes=[[float(v) for v in f.bbox] for f in faces], emb=[np.asarray(f.embedding, np.float32).tolist() for f in faces],
        dec=[dict(top1=d["scores"]["top1_id"], s1=d["scores"]["top1_score"], margin=d["scores"]["margin"], status=d["scores"]["status"]) for d in r["observations"]] if "observations" in r else str(list(r))
    )
json.dump(out, open(sys.argv[1], "w")); print(out["providers"], {k: len(v["boxes"]) for k, v in out["imgs"].items()})
