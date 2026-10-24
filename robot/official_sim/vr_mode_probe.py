#!/usr/bin/env python3
"""Bounded VR_REMOTE_CONTROLLER probe, only inside the networkless twin.

Run after ``official_sim/twin.sh start`` and ``stand``/``suelta``:

    docker exec -e ROS_DOMAIN_ID=77 x2-twin bash -lc \
      'source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; \
       python3 /workspace/x2_ultra/official_sim/vr_mode_probe.py --left-y 0.2'

The axis mapping to metres/second is unknown. A positive left Y input is a
candidate for forward motion, not a proven one. The result reports measured
odometry and the MC mode; it does not claim a passing locomotion profile.
"""
import argparse
import json
import math
import os
import subprocess
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.qos import qos_profile_sensor_data

from keyboard_teleop import Teleop, require_isolated_sim


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--left-y", type=float, default=0.2, help="signed left joystick Y, absolute value <= 0.4")
    ap.add_argument("--left-x", type=float, default=0.0, help="signed left joystick X, absolute value <= 0.4")
    ap.add_argument("--right-x", type=float, default=0.0, help="signed right joystick X, absolute value <= 0.4")
    ap.add_argument("--seconds", type=float, default=2.0, help="0.1 to 5 s")
    args = ap.parse_args()
    require_isolated_sim()
    if os.environ.get("ROS_DOMAIN_ID") != "77":
        ap.error("ROS_DOMAIN_ID must be 77 (direct MC domain)")
    if any(not math.isfinite(v) or abs(v) > 0.4 for v in (args.left_y, args.left_x, args.right_x)):
        ap.error("joystick axes must be finite and within [-0.4, 0.4]")
    if not math.isfinite(args.seconds) or not (0.1 <= args.seconds <= 5.0):
        ap.error("seconds must be between 0.1 and 5")

    rclpy.init()
    node = Teleop({"vx": 0, "vx_back": 0, "vy": 0, "wz": 0, "run": 1})
    odom: list[tuple[float, float]] = []
    node.create_subscription(Odometry, "/aima/hal/odom/state",
                             lambda m: odom.append((m.pose.pose.position.x, m.pose.pose.position.y)),
                             qos_profile_sensor_data)
    process = None
    registered = False
    result = {"requested_action": "VR_REMOTE_CONTROLLER", "input": vars(args)}
    try:
        registered = node.register()
        if not registered:
            raise RuntimeError("SetMcInputSource was not accepted by the MC")
        result["set_action_response"] = node.request_mode("VR_REMOTE_CONTROLLER")
        mode_deadline = time.monotonic() + 3.0
        while time.monotonic() < mode_deadline:
            node.poll_mode()
            if node.mode.startswith("VR_REMOTE_CONTROLLER"):
                break
            rclpy.spin_once(node, timeout_sec=0.05)
        result["mode_during_probe"] = node.mode
        if not node.mode.startswith("VR_REMOTE_CONTROLLER"):
            raise RuntimeError("MC did not enter VR_REMOTE_CONTROLLER")
        command = ["bash", "/workspace/x2_ultra/official_sim/vr_data_probe.sh",
                   "--forward", str(args.left_y), "--lateral", str(args.left_x),
                   "--yaw", str(args.right_x), "--seconds", str(args.seconds), "--lead", "4"]
        odom.clear()
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        while process.poll() is None:
            rclpy.spin_once(node, timeout_sec=0.05)
        out, err = process.communicate(timeout=3)
        result["publisher_exit_code"] = process.returncode
        result["publisher_stdout"] = out.strip()
        result["publisher_stderr"] = err.strip()
        result["odom_samples"] = len(odom)
        result["probe_displacement_m"] = round(math.dist(odom[0], odom[-1]), 4) if len(odom) > 1 else None
        node.poll_mode()
        result["mode_after_probe"] = node.mode
        return 0 if process.returncode == 0 and result["mode_after_probe"].startswith("VR_REMOTE_CONTROLLER") else 1
    except Exception as exc:
        result["error"] = str(exc)
        return 1
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        if registered:
            try:
                result["restore_response"] = node.request_mode("STAND_DEFAULT")
                node.unregister()
            except Exception as exc:
                result["restore_error"] = str(exc)
        node.destroy_node()
        rclpy.shutdown()
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
