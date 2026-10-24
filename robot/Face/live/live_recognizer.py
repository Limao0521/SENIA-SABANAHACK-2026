"""Shared UniFace inference, open-set decisions, and T1 temporal tracking."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

from src.uniface_runner import analyze_timed, build_analyzer, runtime_info

from .temporal_identity import TemporalIdentityManager
from .tracker_adapter import TrackerAdapter
from .identity_registry import IdentityRegistry


ROOT = Path(__file__).resolve().parents[1]
GALLERY_PATH = ROOT / "outputs" / "UF_LAYA_01" / "identity_gallery.sqlite3"
MIN_COSINE = 0.45
MIN_MARGIN = 0.08


def load_readonly_gallery(path: Path = GALLERY_PATH) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"Read-only UniFace gallery not found: {path}")
    uri = f"file:{path.resolve()}?mode=ro&immutable=1"
    with sqlite3.connect(uri, uri=True) as db:
        rows = db.execute(
            "SELECT identity_id, display_name, embedding_dim, embedding FROM identities ORDER BY identity_id"
        ).fetchall()
    gallery = {
        identity_id: {
            "display_name": display_name,
            "embedding": np.frombuffer(blob, dtype=np.float32, count=dimension).copy(),
        }
        for identity_id, display_name, dimension, blob in rows
    }
    if set(gallery) != {"P001", "P002", "P003", "P004"}:
        raise ValueError(f"Expected exactly P001-P004 in gallery; found {sorted(gallery)}")
    for identity_id, item in gallery.items():
        if item["embedding"].shape != (512,) or not np.isfinite(item["embedding"]).all():
            raise ValueError(f"Invalid frozen embedding for {identity_id}")
    return gallery


class LiveRecognizer:
    def __init__(
        self,
        gallery_path: Path = GALLERY_PATH,
        *,
        registry_path: Path | None = None,
    ) -> None:
        self.gallery_path = gallery_path
        self.registry_path = Path(registry_path) if registry_path else None
        self.registry = IdentityRegistry(self.registry_path) if self.registry_path else None
        self.encoder_key = self.registry.active_encoder_key() if self.registry else None
        gallery = (
            self.registry.load_gallery(self.encoder_key)
            if self.registry and self.encoder_key
            else load_readonly_gallery(gallery_path)
        )
        if self.registry and not {"P001", "P002", "P003", "P004", "P005"}.issubset(gallery):
            raise ValueError(f"Institutional registry must load P001-P005; found {sorted(gallery)}")
        self._gallery_lock = threading.Lock()
        self.gallery_revision = 0
        self._install_gallery(gallery)
        os.environ.setdefault("UNIFACE_CACHE_DIR", str(ROOT / "thirdparty" / "uniface-models"))
        self.analyzer = build_analyzer()
        self.tracker = TrackerAdapter()
        self.temporal = TemporalIdentityManager()
        self.frame_id = 0
        self.reset_count = 0
        self.runtime = runtime_info(self.analyzer)

    def _install_gallery(self, gallery: dict) -> None:
        """Build a complete immutable matrix, then swap it as one snapshot."""
        identity_ids = tuple(sorted(gallery))
        if not identity_ids:
            raise ValueError("Cannot install an empty recognition gallery")
        raw = np.vstack([
            np.asarray(gallery[identity_id]["embedding"], dtype=np.float32).reshape(-1)
            for identity_id in identity_ids
        ])
        if raw.shape != (len(identity_ids), 512) or not np.isfinite(raw).all():
            raise ValueError(f"Invalid gallery matrix shape or values: {raw.shape}")
        norms = np.linalg.norm(raw, axis=1)
        if np.any(norms <= 0.0):
            raise ValueError("Gallery contains a zero-norm embedding")
        matrix = np.ascontiguousarray(raw / norms[:, None], dtype=np.float32)
        matrix.setflags(write=False)
        names = {identity_id: gallery[identity_id]["display_name"] for identity_id in identity_ids}
        with self._gallery_lock:
            self.gallery = gallery
            self.identity_names = names
            self._gallery_ids = identity_ids
            self._gallery_matrix = matrix
            self.gallery_revision += 1

    def reload_registry_gallery(self) -> int:
        """Atomically refresh the active registry encoder snapshot."""
        if not self.registry or not self.encoder_key:
            raise RuntimeError("Gallery reload requires an institutional registry-backed recognizer")
        fresh = self.registry.load_gallery(self.encoder_key)
        if not {"P001", "P002", "P003", "P004", "P005"}.issubset(fresh):
            raise ValueError(f"Reloaded registry is missing MVP identities: {sorted(fresh)}")
        self._install_gallery(fresh)
        return len(fresh)

    @staticmethod
    def _decide_embedding(embedding, identity_ids: tuple[str, ...], matrix: np.ndarray) -> dict:
        query = np.asarray(embedding, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if query.shape != (512,) or not np.isfinite(query).all() or norm <= 0.0:
            raise ValueError("Query embedding is not a finite, nonzero 512-D vector")
        values = matrix @ (query / norm)
        if values.size == 1:
            first = second = 0
            top2_score = float("-inf")
            top2_id = ""
        else:
            best_two = np.argpartition(values, -2)[-2:]
            ordered = best_two[np.argsort(values[best_two])[::-1]]
            first, second = int(ordered[0]), int(ordered[1])
            top2_score = float(values[second])
            top2_id = identity_ids[second]
        top1_score = float(values[first])
        margin = top1_score - top2_score
        status = "UNKNOWN" if top1_score < MIN_COSINE else ("AMBIGUOUS" if margin < MIN_MARGIN else "KNOWN")
        return {
            "top1_id": identity_ids[first],
            "top1_score": top1_score,
            "top2_id": top2_id,
            "top2_score": top2_score,
            "margin": margin,
            "status": status,
            "ranking": [(identity_ids[first], top1_score)] + ([(top2_id, top2_score)] if top2_id else []),
        }

    def reset_tracker(self) -> None:
        self.tracker.reset()
        self.temporal.reset()
        self.reset_count += 1

    def process(self, frame) -> dict:
        self.frame_id += 1
        frame_started = time.perf_counter()
        faces, uniface_times = analyze_timed(self.analyzer, frame.image)
        with self._gallery_lock:
            identity_ids = self._gallery_ids
            gallery_matrix = self._gallery_matrix
            identity_names = self.identity_names
        decisions = []
        for face_id, face in enumerate(faces):
            if face.embedding is None:
                score = {
                    "top1_id": None,
                    "top1_score": None,
                    "top2_id": None,
                    "top2_score": None,
                    "margin": None,
                    "status": "NO EMBEDDING",
                }
            else:
                score = self._decide_embedding(face.embedding, identity_ids, gallery_matrix)
            decisions.append(
                {
                    "face_id": face_id,
                    "bbox": [float(value) for value in face.bbox],
                    "confidence": float(face.confidence),
                    "landmarks": np.asarray(face.landmarks).astype(float).tolist(),
                    "scores": score,
                    "person_id_candidate": score.get("top1_id"),
                    "person_id_b0": score.get("top1_id") if score.get("status") == "KNOWN" else None,
                    "display_name_candidate": identity_names.get(score.get("top1_id")),
                    "display_name_b0": (
                        identity_names.get(score.get("top1_id"))
                        if score.get("status") == "KNOWN"
                        else None
                    ),
                }
            )

        state_before = {track_id: identity.state for track_id, identity in self.temporal.tracks.items()}
        tracker_result = self.tracker.update(faces)
        timestamp = frame.received_at
        by_face = {item["face_id"]: item for item in decisions}
        seen_track_ids = set()
        for track in tracker_result["tracks"]:
            track_id = track["track_id"]
            if track["face_id"] is None:
                continue
            seen_track_ids.add(track_id)
            track["identity"] = self.temporal.update(
                track_id=track_id,
                decision=by_face[track["face_id"]]["scores"],
                timestamp=timestamp,
                ambiguous=track["ambiguous"],
            )
        self.temporal.update_unseen(
            seen_track_ids=seen_track_ids,
            tracker_track_ids=tracker_result["active_track_ids"] | tracker_result["lost_track_ids"],
            timestamp=timestamp,
        )
        face_to_track = {
            track["face_id"]: track
            for track in tracker_result["tracks"]
            if track["face_id"] is not None
        }
        for item in decisions:
            track = face_to_track.get(item["face_id"])
            if track:
                identity = track.get("identity") or self.temporal.tracks.get(track["track_id"])
                item["track"] = {
                    "track_id": track["track_id"],
                    "iou": track["iou"],
                    "ambiguous": track["ambiguous"],
                    "state": identity.state if identity else "UNCONFIRMED",
                    "person_id": identity.person_id if identity and identity.state == "CONFIRMED" else None,
                    "display_name": (
                        identity_names.get(identity.person_id)
                        if identity and identity.state == "CONFIRMED"
                        else None
                    ),
                    "candidate_id": identity.candidate_id if identity else None,
                    "candidate_name": identity_names.get(identity.candidate_id) if identity else None,
                    "retained_when_b0_rejected": bool(
                        identity
                        and identity.state == "CONFIRMED"
                        and identity.person_id
                        and item["scores"]["status"] != "KNOWN"
                    ),
                    "revalidation": identity.revalidation if identity else False,
                }
                item["track"]["state_transition"] = (
                    f"{state_before.get(track['track_id'], 'UNCONFIRMED')}->{identity.state}"
                    if identity and state_before.get(track["track_id"], "UNCONFIRMED") != identity.state
                    else None
                )
                item["person_id_t1"] = identity.person_id if identity and identity.state == "CONFIRMED" else None
            else:
                item["track"] = None
                item["person_id_t1"] = None

        track_summaries = []
        for track in tracker_result["tracks"]:
            summary = {key: value for key, value in track.items() if key != "identity"}
            identity = track.get("identity") or self.temporal.tracks.get(track["track_id"])
            summary["identity"] = asdict(identity) if identity else None
            summary["seen_now"] = track.get("face_id") is not None
            summary["state_transition"] = (
                f"{state_before.get(track['track_id'], 'UNCONFIRMED')}->{identity.state}"
                if identity and state_before.get(track["track_id"], "UNCONFIRMED") != identity.state
                else None
            )
            track_summaries.append(summary)
        present_track_ids = {track["track_id"] for track in track_summaries}
        for track_id, identity in self.temporal.tracks.items():
            if track_id in present_track_ids:
                continue
            track_summaries.append({
                "track_id": track_id,
                "bbox": None,
                "face_id": None,
                "iou": 0.0,
                "ambiguous": False,
                "identity": asdict(identity),
                "seen_now": False,
                "state_transition": (
                    f"{state_before.get(track_id, 'UNCONFIRMED')}->{identity.state}"
                    if state_before.get(track_id, "UNCONFIRMED") != identity.state
                    else None
                ),
            })
        return {
            "frame_id": self.frame_id,
            "source_frame_id": frame.frame_id,
            "timestamp_monotonic": timestamp,
            "image": frame.image,
            "observations": decisions,
            "tracks": track_summaries,
            "active_tracks": len(tracker_result["active_track_ids"]),
            "unmatched_tracks": tracker_result["unmatched_tracks"],
            "reassociated_tracks": sorted(tracker_result["reassociated_track_ids"]),
            "track_association_seconds": tracker_result["seconds"],
            "uniface_seconds": uniface_times["image_seconds"],
            "detection_seconds": uniface_times["detection_seconds"],
            "embedding_seconds": uniface_times["embedding_seconds"],
            "receive_decode_seconds": frame.decode_seconds,
            "processing_seconds": time.perf_counter() - frame_started,
            "timestamp_unix": time.time(),
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "runtime": self.runtime,
        }


class SessionLogger:
    """Write structured decisions without retaining frames or face embeddings."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(0o700)
        stamp = time.strftime("%Y%m%d_%H%M%S") + f"_{time.time_ns() % 1_000_000_000:09d}"
        self.path = directory / f"session_{stamp}.jsonl"
        self.stream = self.path.open("a", encoding="utf-8", buffering=1)
        self.path.chmod(0o600)
        self.frames = 0
        self.observations = 0

    def write_frame(self, result: dict) -> None:
        self.frames += 1
        observations = []
        for item in result["observations"]:
            self.observations += 1
            observations.append({
                key: value
                for key, value in item.items()
                if key not in {"landmarks"}
            })
        row = {
            key: value
            for key, value in result.items()
            if key not in {"image", "runtime", "tracks"}
        }
        row["observations"] = observations
        row["tracks"] = result["tracks"]
        self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")

    def close(self) -> None:
        if not self.stream.closed:
            self.stream.flush()
            self.stream.close()
