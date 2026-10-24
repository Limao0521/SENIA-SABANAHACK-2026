"""Run the live UniFace + BYTETracker app with comparison or registry views."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

from .comparative_renderer import render_comparison
from .identity_registry import IdentityRegistry, REGISTRY_PATH
from .institutional_renderer import render_institutional_dashboard
from .frame_buffer import LatestFrameBuffer
from .live_recognizer import LiveRecognizer, SessionLogger
from .mjpeg_client import FrameReceiver
from .profile_service import ProfileService


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "UF_OSFR_04"
IDREG_OUT = ROOT / "outputs" / "UF_IDREG_01"
CONFIG = {
    "experiment": "UF-OSFR-04",
    "uniface_version": "4.1.0",
    "detector": "RetinaFace MNET_V2",
    "recognizer": "EdgeFace XXS",
    "embedding_dimensions": 512,
    "gallery": str(ROOT / "outputs/UF_LAYA_01/identity_gallery.sqlite3"),
    "gallery_mode": "SQLite immutable read-only",
    "baseline_min_cosine": 0.45,
    "baseline_min_margin": 0.08,
    "bytetracker": {
        "implementation": "uniface.BYTETracker 4.1.0",
        "track_thresh": 0.35,
        "low_thresh": 0.05,
        "track_buffer_processed_frames": 12,
        "match_thresh": 0.8,
        "association": "Hungarian assignment on IoU; accepted IoU >= 0.30",
        "ambiguous_when_iou_gap_below": 0.08,
    },
    "temporal_identity": {
        "required_consistent_accepted_observations": 2,
        "confirmation_window_seconds": 2.0,
        "confirmed_persistence_after_low_or_rejected_score_seconds": 1.5,
        "identity_release_after_track_missing_seconds": 1.2,
        "clear_biometrics_on_loss": True,
        "reset_tracker_after_source_gap_seconds": 1.2,
    },
    "default_stream_url": "http://127.0.0.1:8765/stream.mjpg",
    "sensor_capture_timestamp_available": False,
    "record_video_by_default": False,
    "embeddings_written_to_log": False,
    "frames_or_crops_written_to_log": False,
}


class RuntimeResourceMonitor:
    """Sample process RSS and aggregate GPU VRAM away from the render thread."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest = {"ram_mb": None, "vram_mb": None}
        self._thread = threading.Thread(target=self._run, name="resource-monitor", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._latest)

    def _run(self) -> None:
        while not self._stop.is_set():
            values = {"ram_mb": None, "vram_mb": None}
            try:
                with open("/proc/self/statm", "r", encoding="ascii") as stream:
                    resident_pages = int(stream.read().split()[1])
                values["ram_mb"] = round(resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024), 1)
            except (OSError, ValueError, IndexError):
                pass
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                    capture_output=True,
                    text=True,
                    timeout=1.2,
                    check=True,
                )
                values["vram_mb"] = max(int(line.strip()) for line in result.stdout.splitlines() if line.strip())
            except (OSError, subprocess.SubprocessError, ValueError):
                pass
            with self._lock:
                self._latest = values
            self._stop.wait(self.interval)


class RegistryGalleryWatcher:
    """Reload whole gallery snapshots after SQLite writes, off the inference/UI threads."""

    def __init__(self, recognizer: LiveRecognizer, registry_path: Path, interval: float = 1.0) -> None:
        self.recognizer = recognizer
        self.registry_path = registry_path
        self.interval = interval
        self._stop = threading.Event()
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, name="registry-gallery-watcher", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)

    def _run(self) -> None:
        registry = IdentityRegistry(self.registry_path)
        try:
            with registry.connect(readonly=True) as db:
                previous = int(db.execute("PRAGMA data_version").fetchone()[0])
                while not self._stop.wait(self.interval):
                    current = int(db.execute("PRAGMA data_version").fetchone()[0])
                    if current == previous:
                        continue
                    try:
                        self.recognizer.reload_registry_gallery()
                        self.error = None
                        previous = current
                    except Exception as exc:
                        # Keep the last complete matrix if the writer has not committed a valid set.
                        self.error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"


def write_idreg_metrics(
    *,
    path: Path,
    started: float,
    recognizer: LiveRecognizer,
    worker: RecognizerWorker,
    frame_buffer: LatestFrameBuffer,
    profile_snapshot: dict,
    render_fps: float,
    resource_snapshot: dict,
    receiver: FrameReceiver,
    view: str,
) -> None:
    latest = worker.latest() or {}
    cards = profile_snapshot.get("cards", [])
    metrics = latest.get("metrics", {})
    report = {
        "experiment": "UF-IDREG-01",
        "updated_unix": time.time(),
        "started_unix": started,
        "source": receiver.source,
        "view": view,
        "stream_status": frame_buffer.state().get("status"),
        "receiver_error": receiver.error,
        "inference_error": worker.error,
        "frames_received": frame_buffer.state().get("latest_frame_id", 0),
        "frames_inferred": worker.total_frames,
        "faces_in_latest_result": len(latest.get("observations", [])),
        "tracks_in_panel": len(cards),
        "confirmed_profiles": sum(card.get("identity_status") == "CONFIRMED" for card in cards),
        "unknown_profiles": sum(card.get("identity_status") in {"UNKNOWN", "UNCONFIRMED", "TENTATIVE"} for card in cards),
        "gallery_size": len(recognizer.gallery),
        "gallery_revision": recognizer.gallery_revision,
        "fps": {
            "camera": metrics.get("stream_fps"),
            "inference": metrics.get("processing_fps"),
            "render": render_fps,
        },
        "latency_ms": {
            "uniface": latest.get("uniface_seconds", 0.0) * 1000.0,
            "detection": latest.get("detection_seconds", 0.0) * 1000.0,
            "embedding": latest.get("embedding_seconds", 0.0) * 1000.0,
            "bytetracker": latest.get("track_association_seconds", 0.0) * 1000.0,
            "profile_query": profile_snapshot.get("query_latency_ms"),
        },
        "profile_status": profile_snapshot.get("status"),
        "memory_mb": resource_snapshot,
        "frames_saved": False,
        "embeddings_saved_to_logs": False,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    path.chmod(0o600)


class RecognizerWorker:
    def __init__(self, recognizer: LiveRecognizer, buffer: LatestFrameBuffer, logger: SessionLogger) -> None:
        self.recognizer = recognizer
        self.buffer = buffer
        self.logger = logger
        self._stop = threading.Event()
        self._reset = threading.Event()
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._thread = threading.Thread(target=self._run, name="uniface-inference", daemon=True)
        self._durations = deque(maxlen=120)
        self._finished_at = deque(maxlen=120)
        self.total_frames = 0
        self.known_unknown_transitions = 0
        self.confirmed_identity_losses = 0
        self.new_track_ids: set[tuple[int, int]] = set()
        self.reassociation_count = 0
        self._last_baseline_state: dict[int, str] = {}
        self._last_source_received_at: float | None = None
        self.stream_gap_resets = 0
        self.finished = threading.Event()
        self.max_frames = 0
        self._changed = threading.Condition()
        self.error: str | None = None

    def start(self) -> None:
        self._thread.start()

    def request_reset(self) -> None:
        self._reset.set()

    def stop(self) -> None:
        self._stop.set()
        with self._changed:
            self._changed.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)

    def latest(self) -> dict | None:
        with self._lock:
            return self._latest

    def _run(self) -> None:
        consumed = 0
        try:
            while not self._stop.is_set():
                if self._reset.is_set():
                    self.recognizer.reset_tracker()
                    self._reset.clear()
                frame = self.buffer.next_after(consumed, timeout=0.25)
                if frame is None:
                    continue
                consumed = frame.frame_id
                if self.max_frames and self.total_frames >= self.max_frames:
                    break
                gap_reset = False
                if (
                    self._last_source_received_at is not None
                    and frame.received_at - self._last_source_received_at > 1.2
                ):
                    self.recognizer.reset_tracker()
                    self.stream_gap_resets += 1
                    gap_reset = True
                self._last_source_received_at = frame.received_at
                try:
                    result = self.recognizer.process(frame)
                    if gap_reset:
                        result["tracker_reset_reason"] = "source_frame_gap_over_1.2_seconds"
                    self.total_frames += 1
                    for item in result["observations"]:
                        track = item.get("track")
                        if track is None:
                            continue
                        track_id = track["track_id"]
                        track_key = (self.recognizer.reset_count, track_id)
                        state = item["scores"]["status"]
                        if state in {"KNOWN", "UNKNOWN"}:
                            previous = self._last_baseline_state.get(track_key)
                            if previous in {"KNOWN", "UNKNOWN"} and previous != state:
                                self.known_unknown_transitions += 1
                            self._last_baseline_state[track_key] = state
                        self.new_track_ids.add(track_key)
                    for track in result["tracks"]:
                        if track["state_transition"] in {"CONFIRMED->SUSPENDED", "CONFIRMED->LOST"}:
                            self.confirmed_identity_losses += 1
                    finished = time.monotonic()
                    self.reassociation_count += len(result["reassociated_tracks"])
                    self._durations.append(result["processing_seconds"])
                    self._finished_at.append(finished)
                    source = self.buffer.state()
                    result["metrics"] = self._metrics(source, finished, frame.received_at)
                    self.logger.write_frame(result)
                    with self._lock:
                        self._latest = result
                    with self._changed:
                        self._changed.notify_all()
                except Exception as exc:
                    self.error = f"{type(exc).__name__}: {exc}"
                    self.buffer.mark_disconnected("INFERENCE ERROR")
                if self.max_frames and self.total_frames >= self.max_frames:
                    break
        finally:
            self.finished.set()

    def _metrics(self, source_state: dict, now: float, received_at: float) -> dict:
        durations = np.asarray(self._durations, dtype=np.float64)
        finished = list(self._finished_at)
        processing_fps = 0.0
        if len(finished) > 1 and finished[-1] > finished[0]:
            processing_fps = (len(finished) - 1) / (finished[-1] - finished[0])
        timestamps = source_state["received_timestamps"]
        stream_fps = 0.0
        if len(timestamps) > 1 and timestamps[-1] > timestamps[0]:
            stream_fps = (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])
        return {
            "stream_fps": stream_fps,
            "processing_fps": processing_fps,
            "processing_p50_ms": float(np.percentile(durations, 50) * 1000) if durations.size else 0.0,
            "processing_p95_ms": float(np.percentile(durations, 95) * 1000) if durations.size else 0.0,
            "frame_age_ms": max(0.0, now - received_at) * 1000,
            "frames_dropped": source_state["frames_dropped"],
            "known_unknown_transitions": self.known_unknown_transitions,
            "confirmed_identity_losses": self.confirmed_identity_losses,
            "track_ids_created": len(self.new_track_ids),
            "track_reassociations": self.reassociation_count,
        }


def write_runtime_report(
    path: Path,
    recognizer: LiveRecognizer,
    worker: RecognizerWorker,
    logger: SessionLogger,
    receiver: FrameReceiver,
    started: float,
    render_durations: deque,
    render_times: deque,
    *,
    final: bool,
) -> None:
    latest = worker.latest() or {}
    state = worker.buffer.state() if hasattr(worker, "buffer") else {}
    now = time.time()
    render_timestamps = list(render_times)
    render_fps = 0.0
    if len(render_timestamps) > 1 and render_timestamps[-1] > render_timestamps[0]:
        render_fps = (len(render_timestamps) - 1) / (render_timestamps[-1] - render_timestamps[0])
    report = {
        "experiment": "UF-OSFR-04",
        "started_unix": started,
        "session_state": "STOPPED" if final else "RUNNING",
        "updated_unix": now,
        "ended_unix": now if final else None,
        "session_log": str(logger.path),
        "frames_inferred": logger.frames,
        "face_observations": logger.observations,
        "frames_received": state.get("latest_frame_id", 0),
        "frames_dropped": state.get("frames_dropped", 0),
        "stream_status": state.get("status", "unknown"),
        "receiver_error": receiver.error,
        "inference_error": worker.error,
        "tracker_resets": recognizer.reset_count,
        "known_unknown_transitions_on_tracks": worker.known_unknown_transitions,
        "confirmed_identity_losses": worker.confirmed_identity_losses,
        "track_ids_created": len(worker.new_track_ids),
        "track_reassociations": worker.reassociation_count,
        "stream_gap_resets": worker.stream_gap_resets,
        "time_to_first_confirmation_seconds": [
            record.time_to_confirm_seconds
            for record in recognizer.temporal.tracks.values()
            if record.time_to_confirm_seconds is not None
        ],
        "identity_persistence_seconds": {
            str(track_id): record.identity_persistence_seconds
            for track_id, record in recognizer.temporal.tracks.items()
        },
        "runtime": recognizer.runtime,
        "last_metrics": latest.get("metrics"),
        "render_latency_ms": {
            "p50": float(np.percentile(render_durations, 50) * 1000) if render_durations else None,
            "p95": float(np.percentile(render_durations, 95) * 1000) if render_durations else None,
        },
        "render_fps": render_fps if render_times else None,
        "latency_definition": "local receive/decode plus processing; source capture timestamps are not provided by the active source adapter",
        "frames_saved": False,
        "embeddings_saved": False,
    }
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("webcam", "video"), default="webcam")
    parser.add_argument("--view", choices=("institutional",), default="institutional")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH, help="Institutional SQLite path")
    parser.add_argument("--url", default="http://127.0.0.1:8765/stream.mjpg")
    parser.add_argument("--path", help="Local video path when --source video")
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index when --source webcam (default: 0)")
    parser.add_argument("--no-loop-video", action="store_true", help="Stop acquisition at end of local file")
    parser.add_argument("--window-width", type=int, default=1600)
    parser.add_argument("--window-height", type=int, default=980)
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--max-frames", type=int, default=0, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.source == "video" and not args.path:
        raise SystemExit("--path is required with --source video")
    if args.source == "webcam" and args.camera_index < 0:
        raise SystemExit("--camera-index must be zero or greater")
    institutional = args.view == "institutional"
    if institutional and not args.registry.is_file():
        raise SystemExit(
            f"Institutional registry not found: {args.registry}. "
            "Initialize it first with `python -m live.registry_cli init`."
        )

    output_dir = IDREG_OUT if institutional else OUT
    output_dir.mkdir(parents=True, exist_ok=True)
    if not institutional:
        (OUT / "tracker_config.json").write_text(json.dumps(CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
    started = time.time()
    gallery_description = "institutional SQLite gallery" if institutional else "frozen UF-LAYA-01 gallery"
    print(f"Loading {gallery_description} and FaceAnalyzer…", flush=True)
    recognizer = LiveRecognizer(registry_path=args.registry if institutional else None)
    print(f"Gallery: {len(recognizer.gallery)} identities · runtime providers: {recognizer.runtime}", flush=True)

    frame_buffer = LatestFrameBuffer()
    log_dir = output_dir / "session_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = SessionLogger(log_dir)
    window = "UF-IDREG-01 | Institutional Identity Dashboard" if institutional else "UF-OSFR-04 | UniFace vs BYTETracker"
    profile_service = ProfileService(args.registry) if institutional else None
    resource_monitor = RuntimeResourceMonitor() if institutional else None
    gallery_watcher = RegistryGalleryWatcher(recognizer, args.registry) if institutional else None
    window_created = False
    if not args.headless:
        try:
            cv2.namedWindow(window, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(window, args.window_width, args.window_height)
            window_created = True
        except Exception:
            logger.close()
            cv2.destroyAllWindows()
            raise
    receiver = FrameReceiver(
        frame_buffer,
        source=args.source,
        url=args.url,
        path=args.path,
        camera_index=args.camera_index,
        loop_video=not args.no_loop_video,
    )
    worker = RecognizerWorker(recognizer, frame_buffer, logger)
    worker.buffer = frame_buffer
    worker.max_frames = args.max_frames
    scroll_state = {"offset": 0, "max_offset": 0}

    def on_mouse(event, x, _y, flags, _param):
        if not institutional or event != cv2.EVENT_MOUSEWHEEL or x < args.window_width // 2:
            return
        delta = cv2.getMouseWheelDelta(flags)
        direction = -1 if delta > 0 else 1
        scroll_state["offset"] = min(
            scroll_state["max_offset"], max(0, scroll_state["offset"] + direction * max(1, abs(delta) // 120))
        )

    if not args.headless and institutional:
        cv2.setMouseCallback(window, on_mouse)
    if profile_service:
        profile_service.start()
    if resource_monitor:
        resource_monitor.start()
    if gallery_watcher:
        gallery_watcher.start()
    receiver.start()
    worker.start()

    last_result_frame = 0
    render_times = deque(maxlen=60)
    render_durations = deque(maxlen=120)
    show_help = False
    last_state_print = 0.0
    last_render_seconds = 0.0
    app_started_monotonic = time.monotonic()
    last_report_monotonic = 0.0
    normal_exit = False
    latest_profile_snapshot = {"cards": [], "query_latency_ms": 0.0, "status": "CONNECTING"}
    try:
        while True:
            result = worker.latest()
            if result is not None and result["source_frame_id"] != last_result_frame:
                last_result_frame = result["source_frame_id"]
                if profile_service:
                    profile_service.submit(result.get("tracks", []))
                if args.max_frames and worker.finished.is_set() and worker.total_frames >= args.max_frames:
                    normal_exit = True
                    break
            state = frame_buffer.state()
            if state["last_received_at"] is None:
                stale = time.monotonic() - app_started_monotonic > 6.0
                if not stale:
                    state["status"] = "CONNECTING"
            else:
                stale = time.monotonic() - state["last_received_at"] > 2.5
            if stale:
                frame_buffer.mark_disconnected("DISCONNECTED")
                state = frame_buffer.state()
                result = None

            if profile_service:
                latest_profile_snapshot = profile_service.snapshot()
            resource_snapshot = resource_monitor.snapshot() if resource_monitor else {"ram_mb": None, "vram_mb": None}

            if time.monotonic() - last_report_monotonic >= 5.0:
                if institutional:
                    render_fps = (
                        (len(render_times) - 1) / (render_times[-1] - render_times[0])
                        if len(render_times) > 1 and render_times[-1] > render_times[0]
                        else 0.0
                    )
                    write_idreg_metrics(
                        path=IDREG_OUT / "performance_metrics.json",
                        started=started,
                        recognizer=recognizer,
                        worker=worker,
                        frame_buffer=frame_buffer,
                        profile_snapshot=latest_profile_snapshot,
                        render_fps=render_fps,
                        resource_snapshot=resource_snapshot,
                        receiver=receiver,
                        view=args.view,
                    )
                else:
                    write_runtime_report(
                        OUT / "runtime_metrics.json",
                        recognizer,
                        worker,
                        logger,
                        receiver,
                        started,
                        render_durations,
                        render_times,
                        final=False,
                    )
                last_report_monotonic = time.monotonic()

            if args.headless:
                if worker.error:
                    print(f"Inference error: {worker.error}", flush=True)
                    return 2
                if time.monotonic() - last_state_print > 1.0:
                    last_state_print = time.monotonic()
                    state = frame_buffer.state()
                    print(json.dumps({
                        "status": state["status"],
                        "frames_received": state["latest_frame_id"],
                        "frames_inferred": recognizer.frame_id,
                        "faces": len(result["observations"]) if result else 0,
                    }), flush=True)
                if args.max_frames and worker.finished.is_set() and worker.total_frames >= args.max_frames:
                    normal_exit = True
                    break
                if receiver.error and args.source == "video":
                    raise RuntimeError(receiver.error)
                time.sleep(0.05)
                continue

            render_started = time.perf_counter()
            if result is not None:
                result_for_display = dict(result)
                result_for_display["metrics"] = dict(result["metrics"])
                result_for_display["metrics"]["render_latency_ms"] = last_render_seconds * 1000
                if len(render_times) > 1 and render_times[-1] > render_times[0]:
                    result_for_display["metrics"]["render_fps"] = (len(render_times) - 1) / (render_times[-1] - render_times[0])
                else:
                    result_for_display["metrics"]["render_fps"] = 0.0
            else:
                result_for_display = None
            if institutional:
                resource_snapshot = resource_monitor.snapshot() if resource_monitor else resource_snapshot
                display, max_scroll = render_institutional_dashboard(
                    result_for_display,
                    latest_profile_snapshot.get("cards", []),
                    window_width=args.window_width,
                    window_height=args.window_height,
                    stream_status=state["status"],
                    profile_status=latest_profile_snapshot.get("status", "UNKNOWN"),
                    profile_latency_ms=latest_profile_snapshot.get("query_latency_ms", 0.0),
                    render_fps=(
                        (len(render_times) - 1) / (render_times[-1] - render_times[0])
                        if len(render_times) > 1 and render_times[-1] > render_times[0]
                        else 0.0
                    ),
                    scroll_offset=scroll_state["offset"],
                    extra_metrics=resource_snapshot,
                )
                scroll_state["max_offset"] = max_scroll
                scroll_state["offset"] = min(scroll_state["offset"], max_scroll)
            else:
                display = render_comparison(
                    result_for_display,
                    window_width=args.window_width,
                    window_height=args.window_height,
                    stream_status=state["status"],
                )
            if show_help:
                cv2.rectangle(display, (20, 58), (display.shape[1] - 20, 145), (28, 34, 42), -1)
                cv2.putText(display, "Q or Esc quits  |  R resets tracker and temporal identities  |  H closes this help", (36, 96), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 1, cv2.LINE_AA)
            render_times.append(time.monotonic())
            cv2.imshow(window, display)
            last_render_seconds = time.perf_counter() - render_started
            render_durations.append(last_render_seconds)
            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                normal_exit = True
                break
            if key == ord("r"):
                worker.request_reset()
            if key == ord("h"):
                show_help = not show_help
            if institutional and key in (ord("]"), 34):
                scroll_state["offset"] = min(scroll_state["max_offset"], scroll_state["offset"] + 1)
            if institutional and key in (ord("["), 33):
                scroll_state["offset"] = max(0, scroll_state["offset"] - 1)
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                normal_exit = True
                break
    except KeyboardInterrupt:
        normal_exit = True
    finally:
        worker.stop()
        receiver.stop()
        if profile_service:
            profile_service.stop()
            latest_profile_snapshot = profile_service.snapshot()
        if gallery_watcher:
            gallery_watcher.stop()
        if resource_monitor:
            resource_monitor.stop()
        logger.close()
        if not args.headless:
            cv2.destroyAllWindows()
        if institutional:
            final_render_fps = (
                (len(render_times) - 1) / (render_times[-1] - render_times[0])
                if len(render_times) > 1 and render_times[-1] > render_times[0]
                else 0.0
            )
            write_idreg_metrics(
                path=IDREG_OUT / "performance_metrics.json",
                started=started,
                recognizer=recognizer,
                worker=worker,
                frame_buffer=frame_buffer,
                profile_snapshot=latest_profile_snapshot,
                render_fps=final_render_fps,
                resource_snapshot=resource_monitor.snapshot() if resource_monitor else {},
                receiver=receiver,
                view=args.view,
            )
            validation = {
                "experiment": "UF-IDREG-01",
                "updated_at_unix": time.time(),
                "window_created": window_created,
                "window_closed_cleanly": normal_exit,
                "source": args.source,
                "camera_frames_received": frame_buffer.state().get("latest_frame_id", 0),
                "frames_inferred": worker.total_frames,
                "face_observations": logger.observations,
                "profile_service_status": latest_profile_snapshot.get("status"),
                "sqlite_gallery_size": len(recognizer.gallery),
                "runtime": recognizer.runtime,
                "camera_live_validated": (
                    args.source == "webcam" and frame_buffer.state().get("latest_frame_id", 0) > 0
                ),
                "video_or_camera_recorded": False,
                "embeddings_written_to_logs": False,
            }
            (IDREG_OUT / "gui_validation.json").write_text(
                json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            (IDREG_OUT / "gui_validation.json").chmod(0o600)
        else:
            write_runtime_report(
                OUT / "runtime_metrics.json",
                recognizer,
                worker,
                logger,
                receiver,
                started,
                render_durations,
                render_times,
                final=True,
            )

    if worker.error:
        print(f"Inference error: {worker.error}", flush=True)
        return 2
    if not args.headless:
        print(f"Session log: {logger.path}", flush=True)
    return 0 if normal_exit else 1


if __name__ == "__main__":
    raise SystemExit(main())
