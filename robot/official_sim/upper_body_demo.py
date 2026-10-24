#!/usr/bin/env python3
"""Play an arm trajectory through MC's UPPERBODY_REMOTE_SPLIT interface (SIM ONLY).

Runs inside the x2-sim container (ROS 2 Humble + aimdk_msgs). Reads the measured
arms, ramps to the first frame over --ramp seconds, plays the file at its rate,
then holds the last frame until Ctrl+C. MC must already be in URS mode:
    robots/x2/sim.sh ex set_mc_action SD && robots/x2/sim.sh ex set_mc_action URS
"""
import argparse
import json
import time

import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from aimdk_msgs.msg import JointStateArray, MessageHeader, UpperBodyCommandArray

ARM_STATE = "/aima/hal/joint/arm/state"
UPPER_BODY = "/mc/upper_body_command"


class Demo(Node):
    def __init__(self, frames, rate, ramp_s):
        super().__init__("x2_upper_body_demo")
        self.frames, self.rate, self.ramp_s = frames, rate, ramp_s
        self.measured = None
        self.start = None
        self.seq = 0
        self.max_error = 0.0
        state_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                               durability=DurabilityPolicy.TRANSIENT_LOCAL)
        cmd_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                             durability=DurabilityPolicy.VOLATILE)
        self.create_subscription(JointStateArray, ARM_STATE, self.on_state, state_qos)
        self.pub = self.create_publisher(UpperBodyCommandArray, UPPER_BODY, cmd_qos)
        self.create_timer(1.0 / rate, self.tick)

    def on_state(self, msg):
        self.measured = np.array([j.position for j in msg.joints], dtype=float)

    def target(self, now):
        t = now - self.start
        if t < self.ramp_s:
            a = 0.5 - 0.5 * np.cos(np.pi * t / self.ramp_s)
            return self.ramp_from + a * (self.frames[0] - self.ramp_from), "ramp"
        i = int((t - self.ramp_s) * self.rate)
        return self.frames[min(i, len(self.frames) - 1)], "play" if i < len(self.frames) else "hold"

    def tick(self):
        if self.measured is None or self.measured.shape != (14,):
            return
        now = time.monotonic()
        if self.start is None:
            self.start, self.ramp_from = now, self.measured.copy()
            self.get_logger().info(f"measured arms {np.round(self.measured, 3).tolist()}")
        arm, phase = self.target(now)
        msg = UpperBodyCommandArray()
        stamp = self.get_clock().now().nanoseconds
        msg.header = MessageHeader()
        msg.header.stamp.sec, msg.header.stamp.nanosec = divmod(stamp, 1_000_000_000)
        msg.header.frame_id = "mc_upper_body"
        msg.header.sequence = self.seq
        msg.source = "g1_teleop_x2_demo"
        msg.hand_sub_mode = 0
        msg.head_pos = [0.0, 0.0]
        msg.arm_pos = [float(x) for x in arm]
        msg.hand_pos = []
        self.pub.publish(msg)
        if phase == "play":
            self.max_error = max(self.max_error, float(np.max(np.abs(self.measured - arm))))
        if self.seq % int(self.rate) == 0:
            self.get_logger().info(f"{phase}: max |measured - command| during play {self.max_error:.3f} rad")
        self.seq += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("trajectory")
    parser.add_argument("--ramp", type=float, default=3.0)
    args = parser.parse_args()
    data = json.loads(open(args.trajectory).read())
    frames = [np.asarray(f, dtype=float) for f in data["arm_pos"]]
    rclpy.init()
    node = Demo(frames, float(data["rate_hz"]), args.ramp)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
