"""Worker de ASR en proceso propio (venv ``.venv-asr``, faster-whisper) — T6.

El app online corre en el venv del show (sin faster-whisper); este servidor expone
``Asr.transcribe`` por un UNIX socket con JSON-lines, igual que el servidor de
movimiento online (T4). Protocolo::

    → {"cmd": "transcribe", "path": "/ruta/wav"}   o {"cmd":"transcribe","pcm_b64":...,"sr":16000}
    ← {"ok": true, "text": ..., "latency_s": ...}
    → {"cmd": "stop"}

Arranque manual (el cliente AsrClient lo lanza solo)::

    .venv-asr/bin/python -m x2_ultra.hri_runtime.asr.server_proc --sock /tmp/hri_asr.sock
"""
from __future__ import annotations

import argparse
import base64
import json
import socketserver
import sys
import threading

DEFAULT_SOCK = "/tmp/hri_asr.sock"

_ASR = None
_LOCK = threading.Lock()


def _asr():
    global _ASR
    with _LOCK:
        if _ASR is None:
            from x2_ultra.hri_runtime.asr import Asr
            _ASR = Asr()
        return _ASR


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            cmd = msg.get("cmd")
            if cmd == "stop":
                self._send({"ok": True, "cmd": "stopped"})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if cmd == "ping":
                self._send({"ok": True, "cmd": "pong"})
                continue
            if cmd == "transcribe":
                try:
                    import numpy as np
                    if msg.get("path"):
                        out = _asr().transcribe(msg["path"])
                    else:
                        pcm = np.frombuffer(base64.b64decode(msg["pcm_b64"]), dtype=np.float32)
                        out = _asr().transcribe(pcm, int(msg.get("sr", 16000)))
                    out.pop("t_start", None)
                    out.pop("t_end", None)
                    self._send({"ok": True, **out})
                except Exception as exc:                            # pragma: no cover
                    self._send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                continue
            self._send({"ok": False, "error": f"cmd desconocido: {cmd!r}"})

    def _send(self, obj: dict) -> None:
        self.wfile.write(json.dumps(obj, ensure_ascii=False).encode() + b"\n")
        self.wfile.flush()


class _Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sock", default=DEFAULT_SOCK)
    ap.add_argument("--warm", action="store_true", help="carga el modelo antes de anunciar ready")
    a = ap.parse_args(argv)
    import os
    if os.path.exists(a.sock):
        os.unlink(a.sock)
    if a.warm:
        _asr()
    srv = _Server(a.sock, _Handler)
    print(f"asr server listo en {a.sock}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
