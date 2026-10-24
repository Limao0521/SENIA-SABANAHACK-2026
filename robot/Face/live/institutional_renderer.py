"""OpenCV dashboard with equal-area live video and institutional profiles."""

from __future__ import annotations

import cv2
import numpy as np


BG = (24, 27, 31)
PANEL = (31, 35, 41)
CARD = (43, 48, 56)
LINE = (74, 82, 92)
WHITE = (238, 241, 245)
MUTED = (160, 170, 182)
GREEN = (86, 205, 124)
AMBER = (38, 190, 248)
RED = (82, 102, 236)


def _text(canvas, text, point, *, color=WHITE, scale=0.48, thickness=1) -> None:
    cv2.putText(canvas, str(text), point, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thickness, cv2.LINE_AA)


def _fit_image(image: np.ndarray | None, width: int, height: int) -> np.ndarray:
    canvas = np.full((height, width, 3), 17, dtype=np.uint8)
    if image is None or not image.size:
        _text(canvas, "WAITING FOR CAMERA", (24, height // 2), color=AMBER, scale=0.65, thickness=2)
        return canvas
    source_h, source_w = image.shape[:2]
    scale = min(width / source_w, height / source_h)
    resized = cv2.resize(
        image,
        (max(1, int(source_w * scale)), max(1, int(source_h * scale))),
        interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
    )
    y = (height - resized.shape[0]) // 2
    x = (width - resized.shape[1]) // 2
    canvas[y:y + resized.shape[0], x:x + resized.shape[1]] = resized
    return canvas


def _annotate_frame(image: np.ndarray, observations: list[dict]) -> np.ndarray:
    annotated = image.copy()
    for item in observations:
        track = item.get("track")
        if not track:
            label = "UNKNOWN · UNTRACKED"
            color = AMBER
        else:
            state = track.get("state", "UNCONFIRMED")
            person_id = track.get("person_id") if state == "CONFIRMED" else None
            if person_id:
                label = f"{person_id} {track.get('display_name') or ''} · T{track['track_id']:02d} · CONFIRMED".strip()
                color = GREEN
            elif state == "SUSPENDED":
                label = f"VERIFY · T{track['track_id']:02d}"
                color = AMBER
            else:
                label = f"UNKNOWN · T{track['track_id']:02d} · {state}"
                color = RED
        score = item.get("scores", {}).get("top1_score")
        if score is not None:
            label = f"{label} · {score:.2f}"
        x1, y1, x2, y2 = [int(round(value)) for value in item["bbox"]]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)
        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.43, 1)
        label_y = max(th + 7, y1)
        cv2.rectangle(annotated, (x1, label_y - th - 7), (x1 + tw + 8, label_y + baseline), color, -1)
        _text(annotated, label, (x1 + 4, label_y - 3), color=(12, 14, 16), scale=0.43)
    return annotated


def _format_last_seen(seconds: float, seen_now: bool) -> str:
    return "now" if seen_now or seconds < 0.5 else f"{seconds:.1f}s ago"


def render_institutional_dashboard(
    result: dict | None,
    cards: list[dict],
    *,
    window_width: int = 1600,
    window_height: int = 980,
    stream_status: str = "CONNECTING",
    profile_status: str = "CONNECTING",
    profile_latency_ms: float = 0.0,
    render_fps: float = 0.0,
    scroll_offset: int = 0,
    extra_metrics: dict | None = None,
) -> tuple[np.ndarray, int]:
    """Render a 50/50 dashboard and return its image and maximum scroll offset."""
    header_h = 58
    footer_h = 76
    body_h = max(100, window_height - header_h - footer_h)
    left_w = window_width // 2
    right_w = window_width - left_w
    canvas = np.full((window_height, window_width, 3), BG, dtype=np.uint8)
    _text(canvas, "INSTITUTIONAL FACIAL RECOGNITION — LIVE", (18, 27), scale=0.62, thickness=2)
    _text(canvas, "LIVE CAMERA · UNIFACE + BYTETRACKER", (18, 49), color=GREEN, scale=0.43)
    _text(canvas, "INSTITUTIONAL PROFILES", (left_w + 18, 49), color=GREEN, scale=0.43)

    if result is not None:
        video = _annotate_frame(result["image"], result.get("observations", []))
    else:
        video = None
    left_panel = _fit_image(video, left_w, body_h)
    if result is None:
        _text(left_panel, stream_status, (24, body_h // 2 + 32), color=AMBER, scale=0.5)
    canvas[header_h:header_h + body_h, :left_w] = left_panel
    canvas[header_h:header_h + body_h, left_w:] = PANEL
    cv2.line(canvas, (left_w, header_h), (left_w, header_h + body_h), LINE, 2)

    card_x = left_w + 14
    card_w = right_w - 28
    card_h = 132
    gap = 10
    top = header_h + 16
    visible_rows = max(1, (body_h - 24) // (card_h + gap))
    max_scroll = max(0, len(cards) - visible_rows)
    offset = min(max(0, scroll_offset), max_scroll)
    visible_cards = cards[offset:offset + visible_rows]
    if not visible_cards:
        _text(canvas, "No active persons detected", (card_x + 18, top + 35), color=MUTED, scale=0.56)
    for index, profile in enumerate(visible_cards):
        y = top + index * (card_h + gap)
        state = profile["identity_status"]
        accent = GREEN if state == "CONFIRMED" else (AMBER if state in {"SUSPENDED", "PROFILE UNAVAILABLE"} else RED)
        cv2.rectangle(canvas, (card_x, y), (card_x + card_w, y + card_h), CARD, -1)
        cv2.rectangle(canvas, (card_x, y), (card_x + 5, y + card_h), accent, -1)
        if profile.get("person_id"):
            title = f"{profile['display_name']}  ·  {profile['person_id']}"
        else:
            title = f"{profile['display_name']}  ·  Track {profile['track_id']:02d}"
        _text(canvas, title, (card_x + 17, y + 28), color=WHITE, scale=0.54, thickness=2)
        _text(canvas, f"Institutional rank: {profile.get('rank_name') or '—'}", (card_x + 17, y + 56), color=AMBER if profile.get("rank_name") else MUTED, scale=0.46)
        _text(canvas, f"Person ID: {profile.get('person_id') or '—'}    Track ID: {profile['track_id']:02d}", (card_x + 17, y + 82), color=MUTED, scale=0.42)
        seen = _format_last_seen(profile.get("last_seen_seconds", 0.0), profile.get("seen_now", False))
        _text(canvas, f"{state}  ·  Last seen: {seen}", (card_x + 17, y + 109), color=accent, scale=0.42, thickness=1)
    if len(cards) > visible_rows:
        _text(canvas, f"{offset + 1}–{min(offset + visible_rows, len(cards))} of {len(cards)}   ·   wheel / PgUp / PgDn to scroll", (card_x + 4, header_h + body_h - 5), color=MUTED, scale=0.34)

    footer_y = header_h + body_h
    cv2.rectangle(canvas, (0, footer_y), (window_width, window_height), BG, -1)
    metrics = (result or {}).get("metrics", {})
    observations = (result or {}).get("observations", [])
    confirmed = sum(card["identity_status"] == "CONFIRMED" for card in cards)
    unknown = sum(card["identity_status"] in {"UNCONFIRMED", "TENTATIVE"} for card in cards)
    tracks = len(cards)
    extra = extra_metrics or {}
    line1 = (
        f"Webcam {metrics.get('stream_fps', 0):.1f} FPS  |  Infer {metrics.get('processing_fps', 0):.1f} FPS  |  "
        f"Render {render_fps:.1f} FPS  |  Faces {len(observations)}  |  Confirmed {confirmed}  |  Unknown {unknown}  |  Tracks {tracks}"
    )
    line2 = (
        f"UniFace {((result or {}).get('uniface_seconds', 0.0) * 1000):.1f} ms  |  "
        f"BYTETracker {((result or {}).get('track_association_seconds', 0.0) * 1000):.2f} ms  |  "
        f"Profiles {profile_latency_ms:.2f} ms  |  RAM {extra.get('ram_mb', '--')} MB  |  "
        f"VRAM {extra.get('vram_mb', '--')} MB  |  SQLite {profile_status}"
    )
    _text(canvas, line1, (14, footer_y + 23), scale=0.38)
    _text(canvas, line2, (14, footer_y + 46), color=MUTED, scale=0.34)
    _text(canvas, f"Camera: {stream_status}   |   Q/Esc close   R reset   PgUp/PgDn scroll", (14, footer_y + 66), color=MUTED, scale=0.34)
    return canvas, max_scroll
