"""Nodo ROS 2 de la HAL del gemelo digital (corre en el contenedor ROS 2 Humble con aimdk_msgs).

Publica lo mismo que la HAL del robot real (``contract``): estados articulares por grupo, manos con tacto, IMU de
pelvis y pecho, IMU de la RGB-D y del LiDAR, odometría de simulación y los /tf_static internos de los sensores.
Escucha las órdenes ``/aima/hal/joint/*/command`` y ``/aima/hal/joint/hand/command`` y las aplica en
``TwinPhysics``. La física avanza en su propio hilo, a tiempo real.

Los mismos topics HAL se publican en cada dominio DDS conectado (``attach``): el público, donde están los programas
del usuario, y el privado del MC oficial (ver ``bridge``). Para llegar a tiempo real en Python, los mensajes de alta
frecuencia se publican ya serializados (plantillas de ``twin.cdr``) y las órdenes se leen en crudo.
"""
from __future__ import annotations

import threading
import time

import numpy as np
import rclpy
from aimdk_msgs.msg import HandCommandArray, HandStateArray, HandType, JointCommandArray, JointState, JointStateArray
from aimdk_msgs.srv import GetAllJointState, GetHandType
from builtin_interfaces.msg import Time as TimeMsg
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import Imu
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage

from x2_ultra.twin import cdr
from x2_ultra.twin import contract as C
from x2_ultra.twin.physics import TwinPhysics


def qos(spec: tuple) -> QoSProfile:
    rel, dur, depth = spec
    return QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=depth,
                      reliability=ReliabilityPolicy.RELIABLE if rel == C.RELIABLE else ReliabilityPolicy.BEST_EFFORT,
                      durability=DurabilityPolicy.TRANSIENT_LOCAL if dur == C.TRANSIENT_LOCAL
                      else DurabilityPolicy.VOLATILE)


def stamp_now() -> TimeMsg:
    t = time.time()
    return TimeMsg(sec=int(t), nanosec=int((t % 1.0) * 1e9))


class HalEndpoints:
    """Publicadores y suscripciones HAL en un nodo (es decir, en un dominio DDS)."""

    def __init__(self, node: Node, hal: "TwinHal"):
        self.state = {g: node.create_publisher(JointStateArray, C.joint_state_topic(g), qos(C.JOINT_STATE_QOS))
                      for g in C.JOINT_GROUPS}
        for g in C.JOINT_GROUPS:
            node.create_subscription(JointCommandArray, C.joint_command_topic(g),
                                     lambda m, g=g: hal.on_joint_command(g, m), qos(C.JOINT_COMMAND_QOS), raw=True)
        self.hand = node.create_publisher(HandStateArray, C.HAND_STATE_TOPIC, qos(C.HAND_STATE_QOS))
        node.create_subscription(HandCommandArray, C.HAND_COMMAND_TOPIC, hal.on_hand_command,
                                 qos(C.HAND_COMMAND_QOS), raw=True)
        self.imu = {k: node.create_publisher(Imu, t, qos(C.IMU_QOS)) for k, t in C.IMU_TOPICS.items()}
        self.sensor_imu = {"imu_rgbd": node.create_publisher(Imu, C.RGBD_IMU_TOPIC, qos(C.RGBD_IMU_QOS)),
                           "imu_lidar": node.create_publisher(Imu, C.LIDAR_IMU_TOPIC, qos(C.LIDAR_IMU_QOS))}
        self.odom = node.create_publisher(Odometry, C.ODOM_TOPIC, qos(C.ODOM_QOS))


class TwinHal(Node):
    def __init__(self, physics: TwinPhysics, rtf: float = 1.0, node_name: str = "x2_twin_hal"):
        super().__init__(node_name)
        self.phys = physics
        self.rtf = rtf
        self.lock = threading.Lock()
        self.running = False
        self.stats = {"rtf": 0.0, "behind_s": 0.0}
        self.overrides = []          # objetos con .apply(phys) llamados antes de cada paso (URS emulado)
        self.shared = None           # SharedState para visor/cámaras (lo pone run.py)
        dt = physics.model.opt.timestep
        every = lambda hz: max(1, round(1.0 / (hz * dt)))

        self.state_tpl = {g: cdr.joint_state_array(names) for g, names in C.JOINT_GROUPS.items()}
        self.state_every = {g: every(C.JOINT_STATE_RATE[g]) for g in C.JOINT_GROUPS}
        self.hand_tpl = cdr.hand_state_array(C.LEFT_HAND_JOINTS, C.RIGHT_HAND_JOINTS, C.HAND_TYPE_NIMBLE)
        self.hand_every = every(C.HAND_STATE_RATE)
        self.imu_tpl = cdr.imu(C.IMU_FRAME_ID)
        self.imu_every = every(C.IMU_RATE)
        self.sensor_imu_tpl = {"imu_rgbd": cdr.imu(C.RGBD_IMU_FRAME), "imu_lidar": cdr.imu(C.LIDAR_IMU_FRAME)}
        self.sensor_imu_every = every(C.SENSOR_IMU_RATE)
        self.odom_tpl = cdr.odometry(C.ODOM_FRAME_ID, C.ODOM_CHILD_FRAME_ID)
        self.odom_every = every(C.ODOM_RATE)
        self._name_index = {}
        self._hand_index = {n: i for i, n in enumerate(C.LEFT_HAND_JOINTS + C.RIGHT_HAND_JOINTS)}

        self.endpoints = [HalEndpoints(self, self)]
        self.create_service(GetAllJointState, C.service("GetAllJointState"), self._srv_all_joint_state)
        self.create_service(GetHandType, C.service("GetHandType"), self._srv_hand_type)
        self.create_subscription(String, C.SIM_OPTION_TOPIC, self._on_sim_option, 10)
        self.tf_static_pub = self.create_publisher(TFMessage, "/tf_static", qos((C.RELIABLE, C.TRANSIENT_LOCAL, 1)))
        self._publish_tf_static()

    def attach(self, node: Node) -> None:
        """Publica también la HAL en el dominio de ``node`` y acepta sus órdenes (el MC en su dominio privado)."""
        self.endpoints.append(HalEndpoints(node, self))

    # --- órdenes ----------------------------------------------------------------------------------
    def _indices(self, group: str, names: tuple) -> np.ndarray | None:
        key = (group, names)
        idx = self._name_index.get(key)
        if idx is None:
            order = C.JOINT_GROUPS[group]
            if all(not n for n in names):            # el nombre es opcional: se asume el orden de AimDK
                idx = np.arange(len(order)) if len(names) == len(order) else None
            else:
                try:
                    idx = np.array([order.index(n) for n in names])
                except ValueError:
                    self.get_logger().warn(f"{group}: nombres desconocidos {names}; orden ignorada")
                    idx = None
            self._name_index[key] = idx
        return idx

    def on_joint_command(self, group: str, raw: bytes) -> None:
        names, vals = cdr.parse_joint_command_array(raw)
        idx = self._indices(group, names)
        if idx is None:
            return
        c = self.phys.cmd[group]
        with self.lock:
            cur = np.stack([c.q, c.dq, c.tau, c.kp, c.kd], axis=1)
            cur[idx] = vals
            self.phys.set_joint_command(group, *cur.T)

    def on_hand_command(self, raw: bytes) -> None:
        names, vals = cdr.parse_hand_command_array(raw)
        with self.lock:
            target = self.phys.hand_target.copy()
            for name, row in zip(names, vals):
                i = self._hand_index.get(name)
                if i is not None:
                    target[i] = row[0]
            self.phys.set_hand_targets(target)

    def _on_sim_option(self, msg: String) -> None:
        words = msg.data.strip().lower().split()
        if not words:
            return
        with self.lock:
            if words[0] in ("gantry", "portico", "pórtico"):
                on = len(words) < 2 or words[1] in ("on", "1", "true", "si", "sí")
                self.phys.set_gantry(on)
                self.get_logger().info(f"pórtico {'puesto' if on else 'quitado'}")
            elif words[0] == "reset":
                self.phys.reset(gantry=True)
                self.get_logger().info("reinicio (con pórtico)")
            elif words[0] in ("push", "empujon", "empujón") and len(words) >= 3:
                try:
                    force = [float(w) for w in words[1:4]] + [0.0] * (4 - len(words))
                except ValueError:
                    return
                self.phys.push(force[:3])

    # --- servicios -------------------------------------------------------------------------------
    def _srv_all_joint_state(self, request, response):
        with self.lock:
            for g, fld in (("head", "head_joints"), ("arm", "arm_joints"), ("waist", "waist_joints"),
                           ("leg", "leg_joints")):
                q, dq, tau = self.phys.joint_state(g)
                setattr(response, fld, [JointState(name=n, position=float(a), velocity=float(b), effort=float(c))
                                        for n, a, b, c in zip(C.JOINT_GROUPS[g], q, dq, tau)])
        return response

    def _srv_hand_type(self, request, response):
        response.left_hands_type = HandType(value=C.HAND_TYPE_NIMBLE)
        response.right_hands_type = HandType(value=C.HAND_TYPE_NIMBLE)
        return response

    def _publish_tf_static(self) -> None:
        """Marcos internos de los sensores (como sus drivers en el robot); el resto del árbol lo publica el MC."""
        def tf(parent, child, xyz=(0.0, 0.0, 0.0), q=(0.0, 0.0, 0.0, 1.0)):
            t = TransformStamped()
            t.header.stamp = stamp_now()
            t.header.frame_id, t.child_frame_id = parent, child
            t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = xyz
            (t.transform.rotation.x, t.transform.rotation.y, t.transform.rotation.z,
             t.transform.rotation.w) = q
            return t
        self.tf_static_pub.publish(TFMessage(transforms=[
            tf("rgbd_head_front", "rgbd_depth_head_front"),           # color y profundidad alineadas en el gemelo
            tf("rgbd_head_front", C.RGBD_IMU_FRAME),
            tf("stereo_head_front", "stereo_head_front_right", (C.STEREO_BASELINE, 0.0, 0.0)),
            tf(C.LIDAR_FRAME, C.LIDAR_IMU_FRAME),
        ]))

    # --- publicación ----------------------------------------------------------------------------
    def _publish_due(self, k: int) -> None:
        t = time.time()
        sec, nsec = int(t), int((t % 1.0) * 1e9)
        ph, eps = self.phys, self.endpoints
        for g in C.JOINT_GROUPS:
            if k % self.state_every[g] == 0:
                q, dq, tau = ph.joint_state(g)
                data = self.state_tpl[g].pack(sec, nsec, {"position": q, "velocity": dq, "effort": tau})
                for ep in eps:
                    ep.state[g].publish(data)
        if k % self.imu_every == 0:
            for name in C.IMU_TOPICS:
                quat, gyro, acc = ph.imu(name)
                data = self.imu_tpl.pack(sec, nsec, {"orientation": quat[[1, 2, 3, 0]], "angular_velocity": gyro,
                                                     "linear_acceleration": acc})
                for ep in eps:
                    ep.imu[name].publish(data)
        if k % self.odom_every == 0:
            pos, quat, vel, gyro = ph.odom()
            data = self.odom_tpl.pack(sec, nsec, {"position": pos, "orientation": quat[[1, 2, 3, 0]],
                                                  "linear": vel, "angular": gyro})
            for ep in eps:
                ep.odom.publish(data)
        if k % self.sensor_imu_every == 0:
            for site, tpl in self.sensor_imu_tpl.items():
                quat, gyro, acc = ph.sensor_imu(site)
                data = tpl.pack(sec, nsec, {"orientation": quat[[1, 2, 3, 0]], "angular_velocity": gyro,
                                            "linear_acceleration": acc})
                for ep in eps:
                    ep.sensor_imu[site].publish(data)
        if k % self.hand_every == 0:
            q, dq, tau = ph.hand_state()
            touch = {f"{side}_{fld}": arr.tobytes() for side in ("L", "R")
                     for fld, arr in ph.touch_codes(side).items()}
            data = self.hand_tpl.pack(sec, nsec, {"position": q, "velocity": dq, "effort": tau}, touch)
            for ep in eps:
                ep.hand.publish(data)

    # --- bucle de física a tiempo real -------------------------------------------------------------
    def run(self) -> None:
        self.running = True
        dt = self.phys.model.opt.timestep
        k = 0
        t_wall0, t_sim0 = time.monotonic(), self.phys.time
        window_wall, window_sim = time.monotonic(), self.phys.time
        while self.running and rclpy.ok():
            target = t_sim0 + (time.monotonic() - t_wall0) * self.rtf
            behind = target - self.phys.time
            if behind > 0.25:                  # demasiado retraso: se resincroniza en lugar de acelerar
                self.get_logger().warn(f"la física va {behind:.2f} s por detrás del reloj; se resincroniza")
                t_wall0, t_sim0 = time.monotonic(), self.phys.time
                continue
            if behind < dt * 0.5:
                time.sleep(min(0.0005, dt * 0.5))
                continue
            with self.lock:
                for ov in self.overrides:
                    ov.apply(self.phys)
                self.phys.step(1)
                k += 1
                self._publish_due(k)
                if self.shared is not None and k % 10 == 0:
                    self.shared.write(self.phys.data.qpos, self.phys.time, self.phys.gantry)
            now = time.monotonic()
            if now - window_wall >= 1.0:
                self.stats["rtf"] = (self.phys.time - window_sim) / (now - window_wall)
                self.stats["behind_s"] = behind
                window_wall, window_sim = now, self.phys.time

    def stop(self) -> None:
        self.running = False
