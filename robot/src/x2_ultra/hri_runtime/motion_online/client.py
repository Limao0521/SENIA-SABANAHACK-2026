"""Cliente del servidor de movimiento online: ``MotionServer.start_turn`` para el show engine.

Interfaz de la spec (docs/reto-2/HRI_RUNTIME_SPEC.md, T4)::

    class MotionServer:
        def start_turn(self, wav_pcm_stream, sr, t_audio0) -> Iterator[(t, arms14, waist3, head2)]

Sin precomputación: el audio (PCM completo o por chunks, p. ej. stream de TTS) se envía al
proceso servidor (UNIX socket, JSON-lines + float32) y los frames 50 Hz van llegando —y se
yieldean— a medida que la cadena StreamTalk→GMR→SONIC los produce. Cada frame se post-procesa
aquí (recortes prep_arms, amplitud SONIC k=0.7, bounds de waist ±0.10/0.05/0.03 del commit
65de416) y se limita a 10 rad/s + 40 rad/s² por articulación (``JointLimiter``, no emotes).

Solo depende de numpy + stdlib: funciona en el venv del show/engine.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np

from . import protocol as P
from .limits import DT, JointLimiter, postprocess

PREFIX_N = 27            # ticks 50 Hz antes del inicio de audio (hold 2 + blend 0.5 s, prefix fast)
PHYS_PYTHON = os.environ.get("MOTION_PHYS_PYTHON",
                             str(Path.home() / "anaconda3/envs/physicalai/bin/python"))
DEFAULT_SOCK = "/tmp/hri_motion_online.sock"


def _materialize(pcm_stream, sr: int) -> np.ndarray:
    """PCM float32 mono a partir de un ndarray o un iterable de chunks (stream de TTS)."""
    if isinstance(pcm_stream, np.ndarray):
        a = pcm_stream
    elif isinstance(pcm_stream, (bytes, bytearray)):
        a = np.frombuffer(pcm_stream, dtype=np.int16)
    else:
        chunks = []
        for c in pcm_stream:                                     # generador/iterable de chunks
            chunks.append(np.frombuffer(c, dtype=np.int16) if isinstance(c, (bytes, bytearray))
                        else np.asarray(c))
        a = np.concatenate(chunks) if chunks else np.zeros(0)
    a = np.asarray(a).reshape(-1)
    if a.dtype == np.int16:
        a = a.astype(np.float32) / 32768.0
    if a.dtype == np.float64:
        a = a.astype(np.float32)
    if sr and int(sr) != 16000:                                  # remuestreo lineal a 16 kHz
        x0 = np.arange(len(a), dtype=np.float64) / float(sr)
        x1 = np.arange(0.0, len(a) / float(sr), 1.0 / 16000.0)
        a = np.interp(x1, x0, a).astype(np.float32)
    return np.ascontiguousarray(a, np.float32)


class MotionServer:
    """Cliente del proceso servidor; lo lanza si no está activo (``autostart``)."""

    def __init__(self, sock_path: str = DEFAULT_SOCK, autostart: bool = True,
                 python: str = PHYS_PYTHON, ready_timeout: float = 600.0,
                 apply_limits: bool = True, arm_amp_k: float | None = None):
        self.sock_path = str(sock_path)
        self.python = python
        self.ready_timeout = ready_timeout
        self.apply_limits = apply_limits
        self.arm_amp_k = arm_amp_k            # None → k de limits.SONIC_ARM_AMP_K (0.7)
        self.proc: subprocess.Popen | None = None
        self.last_res: dict | None = None     # métricas del último turno (del servidor)
        self.last_mem: dict | None = None
        self._sock: socket.socket | None = None
        self._buf = bytearray()
        if autostart and not self.server_alive():
            self.start_server()

    # ------------------------------------------------------------------ ciclo de vida
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

    def start_server(self) -> None:
        here = Path(__file__).resolve().parent
        env = dict(os.environ, PYTHONUNBUFFERED="1", MUJOCO_GL="egl",
                   OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", OPENBLAS_NUM_THREADS="2")
        env.pop("PYTHONPATH", None)
        log = open(here.parent.parent.parent.parent / "out_diag/motion_online/server.log", "ab") \
            if (here.parent.parent.parent.parent / "out_diag/motion_online").is_dir() \
            else subprocess.DEVNULL
        self.proc = subprocess.Popen([self.python, str(here / "server_proc.py"),
                                      "--sock", self.sock_path], env=env, stdout=log,
                                     stderr=subprocess.STDOUT, start_new_session=True)
        self.wait_ready()

    def wait_ready(self) -> None:
        """Espera a que el servidor acepte conexiones (carga de modelos: 1-4 min)."""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < self.ready_timeout:
            if self.proc is not None and self.proc.poll() is not None:
                raise RuntimeError(f"el servidor murió con código {self.proc.returncode} "
                                   f"(ver out_diag/motion_online/server.log)")
            if os.path.exists(self.sock_path):
                try:
                    self._connect()
                    return
                except OSError:
                    pass
            time.sleep(0.5)
        raise TimeoutError(f"servidor de movimiento no listo en {self.ready_timeout} s")

    def _connect(self) -> None:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.sock_path)
        self._sock, self._buf = s, bytearray()
        for msg in P.recv_lines(self._buf, s, timeout=self.ready_timeout):
            if msg["cmd"] == "ready":
                return
            if msg["cmd"] == "error":
                raise RuntimeError(msg["msg"])
        raise ConnectionError("sin ready del servidor")

    def stop(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None

    # ------------------------------------------------------------------ stream
    def start_turn(self, wav_pcm_stream, sr: int = 16000, t_audio0: float = 0.0,
                   audio_rtf: float = 0.0, split: int = 14
                   ) -> Iterator[tuple[float, np.ndarray, np.ndarray, np.ndarray]]:
        """Iterador 50 Hz de ``(t, arms14, waist3, head2)`` mientras se produce el turno.

        ``t`` es el reloj de audio: ``t_audio0 + (k - PREFIX_N)/50`` (los primeros ``PREFIX_N``
        ticks son el prefijo de entrada y tienen ``t < t_audio0``). ``audio_rtf=1.0`` simula un
        stream de audio en tiempo real (pacea las ventanas de StreamTalk); 0 = audio ya disponible.
        """
        audio = _materialize(wav_pcm_stream, sr)
        if self._sock is None:
            self._connect()
        b64, n = P.encode_audio(audio)
        P.send_line(self._sock, {"cmd": "start_turn", "sr": 16000, "t_audio0": float(t_audio0),
                                 "audio_rtf": float(audio_rtf), "split": int(split),
                                 "n": n, "b64": b64})
        lim_a = lim_w = lim_h = None
        first = True
        for msg in P.recv_lines(self._buf, self._sock, timeout=self.ready_timeout):
            cmd = msg["cmd"]
            if cmd == "frames":
                arr = P.decode_frames(msg["b64"], int(msg["n"]))
                k0 = int(msg["k0"])
                for i in range(arr.shape[0]):
                    k = k0 + i
                    t = t_audio0 + (k - PREFIX_N) * DT
                    arms, waist, head = postprocess(arr[i, :14], arr[i, 14:17], arr[i, 17:19],
                                                    **({} if self.arm_amp_k is None
                                                       else {"k": self.arm_amp_k}))
                    if self.apply_limits:
                        if first:
                            lim_a = JointLimiter(14, arms)
                            lim_w = JointLimiter(3, waist)
                            lim_h = JointLimiter(2, head)
                            first = False
                        arms, waist, head = lim_a.step(arms), lim_w.step(waist), lim_h.step(head)
                    yield t, arms, waist, head
            elif cmd == "done":
                self.last_res = msg.get("res")
                self.last_mem = (msg.get("res") or {}).get("mem")
                return
            elif cmd == "error":
                raise RuntimeError(f"servidor: {msg['msg']}")
        raise ConnectionError("el servidor cerró sin enviar done")


def _cli():                                                          # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("wav")
    ap.add_argument("--sock", default=DEFAULT_SOCK)
    ap.add_argument("--no-start", action="store_true")
    ap.add_argument("--audio-rtf", type=float, default=0.0)
    ap.add_argument("--npz", default="")
    a = ap.parse_args()
    import wave
    w = wave.open(a.wav)
    pcm = np.frombuffer(w.readframes(w.getnframes()),
                        np.int16 if w.getsampwidth() == 2 else np.uint8)
    if w.getsampwidth() != 2:
        pcm = (pcm.astype(np.int16) - 128) * 256
    if w.getnchannels() > 1:
        pcm = pcm.reshape(-1, w.getnchannels()).mean(1).astype(np.int16)
    ms = MotionServer(sock_path=a.sock, autostart=not a.no_start)
    rows = []
    for t, arms, waist, head in ms.start_turn(pcm, w.getframerate(), 0.0, a.audio_rtf):
        rows.append(np.concatenate([[t], arms, waist, head]))
    A = np.array(rows)
    print(f"{len(A)} frames, t {A[0,0]:.2f}..{A[-1,0]:.2f}, first_cmd_s "
          f"{ms.last_res['first_cmd_s']:.3f}, rtf {ms.last_res['rtf']:.2f}, mem {ms.last_mem}")
    if a.npz:
        np.savez(a.npz, t=A[:, 0], arms=A[:, 1:15], waist=A[:, 15:18], head=A[:, 18:20])
    ms.stop()


if __name__ == "__main__":                                           # pragma: no cover
    sys.exit(_cli())
