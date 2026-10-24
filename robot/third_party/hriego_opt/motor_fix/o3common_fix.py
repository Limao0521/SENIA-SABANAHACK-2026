"""Shared by brain (host) and motor (container): wire framing + turn timing helpers. Pure python/numpy.
Frame = <B type><I len><payload>.  type 1: JSON control; type 2: tick chunk <I turn><I k0><I n><d t_send_mono> + n*19 float32
(arms14, waist3, head2 = SONIC *actual* joints, one row per 50 Hz tick).  motor->brain: type 1 JSON events.
The host and the container share CLOCK_MONOTONIC (same kernel, no time namespace), so all stamps are absolute monotonic seconds."""
import json, struct
import numpy as np

SOCK = "/tmp/.X11-unix/o3_motor.sock"          # host path == container path (bind mount of /tmp/.X11-unix; the twin container has network=none)
TICK = 0.02
L_OUT, L_IN = 0.6, 0.7                         # c12 V2 fade-out / fade-in


def send_json(sock, obj):
    b = json.dumps(obj).encode(); sock.sendall(struct.pack("<BI", 1, len(b)) + b)


def send_chunk(sock, turn, k0, arr, t_send):
    a = np.ascontiguousarray(arr, np.float32); p = struct.pack("<IIId", turn, k0, len(a), t_send) + a.tobytes()
    sock.sendall(struct.pack("<BI", 2, len(p)) + p)


def _rx(sock, n):
    b = bytearray()
    while len(b) < n:
        c = sock.recv(n - len(b))
        if not c: raise ConnectionError("closed")
        b += c
    return bytes(b)


def recv_frame(sock):
    t, n = struct.unpack("<BI", _rx(sock, 5)); p = _rx(sock, n)
    if t == 1: return 1, json.loads(p)
    turn, k0, m, ts = struct.unpack("<IIId", p[:20]); return 2, (turn, k0, np.frombuffer(p[20:], np.float32).reshape(m, 19), ts)


def preset_policy(T):
    """c12 dispatch policy: stroke at anchor, else mid, else onset, all limited by the initial-delay cap. -> (name, D_a)"""
    cap = T["max_initial_delay_s"]
    for name in ("stroke", "mid", "onset"):
        d = T["policy_dispatch_s"][name]
        if d >= -cap: return name, d
    return "onset", T["policy_dispatch_s"]["onset"]


def start_offset(prefix_n, preset=None):
    """seconds, relative to audio sample 0, at which the motor must start playing tick 0 (<= -prefix_n*TICK; earlier for a preset whose fade starts before audio)."""
    off = -prefix_n * TICK
    if preset is not None:
        _, D_a = preset_policy(preset); off = min(off, (D_a - L_OUT - 0.3) - 0.1)
    return off
