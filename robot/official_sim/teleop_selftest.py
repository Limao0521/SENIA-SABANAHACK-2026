#!/usr/bin/env python3
"""Autoprueba del camino de keyboard_teleop.py sin teclado físico (simulador oficial, dentro del contenedor).

Usa la misma clase ``Teleop``: registra la fuente ``sdk_x2_ultra_keyboard``, simula "mantener W" (o la tecla que
se pida) durante N segundos publicando a 50 Hz, suelta, y mide odometría y articulaciones de pierna. Con el pórtico
puesto la base no avanza, pero las piernas deben moverse (el MC intenta caminar).

    official_sim/sim.sh shell
    python3 /workspace/x2_ultra/official_sim/teleop_selftest.py --keys w --seconds 3
"""
import argparse
import json
import math
import time

import rclpy
from rclpy.qos import qos_profile_sensor_data
from aimdk_msgs.msg import JointStateArray
from nav_msgs.msg import Odometry

from keyboard_teleop import Teleop, require_isolated_sim


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keys", default="w", help="teclas mantenidas, p. ej. 'w' o 'wq'")
    ap.add_argument("--seconds", type=float, default=3.0)
    args = ap.parse_args()
    require_isolated_sim()
    rclpy.init()
    node = Teleop({"vx": 0.4, "vx_back": 0.3, "vy": 0.5, "wz": 0.4, "run": 1.8})
    state = {"odom": None, "legs": None, "n_odom": 0, "n_legs": 0}

    def on_odom(msg):
        p = msg.pose.pose.position
        state["odom"] = (p.x, p.y)
        state["n_odom"] += 1

    def on_legs(msg):
        state["legs"] = [j.position for j in msg.joints]
        state["n_legs"] += 1

    node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
    node.create_subscription(JointStateArray, "/aima/hal/joint/leg/state", on_legs, qos_profile_sensor_data)
    t_end = time.monotonic() + 5.0
    while (state["odom"] is None or state["legs"] is None) and time.monotonic() < t_end:
        rclpy.spin_once(node, timeout_sec=0.05)
    registered = node.register()
    node.poll_mode()
    start_odom, start_legs = state["odom"], state["legs"]
    leg_span = [0.0] * 12
    node.set_from_keys(set(args.keys))
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        node.publish()
        rclpy.spin_once(node, timeout_sec=0.01)
        if state["legs"] and start_legs:
            leg_span = [max(s, abs(a - b)) for s, a, b in zip(leg_span, state["legs"], start_legs)]
        time.sleep(0.02)
    node.set_from_keys(set())
    for _ in range(25):
        node.publish()
        rclpy.spin_once(node, timeout_sec=0.01)
        time.sleep(0.02)
    end_odom = state["odom"]
    node.unregister()
    disp = math.dist(start_odom, end_odom) if start_odom and end_odom else None
    print(json.dumps({"registered": registered, "mode": node.mode, "keys": args.keys, "seconds": args.seconds,
                      "odom_samples": state["n_odom"], "leg_samples": state["n_legs"],
                      "displacement_m": None if disp is None else round(disp, 4),
                      "max_leg_motion_rad": round(max(leg_span), 4)}, ensure_ascii=False))
    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
