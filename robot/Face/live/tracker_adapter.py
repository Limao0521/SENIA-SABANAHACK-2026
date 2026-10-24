"""UniFace BYTETracker adapter with explicit IoU association to detections."""

from __future__ import annotations

import time

import numpy as np
try:
    from scipy.optimize import linear_sum_assignment
except ImportError:  # a bordo del X2 sin SciPy
    from uniface._scipy_fallback import linear_sum_assignment
from uniface import BYTETracker


def intersection_over_union(box_a, box_b) -> float:
    ax1, ay1, ax2, ay2 = map(float, box_a)
    bx1, by1, bx2, by2 = map(float, box_b)
    iw = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    ih = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    return inter / max(area_a + area_b - inter, 1e-9)


class TrackerAdapter:
    def __init__(
        self,
        *,
        track_thresh: float = 0.35,
        low_thresh: float = 0.05,
        track_buffer: int = 12,
        match_thresh: float = 0.8,
        association_iou_min: float = 0.30,
        ambiguity_delta: float = 0.08,
    ) -> None:
        self.tracker = BYTETracker(
            track_thresh=track_thresh,
            low_thresh=low_thresh,
            track_buffer=track_buffer,
            match_thresh=match_thresh,
        )
        self.association_iou_min = association_iou_min
        self.ambiguity_delta = ambiguity_delta
        self.last_association_unmatched = 0

    def reset(self) -> None:
        self.tracker.reset()
        self.last_association_unmatched = 0

    def update(self, faces: list) -> dict:
        start = time.perf_counter()
        previously_lost = {int(track.track_id) for track in self.tracker.lost_stracks}
        if faces:
            detections = np.asarray(
                [
                    [*np.asarray(face.bbox, dtype=np.float32).reshape(4), float(face.confidence)]
                    for face in faces
                ],
                dtype=np.float32,
            )
        else:
            detections = np.empty((0, 5), dtype=np.float32)
        outputs = self.tracker.update(detections)
        tracks = [
            {
                "track_id": int(round(row[4])),
                "bbox": [float(v) for v in row[:4]],
                "face_id": None,
                "iou": 0.0,
                "ambiguous": False,
            }
            for row in outputs
        ]

        if tracks and faces:
            iou = np.asarray(
                [[intersection_over_union(t["bbox"], face.bbox) for face in faces] for t in tracks],
                dtype=np.float32,
            )
            row_ids, col_ids = linear_sum_assignment(1.0 - iou)
            for row, col in zip(row_ids.tolist(), col_ids.tolist(), strict=False):
                score = float(iou[row, col])
                if score < self.association_iou_min:
                    continue
                track = tracks[row]
                track["face_id"] = col
                track["iou"] = score
                alternatives = sorted(
                    (float(value) for j, value in enumerate(iou[row]) if j != col), reverse=True
                )
                competing = sorted(
                    (float(iou[j, col]) for j in range(len(tracks)) if j != row), reverse=True
                )
                next_face = alternatives[0] if alternatives else 0.0
                next_track = competing[0] if competing else 0.0
                track["ambiguous"] = (
                    (next_face >= self.association_iou_min and score - next_face < self.ambiguity_delta)
                    or (next_track >= self.association_iou_min and score - next_track < self.ambiguity_delta)
                )

        matched_ids = {track["track_id"] for track in tracks if track["face_id"] is not None}
        self.last_association_unmatched = sum(t["face_id"] is None for t in tracks)
        active_track_ids = {int(track.track_id) for track in self.tracker.tracked_stracks}
        lost_track_ids = {int(track.track_id) for track in self.tracker.lost_stracks}
        return {
            "tracks": tracks,
            "active_track_ids": active_track_ids,
            "lost_track_ids": lost_track_ids,
            "matched_track_ids": matched_ids,
            "reassociated_track_ids": previously_lost & active_track_ids,
            "unmatched_tracks": self.last_association_unmatched,
            "seconds": time.perf_counter() - start,
        }
