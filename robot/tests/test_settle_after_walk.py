"""Tras walk_to: sin saltos del limitador ni de brazos (estado SONIC rancio descartado)."""
from __future__ import annotations

import numpy as np
import pytest

from x2_ultra.hri_runtime.backends import aimdk as AIM
from tests.test_backends import FakeStore, write_emote_csv

HZ = 50
TGT = AIM.READY_ARMS + 0.5


def _be(tmp_path):
    csv = tmp_path / "e.csv"
    write_emote_csv(csv)
    cfg = AIM.AimdkConfig(routes={"A": [(0.3, 0.0, 0.5), (0.0, 0.4, 0.5)]})
    return AIM.AimdkBackend(cfg, store=FakeStore(csv), pump=False)


@pytest.mark.parametrize("resume", [False, True])
def test_settle_after_walk(tmp_path, resume):
    be = _be(tmp_path)
    t = 1000.0
    for _ in range(100):
        be.stream_upper_body(t, TGT, np.zeros(3), np.zeros(2)); be.pump_once(t); t += 1 / HZ
    be.walk_to("A")
    be._walk = [(vx, wz, t + (e - be._walk[0][2]) + 1.0) for vx, wz, e in be._walk]
    end = be._walk[-1][2]
    while be.walking():
        be.point("left", t + 0.2)                      # refused
        be.stream_upper_body(t, TGT, np.zeros(3), np.zeros(2))   # refused
        be.pump_once(t); t += 1 / HZ
        if t > end + 0.1:
            break
    assert not be.walking() and be._last_item is None
    clip, steps, prev = [], [], be._last_cmd_arms.copy()
    for i in range(100):
        if resume:
            be.stream_upper_body(t, TGT, np.zeros(3), np.zeros(2))
        n0 = be.limiter.nlim
        be.pump_once(t); t += 1 / HZ
        clip.append(be.limiter.nlim > n0)
        steps.append(np.abs(be._last_cmd_arms - prev).max()); prev = be._last_cmd_arms.copy()
    assert 100.0 * np.mean(clip[:65]) < 5.0
    assert max(steps) <= 0.1
    be.close()
