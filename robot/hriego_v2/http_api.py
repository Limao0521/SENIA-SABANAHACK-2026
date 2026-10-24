from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class HriegoHTTPServer:
    """Loopback-only HTTP/SSE surface; the companion LLM /chat is untouched."""

    def __init__(self, coordinator, event_bus, host: str = "127.0.0.1", port: int = 8765) -> None:
        self.coordinator = coordinator
        self.event_bus = event_bus
        self.server = ThreadingHTTPServer((host, port), self._handler_type())
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name="hriego-http", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        if self.thread.is_alive():
            self.server.shutdown()
        self.server.server_close()
        if self.thread.is_alive():
            self.thread.join(timeout=2.0)

    def _handler_type(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "HRIEGO/0.1"

            def log_message(self, fmt, *args):
                # No request bodies, profile names or query strings in console logs.
                return

            def do_GET(self):
                if self.path == "/health":
                    self._json(200, {"ok": True, "service": "hriego"})
                elif self.path == "/ready":
                    status = owner.coordinator.status()
                    ready = bool(status.get("camera", {}).get("status") == "CONNECTED")
                    self._json(200 if ready else 503, {"ready": ready, "state": status.get("state")})
                elif self.path == "/state":
                    self._json(200, owner.coordinator.status())
                elif self.path == "/events":
                    self._sse()
                else:
                    self._json(404, {"error": "not_found"})

            def do_POST(self):
                if self.path == "/hriego":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        self._json(400, {"error": "invalid_content_length"})
                        return
                    if length < 0 or length > 2048:
                        self._json(413, {"error": "body_too_large"})
                        return
                    try:
                        payload = json.loads(self.rfile.read(length) or b"{}")
                        if not isinstance(payload, dict):
                            raise ValueError("body must be a JSON object")
                        allowed = {"scenario_id"}
                        if set(payload) - allowed:
                            raise ValueError("only scenario_id may be supplied; identity comes from the camera")
                        scenario_id = payload.get("scenario_id")
                        if scenario_id is not None and not isinstance(scenario_id, str):
                            raise ValueError("scenario_id must be a string")
                        code, body = owner.coordinator.start_manual(
                            scenario_id or owner.coordinator.settings.default_scenario_id,
                            idempotency_key=self.headers.get("Idempotency-Key"),
                        )
                        self._json(code, body)
                    except (json.JSONDecodeError, ValueError) as exc:
                        self._json(400, {"error": "invalid_request", "detail": str(exc)})
                    except Exception as exc:
                        self._json(500, {"error": type(exc).__name__})
                elif self.path == "/cancel":
                    cancelled = owner.coordinator.cancel("api")
                    self._json(202 if cancelled else 409, {"cancelled": cancelled})
                else:
                    self._json(404, {"error": "not_found"})

            def _json(self, status: int, value: dict[str, Any]) -> None:
                content = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)

            def _sse(self) -> None:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                sequence = 0
                try:
                    while True:
                        events = owner.event_bus.after(sequence, timeout=1.0)
                        if not events:
                            self.wfile.write(b": heartbeat\n\n")
                            self.wfile.flush()
                            continue
                        for event in events:
                            sequence = event["sequence"]
                            payload = owner.event_bus.encode(event)
                            self.wfile.write(f"id: {sequence}\nevent: {event['event']}\ndata: {payload}\n\n".encode("utf-8"))
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    return

        return Handler
