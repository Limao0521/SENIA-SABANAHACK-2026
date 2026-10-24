"""Lógica de official_sim/keyboard_teleop.py sin ROS: se sustituyen rclpy y aimdk_msgs por módulos falsos."""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def teleop_module(monkeypatch):
    rclpy = types.ModuleType("rclpy")
    node_mod = types.ModuleType("rclpy.node")

    class Node:
        def __init__(self, name):
            self.published = []

        def create_publisher(self, *a, **k):
            node = self
            return types.SimpleNamespace(publish=lambda msg: node.published.append(msg))

        def create_client(self, *a, **k):
            return types.SimpleNamespace(wait_for_service=lambda timeout_sec=0: False)

        def get_clock(self):
            return types.SimpleNamespace(now=lambda: types.SimpleNamespace(to_msg=lambda: 0))

    node_mod.Node = Node
    rclpy.node = node_mod
    msg = types.ModuleType("aimdk_msgs.msg")
    srv = types.ModuleType("aimdk_msgs.srv")

    class Msg:
        def __init__(self, *a, **k):
            pass

    for name in ("McActionCommand", "McInputAction", "McLocomotionVelocity", "MessageHeader", "RequestHeader"):
        setattr(msg, name, type(name, (Msg,), {}))
    for name in ("GetMcAction", "SetMcAction", "SetMcInputSource"):
        setattr(srv, name, type(name, (Msg,), {"Request": Msg}))
    pkg = types.ModuleType("aimdk_msgs")
    pkg.msg, pkg.srv = msg, srv
    for name, mod in (("rclpy", rclpy), ("rclpy.node", node_mod), ("aimdk_msgs", pkg),
                      ("aimdk_msgs.msg", msg), ("aimdk_msgs.srv", srv)):
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("keyboard_teleop", ROOT / "official_sim" / "keyboard_teleop.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_minimum_start_thresholds(teleop_module):
    w = teleop_module._with_min
    assert w(0.0, 0.2) == 0.0
    assert w(0.05, 0.2) == 0.2 and w(-0.05, 0.2) == -0.2      # nunca por debajo del umbral de arranque del MC
    assert w(0.6, 0.2) == 0.6


def test_keys_to_velocity_and_publish(teleop_module):
    node = teleop_module.Teleop({"vx": 0.4, "vx_back": 0.3, "vy": 0.5, "wz": 0.4, "run": 1.8})
    node.set_from_keys({"w", "q"})
    assert node.cmd == (0.4, 0.0, 0.4)
    node.set_from_keys({"s", "d", "shift"})
    vx, vy, wz = node.cmd
    assert vx == pytest.approx(-0.54) and vy == pytest.approx(-0.9) and wz == 0.0
    node.set_from_keys({"w", "shift"})
    assert node.cmd[0] == pytest.approx(0.72)
    node.set_from_keys(set())
    node.publish()
    sent = node.published[-1]
    assert sent.source.startswith("sdk_")                      # convención de AimDK para fuentes propias
    assert (sent.forward_velocity, sent.lateral_velocity, sent.angular_velocity) == (0.0, 0.0, 0.0)


def test_refuses_outside_isolated_container(teleop_module, monkeypatch):
    monkeypatch.delenv("X2_SIM_ONLY", raising=False)
    with pytest.raises(SystemExit):
        teleop_module.require_isolated_sim()
