"""Prueba de la pantalla de la cara del X2 (emoji AimDK PlayEmoji). SOLO PANTALLA: no mueve
nada y no arma nada; cada emoji se muestra en bucle unos segundos y al final queda «calma».

En el robot (PC2), desde ~/x2_ultra:

    docker run --rm --network host --ipc host -e RMW_IMPLEMENTATION=rmw_fastrtps_cpp -e ROS_LOCALHOST_ONLY=0 -e ROS_LOG_DIR=/tmp/ros_logs -e FASTRTPS_DEFAULT_PROFILES_FILE=/agibot/software/entry/cfg/ros_dds_configuration.xml -v /agibot/software/entry/cfg/ros_dds_configuration.xml:/agibot/software/entry/cfg/ros_dds_configuration.xml:ro -v ~/x2_ultra:/workspace/x2_ultra -e PYTHONPATH=/workspace/x2_ultra/src:/workspace/x2_ultra x2-hri:latest python3 /workspace/x2_ultra/deploy/hri_probar_cara.py [--ids 90,170,1] [--segundos 3] [--lista]

``--lista`` imprime las tablas de face.py (EXPRESIONES y EMOJI_IDS) y sale sin tocar ROS:
funciona también en el PC. Códigos de salida: 0 todo ok; 1 sin el tipo/servicio o alguna
llamada falló; 2 algún --ids no existe en la tabla del firmware.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT / "src"), str(ROOT)):        # también usable fuera del contenedor (--lista)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from x2_ultra.hri_runtime import face  # noqa: E402  (face.py no importa ROS al cargarse)


def _parse_ids(raw: str) -> list[int]:
    ids = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            ids.append(int(tok))                 # ValueError ⇒ código 2 en main()
    return ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="hri_probar_cara.py",
                                 description="Muestra emojis en la pantalla de la cara (solo pantalla).")
    ap.add_argument("--ids", default="90,170,1,10", help="IDs de emoji separados por comas")
    ap.add_argument("--segundos", type=float, default=3.0, help="segundos por emoji")
    ap.add_argument("--prioridad", type=int, default=60, help="prioridad PlayEmoji (el X2 real reproduce a 60; con menos se rechaza)")
    ap.add_argument("--lista", action="store_true", help="imprime las tablas y sale (sin ROS)")
    args = ap.parse_args(argv)

    if args.lista:                               # sin ROS: también en el PC
        print("EXPRESIONES (nombre → emotion_id, modo):")
        for name, (eid, mode) in face.EXPRESIONES.items():
            print(f"  {name:<11} {eid:>3}  {'bucle' if mode == face.MODE_LOOP else 'una vez'}"
                  f"  ({face.EMOJI_IDS.get(eid, '?')})")
        print("EMOJI_IDS (id → nombre en el firmware):")
        for eid in sorted(face.EMOJI_IDS):
            print(f"  {eid:>3}  {face.EMOJI_IDS[eid]}")
        return 0

    try:
        ids = _parse_ids(args.ids)
    except ValueError:
        print(f"FALLO: --ids pide enteros separados por comas (recibido: {args.ids!r})")
        return 2
    desconocidos = [i for i in ids if i not in face.EMOJI_IDS]
    if not ids or desconocidos:                  # validación ANTES de tocar ROS
        print(f"FALLO: IDs que no están en la tabla del firmware: {desconocidos or args.ids!r}"
              " (usa --lista para verla)")
        return 2

    import rclpy                                 # solo aquí: --lista funciona sin ROS
    from rclpy.executors import MultiThreadedExecutor

    rclpy.init()
    node = rclpy.create_node("hri_club_cara_prueba")
    ex = MultiThreadedExecutor()
    ex.add_node(node)
    threading.Thread(target=ex.spin, daemon=True).start()
    client = face.RosEmojiClient(node_factory=lambda name: node)
    todo_ok = True
    try:
        for eid in ids:                          # en bucle: se ve mientras dura la espera
            try:
                ok, msg = client.play(eid, face.MODE_LOOP, args.prioridad)
            except face.FaceUnavailable as exc:
                print(f"FALLO: {exc}")
                return 1
            todo_ok &= ok
            print(f"{'ok   ' if ok else 'FALLO'} emoji {eid:>3} ({face.EMOJI_IDS[eid]}, bucle, "
                  f"prioridad {args.prioridad}): {msg}")
            time.sleep(args.segundos)
        try:                                     # al final, cara en calma (una vez)
            ok, msg = client.play(10, face.MODE_ONCE, args.prioridad)
        except face.FaceUnavailable as exc:
            print(f"FALLO: {exc}")
            return 1
        todo_ok &= ok
        print(f"{'ok   ' if ok else 'FALLO'} emoji  10 ({face.EMOJI_IDS[10]}, una vez, "
              f"prioridad {args.prioridad}): {msg}")
    finally:
        ex.shutdown()
        node.destroy_node()                      # proceso desechable: cierra su propio nodo
        rclpy.shutdown()
    print("CARA: OK" if todo_ok else "CARA: CON FALLOS (mira arriba)")
    return 0 if todo_ok else 1


if __name__ == "__main__":
    sys.exit(main())
