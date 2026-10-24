#!/usr/bin/env python3
"""Prueba de URS en el gemelo, dentro del contenedor aislado.

El avance se mide durante la orden de marcha; la deriva al parar va aparte.
El modo MC real se lee directamente en DDS 77, no del shim público.

    twin.sh urs-test --pose reposo --mano abierta
    twin.sh urs-test --pose al_frente --mano agarre --referencia-reposo-m 2.4
    twin.sh urs-test --pose reposo --pose-durante al_frente --referencia-reposo-m 2.4
"""
from __future__ import annotations

import argparse
import json
import math
import threading
import time

import numpy as np
import rclpy
from aimdk_msgs.msg import (HandStateArray, JointStateArray, McActionCommand,
                            McCommonState, MessageHeader, RequestHeader,
                            UpperBodyCommandArray)
from aimdk_msgs.srv import SetMcAction
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data

from keyboard_teleop import Teleop, require_isolated_sim
from x2_ultra.control.poses import ARM_POSES
from x2_ultra.robot import omnihand as H
from x2_ultra.twin.shared import SharedState, TWIN_SHM_NAME

MC_DOMAIN_ID = 77
PERIOD_S = 0.02


def set_mode(node, client, name: str) -> tuple[bool, str]:
    req = SetMcAction.Request()
    req.header = RequestHeader()
    req.source = "x2_twin_urs_selftest"
    req.command = McActionCommand(action_desc=name)
    if not client.wait_for_service(timeout_sec=10.0):
        return False, "servicio no disponible"
    fut = client.call_async(req)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=10.0)
    res = fut.result() if fut.done() else None
    if res is None:
        return False, "sin respuesta"
    return res.response.status.value == 1, res.response.message


def tilt_deg(q) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (q.x ** 2 + q.y ** 2)))))


def yaw_rad(q) -> float:
    return math.atan2(2.0 * (q.w * q.z + q.x * q.y),
                      1.0 - 2.0 * (q.y ** 2 + q.z ** 2))


def rounded(value: float | None, digits: int = 6) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def acceptance(report: dict, *, standard_forward: bool, needs_rest: bool,
               rest_reference: float | None, expected_mode: str | None) -> dict[str, bool]:
    """Los datos ausentes fallan; la comparación de reposo es obligatoria en poses no neutras."""
    arm = report.get("error_brazos_max_andando_rad")
    hand = report.get("error_manos_max_andando_rad")
    height = report.get("altura_min_pelvis_m")
    tilt = report.get("inclinacion_max_grados")
    checks = {
        "urs_aceptado": bool(report.get("urs_aceptado")),
        "fuente_registrada": bool(report.get("fuente_registrada")),
        "modo_mc_privado_recibido": report.get("muestras_modo_mc_privado_andando", 0) > 0,
        "odometria_durante_orden": report.get("muestras_odom_andando", 0) >= 2,
        "odometria_fresca": report.get("edad_odom_inicio_s") is not None
                           and report["edad_odom_inicio_s"] <= 0.2
                           and report.get("edad_odom_fin_s") is not None
                           and report["edad_odom_fin_s"] <= 0.2,
        "duracion_orden": report.get("duracion_orden_s", 0) >= report.get("segundos_solicitados", 0) - 0.05,
        "sin_caidas": report.get("caidas", 1) == 0,
        "altura_pelvis": height is not None and height > 0.45,
        "inclinacion": tilt is not None and tilt < 30.0,
        "seguimiento_brazos": arm is not None and arm <= 0.10,
        "seguimiento_manos": hand is not None and hand <= 0.10,
    }
    if standard_forward:
        advance = report.get("avance_durante_orden_m")
        checks["avance_minimo"] = advance is not None and advance >= 1.5
        if needs_rest:
            checks["referencia_reposo_disponible"] = rest_reference is not None and rest_reference > 0
            checks["avance_relativo_reposo"] = (advance is not None and rest_reference is not None
                                                 and advance >= 0.70 * rest_reference)
    if expected_mode:
        checks["modo_mc_esperado"] = expected_mode in report.get("modos_mc_privados_andando", [])
    if report.get("pose_durante"):
        checks["cambio_pose_ejecutado"] = report.get("cambio_pose_en_s") is not None
        checks["seguimiento_pose_durante"] = (report.get("muestras_brazos_tras_cambio", 0) > 0
                                             and report.get("error_brazos_tras_cambio_rad") is not None
                                             and report["error_brazos_tras_cambio_rad"] <= 0.10)
    return checks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pose", default="al_frente", choices=sorted(ARM_POSES))
    ap.add_argument("--mano", default="agarre", choices=sorted(H.HAND_POSES))
    ap.add_argument("--segundos", type=float, default=5.0, help="tiempo andando")
    ap.add_argument("--vx", type=float, default=0.4)
    ap.add_argument("--teclas", default="w", help="teclas mantenidas: w, s, a, d, q, e (p. ej. wq)")
    ap.add_argument("--de-pie", type=float, default=3.0, help="segundos con la pose antes de andar")
    ap.add_argument("--referencia-reposo-m", type=float, help="avance de reposo del mismo perfil")
    ap.add_argument("--pose-durante", choices=sorted(ARM_POSES), help="cambiar a esta pose a mitad de marcha")
    ap.add_argument("--asentamiento-cambio-s", type=float, default=1.7,
                    help="rampa excluida del error de brazos; estabilidad evaluada todo el tiempo")
    ap.add_argument("--modo-mc-esperado", help="exigir este modo MC real en DDS 77 durante la marcha")
    ap.add_argument("--solo-medicion", action="store_true", help="informar sin imponer código de salida")
    args = ap.parse_args()
    if args.segundos <= 0 or args.de_pie < 0 or args.asentamiento_cambio_s < 0:
        ap.error("--segundos debe ser positivo; --de-pie y --asentamiento-cambio-s no negativos")
    if args.referencia_reposo_m is not None and args.referencia_reposo_m <= 0:
        ap.error("--referencia-reposo-m debe ser positiva")
    if any(k not in "wsadqe" for k in args.teclas):
        ap.error("--teclas admite solo w, s, a, d, q, e")

    require_isolated_sim()
    rclpy.init()
    mc_ctx = rclpy.Context()
    rclpy.init(context=mc_ctx, domain_id=MC_DOMAIN_ID)
    node = Teleop({"vx": args.vx, "vx_back": 0.3, "vy": 0.5, "wz": 0.4, "run": 1.8})
    mc_node = Node("x2_urs_selftest_mc_real", context=mc_ctx)
    lock = threading.Lock()
    state = {"arm": None, "hand": None, "odom": None, "odom_at": None,
             "mc_mode": None, "mc_mode_at": None, "mc_modes_walk": [], "mc_samples_walk": 0}
    walk = {"active": False, "z_min": None, "tilt_max": None, "falls": 0,
            "was_fallen": False, "odom_samples": 0, "arm_samples": 0, "hand_samples": 0,
            "arm_samples_total": 0,
            "arm_error_max": None, "arm_error_raw_max": None, "hand_error_max": None,
            "tracking_after": 0.0, "change_issued": False, "arm_samples_after_change": 0,
            "arm_error_after_change": None, "last_command_at": None, "max_command_interval": None}
    arm_target = np.asarray(ARM_POSES[args.pose].target(0.0), dtype=float)
    hand_target = np.concatenate([H.to_radians("L", H.HAND_POSES[args.mano]),
                                  H.to_radians("R", H.HAND_POSES[args.mano])])
    sequence = [0]

    def on_arm(msg):
        actual = np.asarray([j.position for j in msg.joints], dtype=float)
        with lock:
            state["arm"] = actual
            if walk["active"]:
                walk["arm_samples_total"] += 1
            if walk["active"] and actual.shape == arm_target.shape:
                error = float(np.max(np.abs(actual - arm_target)))
                walk["arm_error_raw_max"] = max(walk["arm_error_raw_max"] or 0.0, error)
                if time.monotonic() >= walk["tracking_after"]:
                    walk["arm_samples"] += 1
                    walk["arm_error_max"] = max(walk["arm_error_max"] or 0.0, error)
                    if walk["change_issued"]:
                        walk["arm_samples_after_change"] += 1
                        walk["arm_error_after_change"] = max(walk["arm_error_after_change"] or 0.0, error)

    def on_hand(msg):
        actual = np.asarray([h.position for h in list(msg.left_hands) + list(msg.right_hands)], dtype=float)
        with lock:
            state["hand"] = actual
            if walk["active"] and actual.shape == hand_target.shape:
                error = float(np.max(np.abs(actual - hand_target)))
                walk["hand_samples"] += 1
                walk["hand_error_max"] = max(walk["hand_error_max"] or 0.0, error)

    def on_odom(msg):
        q, p = msg.pose.pose.orientation, msg.pose.pose.position
        sample = (float(p.x), float(p.y), float(p.z), tilt_deg(q), yaw_rad(q))
        with lock:
            state["odom"] = sample
            state["odom_at"] = time.monotonic()
            if walk["active"]:
                walk["odom_samples"] += 1
                z, tilt = sample[2], sample[3]
                walk["z_min"] = z if walk["z_min"] is None else min(walk["z_min"], z)
                walk["tilt_max"] = tilt if walk["tilt_max"] is None else max(walk["tilt_max"], tilt)
                fallen = z < 0.40 or tilt > 57.0
                if fallen and not walk["was_fallen"]:
                    walk["falls"] += 1
                walk["was_fallen"] = fallen

    def on_mc_state(msg):
        mode = msg.action_info.action_desc
        with lock:
            state["mc_mode"] = mode
            state["mc_mode_at"] = time.monotonic()
            if walk["active"]:
                state["mc_samples_walk"] += 1
                if not state["mc_modes_walk"] or state["mc_modes_walk"][-1] != mode:
                    state["mc_modes_walk"].append(mode)

    node.create_subscription(JointStateArray, "/aima/hal/joint/arm/state", on_arm, qos_profile_sensor_data)
    node.create_subscription(HandStateArray, "/aima/hal/joint/hand/state", on_hand, qos_profile_sensor_data)
    node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, qos_profile_sensor_data)
    mc_node.create_subscription(McCommonState, "/aima/mc/common/state", on_mc_state,
                                QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT))
    upper_pub = node.create_publisher(UpperBodyCommandArray, "/mc/upper_body_command",
                                      QoSProfile(depth=10, reliability=ReliabilityPolicy.BEST_EFFORT))
    mode_client = node.create_client(SetMcAction, "/aimdk_5Fmsgs/srv/SetMcAction")
    report = {"pose": args.pose, "pose_durante": args.pose_durante, "mano": args.mano,
              "teclas": args.teclas, "vx_solicitado_m_s": args.vx,
              "segundos_solicitados": args.segundos, "referencia_reposo_m": args.referencia_reposo_m,
              "dominio_mc_privado": MC_DOMAIN_ID}
    registered = False
    shared = None
    public_executor = private_executor = None
    public_thread = private_thread = None

    def fail(reason: str) -> int:
        report.update(error=reason, aprobado=False, criterios={"preparacion": False})
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return 0 if args.solo_medicion else 2

    def publish_upper() -> None:
        msg = UpperBodyCommandArray()
        now = node.get_clock().now().nanoseconds
        msg.header = MessageHeader()
        msg.header.stamp.sec, msg.header.stamp.nanosec = now // 1_000_000_000, now % 1_000_000_000
        msg.header.sequence = sequence[0]
        sequence[0] += 1
        msg.source = "x2_twin_urs_selftest"
        msg.hand_sub_mode = UpperBodyCommandArray.HAND_DEXTEROUS_JOINT
        msg.head_pos = [0.0, 0.0]
        with lock:
            msg.arm_pos = arm_target.tolist()
        msg.hand_pos = hand_target.tolist()
        upper_pub.publish(msg)

    def run_phase(seconds: float, keys: set[str], *, change_pose: str | None = None) -> float:
        nonlocal arm_target
        node.set_from_keys(keys)
        t0 = time.monotonic()
        next_tick = t0
        changed = False
        while time.monotonic() - t0 < seconds:
            now = time.monotonic()
            if change_pose and not changed and now - t0 >= seconds / 2:
                with lock:
                    arm_target = np.asarray(ARM_POSES[change_pose].target(0.0), dtype=float)
                    walk["tracking_after"] = now + args.asentamiento_cambio_s
                    walk["change_issued"] = True
                report["cambio_pose_en_s"] = rounded(now - t0)
                changed = True
            publish_upper()
            node.publish()  # publica también velocidad cero al parar, sin esperar el timeout del MC
            with lock:
                if walk["active"]:
                    previous = walk["last_command_at"]
                    if previous is not None:
                        interval = now - previous
                        walk["max_command_interval"] = max(walk["max_command_interval"] or 0.0, interval)
                    walk["last_command_at"] = now
            next_tick += PERIOD_S
            time.sleep(max(0.0, next_tick - time.monotonic()))
            with lock:
                if walk["active"] and walk["falls"]:
                    break
        return time.monotonic() - t0

    try:
        try:
            # Solo se leen la secuencia y el tiempo de la cabecera; no hace falta cargar el modelo MuJoCo.
            shared = SharedState(0, name=TWIN_SHM_NAME)
        except FileNotFoundError:
            return fail("no existe la memoria compartida del gemelo")
        deadline = time.monotonic() + 10.0
        ready = private_ready = False
        while time.monotonic() < deadline:
            rclpy.spin_once(node, timeout_sec=0.01)
            rclpy.spin_once(mc_node, timeout_sec=0.01)
            with lock:
                ready = all(state[k] is not None for k in ("arm", "hand", "odom"))
                private_ready = state["mc_mode"] is not None
            if ready and private_ready:
                break
        with lock:
            initial_odom, initial_mode = state["odom"], state["mc_mode"]
        report["modo_mc_privado_inicio"] = initial_mode
        if initial_odom is None or initial_odom[2] < 0.5:
            return fail("robot no está de pie: twin.sh reset && twin.sh stand && twin.sh suelta")
        if not ready:
            return fail("faltan estados HAL de brazo, mano u odometría")
        if not private_ready:
            return fail("no llega /aima/mc/common/state del MC real en DDS 77")

        ok, message = set_mode(node, mode_client, "UPPERBODY_REMOTE_SPLIT")
        node.poll_mode()
        report.update(urs_aceptado=ok, mensaje=message, modo_GetMcAction=node.mode)
        if not ok:
            return fail("SetMcAction(UPPERBODY_REMOTE_SPLIT) rechazado")
        registered = node.register()
        report["fuente_registrada"] = registered
        if not registered:
            return fail("no se pudo registrar la fuente de locomoción")

        public_executor = SingleThreadedExecutor()
        public_executor.add_node(node)
        private_executor = SingleThreadedExecutor(context=mc_ctx)
        private_executor.add_node(mc_node)
        public_thread = threading.Thread(target=public_executor.spin, daemon=True)
        private_thread = threading.Thread(target=private_executor.spin, daemon=True)
        public_thread.start()
        private_thread.start()

        run_phase(args.de_pie, set())
        with lock:
            actual = state["arm"]
            report["error_brazos_de_pie_rad"] = rounded(
                float(np.max(np.abs(actual - arm_target))) if actual is not None
                and actual.shape == arm_target.shape else None)
            start = state["odom"]
            start_age = None if state["odom_at"] is None else time.monotonic() - state["odom_at"]
            walk["active"] = True
            walk["tracking_after"] = 0.0
        sim_start_snapshot = shared.read()
        commands_before = sequence[0]
        duration = run_phase(args.segundos, set(args.teclas), change_pose=args.pose_durante)
        sim_end_snapshot = shared.read()
        commands_walk = sequence[0] - commands_before
        with lock:
            walk["active"] = False
            end = state["odom"]
            end_age = None if state["odom_at"] is None else time.monotonic() - state["odom_at"]
            mode_end_walk = state["mc_mode"]
            metrics = walk.copy()
            modes_walk = list(state["mc_modes_walk"])
            mode_samples = state["mc_samples_walk"]
        run_phase(2.0, set())
        with lock:
            stopped = state["odom"]
            final_mode = state["mc_mode"]
            actual_arm, actual_hand = state["arm"], state["hand"]
            final_arm_target = arm_target.copy()

        distance = math.dist(start[:2], end[:2]) if start and end else None
        post_stop = math.dist(end[:2], stopped[:2]) if end and stopped else None
        forward = ((end[0] - start[0]) * math.cos(start[4])
                   + (end[1] - start[1]) * math.sin(start[4])) if start and end else None
        sim_start = sim_start_snapshot[1] if sim_start_snapshot is not None else None
        sim_end = sim_end_snapshot[1] if sim_end_snapshot is not None else None
        rtf = ((sim_end - sim_start) / duration
               if sim_start is not None and sim_end is not None and duration > 0 else None)
        report.update({
            "duracion_orden_s": rounded(duration),
            "tiempo_simulado_inicio_s": rounded(sim_start),
            "tiempo_simulado_fin_s": rounded(sim_end),
            "factor_tiempo_real": rounded(rtf),
            "ordenes_upper_y_velocidad": commands_walk,
            "frecuencia_ordenes_hz": rounded(commands_walk / duration if duration > 0 else None),
            "intervalo_max_orden_s": rounded(metrics["max_command_interval"]),
            "frecuencia_odom_hz": rounded(metrics["odom_samples"] / duration if duration > 0 else None),
            "frecuencia_brazos_hz": rounded(metrics["arm_samples_total"] / duration if duration > 0 else None),
            "frecuencia_manos_hz": rounded(metrics["hand_samples"] / duration if duration > 0 else None),
            "frecuencia_modo_mc_privado_hz": rounded(mode_samples / duration if duration > 0 else None),
            "error_brazos_andando_rad": rounded(metrics["arm_error_max"]),
            "error_brazos_max_andando_rad": rounded(metrics["arm_error_max"]),
            "error_brazos_max_incluyendo_transicion_rad": rounded(metrics["arm_error_raw_max"]),
            "error_manos_max_andando_rad": rounded(metrics["hand_error_max"]),
            "error_brazos_final_rad": rounded(float(np.max(np.abs(actual_arm - final_arm_target)))
                                              if actual_arm is not None and actual_arm.shape == final_arm_target.shape
                                              else None),
            "error_manos_final_rad": rounded(float(np.max(np.abs(actual_hand - hand_target)))
                                             if actual_hand is not None and actual_hand.shape == hand_target.shape
                                             else None),
            "desplazamiento_m": rounded(distance),
            "avance_durante_orden_m": rounded(forward),
            "desplazamiento_post_parada_m": rounded(post_stop),
            "inclinacion_max_grados": rounded(metrics["tilt_max"]),
            "altura_min_pelvis_m": rounded(metrics["z_min"]),
            "caidas": metrics["falls"],
            "muestras_odom_andando": metrics["odom_samples"],
            "muestras_brazos_evaluadas": metrics["arm_samples"],
            "muestras_brazos_totales": metrics["arm_samples_total"],
            "muestras_brazos_tras_cambio": metrics["arm_samples_after_change"],
            "error_brazos_tras_cambio_rad": rounded(metrics["arm_error_after_change"]),
            "muestras_manos_andando": metrics["hand_samples"],
            "edad_odom_inicio_s": rounded(start_age),
            "edad_odom_fin_s": rounded(end_age),
            "modo_mc_privado_fin_orden": mode_end_walk,
            "modo_mc_privado_final": final_mode,
            "modos_mc_privados_andando": modes_walk,
            "muestras_modo_mc_privado_andando": mode_samples,
            "de_pie": metrics["z_min"] is not None and metrics["z_min"] > 0.45
                      and metrics["tilt_max"] is not None and metrics["tilt_max"] < 30.0,
        })
        warnings = []
        if rtf is None:
            warnings.append("sin lectura coherente del tiempo simulado")
        elif rtf < 0.90:
            warnings.append("factor de tiempo real inferior a 0,90")
        if commands_walk / duration < 40.0:
            warnings.append("órdenes upper/velocidad por debajo de 40 Hz")
        if metrics["max_command_interval"] is not None and metrics["max_command_interval"] >= 0.2:
            warnings.append("intervalo entre órdenes alcanzó el timeout de velocidad del MC (200 ms)")
        report["advertencias"] = warnings
        standard_forward = args.teclas == "w" and math.isclose(args.vx, 0.4, abs_tol=1e-6) \
            and math.isclose(args.segundos, 5.0, abs_tol=1e-6)
        needs_rest = args.pose != "reposo" or args.pose_durante is not None
        report["criterios"] = acceptance(report, standard_forward=standard_forward,
                                         needs_rest=needs_rest, rest_reference=args.referencia_reposo_m,
                                         expected_mode=args.modo_mc_esperado)
        report["aprobado"] = all(report["criterios"].values())
        print(json.dumps(report, ensure_ascii=False, allow_nan=False))
        return 0 if report["aprobado"] or args.solo_medicion else 2
    finally:
        # Teleop.unregister() hace una llamada ROS bloqueante; no compartir su nodo con un ejecutor activo.
        if public_executor is not None:
            public_executor.shutdown(timeout_sec=2.0)
            if public_thread is not None:
                public_thread.join(timeout=2.0)
            public_executor.remove_node(node)
        if private_executor is not None:
            private_executor.shutdown(timeout_sec=2.0)
            if private_thread is not None:
                private_thread.join(timeout=2.0)
            private_executor.remove_node(mc_node)
        if registered:
            node.set_from_keys(set())
            for _ in range(10):
                node.publish()
                time.sleep(PERIOD_S)
            node.unregister()
        if shared is not None:
            shared.close()
        node.destroy_node()
        mc_node.destroy_node()
        rclpy.shutdown()
        rclpy.shutdown(context=mc_ctx)


if __name__ == "__main__":
    raise SystemExit(main())
