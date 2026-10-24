"""Se ejecuta dentro del contenedor x2-twin (official_sim/twin.sh test): lo que escribe twin.cdr, leído con
rclpy.serialization, da el mismo mensaje (los bytes de relleno no se comparan: rclpy deja basura en ellos)."""
import numpy as np
from aimdk_msgs.msg import (HandCommand, HandCommandArray, HandState, HandStateArray, HandType, JointCommand,
                            JointCommandArray, JointState, JointStateArray)
from builtin_interfaces.msg import Time
from nav_msgs.msg import Odometry
from rclpy.serialization import deserialize_message, serialize_message
from sensor_msgs.msg import Imu

from x2_ultra.twin import cdr
from x2_ultra.twin import contract as C

RNG = np.random.default_rng(0)


def test_joint_state_array_bytes():
    names = C.JOINT_GROUPS["leg"]
    q, dq, tau = RNG.normal(size=(3, len(names)))
    m = JointStateArray()
    m.header.stamp = Time(sec=123, nanosec=456)
    m.joints = [JointState(name=n, position=float(a), velocity=float(b), effort=float(c))
                for n, a, b, c in zip(names, q, dq, tau)]
    t = cdr.joint_state_array(names)
    assert deserialize_message(t.pack(123, 456, {"position": q, "velocity": dq, "effort": tau}), JointStateArray) == m


def test_hand_state_array_bytes():
    q, dq, tau = RNG.normal(size=(3, 20))
    m = HandStateArray()
    m.header.stamp = Time(sec=7, nanosec=8)
    m.left_hand_type = HandType(value=1)
    m.right_hand_type = HandType(value=1)
    m.left_hands = [HandState(name=n, position=float(a), velocity=float(b), effort=float(c))
                    for n, a, b, c in zip(C.LEFT_HAND_JOINTS, q[:10], dq[:10], tau[:10])]
    m.right_hands = [HandState(name=n, position=float(a), velocity=float(b), effort=float(c))
                     for n, a, b, c in zip(C.RIGHT_HAND_JOINTS, q[10:], dq[10:], tau[10:])]
    palm = RNG.integers(0, 255, 36, dtype=np.uint8)
    m.left_touch_sensors.palm_touch_data = palm
    tip = RNG.integers(0, 255, 16, dtype=np.uint8)
    m.right_touch_sensors.little_finger_touch_data = tip
    t = cdr.hand_state_array(C.LEFT_HAND_JOINTS, C.RIGHT_HAND_JOINTS, 1)
    out = t.pack(7, 8, {"position": q, "velocity": dq, "effort": tau},
                 {"L_palm_touch_data": palm.tobytes(), "R_little_finger_touch_data": tip.tobytes()})
    assert deserialize_message(out, HandStateArray) == m


def test_imu_and_odometry_bytes():
    ori, gyro, acc = RNG.normal(size=4), RNG.normal(size=3), RNG.normal(size=3)
    m = Imu()
    m.header.stamp, m.header.frame_id = Time(sec=1, nanosec=2), "base_link"
    m.orientation.x, m.orientation.y, m.orientation.z, m.orientation.w = (float(v) for v in ori)
    m.angular_velocity.x, m.angular_velocity.y, m.angular_velocity.z = (float(v) for v in gyro)
    m.linear_acceleration.x, m.linear_acceleration.y, m.linear_acceleration.z = (float(v) for v in acc)
    out = cdr.imu("base_link").pack(1, 2, {"orientation": ori, "angular_velocity": gyro, "linear_acceleration": acc})
    assert deserialize_message(out, Imu) == m
    assert len(out) == len(serialize_message(m))
    pos, quat, lin, ang = RNG.normal(size=3), RNG.normal(size=4), RNG.normal(size=3), RNG.normal(size=3)
    o = Odometry()
    o.header.stamp, o.header.frame_id, o.child_frame_id = Time(sec=3, nanosec=4), "odom", "base_link"
    p = o.pose.pose
    p.position.x, p.position.y, p.position.z = (float(v) for v in pos)
    p.orientation.x, p.orientation.y, p.orientation.z, p.orientation.w = (float(v) for v in quat)
    o.twist.twist.linear.x, o.twist.twist.linear.y, o.twist.twist.linear.z = (float(v) for v in lin)
    o.twist.twist.angular.x, o.twist.twist.angular.y, o.twist.twist.angular.z = (float(v) for v in ang)
    out = cdr.odometry("odom", "base_link").pack(3, 4, {"position": pos, "orientation": quat, "linear": lin,
                                                        "angular": ang})
    assert deserialize_message(out, Odometry) == o
    assert len(out) == len(serialize_message(o))


def test_parse_commands():
    names = C.JOINT_GROUPS["arm"]
    vals = RNG.normal(size=(len(names), 5))
    m = JointCommandArray()
    m.header.frame_id = "x" * 5
    m.joints = [JointCommand(name=n, position=v[0], velocity=v[1], effort=v[2], stiffness=v[3], damping=v[4])
                for n, v in zip(names, vals.tolist())]
    got_names, got = cdr.parse_joint_command_array(serialize_message(m))
    assert got_names == tuple(names) and np.allclose(got, vals)
    h = HandCommandArray()
    hv = RNG.normal(size=(20, 5))
    h.left_hands = [HandCommand(name=n, position=v[0], velocity=v[1], acceleration=v[2], deceleration=v[3],
                                effort=v[4]) for n, v in zip(C.LEFT_HAND_JOINTS, hv[:10].tolist())]
    h.right_hands = [HandCommand(name=n, position=v[0], velocity=v[1], acceleration=v[2], deceleration=v[3],
                                 effort=v[4]) for n, v in zip(C.RIGHT_HAND_JOINTS, hv[10:].tolist())]
    got_names, got = cdr.parse_hand_command_array(serialize_message(h))
    assert got_names == tuple(C.LEFT_HAND_JOINTS + C.RIGHT_HAND_JOINTS) and np.allclose(got, hv)
