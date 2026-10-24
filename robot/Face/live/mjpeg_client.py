"""MJPEG and local-video acquisition with automatic reconnection."""

from __future__ import annotations

import re
import threading
import time
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .frame_buffer import LatestFrameBuffer


class FrameReceiver:
    def __init__(
        self,
        buffer: LatestFrameBuffer,
        *,
        source: str,
        url: str,
        path: str | None = None,
        camera_index: int = 0,
        loop_video: bool = True,
        request_timeout: float = 4.0,
        reconnect_delay: float = 1.0,
    ) -> None:
        self.buffer = buffer
        self.source = source
        self.url = url
        self.path = path
        self.camera_index = camera_index
        self.loop_video = loop_video
        self.request_timeout = request_timeout
        self.reconnect_delay = reconnect_delay
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="frame-receiver", daemon=True)
        self._capture: cv2.VideoCapture | None = None
        self._response = None
        self._response_lock = threading.Lock()
        self.error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._capture is not None:
            self._capture.release()
        with self._response_lock:
            if self._response is not None:
                self._response.close()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        if self.source == "video":
            self._run_video()
        elif self.source == "webcam":
            self._run_webcam()
        else:
            self._run_mjpeg()

    def _run_webcam(self) -> None:
        """Read the selected camera attached to this computer, reconnecting on failure."""
        while not self._stop.is_set():
            capture = cv2.VideoCapture(self.camera_index)
            self._capture = capture
            if not capture.isOpened():
                self.error = f"OpenCV could not open local camera index {self.camera_index}"
                self.buffer.mark_disconnected("CAMERA OPEN FAILED")
                capture.release()
                self._capture = None
                self._stop.wait(self.reconnect_delay)
                continue

            self.error = None
            try:
                capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                self.buffer.mark_disconnected("CAMERA CONNECTED · WAITING FOR FRAME")
                while not self._stop.is_set():
                    started = time.perf_counter()
                    ok, image = capture.read()
                    read_seconds = time.perf_counter() - started
                    if not ok or image is None or not image.size:
                        self.error = f"Local camera index {self.camera_index} stopped returning frames"
                        break
                    self.buffer.publish(image, read_seconds)
            finally:
                capture.release()
                self._capture = None

            if not self._stop.is_set():
                self.buffer.mark_disconnected("CAMERA DISCONNECTED · RECONNECTING")
                self._stop.wait(self.reconnect_delay)
        self.buffer.mark_disconnected()

    def _run_video(self) -> None:
        if not self.path:
            self.error = "--path is required for --source video"
            self.buffer.mark_disconnected("NO VIDEO PATH")
            return
        media_path = str(Path(self.path).expanduser())
        while not self._stop.is_set():
            capture = cv2.VideoCapture(media_path)
            self._capture = capture
            if not capture.isOpened():
                self.error = f"OpenCV could not open video: {media_path}"
                self.buffer.mark_disconnected("VIDEO OPEN FAILED")
                return
            fps = capture.get(cv2.CAP_PROP_FPS)
            delay = 1.0 / min(max(fps, 1.0), 60.0) if fps else 1.0 / 30.0
            while not self._stop.is_set():
                started = time.perf_counter()
                ok, image = capture.read()
                if not ok or image is None:
                    break
                self.buffer.publish(image, time.perf_counter() - started)
                remaining = delay - (time.perf_counter() - started)
                if remaining > 0:
                    self._stop.wait(remaining)
            capture.release()
            self._capture = None
            if not self.loop_video or self._stop.is_set():
                self.buffer.mark_disconnected("VIDEO ENDED")
                return
        self.buffer.mark_disconnected()

    def _run_mjpeg(self) -> None:
        while not self._stop.is_set():
            try:
                self.buffer.mark_disconnected("CONNECTING")
                request = urllib.request.Request(
                    self.url,
                    headers={"Accept": "multipart/x-mixed-replace,image/jpeg"},
                )
                with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                    with self._response_lock:
                        self._response = response
                    try:
                        content_type = response.headers.get("Content-Type", "")
                        if "multipart" not in content_type.lower():
                            raise ValueError(
                                f"{self.url} returned {content_type!r}; expected the MJPEG endpoint "
                                "(the documented endpoint is /stream.mjpg)"
                            )
                        boundary_match = re.search(r"boundary=\"?([^;\" ]+)", content_type, re.I)
                        boundary = boundary_match.group(1).encode() if boundary_match else b"x2frame"
                        self.buffer.mark_disconnected("CONNECTED · WAITING FOR FRAME")
                        self._read_multipart(response, boundary)
                    finally:
                        with self._response_lock:
                            self._response = None
                if not self._stop.is_set():
                    raise ConnectionError("MJPEG response closed")
            except Exception as exc:
                if self._stop.is_set():
                    break
                self.error = f"{type(exc).__name__}: {exc}"
                self.buffer.mark_disconnected("DISCONNECTED")
                self._stop.wait(self.reconnect_delay)

    def _read_multipart(self, response, boundary: bytes) -> None:
        delimiter = b"--" + boundary
        data = bytearray()
        while not self._stop.is_set():
            chunk = response.read(65536)
            if not chunk:
                return
            data.extend(chunk)
            while True:
                start = data.find(delimiter)
                if start < 0:
                    if len(data) > 2 * 1024 * 1024:
                        del data[:-len(delimiter)]
                    break
                header_start = data.find(b"\r\n\r\n", start + len(delimiter))
                if header_start < 0:
                    if start:
                        del data[:start]
                    break
                header_blob = bytes(data[start + len(delimiter):header_start])
                length_match = re.search(rb"content-length:\s*(\d+)", header_blob, re.I)
                body_start = header_start + 4
                if length_match:
                    length = int(length_match.group(1))
                    end = body_start + length
                    if len(data) < end + 2:
                        if start:
                            del data[:start]
                        break
                    image_bytes = bytes(data[body_start:end])
                    del data[:end]
                else:
                    end = data.find(delimiter, body_start)
                    if end < 0:
                        if start:
                            del data[:start]
                        break
                    image_bytes = bytes(data[body_start:end]).rstrip(b"\r\n")
                    del data[:end]

                started = time.perf_counter()
                image = cv2.imdecode(
                    np.frombuffer(image_bytes, dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
                decode_seconds = time.perf_counter() - started
                if image is not None and image.size:
                    self.buffer.publish(image, decode_seconds)
