#!/usr/bin/env python3
"""Bounded AimDK velocity probe for the network-isolated official X2 simulator.

Run inside robots/x2/sim.sh's container, after MC reaches STAND_DEFAULT.
The local MC may keep reporting STAND_DEFAULT after accepting locomotion mode.
This publishes the same high-level SDK message as the robot uses and checks
measured HAL odometry and leg joints. It never addresses a physical robot.
"""

import argparse
import json
import math
import os
import socket
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from nav_msgs.msg import Odometry
from aimdk_msgs.msg import JointStateArray, McInputAction, McLocomotionVelocity
from aimdk_msgs.srv import GetMcAction, SetMcInputSource

SOURCE = 'node.teleop_x2_sim_probe'


def require_isolated_sim():
    interfaces = [name for _, name in socket.if_nameindex() if name != 'lo']
    if os.environ.get('X2_SIM_ONLY') != '1' or os.environ.get('ROS_LOCALHOST_ONLY') != '1' or interfaces:
        raise RuntimeError('La prueba exige el contenedor X2 con --network none y ROS_LOCALHOST_ONLY=1')


class SimProbe(Node):
    def __init__(self):
        super().__init__('teleop_x2_official_sim_probe')
        self.publisher = self.create_publisher(McLocomotionVelocity, '/aima/mc/locomotion/velocity', 10)
        self.mode_client = self.create_client(GetMcAction, '/aimdk_5Fmsgs/srv/GetMcAction')
        self.source_client = self.create_client(SetMcInputSource, '/aimdk_5Fmsgs/srv/SetMcInputSource')
        self.last_odom = None
        self.last_leg = None
        self.odom_time = 0.0
        self.leg_time = 0.0
        self.odom_count = 0
        self.leg_count = 0
        self.create_subscription(Odometry, '/aima/hal/odom/state', self.on_odom, qos_profile_sensor_data)
        self.create_subscription(JointStateArray, '/aima/hal/joint/leg/state', self.on_leg, qos_profile_sensor_data)

    def on_odom(self, msg):
        p = msg.pose.pose.position
        self.last_odom = (float(p.x), float(p.y), float(p.z))
        self.odom_time = time.monotonic()
        self.odom_count += 1

    def on_leg(self, msg):
        self.last_leg = {joint.name: float(joint.position) for joint in msg.joints}
        self.leg_time = time.monotonic()
        self.leg_count += 1

    def call(self, client, request, timeout=3.0):
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError('Servicio AimDK no disponible: ' + client.srv_name)
        request.request.header.stamp = self.get_clock().now().to_msg()
        future = client.call_async(request)
        rclpy.spin_until_future_complete(self, future, timeout_sec=timeout)
        if not future.done() or future.exception():
            raise RuntimeError('Servicio AimDK sin respuesta: ' + client.srv_name)
        response = future.result()
        reply_header = response.response.header if hasattr(response, 'response') else response.header
        if reply_header.code != 0:
            raise RuntimeError('Servicio AimDK rechazó solicitud: ' + client.srv_name)
        return response

    def require_ready_mode(self):
        response = self.call(self.mode_client, GetMcAction.Request())
        mode = response.info.action_desc
        status = response.info.status.value
        if mode not in ('LOCOMOTION_DEFAULT', 'STAND_DEFAULT') or status != 100:
            raise RuntimeError(f'MC requiere STAND/LOCOMOTION en RUNNING; actual={mode}/{status}')
        return mode

    def source_action(self, action):
        request = SetMcInputSource.Request()
        request.action.value = action
        request.input_source.name = SOURCE
        request.input_source.priority = 40
        request.input_source.timeout = 1000
        return self.call(self.source_client, request)

    def register_source(self):
        try:
            self.source_action(McInputAction.INPUTACTION_ADD)
        except RuntimeError:
            self.source_action(McInputAction.INPUTACTION_ENABLE)

    def publish(self, forward):
        message = McLocomotionVelocity()
        message.header.stamp = self.get_clock().now().to_msg()
        message.source = SOURCE
        message.forward_velocity = forward
        message.lateral_velocity = 0.0
        message.angular_velocity = 0.0
        self.publisher.publish(message)

    def await_feedback(self, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.02)
            now = time.monotonic()
            if self.odom_count and self.leg_count and now - self.odom_time < 0.5 and now - self.leg_time < 0.5:
                if len(self.last_leg) != 12:
                    raise RuntimeError(f'Estado de piernas incompleto: {len(self.last_leg)} ejes')
                return
        raise RuntimeError('Sin odometría y estado de 12 articulaciones recientes')

    def stream(self, forward, duration, mode):
        start_odom = self.last_odom
        start_leg = dict(self.last_leg)
        first_count = (self.odom_count, self.leg_count)
        deadline = time.monotonic() + duration
        next_tick = time.monotonic()
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - self.odom_time > 0.5 or now - self.leg_time > 0.5:
                raise RuntimeError('Se perdió la retroalimentación HAL; velocidad detenida')
            if now >= next_tick:
                self.publish(forward)
                next_tick += 0.02
            rclpy.spin_once(self, timeout_sec=min(0.01, max(0.0, next_tick - time.monotonic())))
        delta = math.hypot(self.last_odom[0] - start_odom[0], self.last_odom[1] - start_odom[1])
        leg_delta = max(abs(self.last_leg[k] - start_leg[k]) for k in start_leg)
        return {
            'model': 'lx2501_3_t2d5',
            'controller': 'official_mc',
            'mode_reported': mode,
            'command_m_s': forward,
            'duration_s': duration,
            'horizontal_displacement_m': round(delta, 4),
            'max_leg_delta_rad': round(leg_delta, 4),
            'odom_samples': self.odom_count - first_count[0],
            'leg_samples': self.leg_count - first_count[1],
        }

    def stop(self):
        end = time.monotonic() + 0.6
        while time.monotonic() < end:
            self.publish(0.0)
            rclpy.spin_once(self, timeout_sec=0.02)
        try:
            self.source_action(McInputAction.INPUTACTION_DISABLE)
        except RuntimeError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--forward', type=float, default=0.0, help='0 or 0.2..1.0 m/s')
    parser.add_argument('--duration', type=float, default=3.0, help='seconds, 0.5..10')
    args = parser.parse_args()
    if args.forward != 0.0 and not 0.2 <= abs(args.forward) <= 1.0:
        parser.error('forward debe ser 0 o tener magnitud 0.2..1.0 m/s')
    if not 0.5 <= args.duration <= 10.0:
        parser.error('duration debe estar entre 0.5 y 10 s')
    require_isolated_sim()
    rclpy.init()
    node = SimProbe()
    registered = False
    try:
        mode = node.require_ready_mode()
        node.await_feedback()
        node.register_source()
        registered = True
        result = node.stream(args.forward, args.duration, mode)
        if args.forward and result['horizontal_displacement_m'] < 0.03:
            raise RuntimeError('MC recibió velocidad pero la base no avanzó; comprobar pórtico y modo')
        print(json.dumps(result, sort_keys=True), flush=True)
    finally:
        if registered:
            node.stop()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
