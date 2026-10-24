from __future__ import annotations

import json
import threading
import time
import uuid
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, capacity: int = 256) -> None:
        self._lock = threading.Condition()
        self._events: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._sequence = 0

    def publish(self, event_type: str, **fields: Any) -> dict[str, Any]:
        with self._lock:
            self._sequence += 1
            event = {
                "sequence": self._sequence,
                "event_id": str(uuid.uuid4()),
                "event": event_type,
                "at_unix": time.time(),
                **fields,
            }
            self._events.append(event)
            self._lock.notify_all()
            return dict(event)

    def after(self, sequence: int, timeout: float = 1.0) -> list[dict[str, Any]]:
        with self._lock:
            self._lock.wait_for(lambda: self._sequence > sequence, timeout=timeout)
            return [dict(event) for event in self._events if event["sequence"] > sequence]

    @staticmethod
    def encode(event: dict[str, Any]) -> str:
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"))
