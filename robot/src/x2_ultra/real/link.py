"""Enlace ROS 2 / AimDK con el X2 real: estados, reloj del robot, servicios del MC y publicadores de órdenes.

Corre en el contenedor Humble de ``scripts/robot_real.sh`` (red del PC, cable a develop0 10.0.1.41) o dentro del
gemelo digital (``--gemelo``). Nada se publica ni se llama al importar; los publicadores sólo se crean si el
programa los pide, y las llamadas a servicios las hace el programa cuando la persona arma.

Lecciones del robot aplicadas (g1_teleop, 19/10/2026):
  - las respuestas de servicio entre equipos se pierden: se reenvía la misma petición con el MISMO cliente
    (8 × 250 ms, como los ejemplos de AimDK) y se acepta la primera respuesta que llegue;
  - el MC descarta órdenes continuas de fuentes no registradas: ``SetMcInputSource`` con nombre ``sdk_*`` único
    por sesión, ADD (o MODIFY si un ADD anterior se aplicó sin respuesta) y DELETE al salir;
  - con el robot de pie, no arrancar más de 2 nodos ROS por segundo: cada programa usa un único nodo.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
import threading
import time
import uuid

from x2_ultra.real import control as K
from x2_ultra.robot.joints import ARM_JOINTS, HEAD_JOINTS

TOPIC_MC_STATE = "/aima/mc/common/state"
TOPIC_ARM_STATE = "/aima/hal/joint/arm/state"
TOPIC_HEAD_STATE = "/aima/hal/joint/head/state"
TOPIC_HAND_STATE = "/aima/hal/joint/hand/state"
TOPIC_PMU = "/aima/hal/pmu/state"
TOPIC_ODOM = "/aima/hal/odom/state"
TOPIC_VELOCITY = "/aima/mc/locomotion/velocity"
TOPIC_UPPER_BODY = "/mc/upper_body_command"
TOPIC_BODY_POSE = "/aima/mc/body_pose"      # McBodyPose: no está en el AimDK público (scripts/build_aimdk_overlay.sh)
SRV_SET_ACTION = "/aimdk_5Fmsgs/srv/SetMcAction"
SRV_INPUT_SOURCE = "/aimdk_5Fmsgs/srv/SetMcInputSource"
SOURCE_ADD, SOURCE_MODIFY, SOURCE_DELETE = 1001, 1002, 1003
STAMP_MODES = ("robot", "cero", "pc")
# Códigos de rechazo de SetMcAction (AimDK §5.1.1).
MC_ACTION_CODES = {2: "petición inválida", 3: "acción no registrada", 4: "postura no válida para esa acción",
                   5: "el MC se está recuperando", 6: "protección de seguridad activa", 7: "el MC está arrancando",
                   8: "el robot se está moviendo", 9: "el MC está ocupado con una acción o movimiento",
                   10: "no hay transición posible desde el modo actual"}
CODE_MOTION_BUSY = 9


class ModeRejected(RuntimeError):
    def __init__(self, desc: str, code: int, status, message: str):
        self.code = code
        why = MC_ACTION_CODES.get(code, "rechazado")
        super().__init__(f"{desc}: {why} (code={code}, status={status}){' · ' + message if message else ''}")


def require_target() -> str:
    """Sólo desde scripts/robot_real.sh: X2_REAL=robot (red real) o X2_REAL=gemelo (contenedor del gemelo)."""
    target = os.environ.get("X2_REAL", "")
    if target not in ("robot", "gemelo"):
        raise SystemExit("Lanzar con scripts/robot_real.sh (robot real) o con --gemelo (gemelo digital)")
    if target == "robot" and os.environ.get("ROS_LOCALHOST_ONLY") == "1":
        raise SystemExit("X2_REAL=robot con ROS_LOCALHOST_ONLY=1: así no se ve el robot")
    return target


@dataclass(frozen=True)
class Joints:
    positions: dict[str, float]
    source_wall_ns: int
    received_mono_ns: int


def _stamp_ns(stamp) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


class RobotLink:
    def __init__(self, program: str, *, publish_velocity: bool = False, publish_upper_body: bool = False,
                 publish_body_pose: bool = False):
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
        from rclpy.node import Node
        from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
        from rclpy.signals import SignalHandlerOptions
        from aimdk_msgs import msg as M
        from aimdk_msgs import srv as S
        from nav_msgs.msg import Odometry

        self.rclpy, self.M, self.S = rclpy, M, S
        # Sin los manejadores de rclpy: Ctrl+C no debe cerrar ROS antes de las órdenes finales (ceros o HOLD).
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        tag = uuid.uuid4().hex[:6]
        self.source = f"sdk_x2_ultra_{program}_{tag}"
        self.node = Node(f"x2_ultra_real_{program}_{tag}")
        # BEST_EFFORT + VOLATILE: compatible con cualquier publicador del robot (fiable o no, TRANSIENT_LOCAL o no).
        sensor = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT, durability=DurabilityPolicy.VOLATILE)
        command = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.VOLATILE)
        self.lock = threading.Lock()
        self.clock = K.ClockOffsetEstimator()
        self.joints: dict[str, Joints] = {}
        self.mc = None                     # (modo, estado acción, fuente, body, reproductor, movimiento, recibido)
        self.hands = None                  # (inventario, posiciones, recibido)
        self.battery = None
        self.odom = None                   # (vx, vy, wz, recibido)
        n = self.node
        n.create_subscription(M.McCommonState, TOPIC_MC_STATE, self._on_mc, sensor)
        n.create_subscription(M.JointStateArray, TOPIC_ARM_STATE, lambda m: self._on_joints(m, "arms", ARM_JOINTS), sensor)
        n.create_subscription(M.JointStateArray, TOPIC_HEAD_STATE, lambda m: self._on_joints(m, "head", HEAD_JOINTS), sensor)
        n.create_subscription(M.HandStateArray, TOPIC_HAND_STATE, self._on_hands, sensor)
        n.create_subscription(M.PmuState, TOPIC_PMU, self._on_pmu, sensor)
        n.create_subscription(Odometry, TOPIC_ODOM, self._on_odom, sensor)
        self.velocity_pub = n.create_publisher(M.McLocomotionVelocity, TOPIC_VELOCITY, command) if publish_velocity else None
        self.upper_pub = n.create_publisher(M.UpperBodyCommandArray, TOPIC_UPPER_BODY, command) if publish_upper_body else None
        body_pose = getattr(M, "McBodyPose", None)
        self.body_pose_pub = n.create_publisher(body_pose, TOPIC_BODY_POSE, command) \
            if publish_body_pose and body_pose is not None else None
        self.set_action = n.create_client(S.SetMcAction, SRV_SET_ACTION)
        self.input_source = n.create_client(S.SetMcInputSource, SRV_INPUT_SOURCE)
        self._clients = {}
        self.sequence = 0
        self.registered = False
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(n)
        self._spin = threading.Thread(target=self._spin_loop, daemon=True)
        self._running = True
        self._spin.start()

    # --- recepción ---------------------------------------------------------------------------------------------
    def _spin_loop(self) -> None:
        while self._running and self.rclpy.ok():
            try:
                self.executor.spin_once(timeout_sec=0.05)
            except Exception:  # noqa: BLE001 - al cerrar, el ejecutor puede lanzar; el bucle termina con _running
                if self._running:
                    time.sleep(0.01)

    def _on_mc(self, m) -> None:
        motion = getattr(m, "motion_status", None)
        fsm = getattr(getattr(m, "fsm_state", None), "current_state", None)
        servo = getattr(m, "servo_status", None)
        squat = None if servo is None else (float(servo.squat_offset), bool(servo.squat_enable))
        with self.lock:
            self.mc = (str(m.action_info.action_desc), int(m.action_info.status.value), str(m.input_source.name),
                       int(m.body_status.value),
                       None if motion is None else int(motion.player_state.value),
                       "" if motion is None else str(motion.motion), time.monotonic_ns(),
                       None if fsm is None else int(getattr(fsm, "value", fsm)), squat)

    def _on_joints(self, m, group: str, names: tuple[str, ...]) -> None:
        if len(m.joints) != len(names):
            return
        observed = [str(j.name) for j in m.joints]
        if any(observed) and observed != list(names):
            return
        values = [float(j.position) for j in m.joints]
        if not all(math.isfinite(v) for v in values):
            return
        meas = getattr(m.header, "meas_stamp", None)
        source_ns = _stamp_ns(meas) if meas is not None and _stamp_ns(meas) else _stamp_ns(m.header.stamp)
        sample = Joints(dict(zip(names, values)), source_ns, time.monotonic_ns())
        receipt_wall = time.time_ns()
        with self.lock:
            self.joints[group] = sample
            if group == "arms":
                self.clock.add(source_ns, receipt_wall, sample.received_mono_ns)

    def _on_hands(self, m) -> None:
        inventory = (int(m.left_hand_type.value), int(m.right_hand_type.value), len(m.left_hands), len(m.right_hands))
        positions = [float(j.position) for j in (*m.left_hands, *m.right_hands)]
        with self.lock:
            self.hands = (inventory, positions, time.monotonic_ns())

    def _on_pmu(self, m) -> None:
        with self.lock:
            self.battery = int(m.battery_remaining_capacity_percentage)

    def _on_odom(self, m) -> None:
        t = m.twist.twist
        with self.lock:
            self.odom = (float(t.linear.x), float(t.linear.y), float(t.angular.z), time.monotonic_ns())

    # --- lectura -----------------------------------------------------------------------------------------------
    @staticmethod
    def _age(received_ns: int | None, now_ns: int) -> float | None:
        return None if received_ns is None else (now_ns - received_ns) / 1e9

    def observed(self, graph: bool = True) -> K.Observed:
        """Estado visto; ``graph=False`` no cuenta publicadores (consulta del grafo DDS, más lenta)."""
        now = time.monotonic_ns()
        with self.lock:
            mc, hands, joints, battery = self.mc, self.hands, dict(self.joints), self.battery
            offset = self.clock.estimate(now)["offset_ns"]
        obs = K.Observed(battery=battery, clock_offset_ms=None if offset is None else offset / 1e6)
        if mc is not None:
            obs.mode, obs.action_status, obs.input_source, obs.body_status, obs.player_state, obs.motion = mc[:6]
            obs.mc_age_s, obs.fsm = self._age(mc[6], now), mc[7]
            if mc[8] is not None:
                obs.squat_offset, obs.squat_enable = mc[8]
        for group, attr in (("arms", "arm_age_s"), ("head", "head_age_s")):
            if group in joints:
                setattr(obs, attr, self._age(joints[group].received_mono_ns, now))
        if hands is not None and self._age(hands[2], now) <= 0.5:
            obs.hands = hands[0]
        if graph:
            ours_upper = 1 if self.upper_pub is not None else 0
            ours_velocity = 1 if self.velocity_pub is not None else 0
            obs.upper_body_publishers = max(0, self.node.count_publishers(TOPIC_UPPER_BODY) - ours_upper)
            obs.velocity_publishers = max(0, self.node.count_publishers(TOPIC_VELOCITY) - ours_velocity)
        return obs

    def latest(self, group: str, max_age_s: float = K.FEEDBACK_TIMEOUT_S) -> dict[str, float] | None:
        """Últimas posiciones de ``arms`` o ``head`` si son frescas."""
        with self.lock:
            sample = self.joints.get(group)
        if sample is None or (time.monotonic_ns() - sample.received_mono_ns) / 1e9 > max_age_s:
            return None
        return dict(sample.positions)

    def hand_positions(self) -> list[float] | None:
        with self.lock:
            return None if self.hands is None else list(self.hands[1])

    def odometry(self) -> tuple[float, float, float] | None:
        with self.lock:
            odom = self.odom
        if odom is None or (time.monotonic_ns() - odom[3]) / 1e9 > 0.5:
            return None
        return odom[:3]

    # --- órdenes -----------------------------------------------------------------------------------------------
    def _header(self, stamp_mode: str):
        header = self.M.MessageHeader()
        if stamp_mode == "pc":
            ns = time.time_ns()
        elif stamp_mode == "robot":
            with self.lock:
                offset = self.clock.estimate(time.monotonic_ns())["offset_ns"]
            if offset is None:
                raise RuntimeError("sin hora del robot: no se sella la orden")
            ns = time.time_ns() + offset
        else:
            ns = 0          # sello 0: el MC usa su hora de recepción (configuración probada con los brazos)
        header.stamp.sec, header.stamp.nanosec = ns // 1_000_000_000, ns % 1_000_000_000
        header.sequence = self.sequence
        self.sequence += 1
        return header

    def publish_velocity(self, vx: float, vy: float, wz: float, stamp_mode: str) -> None:
        m = self.M.McLocomotionVelocity()
        m.header = self._header(stamp_mode)
        m.source = self.source
        m.forward_velocity, m.lateral_velocity, m.angular_velocity = float(vx), float(vy), float(wz)
        self.velocity_pub.publish(m)

    def publish_body_pose(self, stand_height_rate: float, stamp_mode: str) -> None:
        """Cuclilla: velocidad de altura (ver control.SQUAT_RATE); el resto de la postura a 0 (sin cambios)."""
        m = self.M.McBodyPose()
        m.header = self._header(stamp_mode)
        m.source = self.source
        m.stand_height = float(stand_height_rate)
        self.body_pose_pub.publish(m)

    def publish_upper_body(self, head: list[float], arms: list[float], hand_sub_mode: int, hand_pos: list[float],
                           stamp_mode: str) -> None:
        if len(head) != 2 or len(arms) != 14 or not all(math.isfinite(v) for v in (*head, *arms, *hand_pos)):
            raise ValueError("orden de tren superior mal formada")
        m = self.M.UpperBodyCommandArray()
        m.header = self._header(stamp_mode)
        m.header.frame_id = "mc_upper_body"
        m.source = self.source
        m.head_pos, m.arm_pos = [float(v) for v in head], [float(v) for v in arms]
        m.hand_sub_mode, m.hand_pos = int(hand_sub_mode), [float(v) for v in hand_pos]
        self.upper_pub.publish(m)

    # --- servicios ---------------------------------------------------------------------------------------------
    def send(self, client, make_request, name: str, *, attempts: int = 8, wait_s: float = 0.25, wait_service: float = 2.0):
        """Reenvía como los ejemplos de AimDK hasta que alguna copia tenga respuesta y devuelve esa respuesta entera,
        sin juzgarla. Puede haberse aplicado más de una copia: quien llama debe tolerarlo (ADD duplicado, modo ya
        pedido; los movimientos se piden sin ``interrupt`` para que el MC rechace la copia mientras reproduce)."""
        if not client.wait_for_service(timeout_sec=wait_service):
            raise RuntimeError(f"{name}: servicio no disponible")
        futures = []
        for _ in range(attempts):
            futures.append(client.call_async(make_request()))
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline and not any(f.done() for f in futures):
                time.sleep(0.005)
            done = next((f for f in futures if f.done()), None)
            if done is not None:
                break
        else:
            raise RuntimeError(f"{name}: sin respuesta tras {attempts} envíos")
        for f in futures:
            if not f.done():
                client.remove_pending_request(f)
        if done.exception() is not None:
            raise RuntimeError(f"{name}: {done.exception()}")
        return done.result()

    def client(self, srv_type, name: str):
        """Un cliente por servicio, reutilizado (uno nuevo tiene que volver a descubrirse)."""
        key = (srv_type, name)
        if key not in self._clients:
            self._clients[key] = self.node.create_client(srv_type, name)
        return self._clients[key]

    def _call(self, client, make_request, name: str, **kw):
        """``send`` + éxito como en SetMcAction/SetMcInputSource: ``header.code`` 0 y, si hay, ``status`` 1."""
        reply = self.send(client, make_request, name, **kw).response
        code = int(reply.header.code)
        status = getattr(getattr(reply, "status", None), "value", None)
        if code != 0 or (status is not None and int(status) != 1):
            message = getattr(reply, "message", "")
            raise RuntimeError(f"{name}: rechazado (code={code}, status={status}) {message}".strip())
        return reply

    def _source_request(self, action: int, priority: int):
        def make():
            req = self.S.SetMcInputSource.Request()
            req.request.header.stamp = self.node.get_clock().now().to_msg()
            req.action.value = action
            req.input_source.name = self.source
            req.input_source.priority = int(priority)
            req.input_source.timeout = 500
            return req
        return make

    def register_source(self, priority: int) -> None:
        if not K.MIN_PRIORITY <= priority <= K.MAX_PRIORITY:
            raise ValueError(f"prioridad {priority} fuera de {K.MIN_PRIORITY}..{K.MAX_PRIORITY} (mando 80)")
        self.registered = True     # un ADD aplicado con la respuesta perdida también necesita DELETE
        self.priority = priority
        try:
            self._call(self.input_source, self._source_request(SOURCE_ADD, priority), "SetMcInputSource ADD")
        except RuntimeError as add_error:
            try:
                self._call(self.input_source, self._source_request(SOURCE_MODIFY, priority), "SetMcInputSource MODIFY")
            except RuntimeError as exc:
                raise RuntimeError(f"{add_error}; {exc}") from exc

    def delete_source(self) -> str | None:
        if not self.registered:
            return None
        try:
            self._call(self.input_source, self._source_request(SOURCE_DELETE, self.priority), "SetMcInputSource DELETE")
            self.registered = False
            return None
        except RuntimeError as exc:
            return str(exc)

    def request_mode(self, desc: str, *, busy_retry_s: float = 0.0) -> None:
        """SetMcAction. Con ``busy_retry_s`` reintenta cada 2 s mientras el MC conteste "ocupado" (code 9): visto en
        el robot al pedir STAND_DEFAULT desde URS con un movimiento de la app recién reproducido (20/10)."""
        def make():
            req = self.S.SetMcAction.Request()
            req.header.stamp = self.node.get_clock().now().to_msg()
            req.source = self.source
            req.command.action_desc = desc
            return req
        deadline = time.monotonic() + busy_retry_s
        while True:
            reply = self.send(self.set_action, make, f"SetMcAction {desc}").response
            code, status = int(reply.header.code), int(reply.status.value)
            if code == 0 and status == 1:
                return
            error = ModeRejected(desc, code, status, str(reply.message))
            if code != CODE_MOTION_BUSY or time.monotonic() + 2.0 > deadline:
                raise error
            time.sleep(2.0)

    def wait_mode(self, desc: str, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            obs = self.observed(graph=False)
            if obs.mode == desc and obs.action_status == K.ACTION_RUNNING and obs.mc_age_s is not None \
                    and obs.mc_age_s <= K.MC_STATE_TIMEOUT_S:
                return True
            time.sleep(0.05)
        return False

    def wait_stable(self, timeout_s: float) -> bool:
        """Espera a que el MC esté en STABLE con su acción en RUNNING (sólo así acepta cambios de modo)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            obs = self.observed(graph=False)
            if obs.fsm == K.FSM_STABLE and obs.action_status == K.ACTION_RUNNING and obs.mc_age_s is not None \
                    and obs.mc_age_s <= K.MC_STATE_TIMEOUT_S:
                return True
            time.sleep(0.05)
        return False

    def wait_ready(self, timeout_s: float = 4.0) -> None:
        """Espera a tener estado del MC, brazos y reloj (o a que pase el tiempo)."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            obs = self.observed(graph=False)
            if obs.mc_age_s is not None and obs.clock_offset_ms is not None and obs.battery is not None \
                    and obs.hands is not None:
                return
            time.sleep(0.1)

    def close(self) -> None:
        self._running = False
        self._spin.join(timeout=1.0)
        try:
            self.node.destroy_node()
        finally:
            self.rclpy.try_shutdown()
