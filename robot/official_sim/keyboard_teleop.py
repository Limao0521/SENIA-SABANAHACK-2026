#!/usr/bin/env python3
"""Teclado WASD/QE para el simulador OFICIAL de AgiBot (MC + sim_mujoco) a través de AimDK.

Se ejecuta dentro del contenedor (ROS 2 Humble + aimdk_msgs): ``official_sim/sim.sh teclado``.
Abre una ventanita Tk con el foco del teclado (mantener pulsado = moverse, soltar = parar):

    W/S avanzar/retroceder · A/D lateral · Q/E girar · Mayús más rápido · Espacio parar
    1 = STAND_DEFAULT (de pie) · 2 = LOCOMOTION_DEFAULT · 0 = DAMPING_DEFAULT · Esc salir

Igual que el ejemplo ``keyboard`` de AimDK, registra una fuente de entrada propia con
``SetMcInputSource`` y publica ``McLocomotionVelocity`` a 50 Hz en ``/aima/mc/locomotion/velocity``.
El MC tiene umbrales de arranque: |vx|,|vy| = 0 o 0,2-1,0 m/s y |wz| = 0 o 0,1-1,0 rad/s; por eso las
velocidades nunca se mandan por debajo de esos mínimos. Si se deja de publicar, el MC pone velocidad 0
a los 200 ms. Con ``--terminal`` usa la consola (modo por pasos, como el ejemplo oficial).
"""
import argparse
import os
import socket
import threading
import time

import rclpy
from rclpy.node import Node
from aimdk_msgs.msg import McActionCommand, McInputAction, McLocomotionVelocity, MessageHeader, RequestHeader
from aimdk_msgs.srv import GetMcAction, SetMcAction, SetMcInputSource

SOURCE = "sdk_x2_ultra_keyboard"   # AimDK pide el prefijo sdk_ para fuentes de desarrollo
MIN_LIN, MIN_ANG = 0.2, 0.1


def require_isolated_sim() -> None:
    """Solo en el contenedor aislado (sin red): este script nunca debe hablar con el robot físico."""
    interfaces = [name for _, name in socket.if_nameindex() if name != "lo"]
    if os.environ.get("X2_SIM_ONLY") != "1" or os.environ.get("ROS_LOCALHOST_ONLY") != "1" or interfaces:
        raise SystemExit("keyboard_teleop.py solo corre en el contenedor de official_sim/sim.sh "
                         "(--network none, ROS_LOCALHOST_ONLY=1, X2_SIM_ONLY=1)")


def _with_min(v: float, minimum: float) -> float:
    if abs(v) < 1e-6:
        return 0.0
    return max(abs(v), minimum) * (1 if v > 0 else -1)


class Teleop(Node):
    def __init__(self, speeds):
        super().__init__("x2_ultra_keyboard_teleop")
        self.speeds = speeds
        self.pub = self.create_publisher(McLocomotionVelocity, "/aima/mc/locomotion/velocity", 10)
        self.src = self.create_client(SetMcInputSource, "/aimdk_5Fmsgs/srv/SetMcInputSource")
        self.set_action = self.create_client(SetMcAction, "/aimdk_5Fmsgs/srv/SetMcAction")
        self.get_action = self.create_client(GetMcAction, "/aimdk_5Fmsgs/srv/GetMcAction")
        self.cmd = (0.0, 0.0, 0.0)
        self.mode = "?"
        self.lock = threading.Lock()

    def _call(self, client, req, timeout=2.0):
        if not client.wait_for_service(timeout_sec=timeout):
            return None
        for _ in range(8):  # como los ejemplos de AimDK: reintentar, el par remoto a veces no contesta
            stamp = self.get_clock().now().to_msg()
            if hasattr(req, "request"):
                req.request.header.stamp = stamp
            else:
                req.header.stamp = stamp
            fut = client.call_async(req)
            rclpy.spin_until_future_complete(self, fut, timeout_sec=0.3)
            if fut.done():
                return fut.result()
        return None

    def register(self) -> bool:
        for action in (McInputAction.INPUTACTION_ADD, McInputAction.INPUTACTION_ENABLE):
            req = SetMcInputSource.Request()
            req.action.value = action
            req.input_source.name = SOURCE
            req.input_source.priority = 40
            req.input_source.timeout = 1000
            res = self._call(self.src, req)
            if res is not None and res.response.header.code == 0:
                return True
        return False

    def unregister(self) -> None:
        req = SetMcInputSource.Request()
        req.action.value = McInputAction.INPUTACTION_DISABLE
        req.input_source.name = SOURCE
        self._call(self.src, req, timeout=0.5)

    def request_mode(self, desc: str) -> str:
        req = SetMcAction.Request()
        req.header = RequestHeader()
        req.source = SOURCE
        req.command = McActionCommand()
        req.command.action_desc = desc
        res = self._call(self.set_action, req)
        return "sin respuesta" if res is None else (res.response.message or f"código {res.response.header.code}")

    def poll_mode(self) -> None:
        req = GetMcAction.Request()
        res = self._call(self.get_action, req, timeout=0.5)
        if res is not None:
            self.mode = f"{res.info.action_desc} ({res.info.status.value})"

    def publish(self) -> None:
        with self.lock:
            vx, vy, wz = self.cmd
        msg = McLocomotionVelocity()
        msg.header = MessageHeader()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.source = SOURCE
        msg.forward_velocity = _with_min(vx, MIN_LIN)
        msg.lateral_velocity = _with_min(vy, MIN_LIN)
        msg.angular_velocity = _with_min(wz, MIN_ANG)
        self.pub.publish(msg)

    def set_from_keys(self, keys: set) -> None:
        s = self.speeds
        run = s["run"] if "shift" in keys else 1.0
        vx = (s["vx"] * ("w" in keys) - s["vx_back"] * ("s" in keys)) * run
        vy = s["vy"] * (("a" in keys) - ("d" in keys)) * run
        wz = s["wz"] * (("q" in keys) - ("e" in keys)) * run
        with self.lock:
            self.cmd = (min(max(vx, -1.0), 1.0), min(max(vy, -1.0), 1.0), min(max(wz, -1.0), 1.0))


def run_tk(node: Teleop) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("X2 · teclado (simulador oficial)")
    root.geometry("560x260")
    root.configure(bg="#141a24")
    font = ("DejaVu Sans Mono", 11)
    label = tk.Label(root, justify="left", font=font, fg="#e8eef8", bg="#141a24")
    label.pack(padx=14, pady=10, anchor="w")
    keys: set = set()
    pending_release: dict = {}
    names = {"w", "a", "s", "d", "q", "e"}

    def press(ev):
        k = ev.keysym.lower()
        if k in ("shift_l", "shift_r"):
            keys.add("shift")
        elif k in names:
            pending_release.pop(k, None)
            keys.add(k)
        elif k == "space":
            keys.clear()
        elif k == "1":
            node.get_logger().info("STAND_DEFAULT: " + node.request_mode("STAND_DEFAULT"))
        elif k == "2":
            node.get_logger().info("LOCOMOTION_DEFAULT: " + node.request_mode("LOCOMOTION_DEFAULT"))
        elif k == "0":
            keys.clear()
            node.get_logger().info("DAMPING_DEFAULT: " + node.request_mode("DAMPING_DEFAULT"))
        elif k == "escape":
            root.destroy()
            return
        node.set_from_keys(keys)

    def release(ev):
        k = ev.keysym.lower()
        if k in ("shift_l", "shift_r"):
            keys.discard("shift")
            node.set_from_keys(keys)
        elif k in names:
            # X11 genera pares soltar/pulsar falsos con la autorrepetición: confirmar tras 40 ms
            pending_release[k] = time.monotonic()
            root.after(40, lambda kk=k: confirm(kk))

    def confirm(k):
        t = pending_release.pop(k, None)
        if t is not None:
            keys.discard(k)
            node.set_from_keys(keys)

    def tick():
        node.publish()
        rclpy.spin_once(node, timeout_sec=0)
        vx, vy, wz = node.cmd
        label.config(text=(
            "W/S avanzar/retroceder · A/D lateral · Q/E girar\n"
            "Mayús rápido · Espacio parar · Esc salir\n"
            "1 de pie (STAND) · 2 locomoción (LOCOMOTION) · 0 amortiguar\n\n"
            f"orden: vx {_with_min(vx, MIN_LIN):+.2f} m/s  vy {_with_min(vy, MIN_LIN):+.2f} m/s  "
            f"wz {_with_min(wz, MIN_ANG):+.2f} rad/s\n"
            f"modo MC: {node.mode}\n"
            "(la ventana debe tener el foco del teclado)"))
        root.after(20, tick)

    def mode_tick():
        node.poll_mode()
        root.after(1500, mode_tick)

    root.bind("<KeyPress>", press)
    root.bind("<KeyRelease>", release)
    root.after(20, tick)
    root.after(300, mode_tick)
    root.focus_force()
    root.mainloop()


def run_terminal(node: Teleop) -> None:
    import curses

    def loop(stdscr):
        curses.cbreak()
        stdscr.nodelay(True)
        vx = vy = wz = 0.0
        while True:
            ch = stdscr.getch()
            if ch == 27:
                break
            c = chr(ch).lower() if 0 <= ch < 256 else ""
            if c == " ":
                vx = vy = wz = 0.0
            elif c in "ws":
                vx = round(min(max(vx + (0.1 if c == "w" else -0.1), -1.0), 1.0), 2)
            elif c in "ad":
                vy = round(min(max(vy + (0.1 if c == "a" else -0.1), -1.0), 1.0), 2)
            elif c in "qe":
                wz = round(min(max(wz + (0.1 if c == "q" else -0.1), -1.0), 1.0), 2)
            elif c == "1":
                node.request_mode("STAND_DEFAULT")
            elif c == "2":
                node.request_mode("LOCOMOTION_DEFAULT")
            elif c == "0":
                vx = vy = wz = 0.0
                node.request_mode("DAMPING_DEFAULT")
            with node.lock:
                node.cmd = (vx, vy, wz)
            node.publish()
            rclpy.spin_once(node, timeout_sec=0)
            stdscr.erase()
            stdscr.addstr(0, 0, "Modo terminal (por pasos): W/S A/D Q/E suman 0.1 · Espacio parar · Esc salir")
            stdscr.addstr(2, 0, f"vx {_with_min(vx, MIN_LIN):+.2f}  vy {_with_min(vy, MIN_LIN):+.2f}  "
                                f"wz {_with_min(wz, MIN_ANG):+.2f}")
            time.sleep(0.02)

    curses.wrapper(loop)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--terminal", action="store_true", help="consola en vez de ventana Tk (modo por pasos)")
    ap.add_argument("--vx", type=float, default=0.4)
    ap.add_argument("--vx-back", type=float, default=0.3)
    ap.add_argument("--vy", type=float, default=0.5, help="lateral: el MC necesita ~0,5 m/s para arrancar parado")
    ap.add_argument("--wz", type=float, default=0.4)
    ap.add_argument("--run", type=float, default=1.8)
    args = ap.parse_args()
    require_isolated_sim()
    rclpy.init()
    node = Teleop({"vx": args.vx, "vx_back": args.vx_back, "vy": args.vy, "wz": args.wz, "run": args.run})
    if not node.register():
        node.get_logger().error("No se pudo registrar la fuente de entrada (¿MC arrancado?)")
    try:
        (run_terminal if args.terminal else run_tk)(node)
    finally:
        with node.lock:
            node.cmd = (0.0, 0.0, 0.0)
        for _ in range(15):
            node.publish()
            time.sleep(0.02)
        node.unregister()
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
