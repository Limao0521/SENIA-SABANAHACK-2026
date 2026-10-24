"""Emit confirmed Face identities as JSON lines for the Sabana simulation.

Run in Face/.venv-face with the same environment as scripts/run_hriego_v2.sh.
Only a single confirmed, active face is ever forwarded to the simulator.

The same webcam frames are also scanned for the visitor's credential QR (zxing-cpp, or OpenCV without it;
zxing reads a phone screen at a distance far better); each new code is
forwarded as ``{"event": "qr", "text": ...}``. The simulator verifies it against the confirmed face
(``x2_ultra.hri_runtime.confirm``); this process never trusts or interprets the payload.
"""
from __future__ import annotations

import json
import sys
import threading
import time

from .coordinator import HriCoordinator
from .perception import FacePerception
from .settings import Settings


def main() -> int:
    settings = Settings()
    if not settings.identity_db.is_file():
        raise FileNotFoundError(settings.identity_db)
    last: str | None = None
    lock = threading.Lock()

    def observation(value: dict) -> None:
        nonlocal last
        profile = HriCoordinator._single_confirmed_profile(value)
        person_id = profile["person_id"] if profile else None
        with lock:
            if person_id == last:
                return
            last = person_id
            message = {"event": "profile", "profile": profile} if profile else {"event": "absent"}
            print(json.dumps(message, ensure_ascii=False), flush=True)

    perception = FacePerception(settings, observation, lambda *_args, **_kw: None)
    stop = threading.Event()
    try:
        perception.start()
        threading.Thread(target=scan_qr, args=(perception, stop), name="qr-scan", daemon=True).start()
        print(json.dumps({"event": "ready"}), flush=True)
        threading.Event().wait()
    except KeyboardInterrupt:
        return 0
    finally:
        stop.set()
        perception.stop()
    return 0


QR_PERIOD_S = 0.25      # scans per second are cheap next to the face pipeline
QR_REPEAT_S = 5.0       # the same code held in front of the camera is re-sent only after this


def _qr_reader():
    """frame BGR -> texto del QR (o vacío)."""
    import cv2

    try:
        import zxingcpp

        def read(frame) -> str:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found = zxingcpp.read_barcodes(gray, formats=zxingcpp.BarcodeFormat.QRCode)
            return next((r.text for r in found if r.text), "")
        return read
    except ImportError:
        detector = cv2.QRCodeDetector()
        return lambda frame: detector.detectAndDecode(frame)[0]


def scan_qr(perception: FacePerception, stop: threading.Event) -> None:
    read = _qr_reader()
    last, last_t = None, 0.0
    while not stop.wait(QR_PERIOD_S):
        frame = perception.snapshot().frame
        if frame is None:
            continue
        try:
            text = read(frame)
        except Exception:                           # noqa: BLE001 - a frame that cannot be scanned
            continue
        now = time.monotonic()
        if text and (text != last or now - last_t > QR_REPEAT_S):
            last, last_t = text, now
            print(json.dumps({"event": "qr", "text": text[:512]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Face bridge: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        raise
