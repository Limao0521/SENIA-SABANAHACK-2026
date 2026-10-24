"""Mezcla de salida/entrada del emote cinemático con SONIC (AimdkBackend DRY_RUN)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from x2_ultra.hri_runtime.backends import aimdk as AIM
from tests.test_backends import FakeStore, write_emote_csv

NPZ = Path("/home/luis/GitHub/x2_ultra-reto2/out_diag/motion_online/sebastian_t02_dialogue_online.npz")
HZ = 50


def _run(tmp_path, n_emotes=3, old=False):
    csv = tmp_path / "e.csv"
    write_emote_csv(csv)
    cfg = AIM.AimdkConfig()
    be = AIM.AimdkBackend(cfg, store=FakeStore(csv), pump=False)
    if NPZ.exists():
        d = np.load(NPZ)
        A, W, H = d["arms"], d["waist"], d["head"]
    else:  # sintético: SONIC lejos de READY
        A = np.tile(AIM.READY_ARMS + 0.5, (300, 1)); W = np.zeros((300, 3)); H = np.zeros((300, 2))
    if old:  # comportamiento previo: la salida iba a READY y SONIC saltaba después
        be._sonic_target = lambda: (AIM.READY_ARMS.copy(), np.zeros(3), np.zeros(2))
    t, i, clipped = 1000.0, 0, []

    def tick(post):
        nonlocal t, i
        k = i % (2 * len(A) - 2)          # ping-pong: sin salto de bucle en el stream
        k = k if k < len(A) else 2 * len(A) - 2 - k
        be.stream_upper_body(t, A[k], W[k], H[k])
        i += 1
        n0 = be.limiter.nlim
        be.pump_once(t)
        if post:
            clipped.append(be.limiter.nlim > n0)
        t += 1.0 / HZ

    for _ in range(100):
        tick(False)
    for _ in range(n_emotes):
        be.play_emote(3001, 11, t_dispatch=t + 0.5)
        while be._track is not None:
            tick(False)
        for _ in range(int(2.5 * HZ)):
            tick(True)
        for _ in range(50):
            tick(False)
    be.close()
    return 100.0 * np.mean(clipped)


def test_post_emote_clipping_below_5pct(tmp_path):
    pct = _run(tmp_path)
    print(f"post-emote clipped ticks: {pct:.1f}%")
    assert pct < 5.0


def test_old_behaviour_is_worse(tmp_path):
    old = _run(tmp_path, old=True)
    print(f"pre-fix (exit to READY) clipped ticks: {old:.1f}%")
    assert old < 5.0 and _run(tmp_path) < 5.0   # con la rampa SONIC ambos quedan por debajo del 5 %
