"""Protocolo IPC del servidor de movimiento online: UNIX socket, JSON-lines + frames float32.

Cada mensaje es una línea UTF-8 terminada en ``\\n`` (JSON). Los frames de movimiento y el
audio viajan como float32 binario codificado en base64 dentro del JSON (campos ``b64``/``n``),
de modo que los valores float32 llegan exactos y el parsing es trivial y robusto.

Cliente → servidor:
  {"cmd":"start_turn","sr":16000,"t_audio0":0.0,"audio_rtf":0.0,"split":14,"n":N,"b64":...}
Servidor → cliente:
  {"cmd":"ready","load_s":...}                                  al conectar
  {"cmd":"frames","k0":K,"n":M,"t_mono":...,"b64":...}          M×19 float32 (arms14,waist3,head2)
  {"cmd":"done","res":{...},"mem":{"pss_mb":...,"vram_mb":...}} fin de turno
  {"cmd":"error","msg":...}
"""
from __future__ import annotations

import base64
import json

N_COLS = 19          # arms14 + waist3 + head2


def encode_frames(arr) -> str:
    """(n,19) float32 → base64 de bytes little-endian."""
    import numpy as np
    a = np.ascontiguousarray(arr, dtype="<f4")
    return base64.b64encode(a.tobytes()).decode("ascii")


def decode_frames(b64: str, n: int):
    """base64 → (n,19) float32."""
    import numpy as np
    raw = base64.b64decode(b64)
    return np.frombuffer(raw, dtype="<f4").reshape(n, N_COLS)


def encode_audio(pcm) -> tuple[str, int]:
    import numpy as np
    a = np.ascontiguousarray(pcm, dtype="<f4").reshape(-1)
    return base64.b64encode(a.tobytes()).decode("ascii"), int(a.size)


def decode_audio(b64: str, n: int):
    import numpy as np
    return np.frombuffer(base64.b64decode(b64), dtype="<f4").reshape(-1)[:n].copy()


def send_line(sock, obj: dict) -> None:
    sock.sendall((json.dumps(obj, default=float) + "\n").encode("utf-8"))


def recv_lines(buf: bytearray, sock, timeout: float | None = None):
    """Generador: recibe líneas completas desde ``sock``, acumulando en ``buf`` (in/out)."""
    if timeout is not None:
        sock.settimeout(timeout)
    while True:
        i = buf.find(b"\n")
        if i >= 0:
            line = bytes(buf[:i])
            del buf[:i + 1]
            if line.strip():
                yield json.loads(line.decode("utf-8"))
            continue
        chunk = sock.recv(1 << 20)
        if not chunk:
            raise ConnectionError("el servidor cerró la conexión")
        buf.extend(chunk)
