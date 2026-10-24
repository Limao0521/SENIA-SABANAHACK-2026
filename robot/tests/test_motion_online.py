"""T4 — movimiento co-speech online: tests ligeros (sin MuJoCo, sin servidor real).

Cubren: ``JointLimiter`` (10 rad/s + 40 rad/s² de la spec), ``postprocess`` (recortes
prep_arms, k=0.7 del commit 65de416, bounds de waist ±0.10/0.05/0.03), el protocolo
JSON-lines + float32 (ida y vuelta exacta), la conversión de PCM del cliente y el
iterador ``MotionServer.start_turn`` contra un servidor falso en un UNIX socket.
"""
import json
import socket
import threading
from pathlib import Path

import numpy as np
import pytest

from x2_ultra.hri_runtime.motion_online import limits as L
from x2_ultra.hri_runtime.motion_online import protocol as P
from x2_ultra.hri_runtime.motion_online.client import PREFIX_N, MotionServer, _materialize


# --------------------------------------------------------------------------- limiter
def test_joint_limiter_velocity_cap():
    lim = L.JointLimiter(2, np.zeros(2), vmax=10.0, amax=1e9, dt=0.02)
    q = lim.step(np.full(2, 5.0))                      # paso enorme → limitado a vmax*dt
    assert np.allclose(q, 10.0 * 0.02)
    v = np.diff([np.zeros(2), q, lim.step(np.full(2, 5.0))], axis=0) / 0.02
    assert np.all(np.abs(v) <= 10.0 + 1e-9)


def test_joint_limiter_accel_cap_and_convergence():
    lim = L.JointLimiter(1, np.zeros(1), vmax=10.0, amax=40.0, dt=0.02)
    qs = [float(lim.step(np.array([1.0]))[0]) for _ in range(200)]
    v = np.diff([0.0] + qs) / 0.02
    a = np.diff(v) / 0.02
    assert np.all(np.abs(v) <= 10.0 + 1e-6)            # velocidad de spec (no emote)
    assert np.all(np.abs(a) <= 40.0 + 1e-6)            # aceleración limitada
    assert qs[-1] == pytest.approx(1.0, abs=1e-6)      # converge al objetivo
    assert np.all(np.diff(qs) >= -1e-12)               # monótono (sin overshoot)


def test_joint_limiter_reset():
    lim = L.JointLimiter(1, np.zeros(1))
    lim.step(np.array([0.1]))
    lim.reset(np.array([0.5]))
    assert np.allclose(lim.q, 0.5) and np.allclose(lim.v, 0.0)


# --------------------------------------------------------------------------- post-proceso
def test_postprocess_waist_bounds_and_arm_amp():
    arms, waist, head = L.postprocess(np.full(14, 50.0), np.array([0.35, 0.15, 0.10]),
                                      np.array([9.0, -9.0]))
    assert np.all(arms <= L.ARM_HI + 1e-9) and np.all(arms >= L.ARM_LO - 1e-9)   # prep_arms
    assert np.allclose(np.abs(waist), [0.10, 0.05, 0.03])                         # 65de416
    assert np.allclose(np.abs(head), L.HEAD_LIMIT)
    a2, _, _ = L.postprocess(np.zeros(14), np.zeros(3), np.zeros(2), k=1.0)
    a3, _, _ = L.postprocess(np.zeros(14), np.zeros(3), np.zeros(2))              # k=0.7
    # cmd = REST + k·(cmd − REST): con cmd=0 → (1−k)·REST
    assert np.allclose(a3, L.REST_ARMS + L.SONIC_ARM_AMP_K * (a2 - L.REST_ARMS))
    assert L.SONIC_ARM_AMP_K == pytest.approx(0.7)


def test_limits_spec_constants():
    assert L.VEL_LIMIT == 10.0 and L.ACC_LIMIT == 40.0 and L.DT == 0.02
    assert L.SPEC_VEL_EMOTE["hip_roll"] == 40.0 and L.SPEC_VEL_EMOTE["head"] == 30.0
    assert len(L.ARM_JOINTS) == 14 and L.ARM_JOINTS[0] == "left_shoulder_pitch_joint"


# --------------------------------------------------------------------------- protocolo
def test_protocol_frames_exact_float32_roundtrip():
    rng = np.random.default_rng(0)
    arr = rng.standard_normal((7, 19)).astype(np.float32)
    out = P.decode_frames(P.encode_frames(arr), 7)
    assert out.dtype == np.float32 and np.array_equal(out, arr)      # exacto (float32 binario)


def test_protocol_audio_roundtrip():
    pcm = np.linspace(-1, 1, 16000, dtype=np.float32)
    b64, n = P.encode_audio(pcm)
    assert n == 16000 and np.array_equal(P.decode_audio(b64, n), pcm)


# --------------------------------------------------------------------------- cliente
def test_materialize_int16_chunks_and_resample():
    a = _materialize(np.array([0, 16384, -32768], np.int16), 16000)
    assert a.dtype == np.float32 and np.allclose(a, [0.0, 0.5, -1.0], atol=1e-4)
    chunks = [np.zeros(8000, np.float32), np.ones(8000, np.float32)]
    assert len(_materialize(iter(chunks), 16000)) == 16000
    pcm44 = np.zeros(44100, np.float32)
    assert abs(len(_materialize(pcm44, 44100)) - 16000) <= 1          # 1 s @44.1k → 16 kHz


class _FakeServer(threading.Thread):
    """Servidor mínimo que habla el protocolo: ready → frames → done."""
    daemon = True

    def __init__(self, path, n_frames=60):
        super().__init__()
        self.path, self.n, self.request = path, n_frames, None
        Path(path).unlink(missing_ok=True)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(1)

    def run(self):
        conn, _ = self.srv.accept()
        P.send_line(conn, {"cmd": "ready"})
        buf = bytearray()
        for msg in P.recv_lines(buf, conn):
            self.request = msg
            rng = np.random.default_rng(1)
            arr = (0.01 * rng.standard_normal((self.n, 19))).astype(np.float32)
            P.send_line(conn, {"cmd": "frames", "k0": 0, "n": self.n,
                               "t_mono": 0.0, "b64": P.encode_frames(arr)})
            P.send_line(conn, {"cmd": "done", "res": {"first_cmd_s": 0.42,
                                                      "mem": {"pss_mb": 1, "vram_mb": 2}}})
            break
        conn.close()
        self.srv.close()


def test_start_turn_yields_50hz_timeline(tmp_path):
    sock = str(tmp_path / "fake.sock")
    fake = _FakeServer(sock, n_frames=60)
    fake.start()
    ms = MotionServer(sock_path=sock, autostart=False)
    out = list(ms.start_turn(np.zeros(16000, np.float32), 16000, t_audio0=10.0))
    fake.join(timeout=5)
    assert len(out) == 60
    t0 = out[0][0]
    assert t0 == pytest.approx(10.0 - PREFIX_N * 0.02)               # prefijo antes del audio
    assert out[-1][0] == pytest.approx(t0 + 59 * 0.02)
    for t, arms, waist, head in out:
        assert arms.shape == (14,) and waist.shape == (3,) and head.shape == (2,)
        assert np.all(np.abs(waist) <= L.WAIST_BOUNDS_ONLINE + 1e-9)  # bounds aplicados
    assert ms.last_res["first_cmd_s"] == 0.42 and ms.last_mem["vram_mb"] == 2
    assert fake.request["cmd"] == "start_turn" and fake.request["n"] == 16000


# --------------------------------------------------------------------------- engine (hook T4)
def test_engine_spec_vel_backstop():
    pytest.importorskip("mujoco")
    from x2_ultra.show.engine import (SPEC_VEL_ARMS, SPEC_VEL_HEAD, SPEC_VEL_JOINT,
                                      SPEC_VEL_WAIST, spec_vel_clip)
    assert SPEC_VEL_JOINT == 10.0
    assert SPEC_VEL_ARMS.shape == (14,) and SPEC_VEL_WAIST.shape == (3,) and SPEC_VEL_HEAD.shape == (2,)
    old = np.zeros(14)
    new = spec_vel_clip(old, np.full(14, 10.0), SPEC_VEL_ARMS, 0.002)   # 1 tick de 2 ms
    assert np.allclose(new, 10.0 * 0.002)                              # ≤ 10 rad/s por articulación
    assert np.allclose(spec_vel_clip(old, np.full(14, 0.01), SPEC_VEL_ARMS, 0.002), 0.01)
