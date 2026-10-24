"""Comprobación ROS de SOLO LECTURA para el deploy con actuación (la usa deploy/x2_listo.sh).

Crea un nodo sin publicadores ni clientes, espera al descubrimiento DDS y comprueba que el robot
publica lo que necesitan AimdkBackend y el runner conversacional, que nadie más está mandando
brazos o velocidad, y que los tipos de aimdk_msgs que usa el backend existen en la imagen.

También mira (AVISO, nunca fallo) la cara: tipo aimdk_msgs.srv.PlayEmoji y servicio /aimdk_5Fmsgs/srv/PlayEmoji (PC3); sin él la cara no cambia y HRI_EXPRESIONES se desactiva sola.

Sale con 0 si todo lo obligatorio está; 1 si falta algo obligatorio.
"""
from __future__ import annotations

import re
import sys
import time

REQUIRED = {
    "/aima/mc/common/state": "estado del MC (modo, fsm, acción)",
    "/aima/hal/joint/arm/state": "brazos medidos (limitador y preflight)",
    "/aima/hal/imu/torso/state": "inclinación (interlock de caída)",
    "/aima/mc/leg_odometry": "altura relativa (interlock de caída)",
    "/aima/hal/audio/capture": "micrófono",
}
OPTIONAL = {
    "/aima/hal/odom/state": "pose del HAL (v1.1.4 no la publica; se usa IMU + leg_odometry)",
    "/aima/hal/joint/head/state": "cabeza medida",
    "/aima/hal/joint/waist/state": "cintura medida",
    "/aima/hal/joint/hand/state": "manos",
    "/aima/hal/pmu/state": "batería",
}
COMMAND_TOPICS = ("/mc/upper_body_command", "/aima/mc/locomotion/velocity", "/aima/mc/body_pose")
# Nodos del sistema de AgiBot con publicador permanente (app, mando, interacción, planificador).
SYSTEM_PUBLISHERS = re.compile(r"^(app_proxy|soc\d+_rc\d*|soc\d+_interaction_slave\d*|aimrt_planning_planning_node\d*)$")
MSG_TYPES = ("McBodyPose", "JointCommandArray", "JointStateArray", "AudioPlayback")
SRV_TYPES = ("SetMcInputSource", "SetMcAction", "SetMcPresetMotion")


def main() -> int:
    ok = True
    try:
        from aimdk_msgs import msg as AM, srv as AS
    except Exception as exc:  # noqa: BLE001
        print(f"FALLO  aimdk_msgs no importa: {exc}")
        return 1
    for name in MSG_TYPES:
        has = hasattr(AM, name)
        print(f"{'ok   ' if has else 'AVISO'}  aimdk_msgs.msg.{name}"
              + ("" if has else "  (sin él: McBodyPose ⇒ la cabeza no gira; JointCommandArray ⇒ sin canal hal)"))
    for name in SRV_TYPES:
        has = hasattr(AS, name)
        ok &= has or name == "SetMcPresetMotion"
        print(f"{'ok   ' if has else 'FALLO'}  aimdk_msgs.srv.{name}")
    has = hasattr(AS, "PlayEmoji")     # cara: AVISO, nunca fallo (HRI_EXPRESIONES se desactiva sola)
    print(f"{'ok   ' if has else 'AVISO'}  aimdk_msgs.srv.PlayEmoji"
          + ("" if has else "  (sin él la cara no cambia; HRI_EXPRESIONES se desactiva sola)"))

    import rclpy
    rclpy.init()
    node = rclpy.create_node("hri_club_check_readonly")
    try:
        time.sleep(4.0)                     # descubrimiento DDS
        topics = dict(node.get_topic_names_and_types())
        for t, why in REQUIRED.items():
            n = node.count_publishers(t) if t in topics else 0
            ok &= n > 0
            print(f"{'ok   ' if n else 'FALLO'}  {t}  ({why}; {n} publicador(es))")
        for t, why in OPTIONAL.items():
            n = node.count_publishers(t) if t in topics else 0
            print(f"{'ok   ' if n else 'aviso'}  {t}  ({why}; {n} publicador(es))")
        for t in COMMAND_TOPICS:
            names = sorted({i.node_name for i in node.get_publishers_info_by_topic(t)})
            ajenos = [n for n in names if not SYSTEM_PUBLISHERS.match(n)]
            ok &= not ajenos
            print(f"{'ok   ' if not ajenos else 'FALLO'}  {t}  (ajenos: {ajenos or 'ninguno'}; "
                  f"sistema AgiBot: {[n for n in names if n not in ajenos] or 'ninguno'})")
        srvs = dict(node.get_service_names_and_types())
        n = len(srvs.get("/aimdk_5Fmsgs/srv/PlayEmoji", []))   # cara: AVISO, nunca fallo
        print(f"{'ok   ' if n else 'AVISO'}  /aimdk_5Fmsgs/srv/PlayEmoji  "
              f"(pantalla de la cara, PC3; {n} servidor(es))")
    finally:
        node.destroy_node()
        rclpy.shutdown()
    print("ROS: OK" if ok else "ROS: FALTA ALGO OBLIGATORIO")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
