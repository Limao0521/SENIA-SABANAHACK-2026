"""Two-panel, synchronized OpenCV desktop renderer."""

from __future__ import annotations

import cv2
import numpy as np


KNOWN = (48, 180, 80)
UNKNOWN = (50, 105, 230)
NEUTRAL = (0, 190, 255)
WHITE = (240, 240, 240)
BG = (25, 28, 32)


def _text(image, text, point, *, color=WHITE, scale=0.5, thickness=1) -> None:
    cv2.putText(image, text, point, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def _draw_label(image, text: str, x: int, y: int, color) -> None:
    (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    y0 = max(0, y - th - baseline - 8)
    cv2.rectangle(image, (x, y0), (x + tw + 10, y0 + th + baseline + 6), color, -1)
    _text(image, text, (x + 5, y0 + th + 1), color=(10, 10, 10), scale=0.45)


def render_panel(image, observations, *, tracked: bool, width: int, height: int) -> np.ndarray:
    canvas = np.full((height, width, 3), BG, dtype=np.uint8)
    if image is None:
        return canvas
    annotated = image.copy()
    for item in observations:
        x1, y1, x2, y2 = [int(round(v)) for v in item["bbox"]]
        scores = item["scores"]
        if tracked:
            track = item.get("track")
            if track is None:
                label = f"UNASSOC · face {item['face_id']}"
                color = NEUTRAL
            else:
                person_id = track.get("person_id")
                display_name = track.get("display_name")
                track_name = f"T{track['track_id']:02d}"
                state = track["state"]
                if person_id and state == "CONFIRMED":
                    name = f"{person_id} {display_name or ''}".strip()
                    color = KNOWN
                elif state == "SUSPENDED":
                    name = "VERIFY"
                    color = NEUTRAL
                elif state == "TENTATIVE" and track.get("candidate_id"):
                    candidate = track["candidate_id"]
                    candidate_name = track.get("candidate_name") or ""
                    name = f"? {candidate} {candidate_name}".strip()
                    color = NEUTRAL
                else:
                    name = "UNKNOWN" if scores["status"] == "UNKNOWN" else state
                    color = UNKNOWN if name == "UNKNOWN" else NEUTRAL
                value = scores.get("top1_score")
                score_suffix = f" s={value:.2f}" if value is not None else ""
                hold_suffix = " · HOLD" if track.get("retained_when_b0_rejected") else ""
                label = f"{name} · {track_name}{score_suffix}{hold_suffix}"
        else:
            status = scores["status"]
            person_id = scores.get("top1_id") if status == "KNOWN" else None
            display_name = item.get("display_name_b0")
            name = f"{person_id} {display_name or ''}".strip() if person_id else status
            value = scores.get("top1_score")
            margin = scores.get("margin")
            detail = f"s={value:.2f}" if value is not None else "s=--"
            if margin is not None:
                detail += f" m={margin:.2f}"
            label = f"{name} · {detail}"
            color = KNOWN if person_id else UNKNOWN
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        _draw_label(annotated, label, x1, y1, color)

    source_h, source_w = annotated.shape[:2]
    scale = min(width / source_w, height / source_h)
    resized = cv2.resize(
        annotated,
        (max(1, int(source_w * scale)), max(1, int(source_h * scale))),
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
    )
    top = (height - resized.shape[0]) // 2
    left = (width - resized.shape[1]) // 2
    canvas[top : top + resized.shape[0], left : left + resized.shape[1]] = resized
    return canvas


def render_comparison(
    result: dict | None,
    *,
    window_width: int = 1600,
    window_height: int = 980,
    stream_status: str = "CONNECTING",
) -> np.ndarray:
    panel_width = window_width // 2
    header_height = 46
    footer_height = 92
    video_height = max(100, window_height - header_height - footer_height)
    canvas = np.full((window_height, window_width, 3), BG, dtype=np.uint8)
    _text(canvas, "UF-OSFR-04 | LIVE FACIAL RECOGNITION", (18, 31), scale=0.68, thickness=2)
    _text(canvas, "B0 — UNIFACE · NO TRACKING", (18, header_height + 20), color=(175, 205, 255), scale=0.52)
    _text(canvas, "T1 — UNIFACE + BYTETRACK", (panel_width + 18, header_height + 20), color=(175, 255, 190), scale=0.52)
    if result is None:
        left = render_panel(None, [], tracked=False, width=panel_width, height=video_height)
        right = render_panel(None, [], tracked=True, width=panel_width, height=video_height)
        message = "DISCONNECTED" if "DISCONNECTED" in stream_status else stream_status
        _text(left, message, (24, video_height // 2), color=NEUTRAL, scale=0.65)
        _text(right, message, (24, video_height // 2), color=NEUTRAL, scale=0.65)
    else:
        left = render_panel(result["image"], result["observations"], tracked=False, width=panel_width, height=video_height)
        right = render_panel(result["image"], result["observations"], tracked=True, width=panel_width, height=video_height)
    y = header_height + 30
    canvas[y : y + video_height, :panel_width] = left
    canvas[y : y + video_height, panel_width : panel_width * 2] = right
    cv2.line(canvas, (panel_width, header_height), (panel_width, header_height + video_height), (80, 86, 94), 1)

    footer_y = header_height + video_height + 10
    if result is None:
        footer_lines = ["Stream FPS: --  | Processing FPS: --  | Latency: --  | Faces: 0  | Active tracks: 0", "Local inference timing; sensor-to-screen latency unavailable until capture timestamps are exposed."]
    else:
        metrics = result.get("metrics", {})
        footer_lines = [
            f"Stream {metrics.get('stream_fps', 0):.1f} FPS | Infer {metrics.get('processing_fps', 0):.1f} FPS | Render {metrics.get('render_fps', 0):.1f} FPS | Infer {result.get('processing_seconds', 0)*1000:.0f} ms | Faces {len(result['observations'])} | Tracks {result.get('active_tracks', 0)}",
            f"Infer p50/p95 {metrics.get('processing_p50_ms', 0):.0f}/{metrics.get('processing_p95_ms', 0):.0f} ms | Tracker {result.get('track_association_seconds', 0)*1000:.2f} ms | Render {metrics.get('render_latency_ms', 0):.1f} ms | Frame age {metrics.get('frame_age_ms', 0):.0f} ms | Dropped {metrics.get('frames_dropped', 0)}",
        ]
    _text(canvas, footer_lines[0], (18, footer_y + 20), scale=0.48)
    _text(canvas, footer_lines[1], (18, footer_y + 42), color=(175, 185, 195), scale=0.42)
    _text(canvas, "Q / Esc: quit     R: reset tracker     H: help", (18, footer_y + 68), color=(175, 185, 195), scale=0.42)
    return canvas
