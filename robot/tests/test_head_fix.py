"""Head fix: turn_head sign (body_pose) and HAL head channel (dry-run)."""
import numpy as np
import pytest

from x2_ultra.hri_runtime.backends import aimdk as AIM


def _be(channel):
    return AIM.AimdkBackend(AIM.AimdkConfig(head_channel=channel), store=None, pump=False)


@pytest.mark.parametrize("yaw,sign", [(0.3, 1), (-0.3, -1), (0.0, 0)])
def test_body_pose_turn_head_sign(yaw, sign):
    be = _be("body_pose")
    try:
        us = []
        for _ in range(40):
            be._send_body_pose(np.array([0.0, 0.0, 0.0, yaw]))
            us.append(be._link.commands[-1]["u"][3])
        if sign == 0:
            assert max(abs(u) for u in us) < 1e-9
        else:
            assert us[0] * sign > 0 and any(u * sign > 0 for u in us)
            assert not any(u * sign < 0 for u in us[:5])
    finally:
        be.close()


def test_hal_channel_clips_and_zeroes_turn_head():
    be = _be("hal")
    try:
        be._head_tgt = np.array([2.0, -2.0])
        for _ in range(30):
            be._publish_head_hal()
        cmds = [c for c in be._link.commands if c["kind"] == "head_hal"]
        assert cmds[-1]["yaw"] == pytest.approx(AIM.HEAD_YAW_LIM)
        assert cmds[-1]["pitch"] == pytest.approx(AIM.HEAD_PITCH_LO)
        prev = 0.0
        for c in cmds:
            assert abs(c["yaw"] - prev) <= AIM.HEAD_RATE * 0.02 + 1e-9
            prev = c["yaw"]
        be._link.commands.clear()
        be._href2 = np.array([0.3, 0.1])
        be._q.append((0.0, np.zeros(14), np.zeros(3), np.array([0.3, 0.1]))) if hasattr(be, "_q") else None
        be.pump_once(1000.0)
        bp = [c for c in be._link.commands if c["kind"] == "body_pose"]
        assert bp and all(c["u"][3] == 0.0 for c in bp)
        assert any(c["kind"] == "head_hal" for c in be._link.commands)
    finally:
        be.close()
