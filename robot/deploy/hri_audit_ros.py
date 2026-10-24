"""Auditoría ROS de HRIEGO (solo lectura): qué publica y qué servicios usa cada nodo hriego_*.

Sale con código 1 si algún nodo de HRIEGO publica fuera de la lista blanca de audio o
usa un servicio que no sea el foco de audio o el emoji de la cara (PlayEmoji). Lo usan deploy/hri_sin_actuacion.sh y
deploy/hri_club_sin_actuacion.sh (runner conversacional: nodos hri_club_*).

Prefijos auditados: HRI_AUDIT_PREFIXES (coma-separados; default "hriego_,hri_club_").
HRI_AUDIT_REQUIRE=1: sale con código 2 si no encuentra ningún nodo con esos prefijos.
"""
from __future__ import annotations

import os
import sys
import time

import rclpy

ALLOWED_PUBLISH = {"/aima/hal/audio/playback", "/rosout", "/parameter_events"}
# = hriego_v2/robot_io.ALLOWED_SERVICES (PlayEmoji: emoji en la pantalla de la cara, sin movimiento)
ALLOWED_CLIENTS = {"/aimdk_5Fmsgs/srv/RequestAudioFocus", "/aimdk_5Fmsgs/srv/AbandonAudioFocus",
                   "/aimdk_5Fmsgs/srv/PlayEmoji", "/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest"}
PREFIXES = tuple(p.strip() for p in os.environ.get("HRI_AUDIT_PREFIXES", "hriego_,hri_club_").split(",")
                 if p.strip())


def main() -> int:
    rclpy.init()
    node = rclpy.create_node("hriego_audit_readonly")
    time.sleep(3.0)  # descubrimiento DDS
    bad = []
    found = 0
    for name, ns in node.get_node_names_and_namespaces():
        if not name.startswith(PREFIXES) or name == "hriego_audit_readonly":
            continue
        found += 1
        pubs = [t for t, _ in node.get_publisher_names_and_types_by_node(name, ns)]
        subs = [t for t, _ in node.get_subscriber_names_and_types_by_node(name, ns)]
        clients = [s for s, _ in node.get_client_names_and_types_by_node(name, ns)]
        print(f"nodo {ns.rstrip('/')}/{name}")
        print(f"  publica:     {sorted(pubs)}")
        print(f"  se suscribe: {sorted(subs)}")
        print(f"  servicios:   {sorted(clients)}")
        bad += [f"{name} publica {t}" for t in pubs if t not in ALLOWED_PUBLISH]
        bad += [f"{name} usa servicio {s}" for s in clients
                if s not in ALLOWED_CLIENTS and not s.startswith(f"/{name}/")]
    node.destroy_node()
    rclpy.shutdown()
    if not found:
        print(f"No hay nodos {'/'.join(p + '*' for p in PREFIXES)} activos (¿HRIEGO arrancado?).")
    if bad:
        print("ACTUACIÓN NO PERMITIDA DETECTADA:\n  " + "\n  ".join(bad))
        return 1
    print("OK: HRIEGO no publica nada fuera del audio." if found else "")
    if not found and os.environ.get("HRI_AUDIT_REQUIRE") == "1":
        return 2      # se esperaba ver los nodos del runner (deploy/hri_club_sin_actuacion.sh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
