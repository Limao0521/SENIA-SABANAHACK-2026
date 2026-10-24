"""UniFace model construction and instrumented FaceAnalyzer inference."""

import hashlib
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from uniface import FaceAnalyzer

from .image_source import FileImageSource


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_analyzer():
    # This is the requested native default FaceAnalyzer: RetinaFace MNET_V2 + EdgeFace XXS.
    # Recent ONNX Runtime GPU wheels can share CUDA/cuDNN libraries from PyTorch.
    if "CUDAExecutionProvider" in ort.get_available_providers() and hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    return FaceAnalyzer()


def read_bgr(path: Path):
    return FileImageSource().read(path)


def analyze_timed(analyzer, image):
    detector_times, embedding_times = [], []
    original_detect = analyzer.detector.detect
    original_embed = analyzer.recognizer.get_normalized_embedding

    def timed_detect(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_detect(*args, **kwargs)
        finally:
            detector_times.append(time.perf_counter() - start)

    def timed_embed(*args, **kwargs):
        start = time.perf_counter()
        try:
            return original_embed(*args, **kwargs)
        finally:
            embedding_times.append(time.perf_counter() - start)

    analyzer.detector.detect = timed_detect
    analyzer.recognizer.get_normalized_embedding = timed_embed
    start = time.perf_counter()
    try:
        faces = analyzer.analyze(image)
    finally:
        total = time.perf_counter() - start
        analyzer.detector.detect = original_detect
        analyzer.recognizer.get_normalized_embedding = original_embed
    return faces, {
        "image_seconds": total,
        "detection_seconds": sum(detector_times),
        "embedding_seconds": sum(embedding_times),
        "face_embedding_seconds": embedding_times,
    }


def embedding_quality(embedding):
    if embedding is None:
        return False, {"reason": "embedding_missing"}
    vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    ok = vector.shape == (512,) and np.isfinite(vector).all() and norm > 0
    return ok, {"dimension": int(vector.size), "norm": norm, "finite": bool(np.isfinite(vector).all())}


def similarities(query, gallery):
    q = np.asarray(query, dtype=np.float32).reshape(-1)
    qnorm = float(np.linalg.norm(q))
    if q.size != 512 or qnorm <= 0 or not np.isfinite(q).all():
        raise ValueError("Query embedding is not a finite, nonzero 512-D vector")
    q = q / qnorm
    scores = {}
    for identity_id, item in gallery.items():
        ref = np.asarray(item["embedding"], dtype=np.float32).reshape(-1)
        scores[identity_id] = float(np.dot(q, ref / np.linalg.norm(ref)))
    return scores


def decide(scores, min_cosine, ambiguity_margin):
    ranking = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    best_id, best_score = ranking[0]
    second_score = ranking[1][1] if len(ranking) > 1 else float("-inf")
    margin = best_score - second_score
    if best_score < min_cosine:
        status = "UNKNOWN"
    elif margin < ambiguity_margin:
        status = "AMBIGUOUS"
    else:
        status = "KNOWN"
    return {
        "top1_id": best_id,
        "top1_score": best_score,
        "top2_id": ranking[1][0] if len(ranking) > 1 else "",
        "top2_score": second_score,
        "margin": margin,
        "status": status,
        "ranking": ranking,
    }


def runtime_info(analyzer=None):
    info = {"onnxruntime": ort.__version__, "available_providers": ort.get_available_providers()}
    if analyzer is not None:
        info["active_session_providers"] = {
            "detector": analyzer.detector.session.get_providers(),
            "recognizer": analyzer.recognizer.session.get_providers(),
        }
    return info
