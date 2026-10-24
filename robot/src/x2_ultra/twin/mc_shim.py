"""Puente entre el dominio DDS público (programas del usuario) y el privado del MC oficial.

Por qué dos dominios: para emular UPPERBODY_REMOTE_SPLIT hay que atender ``SetMcAction``/``GetMcAction`` con sus
nombres públicos, pero el MC también los sirve y el plugin ROS 2 de su runtime (AimRT 0.8.24, fork de AgiBot) no
aplica ``remapping_rule`` a los servidores (llegó en AimRT 0.9). Con el MC en su propio dominio (``MC_DOMAIN_ID``):

  - la HAL se publica en los dos dominios (``TwinHal.attach``): los 500 Hz no pasan por ningún puente;
  - los topics y servicios del MC que usan los programas se reenvían (tablas de abajo);
  - ``SetMcAction``/``GetMcAction`` los atiende el gemelo: por defecto URS y HEAD_ONLY se emulan (``twin.urs``)
    y el resto se le pide al MC; con ``--sin-urs`` todas las acciones se reenvían al MC sin alterar la respuesta;
  - ``/mc/upper_body_command`` se aplica localmente en modo emulado y se reenvía al MC en modo nativo.
"""
from __future__ import annotations

import threading
import time

from aimdk_msgs.msg import CommonState, McCommonState, UpperBodyCommandArray
from aimdk_msgs.srv import GetMcAction, SetMcAction
from builtin_interfaces.msg import Time as TimeMsg
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node
from rosidl_runtime_py.utilities import get_message, get_service

from x2_ultra.twin import contract as C
from x2_ultra.twin.ros_hal import qos
from x2_ultra.twin.urs import HEAD_ONLY, URS, UpperBodyEmulator

MC_DOMAIN_ID = 77
BE, REL, VOL, TL = C.BEST_EFFORT, C.RELIABLE, C.VOLATILE, C.TRANSIENT_LOCAL

# (topic, tipo, QoS del lado que recibe, QoS del lado que publica)
MC_TOPICS_OUT = (                        # MC -> público
    ("/aima/mc/leg_odometry", "nav_msgs/msg/Odometry", (BE, VOL, 10), (BE, TL, 1)),
    ("/tf", "tf2_msgs/msg/TFMessage", (REL, VOL, 100), (REL, VOL, 100)),
    ("/tf_static", "tf2_msgs/msg/TFMessage", (REL, TL, 10), (REL, TL, 10)),
)
MC_TOPICS_IN = (                         # público -> MC
    ("/aima/mc/locomotion/velocity", "aimdk_msgs/msg/McLocomotionVelocity", (BE, VOL, 10), (BE, VOL, 10)),
    ("/aima/mc/body_pose", "aimdk_msgs/msg/McBodyPose", (BE, VOL, 10), (BE, VOL, 10)),
    ("/aima/teleop_bridge/vr_data", "aimdk_msgs/msg/VRData", (BE, VOL, 10), (BE, VOL, 10)),
    ("/aima/mc/joint/retargeting", "aimdk_msgs/msg/McRetargetFrame", (BE, VOL, 10), (BE, VOL, 10)),
)
MC_SERVICES = ("ActivateMcSecureAction", "GetCurrentInputSource", "GetMcLocomotionSpeedStatus", "GetMcMotions",
               "GetMcPresetMotionState", "GetRobotRuntimeModel", "RegisterCustomMotion", "SetMcInputSource",
               "SetMcLocomotionSpeedMode", "SetMcMotion", "SetMcMotionSeq", "SetMcMotionStreaming",
               "SetMcPresetMotion", "SetMcSecureMotion", "SetRobotRuntimeModel")
COMMON_STATE_TOPIC = "/aima/mc/common/state"
MC_KEEPS_EMULATION = ("STAND_DEFAULT", "LOCOMOTION_DEFAULT")


def _interface(getter, name: str):
    """Tipo del SDK o None: el aimdk_msgs público no trae algunos tipos internos del MC (McBodyPose, VRData…),
    así que tampoco un programa de usuario podría usarlos."""
    try:
        return getter(name)
    except (AttributeError, ModuleNotFoundError, ValueError):
        return None


def _call(client, request, timeout: float = 3.0):
    """Llamada bloqueante a un servicio de otro dominio (su ejecutor, en otro hilo, completa el futuro)."""
    if not client.wait_for_service(timeout_sec=timeout):
        return None
    fut = client.call_async(request)
    done = threading.Event()
    fut.add_done_callback(lambda _f: done.set())
    if not done.wait(timeout):
        client.remove_pending_request(fut)
        return None
    return fut.result()


def _now() -> TimeMsg:
    t = time.time()
    return TimeMsg(sec=int(t), nanosec=int((t % 1.0) * 1e9))


class UpperBodyShim(Node):
    def __init__(self, hal, mc_node: Node, emulate: bool = True):
        super().__init__("x2_twin_mc_shim")
        self.hal, self.mc_node = hal, mc_node
        self.emu = UpperBodyEmulator(enabled=emulate)
        if emulate:
            hal.overrides.append(self.emu)
        group = ReentrantCallbackGroup()

        # --- topics -------------------------------------------------------------------------------
        skipped = []
        for out, table in ((True, MC_TOPICS_OUT), (False, MC_TOPICS_IN)):
            for topic, type_name, rx_qos, tx_qos in table:
                msg_type = _interface(get_message, type_name)
                if msg_type is None:
                    skipped.append(topic)
                    continue
                rx, tx = (mc_node, self) if out else (self, mc_node)
                pub = tx.create_publisher(msg_type, topic, qos(tx_qos))
                rx.create_subscription(msg_type, topic, pub.publish, qos(rx_qos), raw=True)
        self.state_pub = self.create_publisher(McCommonState, COMMON_STATE_TOPIC, qos((BE, TL, 1)))
        mc_node.create_subscription(McCommonState, COMMON_STATE_TOPIC, self._on_common_state, qos((BE, VOL, 10)))

        # --- servicios del MC ----------------------------------------------------------------------
        for name in MC_SERVICES:
            srv = _interface(get_service, f"aimdk_msgs/srv/{name}")
            if srv is None:
                skipped.append(name)
                continue
            client = mc_node.create_client(srv, C.service(name))
            self.create_service(srv, C.service(name), self._forwarder(client), callback_group=group)
        if skipped:
            self.get_logger().info(f"sin puente (tipos que no están en el aimdk_msgs del SDK): {', '.join(skipped)}")
        self.mc_set = mc_node.create_client(SetMcAction, C.service("SetMcAction"))
        self.mc_get = mc_node.create_client(GetMcAction, C.service("GetMcAction"))
        self.create_service(SetMcAction, C.service("SetMcAction"), self._set_action, callback_group=group)
        self.create_service(GetMcAction, C.service("GetMcAction"), self._get_action, callback_group=group)

        # --- tren superior: la física solo se modifica en modo emulado ------------------------------
        if emulate:
            self.create_subscription(UpperBodyCommandArray, C.UPPER_BODY_TOPIC, self._on_upper_body,
                                     qos(C.UPPER_BODY_QOS))
        else:
            self.upper_body_pub = mc_node.create_publisher(UpperBodyCommandArray, C.UPPER_BODY_TOPIC,
                                                           qos(C.UPPER_BODY_QOS))
            self.create_subscription(UpperBodyCommandArray, C.UPPER_BODY_TOPIC, self.upper_body_pub.publish,
                                     qos(C.UPPER_BODY_QOS), raw=True)

    # ------------------------------------------------------------------------------------------
    def _forwarder(self, client):
        def forward(request, response):
            result = _call(client, request)
            return result if result is not None else response
        return forward

    def _mc_action(self) -> str | None:
        result = _call(self.mc_get, GetMcAction.Request())
        return None if result is None else result.info.action_desc

    def _set_action(self, request, response):
        desc = request.command.action_desc
        r = response.response
        r.header.stamp = _now()
        if self.emu.enabled and desc in (URS, HEAD_ONLY):
            current = self._mc_action()
            if current == "STAND_DEFAULT":
                with self.hal.lock:
                    self.emu.set_mode(desc)
                r.status.value, r.message = CommonState.SUCCESS, f"{desc} (emulado por el gemelo)"
                self.get_logger().info(f"modo {desc} emulado (fuente {request.source!r})")
            else:
                r.status.value = CommonState.FAILURE
                r.message = f"{desc} requiere STAND_DEFAULT; el MC está en {current}"
            return response
        result = _call(self.mc_set, request)
        if result is None:
            r.status.value, r.message = CommonState.FAILURE, "el MC no responde"
            return response
        if result.response.status.value == CommonState.SUCCESS and self.emu.mode is not None:
            with self.hal.lock:
                self.emu.set_mode(None)
            self.get_logger().info(f"fin del modo emulado: {desc}")
        return result

    def _get_action(self, request, response):
        result = _call(self.mc_get, request)
        if result is None:
            return response
        if self.emu.mode is not None:
            result.info.action_desc = self.emu.mode
            result.info.current_action.value = 0
        return result

    def _on_common_state(self, msg: McCommonState) -> None:
        if self.emu.mode is not None:
            # Con velocidad, el MC v1.0.0 pasa solo de STAND_DEFAULT a LOCOMOTION_DEFAULT y vuelve al parar: el modo
            # emulado sigue en los dos. Cualquier otro (amortiguamiento, caída, otro modo) lo termina.
            if msg.action_info.action_desc not in MC_KEEPS_EMULATION:
                with self.hal.lock:
                    self.emu.set_mode(None)
                self.get_logger().warn(f"el MC pasó a {msg.action_info.action_desc}: fin del modo emulado")
            else:
                msg.action_info.action_desc = self.emu.mode
        self.state_pub.publish(msg)

    def _on_upper_body(self, msg: UpperBodyCommandArray) -> None:
        st = msg.header.stamp
        age = None if (st.sec == 0 and st.nanosec == 0) else time.time() - (st.sec + st.nanosec * 1e-9)
        with self.hal.lock:
            self.emu.on_command(msg.hand_sub_mode, msg.head_pos, msg.arm_pos, msg.hand_pos, age)
