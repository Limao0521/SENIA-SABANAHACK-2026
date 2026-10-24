"""Single-slot, latest-frame-wins buffer shared by acquisition and inference."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np
from collections import deque


@dataclass(slots=True)
class Frame:
    frame_id: int
    image: np.ndarray
    received_at: float
    decode_seconds: float


class LatestFrameBuffer:
    """Keep one decoded frame and count replacements before consumption."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._latest: Frame | None = None
        self._sequence = 0
        self._consumed_sequence = 0
        self._dropped = 0
        self._connected = False
        self._status = "CONNECTING"
        self._last_received_at: float | None = None
        self._received_at = deque(maxlen=120)

    def publish(self, image: np.ndarray, decode_seconds: float) -> Frame:
        with self._condition:
            if self._sequence > self._consumed_sequence:
                self._dropped += 1
            self._sequence += 1
            frame = Frame(
                frame_id=self._sequence,
                image=image,
                received_at=time.monotonic(),
                decode_seconds=decode_seconds,
            )
            self._latest = frame
            self._connected = True
            self._status = "CONNECTED"
            self._last_received_at = frame.received_at
            self._received_at.append(frame.received_at)
            self._condition.notify_all()
            return frame

    def next_after(self, frame_id: int, timeout: float = 0.5) -> Frame | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None and self._latest.frame_id > frame_id,
                timeout=timeout,
            )
            frame = self._latest
            if frame is None or frame.frame_id <= frame_id:
                return None
            self._consumed_sequence = frame.frame_id
            return Frame(
                frame_id=frame.frame_id,
                image=frame.image,
                received_at=frame.received_at,
                decode_seconds=frame.decode_seconds,
            )

    def mark_disconnected(self, status: str = "DISCONNECTED") -> None:
        with self._condition:
            self._connected = False
            self._status = status
            self._condition.notify_all()

    def state(self) -> dict:
        with self._condition:
            return {
                "connected": self._connected,
                "status": self._status,
                "latest_frame_id": self._sequence,
                "frames_dropped": self._dropped,
                "last_received_at": self._last_received_at,
                "received_timestamps": list(self._received_at),
            }
