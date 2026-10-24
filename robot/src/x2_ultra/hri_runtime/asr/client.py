"""Cliente del worker de ASR (stdlib + numpy): lanza ``server_proc.py`` en el venv
``.venv-asr`` si no está vivo y transcribe por UNIX socket (JSON-lines)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

DEFAULT_SOCK = "/tmp/hri_asr.sock"
REPO = Path(__file__).resolve().parents[3]
ASR_PYTHON = os.environ.get("ASR_PYTHON", str(REPO / ".venv-asr/bin/python"))


class AsrClient:
    """Misma forma que ``Asr.transcribe`` pero en el proceso/venv del modelo."""

    def __init__(self, sock_path: str = DEFAULT_SOCK, autostart: bool = True,
                 python: str = ASR_PYTHON, ready_timeout: float = 120.0, warm: bool = True):
        self.sock_path = str(sock_path)
        self.python = python
        self.ready_timeout = ready_timeout
        self.proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._buf = b""
        if autostart and not self.server_alive():
            self.start_server(warm=warm)
        if self._sock is None:
            self._connect(ready_timeout)

    def server_alive(self) -> bool:
        if not os.path.exists(self.sock_path):
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(self.sock_path)
            s.close()
            return True
        except OSError:
            return False

    def start_server(self, warm: bool = True) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        env["PYTHONPATH"] = str(REPO / "src") + os.pathsep + env.get("PYTHONPATH", "")
        log_dir = REPO / "out_diag/asr"
        log_dir.mkdir(parents=True, exist_ok=True)
        args = [self.python, "-m", "x2_ultra.hri_runtime.asr.server_proc", "--sock", self.sock_path]
        if warm:
            args.append("--warm")
        self.proc = subprocess.Popen(args, env=env, cwd=str(REPO), start_new_session=True,
                                     stdout=open(log_dir / "server.log", "ab"),
                                     stderr=subprocess.STDOUT)
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < self.ready_timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"asr server murió (código {self.proc.returncode}); ver out_diag/asr/server.log")
            if self.server_alive():
                self._connect(self.ready_timeout)
                return
            time.sleep(0.3)
        raise TimeoutError("asr server no listo a tiempo")

    def _connect(self, timeout: float) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(self.sock_path)
        self._sock, self._buf = s, b""

    def _rpc(self, msg: dict) -> dict:
        assert self._sock is not None
        self._sock.sendall(json.dumps(msg, ensure_ascii=False).encode() + b"\n")
        while True:
            i = self._buf.find(b"\n")
            if i >= 0:
                line, self._buf = self._buf[:i], self._buf[i + 1:]
                return json.loads(line)
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError("asr server cerró la conexión")
            self._buf += chunk

    def transcribe(self, wav_path_or_pcm, sr: int = 16000) -> dict:
        if isinstance(wav_path_or_pcm, (str, os.PathLike)):
            return self._rpc({"cmd": "transcribe", "path": str(wav_path_or_pcm)})
        import base64
        import numpy as np
        pcm = np.ascontiguousarray(np.asarray(wav_path_or_pcm, dtype=np.float32).reshape(-1))
        return self._rpc({"cmd": "transcribe", "sr": int(sr),
                          "pcm_b64": base64.b64encode(pcm.tobytes()).decode()})

    def stop(self, kill_server: bool = False) -> None:
        if kill_server and self._sock is not None:
            try:
                self._rpc({"cmd": "stop"})
            except (OSError, ConnectionError):
                pass
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
