"""Hold-last-frame, rampa de entrada/salida de SONIC (AimdkBackend DRY_RUN)."""
from __future__ import annotations

import numpy as np

from x2_ultra.hri_runtime.backends import aimdk as AIM
from tests.test_backends import FakeStore, write_emote_csv

HZ = 50
TGT = AIM.READY_ARMS + 0.5


def _be(tmp_path):
    csv = tmp_path / "e.csv"
    write_emote_csv(csv)
    return AIM.AimdkBackend(AIM.AimdkConfig(), store=FakeStore(csv), pump=False)


class Sim:
    def __init__(self, be):
        self.be, self.t, self.clip, self.cmd = be, 1000.0, [], []

    def tick(self, feed=True):
        if feed:
            self.be.stream_upper_body(self.t, TGT, np.zeros(3), np.zeros(2))
        n0 = self.be.limiter.nlim
        self.be.pump_once(self.t)
        self.clip.append(self.be.limiter.nlim > n0)
        self.cmd.append(self.be._last_cmd_arms.copy())
        self.t += 1.0 / HZ


def test_no_ready_spikes_with_jitter(tmp_path):
    be = _be(tmp_path); s = Sim(be)
    rng = np.random.default_rng(0)
    for _ in range(150):
        s.tick(feed=bool(rng.random() > 0.3))      # 30 % de cuadros perdidos (huecos de 1-2 ticks)
    mid = s.cmd[75:]
    d = [np.abs(c - AIM.READY_ARMS).max() for c in mid]
    assert min(d) > 0.3, "READY publicado a mitad de stream"
    be.close()


def test_entry_ramp_from_ready(tmp_path):
    be = _be(tmp_path); s = Sim(be)
    for _ in range(20):
        s.tick(feed=False)
    s.clip.clear()
    for _ in range(HZ):
        s.tick()
    assert 100.0 * np.mean(s.clip) < 5.0
    be.close()


def _emote_then_sonic(tmp_path, continuous):
    be = _be(tmp_path); s = Sim(be)
    for _ in range(100):
        s.tick()
    be.play_emote(3001, 11, t_dispatch=s.t + 0.5)
    while be._track is not None:
        s.tick(feed=continuous)
    s.clip.clear()
    for _ in range(int(2.5 * HZ)):
        s.tick()
    be.close()
    return 100.0 * np.mean(s.clip)


def test_emote_then_sonic_continuous(tmp_path):
    assert _emote_then_sonic(tmp_path, True) < 5.0


def test_emote_then_sonic_feed_after(tmp_path):
    assert _emote_then_sonic(tmp_path, False) < 5.0


def test_stream_stop_ramps_to_ready(tmp_path):
    be = _be(tmp_path); s = Sim(be)
    for _ in range(100):
        s.tick()
    s.clip.clear(); s.cmd.clear()
    for _ in range(int(2.0 * HZ)):
        s.tick(feed=False)
    assert not any(s.clip)
    assert np.abs(s.cmd[-1] - AIM.READY_ARMS).max() < 0.02
    steps = [np.abs(b - a).max() for a, b in zip(s.cmd, s.cmd[1:])]
    assert max(steps) < 0.1                         # sin salto
    be.close()
