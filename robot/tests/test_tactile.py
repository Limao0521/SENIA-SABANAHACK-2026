from types import SimpleNamespace as NS

from x2_ultra.robot.omnihand import TACTILE_FIELDS, TACTILE_REGIONS
from x2_ultra.robot.tactile import decode


def _touch(value):
    return NS(**{f: [value] * size for f, (_, size) in zip(TACTILE_FIELDS, TACTILE_REGIONS)})


def test_decode_regions_and_unavailable_hand():
    msg = NS(header=NS(sequence=7, stamp=NS(sec=1, nanosec=5)),
             left_hand_type=NS(value=1), left_touch_sensors=_touch(9),
             right_hand_type=NS(value=0), right_touch_sensors=_touch(3))
    f = decode(msg)
    assert f.sequence == 7 and f.stamp_ns == 1_000_000_005
    assert f.left.available and f.left.regions["palm"].size == 25 and f.left.regions["thumb"].size == 16
    assert f.left.summary()["index"] == 9
    assert not f.right.available and f.right.regions == {}     # tipo 0: no se reportan ceros falsos
