"""Robot I/O for HRIEGO v2 on board the X2 Ultra (PC2, Jetson Orin NX).

The cameras of the X2 are owned by the AgiBot HAL and published as ROS 2 topics,
and the speakers live on the other PC (hal_audio, 10.0.1.42) behind the AimDK
audio service. These adapters keep the rest of HRIEGO unchanged:

- ``RosImageCapture`` mimics the subset of ``cv2.VideoCapture`` used by
  ``perception.py`` / ``resources.py`` (isOpened/read/set/release) on top of a
  ``sensor_msgs/Image`` (rgb8/bgr8/mono8) or ``sensor_msgs/CompressedImage`` topic.
- ``AimdkAudioPlayer`` mimics ``hriego.workers.AudioPlayer`` (play/stop): it asks
  for audio focus and streams the WAV as S16LE PCM to ``/aima/hal/audio/playback``,
  the same path as AimDK's ``play_audio_stream.py``.

Selected with HRIEGO_CAMERA_SOURCE=ros:<topic> and HRIEGO_AUDIO_BACKEND=aimdk.
"""
from __future__ import annotations

import atexit
import logging
import os
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable

import numpy as np

log = logging.getLogger("hriego_v2.robot_io")

_ros_lock = threading.Lock()
_ros_executor = None
_ros_thread: threading.Thread | None = None

# Seguridad (HRIEGO_NO_ACTUATION=1): HRIEGO solo puede publicar audio, pedir/soltar
# el foco de audio y mostrar un emoji en la pantalla de la cara (PlayEmoji: solo pantalla,
# sin movimiento; hri_runtime/face.py). Cualquier otro publicador o cliente de servicio
# (p. ej. de locomoción, brazos o presets del MC) se rechaza aquí, antes de llegar a DDS.
ALLOWED_PUBLISH_TOPICS = frozenset({"/aima/hal/audio/playback"})
ALLOWED_SERVICES = frozenset({"/aimdk_5Fmsgs/srv/RequestAudioFocus",
                              "/aimdk_5Fmsgs/srv/AbandonAudioFocus",
                              "/aimdk_5Fmsgs/srv/PlayEmoji",
                              # pausar/reanudar el asistente de voz de fábrica (RUN_MODE), sin movimiento
                              "/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest"})


class ActuationBlocked(PermissionError):
    pass


def _guard_node(node) -> None:
    if os.getenv("HRIEGO_NO_ACTUATION", "1") != "1":
        return
    create_publisher, create_client = node.create_publisher, node.create_client

    def guarded_publisher(msg_type, topic, *args, **kwargs):
        if topic not in ALLOWED_PUBLISH_TOPICS:
            raise ActuationBlocked(f"HRIEGO_NO_ACTUATION: publicador no permitido en {topic}")
        return create_publisher(msg_type, topic, *args, **kwargs)

    def guarded_client(srv_type, srv_name, *args, **kwargs):
        if srv_name not in ALLOWED_SERVICES:
            raise ActuationBlocked(f"HRIEGO_NO_ACTUATION: servicio no permitido {srv_name}")
        return create_client(srv_type, srv_name, *args, **kwargs)

    node.create_publisher = guarded_publisher
    node.create_client = guarded_client


def _ros_node(name: str):
    """Create a node spinning on a shared background executor (rclpy init once)."""
    global _ros_executor, _ros_thread
    import rclpy
    from rclpy.executors import MultiThreadedExecutor

    with _ros_lock:
        if not rclpy.ok():
            rclpy.init()
        if _ros_executor is None:
            _ros_executor = MultiThreadedExecutor(num_threads=2)
            _ros_thread = threading.Thread(target=_ros_executor.spin, name="hriego-ros", daemon=True)
            _ros_thread.start()
            atexit.register(_ros_shutdown)
        node = rclpy.create_node(f"{name}_{os.getpid()}")
        _guard_node(node)
        _ros_executor.add_node(node)
        return node


def _ros_shutdown() -> None:
    """Para el executor antes de que el intérprete termine (evita std::terminate)."""
    import rclpy

    with _ros_lock:
        if _ros_executor is not None:
            _ros_executor.shutdown(timeout_sec=1.0)
        if _ros_thread is not None:
            _ros_thread.join(timeout=2.0)
        if rclpy.ok():
            rclpy.shutdown()


def _ros_drop(node) -> None:
    with _ros_lock:
        if _ros_executor is not None:
            _ros_executor.remove_node(node)
    node.destroy_node()


def open_camera(settings):
    """cv2.VideoCapture for a local V4L2 index, RosImageCapture for ros:<topic>."""
    source = settings.camera_source
    if source.startswith("ros:"):
        return RosImageCapture(source[4:], timeout_s=settings.camera_open_timeout_s)
    import cv2
    return cv2.VideoCapture(int(source), cv2.CAP_V4L2)


class RosImageCapture:
    """Latest-frame reader for a ROS image topic with a VideoCapture-like API."""

    def __init__(self, topic: str, *, timeout_s: float = 5.0) -> None:
        import cv2
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import CompressedImage, Image

        self.cv2 = cv2
        self.topic = topic
        self.compressed = topic.rstrip("/").endswith("/compressed")
        self._cond = threading.Condition()
        self._frame = None
        self._seq = 0
        self._read_seq = 0
        self._closed = False
        self._error: str | None = None
        self._node = _ros_node("hriego_camera")
        # Best-effort subscriptions also match reliable publishers.
        self._sub = self._node.create_subscription(
            CompressedImage if self.compressed else Image, topic, self._on_msg, qos_profile_sensor_data)
        with self._cond:
            self._cond.wait_for(lambda: self._frame is not None or self._closed, timeout=timeout_s)

    def _on_msg(self, msg) -> None:
        try:
            frame = self._decode(msg)
        except Exception as exc:  # malformed frame: keep the last good one
            self._error = f"{type(exc).__name__}: {exc}"
            return
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._cond.notify_all()

    def _decode(self, msg):
        if self.compressed:
            frame = self.cv2.imdecode(np.frombuffer(msg.data, np.uint8), self.cv2.IMREAD_COLOR)
            if frame is None:
                raise ValueError("imdecode falló")
            return frame
        h, w, step, enc = msg.height, msg.width, msg.step, msg.encoding.lower()
        channels = {"rgb8": 3, "bgr8": 3, "rgba8": 4, "bgra8": 4, "mono8": 1}.get(enc)
        if channels is None:
            raise ValueError(f"encoding no soportado: {msg.encoding}")
        buf = np.frombuffer(msg.data, np.uint8).reshape(h, step)[:, : w * channels]
        img = buf.reshape(h, w, channels) if channels > 1 else buf.reshape(h, w)
        conv = {"rgb8": self.cv2.COLOR_RGB2BGR, "rgba8": self.cv2.COLOR_RGBA2BGR,
                "bgra8": self.cv2.COLOR_BGRA2BGR, "mono8": self.cv2.COLOR_GRAY2BGR}.get(enc)
        return self.cv2.cvtColor(img, conv) if conv is not None else img.copy()

    def isOpened(self) -> bool:  # noqa: N802 (cv2 API)
        return not self._closed and self._frame is not None

    def set(self, _prop, _value) -> bool:
        return True

    def read(self, timeout_s: float = 2.0):
        """Block until a frame newer than the last read arrives (like a camera)."""
        with self._cond:
            ok = self._cond.wait_for(lambda: self._seq > self._read_seq or self._closed, timeout=timeout_s)
            if not ok or self._closed or self._frame is None:
                return False, None
            self._read_seq = self._seq
            return True, self._frame

    def release(self) -> None:
        with self._cond:
            if self._closed:
                return
            self._closed = True
            self._cond.notify_all()
        try:
            self._node.destroy_subscription(self._sub)
        finally:
            _ros_drop(self._node)


class AimdkAudioPlayer:
    """Plays a WAV through the X2 speakers (AimDK audio focus + PCM stream)."""

    PRIORITY = 8
    CHUNK_S = 0.04

    def __init__(self, progress: Callable[[dict], None] | None = None) -> None:
        self.progress = progress or (lambda _event: None)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._node = None
        self._pub = None
        self._focus_req = None
        self._focus_rel = None
        self.pkg_name = f"hriego_v2_{os.getpid()}"

    def _ensure(self) -> None:
        if self._node is not None:
            return
        from aimdk_msgs.msg import AudioPlayback
        from aimdk_msgs.srv import AbandonAudioFocus, RequestAudioFocus
        from rclpy.qos import QoSProfile, QoSReliabilityPolicy

        self._node = _ros_node("hriego_audio")
        self._pub = self._node.create_publisher(
            AudioPlayback, "/aima/hal/audio/playback",
            QoSProfile(depth=10, reliability=QoSReliabilityPolicy.RELIABLE))
        self._focus_req = self._node.create_client(RequestAudioFocus, "/aimdk_5Fmsgs/srv/RequestAudioFocus")
        self._focus_rel = self._node.create_client(AbandonAudioFocus, "/aimdk_5Fmsgs/srv/AbandonAudioFocus")

    def _call(self, client, request, timeout_s: float = 3.0):
        if not client.wait_for_service(timeout_sec=timeout_s):
            raise RuntimeError(f"Servicio de audio AimDK no disponible: {client.srv_name}")
        future = client.call_async(request)
        done = threading.Event()
        future.add_done_callback(lambda _f: done.set())
        if not done.wait(timeout_s):
            raise TimeoutError(f"Sin respuesta de {client.srv_name}")
        return future.result()

    def _requester(self):
        from aimdk_msgs.msg import FocusRequester

        requester = FocusRequester()
        requester.pkg_name = self.pkg_name
        requester.priority = self.PRIORITY  # must match between request and abandon
        return requester

    def _request_focus(self) -> None:
        from aimdk_msgs.msg import CommonState
        from aimdk_msgs.srv import RequestAudioFocus

        req = RequestAudioFocus.Request()
        req.focus_requester = self._requester()
        response = self._call(self._focus_req, req)
        if response.reponse.status.value != CommonState.SUCCESS or not response.focus_response.focus_gain:
            raise RuntimeError(f"AimDK no concedió el foco de audio: {response.reponse.message}")

    def _abandon_focus(self) -> None:
        from aimdk_msgs.srv import AbandonAudioFocus

        req = AbandonAudioFocus.Request()
        req.focus_requester = self._requester()
        try:
            self._call(self._focus_rel, req)
        except Exception as exc:
            log.warning("No se pudo liberar el foco de audio: %s", exc)

    def play(self, path: Path, *, cancel_event: threading.Event | None = None) -> dict[str, Any]:
        from aimdk_msgs.msg import AudioPlayback

        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        with wave.open(str(path), "rb") as wav:
            channels, width, rate = wav.getnchannels(), wav.getsampwidth(), wav.getframerate()
            pcm = wav.readframes(wav.getnframes())
        if width != 2:
            raise ValueError(f"WAV de {8 * width} bits; se esperaba S16LE")
        with self._lock:
            self._ensure()
            self._stop.clear()
            started = time.perf_counter()
            # hold_focus (runner HRI): el foco se pide una vez por turno y se suelta con
            # release_focus() al quedar la cola de voz vacía; antes cada frase pagaba pedir y
            # soltar el foco (dos servicios al PC3, con reintentos) y dejaba un hueco entre frases.
            if not (getattr(self, "hold_focus", False) and getattr(self, "_focus_held", False)):
                self._request_focus()
                self._focus_held = True
            self.progress({"component": "Audio", "status": "playing", "player": "aimdk"})
            try:
                bytes_per_s = rate * channels * 2
                chunk = max(2 * channels, int(bytes_per_s * self.CHUNK_S) // (2 * channels) * (2 * channels))
                t0 = time.monotonic()
                for i, offset in enumerate(range(0, len(pcm), chunk)):
                    if self._stop.is_set() or (cancel_event is not None and cancel_event.is_set()):
                        raise RuntimeError("Reproducción cancelada")
                    block = pcm[offset:offset + chunk]
                    msg = AudioPlayback()
                    msg.stamps = self._node.get_clock().now().to_msg()
                    msg.info.channels = channels
                    msg.info.sample_rate = rate
                    msg.info.size = len(block)
                    msg.info.sample_format = "s16le"
                    msg.info.coding_format = "pcm"
                    msg.data.data = block
                    msg.pkg_name = self.pkg_name
                    self._pub.publish(msg)
                    # Pace in real time (with a small lead) like play_audio_stream.py.
                    ahead = (i + 1) * self.CHUNK_S - (time.monotonic() - t0) - 0.2
                    if ahead > 0:
                        time.sleep(ahead)
                tail = len(pcm) / bytes_per_s - (time.monotonic() - t0)
                if tail > 0:
                    self._stop.wait(tail)
            finally:
                if not getattr(self, "hold_focus", False):
                    self._abandon_focus()
                    self._focus_held = False
            return {"player": "aimdk", "duration_s": round(time.perf_counter() - started, 4)}

    def release_focus(self) -> None:
        """Suelta el foco retenido por hold_focus (fin de turno)."""
        with self._lock:
            if getattr(self, "_focus_held", False):
                self._abandon_focus()
                self._focus_held = False

    def stop(self) -> None:
        self._stop.set()


class NullAudioPlayer:
    """HRIEGO_AUDIO_BACKEND=none: no suena nada; espera la duración del WAV para que
    la FSM mantenga los mismos tiempos que con altavoces."""

    def __init__(self, progress: Callable[[dict], None] | None = None) -> None:
        self.progress = progress or (lambda _event: None)
        self._stop = threading.Event()

    def play(self, path: Path, *, cancel_event: threading.Event | None = None) -> dict[str, Any]:
        path = Path(path)
        if not path.is_file():
            raise FileNotFoundError(path)
        with wave.open(str(path), "rb") as wav:
            duration = wav.getnframes() / float(wav.getframerate())
        self._stop.clear()
        self.progress({"component": "Audio", "status": "muted", "player": "none"})
        log.info("Audio silenciado (HRIEGO_AUDIO_BACKEND=none): %s, %.2fs", path.name, duration)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if self._stop.is_set() or (cancel_event is not None and cancel_event.is_set()):
                raise RuntimeError("Reproducción cancelada")
            time.sleep(0.05)
        return {"player": "none", "duration_s": round(duration, 4)}

    def stop(self) -> None:
        self._stop.set()
