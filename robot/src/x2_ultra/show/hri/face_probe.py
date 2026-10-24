"""Sonda de identificación facial: se ejecuta con el venv de Face (subproceso, ver ``face_id``).

Recibe una foto (la «cámara simulada»: el huésped que llega), la analiza con el FaceAnalyzer nativo
(RetinaFace MNET_V2 + EdgeFace XXS), compara contra la galería institucional (SQLite de Face) con
los umbrales del reconocedor en vivo (coseno ≥ 0,45, margen ≥ 0,08) e imprime una línea JSON con
person_id, display_name, rank_code y latencias. Con varias caras, toma la más grande (el huésped).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FACE_ROOT = Path(os.environ.get(
    "X2_FACE_ROOT", str(Path(__file__).resolve().parents[4] / "Face")))
sys.path[:0] = [str(FACE_ROOT / "thirdparty" / "uniface"), str(FACE_ROOT)]

MIN_COSINE = 0.45       # umbrales de live/live_recognizer.py
MIN_MARGIN = 0.08


def main(argv: list[str]) -> int:
    import numpy as np
    photo = Path(argv[0]).expanduser()
    t_start = time.perf_counter()
    result: dict = {"photo": str(photo), "status": "NO_FACE", "faces": 0}
    try:
        from src.uniface_runner import analyze_timed, build_analyzer, read_bgr
        from live.identity_registry import IdentityRegistry

        image = read_bgr(photo)
        if image is None:
            result["error"] = "cv2 no pudo leer la imagen"
            print(json.dumps(result))
            return 1
        analyzer = build_analyzer()
        faces, timing = analyze_timed(analyzer, image)
        result["faces"] = len(faces)
        result["uniface_seconds"] = {k: v for k, v in timing.items() if k != "face_embedding_seconds"}
        if faces:
            face = max(faces, key=lambda f: abs(f.bbox[2] - f.bbox[0]) * abs(f.bbox[3] - f.bbox[1]))
            result["bbox"] = [float(v) for v in face.bbox]
            result["confidence"] = float(face.confidence)
            embedding = getattr(face, "embedding", None)
            if embedding is not None:
                registry = IdentityRegistry()
                encoder = registry.active_encoder_key()
                gallery = registry.load_gallery(encoder)
                ids = sorted(gallery)
                matrix = np.stack([gallery[i]["embedding"] for i in ids])
                q = np.asarray(embedding, dtype=np.float32).reshape(-1)
                q = q / float(np.linalg.norm(q))
                values = matrix @ q
                order = np.argsort(values)[::-1]
                top1 = ids[int(order[0])]
                s1 = float(values[int(order[0])])
                s2 = float(values[int(order[1])]) if len(ids) > 1 else float("-inf")
                margin = s1 - s2
                status = ("UNKNOWN" if s1 < MIN_COSINE
                          else ("AMBIGUOUS" if margin < MIN_MARGIN else "KNOWN"))
                result.update(status=status, top1_id=top1, top1_score=s1,
                              top2_id=ids[int(order[1])] if len(ids) > 1 else None, top2_score=s2,
                              margin=margin)
                if status == "KNOWN":
                    profile = registry.get_profiles([top1]).get(top1) or {}
                    result["profile"] = {**profile, "identity_status": "CONFIRMED"}
    except Exception as exc:            # la sonda nunca debe tumbar el show
        result["status"] = "ERROR"
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["latency_s"] = round(time.perf_counter() - t_start, 3)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
