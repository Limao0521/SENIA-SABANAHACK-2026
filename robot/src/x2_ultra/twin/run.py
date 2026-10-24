"""Arranca el gemelo digital dentro del contenedor ``x2-twin`` (lo lanza ``official_sim/twin.sh start``).

    python3 -m x2_ultra.twin.run [--escena mesa|vacia] [--sin-visor] [--camaras reducida|media|real|no]
                                 [--sin-lidar] [--sin-audio] [--sin-urs] [--sin-portico]

Procesos/hilos:
  - física + HAL (``ros_hal.TwinHal``) a tiempo real en un hilo propio;
  - nodos ROS (órdenes, servicios, URS emulado, audio) en un ejecutor multihilo;
  - visor MuJoCo y cámaras/LiDAR en procesos aparte (render EGL en la GPU), con la postura del robot por memoria
    compartida: dibujar en el mismo proceso frenaba la física por debajo de tiempo real.
"""
from __future__ import annotations

import argparse
import os
import threading
import time


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--escena", default="mesa", choices=("mesa", "vacia"))
    ap.add_argument("--sin-visor", action="store_true", help="sin ventana de MuJoCo")
    ap.add_argument("--camaras", default="reducida", choices=("reducida", "media", "real", "no"))
    ap.add_argument("--sin-lidar", action="store_true")
    ap.add_argument("--sin-audio", action="store_true")
    ap.add_argument("--sin-urs", action="store_true", help="sin el UPPERBODY_REMOTE_SPLIT emulado")
    ap.add_argument("--sin-portico", action="store_true", help="arrancar suelto (se cae hasta que el MC lo levante)")
    ap.add_argument("--rtf", type=float, default=1.0, help="factor de tiempo real")
    args = ap.parse_args(argv)

    import rclpy
    from rclpy.executors import MultiThreadedExecutor, SingleThreadedExecutor

    from x2_ultra.twin.physics import TwinPhysics
    from x2_ultra.twin.ros_hal import TwinHal

    rclpy.init()
    phys = TwinPhysics(scene=args.escena, gantry=not args.sin_portico)
    hal = TwinHal(phys, rtf=args.rtf)
    nodes = []                                  # nodos que no son la HAL (URS emulado, audio)
    # El MC oficial vive en su propio dominio DDS: la HAL se publica también allí y el puente le pasa lo demás
    # (ver mc_shim: así el gemelo puede atender SetMcAction/GetMcAction y emular URS y HEAD_ONLY).
    from rclpy.node import Node

    from x2_ultra.twin.mc_shim import MC_DOMAIN_ID, UpperBodyShim
    mc_ctx = rclpy.Context()
    rclpy.init(context=mc_ctx, domain_id=MC_DOMAIN_ID)
    mc_node = Node("x2_twin_mc_side", context=mc_ctx)
    hal.attach(mc_node)
    nodes.append(UpperBodyShim(hal, mc_node, emulate=not args.sin_urs))
    if not args.sin_audio:
        from x2_ultra.twin.ros_audio import TwinAudio
        nodes.append(TwinAudio())
    # Postura en memoria compartida para los procesos que dibujan (visor, cámaras y LiDAR).
    import multiprocessing as mp

    from x2_ultra.twin import procs
    from x2_ultra.twin.shared import TWIN_SHM_NAME, SharedState
    shared = SharedState(phys.model.nq, name=TWIN_SHM_NAME, create=True)
    shared.write(phys.data.qpos, phys.time, phys.gantry)
    hal.shared = shared
    ctx = mp.get_context("spawn")
    stop_event = ctx.Event()
    children = []
    if not args.sin_visor and os.environ.get("DISPLAY"):
        children.append(ctx.Process(target=procs.viewer_main, args=(shared.name, phys.model.nq, args.escena, stop_event),
                                    name="visor", daemon=True))
    if args.camaras != "no" or not args.sin_lidar:
        children.append(ctx.Process(target=procs.sensors_main,
                                    args=(shared.name, phys.model.nq, args.escena, args.camaras, not args.sin_lidar,
                                          stop_event), name="sensores", daemon=True))
    for c in children:
        c.start()

    # La HAL tiene su propio ejecutor de un hilo (órdenes a ~1,6 kHz); el resto va en otro, multihilo.
    hal_exec = SingleThreadedExecutor()
    hal_exec.add_node(hal)
    threading.Thread(target=hal_exec.spin, daemon=True).start()
    executor = MultiThreadedExecutor(num_threads=4)
    for n in nodes:
        executor.add_node(n)
    threading.Thread(target=executor.spin, daemon=True).start()
    mc_exec = SingleThreadedExecutor(context=mc_ctx)
    mc_exec.add_node(mc_node)
    threading.Thread(target=mc_exec.spin, daemon=True).start()
    physics_thread = threading.Thread(target=hal.run, daemon=True)
    physics_thread.start()
    hal.get_logger().info(f"gemelo X2 Ultra-N en marcha (escena {args.escena}, cámaras {args.camaras}, "
                          f"lidar {'no' if args.sin_lidar else 'sí'}, audio {'no' if args.sin_audio else 'sí'}, "
                          f"URS emulado {'no' if args.sin_urs else 'sí'}, pórtico {'no' if args.sin_portico else 'sí'})")

    def status_loop():
        while physics_thread.is_alive():
            time.sleep(10.0)
            hal.get_logger().info(f"tiempo real ×{hal.stats['rtf']:.2f} · t={phys.time:.1f} s · "
                                  f"pórtico {'sí' if phys.gantry else 'no'}")
    threading.Thread(target=status_loop, daemon=True).start()

    try:
        while physics_thread.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        hal.stop()
        stop_event.set()
        for c in children:
            c.join(timeout=3)
        shared.close()
        rclpy.shutdown(context=mc_ctx)
        rclpy.shutdown()


if __name__ == "__main__":
    main()
