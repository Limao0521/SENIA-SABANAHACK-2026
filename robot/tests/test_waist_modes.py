"""waist_mode: off (default) | slew | lowpass."""
import numpy as np

from x2_ultra.hri_runtime.backends import aimdk as AIM
from tests.test_backends import FakeStore, write_emote_csv

HZ = 50


def _be(mode, tmp_path=None, **kw):
    store = None
    if tmp_path is not None:
        csv = tmp_path / "e.csv"
        write_emote_csv(csv)
        store = FakeStore(csv)
    be = AIM.AimdkBackend(AIM.AimdkConfig(waist_mode=mode, **kw), store=store, pump=False)
    be.refs = []
    orig = be._send_body_pose
    def spy(ref):
        be.refs.append(np.array(ref, dtype=float))
        orig(ref)
    be._send_body_pose = spy
    return be


def _sine(be, f, amp=0.2, secs=8.0):
    t = 1000.0
    for i in range(int(secs * HZ)):
        w = amp * np.sin(2 * np.pi * f * i / HZ)
        be.stream_upper_body(t, AIM.READY_ARMS, np.array([w, 0.0, 0.0]), np.zeros(2))
        be.pump_once(t)
        t += 1 / HZ
    r = np.array(be.refs)[-int(4 * HZ):, 0]
    return (r.max() - r.min()) / 2


def test_lowpass_attenuates_5hz():
    be = _be("lowpass")
    try:
        assert _sine(be, 5.0) < 0.3 * 0.2
    finally:
        be.close()


def test_lowpass_passes_03hz():
    be = _be("lowpass")
    try:
        a = _sine(be, 0.3, amp=0.1)
        assert abs(a - 0.1) < 0.02
    finally:
        be.close()


def test_off_zero_waist_incl_emote(tmp_path):
    be = _be("off", tmp_path)
    try:
        _sine(be, 0.3, secs=2)
        be.play_emote(3001, 0, 1000.0 + 2.0)
        t = 1002.0
        for _ in range(200):
            be.pump_once(t); t += 1 / HZ
        assert all(np.all(r[:3] == 0.0) for r in be.refs)
        assert len(be.refs) > 200
    finally:
        be.close()


def test_off_is_default_and_slew_still_works():
    assert AIM.AimdkConfig().waist_mode == "off"
    be = _be("slew")
    try:
        a = _sine(be, 0.3, amp=0.1)
        assert a > 0.05
    finally:
        be.close()


def test_off_waist_zero_with_measured_offset_head_active():
    be = _be("off")
    try:
        sent = []
        be._link.wh_state = {"waist": [0.3, -0.2, 0.1]}
        be._link.send_body_pose = lambda u: sent.append(np.array(u, dtype=float))
        be._link.latest = lambda g, *a, **k: {AIM.J.HEAD_JOINTS[0]: 0.0}
        for _ in range(50):
            be._send_body_pose(np.array([0.5, 0.5, 0.5, 0.8]))
        assert all(np.all(u[:3] == 0.0) for u in sent)
        assert any(u[3] != 0.0 for u in sent)
        assert all(sum(d) == 0.0 for d in list(be._bp_inflight)[:3])
    finally:
        be.close()
