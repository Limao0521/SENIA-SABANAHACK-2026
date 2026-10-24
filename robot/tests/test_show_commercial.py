"""The bundled commercial replay pack is complete and fits the Sabana stage."""
from types import SimpleNamespace

import numpy as np

from x2_ultra.robot.joints import ARM_JOINTS, DEFAULT_POSE
from x2_ultra.show.app import ShowWindow
from x2_ultra.show.commercial import CommercialShow


def test_commercial_pack_replays_both_stage_robots():
    show = CommercialShow()
    assert len(show.lines) == 19
    assert {line.robot for line in show.lines} == {"ultra", "edu"}
    assert all(line.audio.is_file() and np.isfinite(line.arms).all() for line in show.lines)
    assert all(a.end < b.start for a, b in zip(show.lines, show.lines[1:]))
    assert show.lines[-1].end < show.duration
    assert [line.number for line in show.due(show.duration)] == list(range(1, 20))
    assert show.due(show.duration) == []
    assert {key: value.shape for key, value in show.targets(75).items()} == {
        "ultra": (14,), "edu": (14,)
    }


def test_only_speaking_robot_moves_arms_and_show_finishes_on_last_line():
    show = CommercialShow()
    rest = np.array([DEFAULT_POSE[name] for name in ARM_JOINTS])
    first, second = show.lines[:2]
    for moment in (0, first.end + 0.1, show.lines[-1].end + 0.1):
        assert all(np.array_equal(target, rest) for target in show.targets(moment).values())
    active = show.targets(first.start + 1.0)
    assert np.array_equal(active["ultra"], first.arms[50])
    assert np.array_equal(active["edu"], rest)
    active = show.targets(second.start + 1.0)
    assert np.array_equal(active["ultra"], rest)
    assert np.array_equal(active["edu"], second.arms[50])
    assert not show.finished(show.lines[-1].end - 0.01)
    assert show.finished(show.lines[-1].end)


def test_farewell_runs_once_after_both_dances_and_returns_to_rest():
    poses, audio = [], []
    rigs = {key: SimpleNamespace(dance=object()) for key in ("ultra", "edu")}
    fake = SimpleNamespace(
        engine=SimpleNamespace(rigs=rigs, play_pose=lambda key, pose: poses.append((key, pose))),
        _commercial_outro_pending=True, _commercial_outro_until=0.0,
        _commercial_outro_line=CommercialShow().lines[17],
        _play_commercial_audio=lambda path: audio.append(path),
        viewport=SimpleNamespace(subtitle=""), flash=lambda *args: None,
    )
    ShowWindow._poll_commercial_outro(fake)
    assert poses == [] and audio == []
    for rig in rigs.values():
        rig.dance = None
    ShowWindow._poll_commercial_outro(fake)
    assert poses == [("ultra", "saludar"), ("edu", "saludar")]
    assert len(audio) == 1 and audio[0].name == "018_ULTRA_despedida.wav"
    assert "Muchas gracias" in fake.viewport.subtitle
    ShowWindow._poll_commercial_outro(fake)
    assert len(audio) == 1
    fake._commercial_outro_until = 1.0
    ShowWindow._poll_commercial_outro(fake)
    assert poses[-2:] == [("ultra", "reposo"), ("edu", "reposo")]
