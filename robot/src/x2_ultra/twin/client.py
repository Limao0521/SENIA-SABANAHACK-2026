"""La app de control (ventana Qt) manejando el gemelo por la interfaz pública de AimDK, como haría con el robot.

Tiene la misma interfaz que ``SimEngine`` (lo que usan ``app.main`` y ``app.panels``), pero no mueve la física:

  - WASD/QE → ``/aima/mc/locomotion/velocity`` a 50 Hz, con la fuente ``sdk_x2_ultra_gui`` registrada con
    ``SetMcInputSource`` y los mínimos de arranque del MC (0,2 m/s y 0,1 rad/s);
  - brazos (sliders, IK, poses) y manos → ``/mc/upper_body_command`` en UPPERBODY_REMOTE_SPLIT, a 50 Hz;
  - flechas → empujón y R → reinicio, por ``/aima/sim/option/command`` (solo simulación).

La postura para la vista 3D se lee de la memoria compartida del gemelo (mismo contenedor). Al arrancar prepara el
robot como se hace con el real: STAND_DEFAULT con el pórtico puesto, quitar el pórtico y pedir URS.
"""
from __future__ import annotations

import math
import threading
import time
from types import SimpleNamespace

import numpy as np
import rclpy
from aimdk_msgs.msg import (HandStateArray, McActionCommand, McInputAction, McLocomotionVelocity, MessageHeader,
                            RequestHeader, UpperBodyCommandArray)
from aimdk_msgs.srv import GetMcAction, SetMcAction, SetMcInputSource
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from std_msgs.msg import String

from x2_ultra.control.poses import ARM_POSES, clamp_arms
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H
from x2_ultra.sim.engine import EngineInfo, SpeedLimits
from x2_ultra.twin import contract as C
from x2_ultra.twin.model import build_twin_model
from x2_ultra.twin.ros_hal import qos
from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState

SOURCE = "sdk_x2_ultra_gui"
C_URS = "UPPERBODY_REMOTE_SPLIT"
MIN_LIN, MIN_ANG = 0.2, 0.1
MC_BOUNDS = {"vx": (-1.0, 0.8), "vy": (-1.0, 1.0), "wz": (-1.0, 1.0)}      # speed_state.yaml, modo LOW
FALL_HEIGHT = 0.40


def _with_min(v: float, vmin: float) -> float:
    return 0.0 if abs(v) < 1e-3 else math.copysign(max(abs(v), vmin), v)


class TwinClient:
    def __init__(self, scene: str = "mesa"):
        self.model = build_twin_model(scene)
        try:
            self.state = SharedState(self.model.nq, name=TWIN_SHM_NAME)
        except FileNotFoundError:
            raise SystemExit("El gemelo no está en marcha: official_sim/twin.sh start") from None
        self.torso = self.model.body("torso_link").id
        self.info = EngineInfo(policy="MC oficial + URS emulado", state="conectando")
        self.speed = SpeedLimits()
        self.heading_hold = False
        self.paused = False
        self.is_twin = True
        self.policy = SimpleNamespace(name="mc_oficial", controls_arms=False)
        self.arm_target = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
        self.head_target = np.zeros(2)
        self.hand_target = np.zeros(20)
        self.anim, self.anim_t0 = None, 0.0
        self.cmd_target = np.zeros(3)
        self.lock = threading.Lock()
        self.urs = False
        self._qpos = None
        self._rtf = (time.monotonic(), 0.0)
        self._running = False
        self._threads: list[threading.Thread] = []

        rclpy.init()
        self.node = Node("x2_twin_gui")
        n = self.node
        self.vel_pub = n.create_publisher(McLocomotionVelocity, "/aima/mc/locomotion/velocity", 10)
        self.ub_pub = n.create_publisher(UpperBodyCommandArray, C.UPPER_BODY_TOPIC, qos(C.UPPER_BODY_QOS))
        self.opt_pub = n.create_publisher(String, C.SIM_OPTION_TOPIC, 10)
        self.set_action = n.create_client(SetMcAction, C.service("SetMcAction"))
        self.get_action = n.create_client(GetMcAction, C.service("GetMcAction"))
        self.input_src = n.create_client(SetMcInputSource, C.service("SetMcInputSource"))
        n.create_subscription(HandStateArray, C.HAND_STATE_TOPIC, self._on_hand, qos_profile_sensor_data)
        self._hand_n = 0
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(n)

    # --- ROS -------------------------------------------------------------------------------------
    def _call(self, client, request, timeout: float = 3.0):
        if not client.wait_for_service(timeout_sec=timeout):
            return None
        fut = client.call_async(request)
        done = threading.Event()
        fut.add_done_callback(lambda _f: done.set())
        return fut.result() if done.wait(timeout) else None

    def mc_mode(self) -> str:
        res = self._call(self.get_action, GetMcAction.Request(), timeout=2.0)
        return "?" if res is None else res.info.action_desc

    def request_mode(self, desc: str) -> bool:
        req = SetMcAction.Request()
        req.header = RequestHeader()
        req.source = SOURCE
        req.command = McActionCommand(action_desc=desc)
        res = self._call(self.set_action, req, timeout=5.0)
        return res is not None and res.response.status.value == 1

    def _register(self, enable: bool = True) -> None:
        actions = (McInputAction.INPUTACTION_ADD, McInputAction.INPUTACTION_ENABLE) if enable else \
            (McInputAction.INPUTACTION_DISABLE,)
        for action in actions:
            req = SetMcInputSource.Request()
            req.action.value = action
            req.input_source.name, req.input_source.priority, req.input_source.timeout = SOURCE, 40, 1000
            self._call(self.input_src, req, timeout=2.0)

    def _sim_option(self, text: str) -> None:
        self.opt_pub.publish(String(data=text))

    def _on_hand(self, msg: HandStateArray) -> None:
        self._hand_n += 1
        if self._hand_n % 10:                     # basta con ~20 Hz para las barras de tacto
            return
        touch = {}
        for side, t in (("L", msg.left_touch_sensors), ("R", msg.right_touch_sensors)):
            for region, fld in (("palm", "palm_touch_data"), ("thumb", "thumb_touch_data"),
                                ("index", "index_finger_touch_data"), ("middle", "middle_finger_touch_data"),
                                ("ring", "ring_finger_touch_data"), ("pinky", "little_finger_touch_data")):
                codes = np.asarray(getattr(t, fld), dtype=float)
                touch[f"{side}_{region}"] = float(codes.max()) / 255.0 * 10.0 if codes.size else 0.0
        self.info.touch = touch

    # --- bucles ------------------------------------------------------------------------------------
    def _prepare(self) -> None:
        """Como con el robot real: STAND_DEFAULT con el pórtico, soltarlo y pedir URS."""
        self.info.state = "esperando al MC"
        while self._running and self.mc_mode() == "?":
            time.sleep(1.0)
        mode = self.mc_mode()
        if mode not in ("STAND_DEFAULT", "LOCOMOTION_DEFAULT", C_URS):
            self.info.state = "pidiendo STAND_DEFAULT"
            for _ in range(60):
                if not self._running:
                    return
                self.request_mode("STAND_DEFAULT")
                time.sleep(2.0)
                if self.mc_mode() == "STAND_DEFAULT":
                    break
        snap = self.state.read()
        if snap is not None and snap[2]:          # pórtico puesto: se quita ya de pie
            time.sleep(2.0)
            self._sim_option("gantry off")
            time.sleep(1.0)
        self._register(True)
        self.urs = self.request_mode(C_URS)
        self.info.state = self.mc_mode()

    def _publish_loop(self) -> None:
        seq = 0
        while self._running:
            t0 = time.monotonic()
            with self.lock:
                vx, vy, wz = self.cmd_target
                arms = self.anim.target(time.monotonic() - self.anim_t0) if self.anim is not None else self.arm_target
                arms, hands, head = clamp_arms(arms), self.hand_target.copy(), self.head_target.copy()
            stamp = self.node.get_clock().now().to_msg()
            if self.urs and not self.paused:
                v = McLocomotionVelocity()
                v.header = MessageHeader()
                v.header.stamp, v.source = stamp, SOURCE
                v.forward_velocity, v.lateral_velocity = _with_min(vx, MIN_LIN), _with_min(vy, MIN_LIN)
                v.angular_velocity = _with_min(wz, MIN_ANG)
                self.vel_pub.publish(v)
                m = UpperBodyCommandArray()
                m.header = MessageHeader()
                m.header.stamp, m.header.sequence = stamp, seq
                m.source = SOURCE
                m.hand_sub_mode = C.HAND_DEXTEROUS_JOINT
                m.head_pos, m.arm_pos, m.hand_pos = head.tolist(), arms.tolist(), hands.tolist()
                self.ub_pub.publish(m)
                seq += 1
            time.sleep(max(0.0, 0.02 - (time.monotonic() - t0)))

    def _monitor_loop(self) -> None:
        """Modo del MC para el HUD (2 Hz). Si sale de URS (caída, amortiguamiento…), se vuelve a preparar."""
        while self._running:
            time.sleep(0.5)
            if not self.urs:
                continue
            mode = self.mc_mode()
            self.info.state = "caido" if self.info.base_pos[2] < FALL_HEIGHT else mode
            if mode not in (C_URS, "?"):
                self.urs = False
                self._prepare()

    # --- interfaz de SimEngine para la app ----------------------------------------------------------
    def start(self) -> None:
        self._running = True
        for target in (self.executor.spin, self._publish_loop, self._prepare, self._monitor_loop):
            th = threading.Thread(target=target, daemon=True)
            th.start()
            self._threads.append(th)

    def stop(self) -> None:
        if not self._running:
            return
        self.cmd_target = np.zeros(3)
        time.sleep(0.1)
        self._running = False
        self._register(False)
        self.executor.shutdown(timeout_sec=1.0)
        self.state.close()
        rclpy.shutdown()

    def snapshot(self) -> tuple[np.ndarray, EngineInfo]:
        snap = self.state.read()
        if snap is not None:
            qpos, t, _gantry, _ = snap
            self._qpos = qpos
            now = time.monotonic()
            w0, s0 = self._rtf
            if now - w0 > 1.0:
                self.info.rtf = (t - s0) / (now - w0) if s0 else 0.0
                self._rtf = (now, t)
            self.info.sim_time = t
            self.info.base_pos = qpos[:3].copy()
            w, x, y, z = qpos[3:7]
            self.info.yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
        self.info.cmd = self.cmd_target.copy()
        self.info.cmd_target = self.cmd_target.copy()
        q = self._qpos if self._qpos is not None else self.model.qpos0.copy()
        return q.copy(), self.info

    def set_velocity_target(self, vx: float, vy: float, wz: float) -> None:
        self.cmd_target = np.array([np.clip(vx, *MC_BOUNDS["vx"]), np.clip(vy, *MC_BOUNDS["vy"]),
                                    np.clip(wz, *MC_BOUNDS["wz"])])

    def stop_walking(self) -> None:
        self.cmd_target = np.zeros(3)

    def set_arm_targets(self, q14, keep_animation: bool = False) -> None:
        with self.lock:
            self.arm_target = clamp_arms(np.asarray(q14, dtype=float))
            if not keep_animation:
                self.anim = None
                self.info.pose = ""

    def set_head_targets(self, yaw: float, pitch: float) -> None:
        """Se guarda, pero en URS el MC ignora la cabeza (AimDK: la cabeza solo en HEAD_ONLY)."""
        self.head_target = np.array([yaw, pitch])

    def set_hand_targets(self, side: str, q10) -> None:
        joints = H.LEFT_HAND_JOINTS if side == "L" else H.RIGHT_HAND_JOINTS
        lo = np.array([H.HAND_RANGE[n][0] for n in joints])
        hi = np.array([H.HAND_RANGE[n][1] for n in joints])
        sl = slice(0, 10) if side == "L" else slice(10, 20)
        with self.lock:
            self.hand_target[sl] = np.clip(q10, lo, hi)

    def set_hand_pose(self, side: str, pose: str) -> None:
        for s in (("L", "R") if side == "LR" else (side,)):
            self.set_hand_targets(s, H.pose_radians(s, pose))

    def play_pose(self, name: str) -> None:
        pose = ARM_POSES[name]
        with self.lock:
            self.arm_target = pose.target(0.0)
            self.anim = pose if pose.wave else None
            self.anim_t0 = time.monotonic()
            self.info.pose = name
        for side, hp in pose.hands.items():
            self.set_hand_pose(side, hp)

    def push(self, direction=None, force: float = 120.0, duration: float = 0.15) -> None:
        if direction is None:
            a = np.random.uniform(0, 2 * math.pi)
            direction = np.array([math.cos(a), math.sin(a), 0.0])
        f = np.asarray(direction, dtype=float) / np.linalg.norm(direction) * force
        self._sim_option(f"push {f[0]:.1f} {f[1]:.1f} {f[2]:.1f}")

    def reset(self) -> None:
        self.urs = False
        self.cmd_target = np.zeros(3)
        self._sim_option("reset")
        threading.Thread(target=self._prepare, daemon=True).start()

