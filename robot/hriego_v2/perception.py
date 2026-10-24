from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .robot_io import open_camera


@dataclass
class PerceptionSnapshot:
    camera_connected: bool = False
    camera_status: str = "STARTING"
    camera_error: str | None = None
    frame: Any | None = None
    result: dict[str, Any] | None = None
    profile_snapshot: dict[str, Any] | None = None
    received_frames: int = 0
    inferred_frames: int = 0
    dropped_frames: int = 0
    camera_fps: float = 0.0
    inference_fps: float = 0.0
    updated_at: float = 0.0


class FacePerception:
    """Thin webcam adapter around Face's existing UniFace/track/profile stack."""

    def __init__(self, settings, on_observation: Callable[[dict[str, Any]], None],
                 publish: Callable[..., Any]) -> None:
        import cv2
        from live.live_recognizer import LiveRecognizer
        from live.profile_service import ProfileService

        self.cv2 = cv2
        self.settings = settings
        self.on_observation = on_observation
        self.publish = publish
        self.recognizer = LiveRecognizer(registry_path=settings.identity_db)
        self.profile_service = ProfileService(settings.identity_db, poll_interval=0.25, lost_display_seconds=2.0)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._latest: tuple[int, Any, float, float] | None = None
        self._latest_frame_id = 0
        self._last_inferred_id = 0
        self._snapshot = PerceptionSnapshot()
        self._camera = None
        self._threads: list[threading.Thread] = []
        self._receive_times: deque[float] = deque(maxlen=60)
        self._infer_times: deque[float] = deque(maxlen=60)

    def start(self) -> None:
        self.profile_service.start()
        self._camera = open_camera(self.settings)
        if not self._camera.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara {self.settings.camera_source}")
        self._camera.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
        self._threads = [
            threading.Thread(target=self._receive_loop, name="hriego-camera", daemon=True),
            threading.Thread(target=self._infer_loop, name="hriego-uniface", daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        self.publish("camera_started", camera_index=self.settings.camera_source,
                     providers=self.recognizer.runtime.get("active_providers"))

    def stop(self) -> None:
        self._stop.set()
        if self._camera is not None:
            self._camera.release()
        for thread in self._threads:
            if thread.is_alive():
                thread.join(timeout=3.0)
        self.profile_service.stop()

    def snapshot(self) -> PerceptionSnapshot:
        with self._lock:
            prof = self.profile_service.snapshot()
            return PerceptionSnapshot(
                **{**self._snapshot.__dict__, "profile_snapshot": prof}
            )

    def _receive_loop(self) -> None:
        while not self._stop.is_set():
            started = time.perf_counter()
            ok, frame = self._camera.read() if self._camera is not None else (False, None)
            decode_s = time.perf_counter() - started
            now = time.monotonic()
            if not ok or frame is None or not frame.size:
                with self._lock:
                    was_connected = self._snapshot.camera_connected
                    self._snapshot.camera_connected = False
                    self._snapshot.camera_status = "DISCONNECTED"
                    self._snapshot.camera_error = "La webcam dejó de entregar cuadros"
                if was_connected:
                    self.publish("camera_disconnected")
                    self.on_observation({"face_count": 0, "observations": [], "tracks": [], "cards": [],
                                         "profile_status": "DISCONNECTED", "camera_connected": False,
                                         "timestamp": time.monotonic()})
                self._camera.release()
                self._stop.wait(1.0)
                if not self._stop.is_set():
                    self._camera = open_camera(self.settings)
                    if self._camera.isOpened():
                        self._camera.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
                    else:
                        self._camera.release()
                        self._camera = None
                        self._stop.wait(2.0)
                continue
            with self._lock:
                if self._latest is not None and self._latest[0] > self._last_inferred_id:
                    self._snapshot.dropped_frames += 1
                self._latest_frame_id += 1
                self._latest = (self._latest_frame_id, frame, now, decode_s)
                self._receive_times.append(now)
                self._snapshot.frame = frame
                self._snapshot.camera_connected = True
                self._snapshot.camera_status = "CONNECTED"
                self._snapshot.camera_error = None
                self._snapshot.received_frames += 1
                self._snapshot.camera_fps = self._rate(self._receive_times)
                self._snapshot.updated_at = now

    def _infer_loop(self) -> None:
        from live.frame_buffer import Frame

        while not self._stop.is_set():
            with self._lock:
                packet = self._latest
            if packet is None or packet[0] <= self._last_inferred_id:
                self._stop.wait(0.01)
                continue
            frame_id, image, received_at, decode_s = packet
            try:
                result = self.recognizer.process(Frame(
                    frame_id=frame_id, image=image, received_at=received_at, decode_seconds=decode_s
                ))
                self.profile_service.submit(result.get("tracks", []))
                self._last_inferred_id = frame_id
                self._infer_times.append(time.monotonic())
                with self._lock:
                    self._snapshot.result = result
                    self._snapshot.inferred_frames += 1
                    self._snapshot.inference_fps = self._rate(self._infer_times)
                    self._snapshot.updated_at = time.monotonic()
                    self._snapshot.camera_status = "CONNECTED"
                    self._snapshot.camera_connected = True
                    prof = self.profile_service.snapshot()
                    self._snapshot.profile_snapshot = prof
                self.on_observation({
                    "face_count": len(result.get("observations", [])),
                    "observations": result.get("observations", []),
                    "tracks": result.get("tracks", []),
                    "cards": prof.get("cards", []),
                    "profile_status": prof.get("status"),
                    "camera_connected": True,
                    "timestamp": time.monotonic(),
                })
            except Exception as exc:
                self._last_inferred_id = frame_id
                with self._lock:
                    self._snapshot.camera_status = "INFERENCE ERROR"
                    self._snapshot.camera_error = f"{type(exc).__name__}: {exc}"
                self.publish("perception_error", error_class=type(exc).__name__)
                self._stop.wait(0.2)

    @staticmethod
    def _rate(samples) -> float:
        if len(samples) < 2 or samples[-1] <= samples[0]:
            return 0.0
        return round((len(samples) - 1) / (samples[-1] - samples[0]), 2)
