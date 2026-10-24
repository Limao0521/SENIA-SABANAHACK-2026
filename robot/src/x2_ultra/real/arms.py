"""Brazos del X2 Ultra REAL, cada uno por separado y articulación a articulación, en UPPERBODY_REMOTE_SPLIT.

MUEVE EL ROBOT. Lo lanza una persona que mira el robot, con el mando a mano: ``scripts/robot_real.sh brazos``.
El MC mantiene piernas, cintura y equilibrio; este programa sólo manda cabeza (quieta), brazos y manos (quietas).
Guía: docs/09-robot-real.md.

Ventana Tk (debe tener el foco):

    Intro armar (pide URS desde STAND_DEFAULT) · Tab o ←/→ brazo izquierdo/derecho · 1-7 articulación
    ↑/↓ (o W/S) mover la articulación mientras se mantiene · H volver a la postura inicial · Espacio parar
    R reanclar tras un fallo de seguimiento · P pedir STAND_DEFAULT (en la postura inicial) · Esc salir

Cómo manda: ``/mc/upper_body_command`` a 50 Hz desde que se arma. El objetivo parte de la postura medida al armar
(el ancla) y cada articulación se mueve a ``--velocidad`` (0,15 rad/s), sin salir de su rango URDF menos 0,05 rad
ni alejarse más de ``--envolvente`` (0,4 rad) del ancla. La cabeza queda en su postura medida al armar. Sin manos
instaladas se manda ``hand_sub_mode=1`` + ``[0, 0]`` (con 0 el MC descarta todo); con OmniHand, la postura de las
manos medida al armar.

Protecciones: si un eje se aparta del objetivo más de ``--seguimiento`` (0,12 rad; el hundimiento normal es ≈0,03)
durante 0,5 s, o dejan de llegar los estados de brazo, el objetivo se congela (se sigue mandando quieto). Si el MC
sale de URS, deja de informar de pie, arranca el reproductor de movimientos o se pierde su estado, se deja de mandar:
en URS los brazos se quedan en su última postura. Al salir se mandan diez órdenes quietas y se borra la fuente; el MC
sigue en URS con los brazos donde estén (volver a STAND_DEFAULT con P, el mando o la app).

Nunca pide DAMPING, PASSIVE ni JOINT_DEFAULT: sólo UPPERBODY_REMOTE_SPLIT (al armar) y STAND_DEFAULT (P). En URS el MC
sigue con piernas, cintura y equilibrio; si este programa se para, el robot sigue de pie y los brazos quietos.
"""
from __future__ import annotations

import argparse
import threading
import time

from x2_ultra.real import control as K
from x2_ultra.real.link import STAMP_MODES, TOPIC_UPPER_BODY, RobotLink, require_target
from x2_ultra.real.ui import ARMED_BG, BG, FG, FONT, HeldKeys, SessionLog, fmt, stop_on_signals
from x2_ultra.robot.joints import ARM_JOINTS, HEAD_JOINTS, LEFT_ARM_JOINTS, RIGHT_ARM_JOINTS

TICK_S = 0.02
UI_TIMEOUT_S = 0.25
SIDES = {"izquierdo": LEFT_ARM_JOINTS, "derecho": RIGHT_ARM_JOINTS}
LABELS = ("hombro pitch", "hombro roll", "hombro yaw", "codo", "muñeca yaw", "muñeca pitch", "muñeca roll")
HOME_TOLERANCE = 0.02
STABLE_WAIT_S = 6.0


class ArmsController:
    def __init__(self, link: RobotLink, *, envelope: float, speed: float, tracking: float, priority: int,
                 stamp_mode: str, ignore_battery: bool, log: SessionLog):
        self.link, self.log = link, log
        self.envelope, self.speed, self.priority, self.stamp_mode = envelope, speed, priority, stamp_mode
        self.ignore_battery = ignore_battery
        self.monitor = K.TrackingMonitor(limit=tracking)
        self.lock = threading.Lock()
        self.side = "izquierdo"
        self.index = 0
        self.direction = 0
        self.homing = False
        self.frozen = ""            # motivo por el que el objetivo no se mueve ("" = libre)
        self.ui_beat = time.monotonic()
        self.armed = False          # se están publicando órdenes
        self.busy = False
        self.ending = False         # se pidió STAND_DEFAULT: salir de URS es lo esperado
        self.message = ""
        self.problems: list[str] = ["comprobando el robot…"]
        self.jog: K.ArmJog | None = None
        self.head: list[float] = []
        self.hand_mode, self.hand_pos = K.HAND_SUB_MODE_CLAW, [0.0, 0.0]
        self.measured: dict[str, float] | None = None
        self.obs = K.Observed()
        self._last_check = self._last_log = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    @property
    def joint(self) -> str:
        return SIDES[self.side][self.index]

    # --- órdenes de la persona -------------------------------------------------------------------------------
    def beat(self) -> None:
        self.ui_beat = time.monotonic()

    def set_direction(self, direction: int) -> None:
        with self.lock:
            self.direction = direction
            if direction:
                self.homing = False

    def select(self, *, side: str | None = None, index: int | None = None) -> None:
        with self.lock:
            if self.direction:
                return                  # no cambiar de eje con uno en marcha
            if side is not None:
                self.side = side
            if index is not None:
                self.index = index

    def toggle_side(self) -> None:
        self.select(side="derecho" if self.side == "izquierdo" else "izquierdo")

    def home(self) -> None:
        with self.lock:
            if self.jog is not None and not self.frozen:
                self.homing, self.direction = True, 0

    def halt(self) -> None:
        with self.lock:
            self.homing, self.direction = False, 0

    def reanchor(self) -> None:
        """Tras un fallo de seguimiento: el objetivo pasa a la postura medida (dentro de los mismos límites)."""
        measured = self.link.latest("arms")
        with self.lock:
            if self.jog is None or not self.frozen or measured is None:
                return
            for name in ARM_JOINTS:
                low, high = self.jog.bounds[name]
                self.jog.target[name] = min(high, max(low, measured[name]))
            self.monitor.since = None
            self.frozen, self.homing, self.direction = "", False, 0
        self.message = "reanclado en la postura medida"
        self.log.write("reanclado", objetivo=self.jog.positions())

    def arm(self) -> None:
        if self.armed or self.busy:
            return
        self.busy = True
        threading.Thread(target=self._arm, daemon=True).start()

    def _arm(self) -> None:
        try:
            obs = self.link.observed()
            problems = K.preflight(obs, "brazos", need_clock=self.stamp_mode == "robot",
                                   ignore_battery=self.ignore_battery)
            if problems:
                self.message = "no se arma: " + problems[0]
                return
            if obs.hands == K.HANDS_OMNIHAND:
                hands = self.link.hand_positions()
                if hands is None or len(hands) != 20:
                    self.message = "no se arma: faltan las posiciones de las OmniHand"
                    return
                self.hand_mode, self.hand_pos = K.HAND_SUB_MODE_DEXTEROUS, hands
            else:
                self.hand_mode, self.hand_pos = K.HAND_SUB_MODE_CLAW, [0.0, 0.0]
            if obs.mode == K.MODE_STAND:
                # Tras andar el MC pasa 2-4 s en MOVING y rechaza los cambios de modo (robot, 20/10).
                self.message = "esperando a que el MC esté estable…"
                if not self.link.wait_stable(STABLE_WAIT_S):
                    self.message = f"no se arma: el MC no se estabilizó en {STABLE_WAIT_S:.0f} s"
                    return
                self.message = "pidiendo UPPERBODY_REMOTE_SPLIT…"
                try:
                    self.link.request_mode(K.MODE_URS)
                except RuntimeError as exc:
                    # La respuesta puede perderse aunque el MC lo aplique: manda el modo observado.
                    self.log.write("aviso", donde="pedir URS", error=str(exc))
                if not self.link.wait_mode(K.MODE_URS, 8.0):
                    self.message = "no se arma: el MC no pasó a UPPERBODY_REMOTE_SPLIT"
                    return
            deadline = time.monotonic() + 2.0
            while self.link.node.count_subscribers(TOPIC_UPPER_BODY) < 1 and time.monotonic() < deadline:
                time.sleep(0.05)
            if self.link.node.count_subscribers(TOPIC_UPPER_BODY) < 1:
                self.message = "no se arma: no aparece el suscriptor del MC en /mc/upper_body_command"
                return
            self.message = "registrando la fuente de entrada…"
            self.link.register_source(self.priority)
            arms, head = self.link.latest("arms"), self.link.latest("head")
            if arms is None or head is None:
                self.message = "no se arma: estados de brazo o cabeza no frescos"
                return
            with self.lock:
                self.jog = K.ArmJog(arms, envelope=self.envelope, speed=self.speed)
                self.head = [head[n] for n in HEAD_JOINTS]
                self.monitor.since = None
                self.frozen, self.homing, self.direction, self.ending = "", False, 0, False
                self.armed = True
            self.message = f"ARMADO en URS (fuente {self.link.source})"
            self.log.write("armado", ancla=self.jog.positions(), cabeza=self.head, mano=self.hand_mode,
                           envolvente=self.envelope, velocidad=self.speed, sello=self.stamp_mode)
        except (RuntimeError, ValueError) as exc:
            self.message = f"no se arma: {exc}"
            self.log.write("error", donde="armar", error=str(exc))
        finally:
            self.busy = False

    def request_stand(self) -> None:
        if self.busy or self.jog is None or not self.armed:
            self.message = "P sólo con los brazos armados (si no, STAND_DEFAULT con el mando o la app)"
            return
        if self.direction or self.homing or self.jog.distance_home() > HOME_TOLERANCE:
            self.message = "STAND_DEFAULT sólo en la postura inicial y parado (H para volver)"
            return
        self.busy = True

        def work():
            try:
                self.ending = True
                if not self.link.wait_stable(STABLE_WAIT_S):
                    raise RuntimeError(f"el MC no se estabilizó en {STABLE_WAIT_S:.0f} s; STAND_DEFAULT no pedido")
                self.message = "pidiendo STAND_DEFAULT (si el MC está ocupado, se reintenta 12 s)…"
                self.link.request_mode(K.MODE_STAND, busy_retry_s=12.0)
                self.message = "STAND_DEFAULT pedido"
            except RuntimeError as exc:
                self.message = f"{exc} → usar el mando o la app para volver a STAND_DEFAULT"
                if self.link.wait_mode(K.MODE_STAND, 3.0):
                    self.message = "STAND_DEFAULT (respuesta perdida, modo observado)"
                else:
                    self.ending = False
            finally:
                self.log.write("modo", pedido=K.MODE_STAND, resultado=self.message)
                self.busy = False
        threading.Thread(target=work, daemon=True).start()

    # --- bucle de 50 Hz ------------------------------------------------------------------------------------
    def _fatal(self, obs: K.Observed) -> str | None:
        if obs.mc_age_s is None or obs.mc_age_s > K.MC_STATE_TIMEOUT_S:
            return "se perdió el estado del MC"
        if obs.mode != K.MODE_URS:
            return f"el MC pasó a {obs.mode}"
        if obs.body_status != K.BODY_STAND:
            return f"el MC ya no informa de pie (body_status={obs.body_status})"
        if obs.player_state != K.PLAYER_IDLE:
            return f"el reproductor de movimientos del MC arrancó ({obs.motion!r})"
        return None

    def stop_publishing(self, reason: str) -> None:
        with self.lock:
            was, self.armed = self.armed, False
            self.homing, self.direction = False, 0
        if was:
            self.message = ("fin: MC en STAND_DEFAULT" if self.ending else f"PARADO (sin órdenes): {reason}")
            self.log.write("parado", motivo=reason)

    def _loop(self) -> None:
        last = time.monotonic()
        while self._running:
            now = time.monotonic()
            self.tick(now, now - last)
            last = now
            time.sleep(max(0.0, TICK_S - (time.monotonic() - now)))

    def tick(self, now: float, dt: float) -> None:
        graph = now - self._last_check > 1.0
        obs = self.link.observed(graph=graph)
        self.obs = obs
        if graph:
            self._last_check = now
            if not self.armed:
                self.problems = K.preflight(obs, "brazos", need_clock=self.stamp_mode == "robot",
                                            ignore_battery=self.ignore_battery)
        self.measured = self.link.latest("arms")
        if self.armed:
            reason = self._fatal(obs)
            if reason:
                self.stop_publishing(reason)
        if self.armed:
            self._step(now, dt)
        if now - self._last_log >= 0.1 and self.jog is not None:
            self._last_log = now
            m = self.measured
            self.log.write("muestra", armado=self.armed, objetivo=[round(v, 4) for v in self.jog.positions()],
                           medido=None if m is None else [round(m[n], 4) for n in ARM_JOINTS],
                           congelado=self.frozen, modo=obs.mode, cuerpo=obs.body_status, bateria=obs.battery)

    def _step(self, now: float, dt: float) -> None:
        with self.lock:
            jog, measured = self.jog, self.measured
            if measured is None:
                self.frozen = self.frozen or "no llegan estados de brazo frescos"
            elif self.monitor.update(jog.target, measured, now) and not self.frozen.startswith("seguimiento"):
                name, error = self.monitor.worst
                self.frozen = f"seguimiento: {name} a {error:.3f} rad del objetivo (R para reanclar)"
                self.log.write("congelado", motivo=self.frozen)
            elif self.frozen.startswith("no llegan"):
                self.frozen = ""
            moving = not self.frozen and now - self.ui_beat <= UI_TIMEOUT_S
            if moving and self.homing:
                if jog.home_step(dt):
                    self.homing = False
            elif moving and self.direction:
                jog.jog(self.joint, self.direction, dt)
            if self.frozen:
                self.homing, self.direction = False, 0
            arms = jog.positions()
        try:
            self.link.publish_upper_body(self.head, arms, self.hand_mode, self.hand_pos, self.stamp_mode)
        except (RuntimeError, ValueError) as exc:
            self.stop_publishing(str(exc))

    def shutdown(self) -> None:
        """Diez órdenes quietas (si se estaba mandando) y borrar la fuente."""
        with self.lock:
            was_armed, self.armed = self.armed, False
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if was_armed and self.jog is not None:
            for _ in range(10):
                try:
                    self.link.publish_upper_body(self.head, self.jog.positions(), self.hand_mode, self.hand_pos,
                                                 self.stamp_mode)
                except (RuntimeError, ValueError):
                    break
                time.sleep(TICK_S)
        error = self.link.delete_source()
        self.log.write("salida", borrar_fuente=error or "ok")
        if error:
            print(f"aviso: {error} (la fuente caduca sola; se borra al reiniciar el MC)")


def run_window(ctl: ArmsController, stop) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("X2 REAL · brazos")
    root.geometry("900x560")
    root.configure(bg=BG)
    label = tk.Label(root, justify="left", font=FONT, fg=FG, bg=BG, anchor="nw")
    label.pack(padx=14, pady=10, fill="both", expand=True)
    held = HeldKeys(root, {"up", "down", "w", "s"}, lambda: ctl.set_direction(
        (("up" in held.down or "w" in held.down) - ("down" in held.down or "s" in held.down))))

    def press(event):
        if held.press(event):
            return
        k = held.name(event)
        if k in ("return", "kp_enter"):
            ctl.arm()
        elif k in ("tab", "iso_left_tab"):
            ctl.toggle_side()
            return "break"              # que Tab no mueva el foco de la ventana
        elif k == "left":
            ctl.select(side="izquierdo")
        elif k == "right":
            ctl.select(side="derecho")
        elif k in "1234567" and len(k) == 1:
            ctl.select(index=int(k) - 1)
        elif k == "h":
            ctl.home()
        elif k == "space":
            ctl.halt()
        elif k == "r":
            ctl.reanchor()
        elif k == "p":
            ctl.request_stand()
        elif k == "escape":
            root.destroy()

    def row(name: str, label_: str, number: int) -> str:
        jog, m = ctl.jog, ctl.measured
        mark = "▶" if name == ctl.joint else " "
        if jog is None:
            target = anchor = low = high = None
        else:
            target, anchor = jog.target[name], jog.anchor[name]
            low, high = jog.bounds[name]
        measured = None if m is None else m[name]
        error = None if (measured is None or target is None) else measured - target
        return (f"{mark} {number} {label_:<13} medido {fmt(measured)}  objetivo {fmt(target)}  "
                f"error {fmt(error, 6, 3)}  ancla {fmt(anchor)}  [{fmt(low)}, {fmt(high)}]")

    def tick():
        if stop.is_set():
            root.destroy()
            return
        ctl.beat()
        o = ctl.obs
        lines = [
            "Intro armar (pide URS) · Tab o ←/→ brazo · 1-7 articulación · ↑/↓ (W/S) mover mientras se mantiene",
            "H volver a la postura inicial · Espacio parar · R reanclar tras un fallo de seguimiento",
            "P pedir STAND_DEFAULT (en la postura inicial) · Esc salir",
            "",
            f"estado: {ctl.message}",
            f"MC: {o.mode or '?'} ({o.action_status})  de pie {o.body_status == K.BODY_STAND}  "
            f"batería {o.battery if o.battery is not None else '?'} %  manos {o.hands}  "
            f"otros publicadores {o.upper_body_publishers}",
        ]
        if ctl.frozen:
            lines.append(f"CONGELADO: {ctl.frozen}")
        elif ctl.homing:
            lines.append("volviendo a la postura inicial…")
        for side, names in SIDES.items():
            lines += ["", f"brazo {side}" + ("  ◀ activo" if side == ctl.side else "")]
            lines += [row(n, LABELS[i], i + 1) for i, n in enumerate(names)]
        if not ctl.armed and ctl.jog is None:
            lines += ["", "falta:"] + [f"  · {p}" for p in ctl.problems[:5]] if ctl.problems else \
                ["", "todo en orden: Intro para armar (pide URS)"]
        label.config(text="\n".join(lines), bg=ARMED_BG if ctl.armed else BG)
        root.configure(bg=ARMED_BG if ctl.armed else BG)
        root.after(50, tick)

    root.bind("<KeyPress>", press)
    root.bind("<KeyRelease>", held.release)
    root.after(50, tick)
    root.focus_force()
    root.mainloop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--velocidad", type=float, default=K.DEFAULT_ARM_SPEED,
                    help=f"rad/s de cada articulación, hasta {K.MAX_ARM_SPEED}")
    ap.add_argument("--envolvente", type=float, default=K.DEFAULT_ENVELOPE,
                    help=f"rad máximos desde la postura al armar, hasta {K.MAX_ENVELOPE}")
    ap.add_argument("--seguimiento", type=float, default=0.12, help="error [rad] que congela el objetivo, 0,05..0,3")
    ap.add_argument("--prioridad", type=int, default=65, help="prioridad de la fuente, 20..69 (65 funcionó el 19/10)")
    ap.add_argument("--sello", choices=STAMP_MODES, default="cero",
                    help="sello de las órdenes: cero (probado con los brazos el 19/10), robot o pc")
    ap.add_argument("--ignorar-bateria", action="store_true", help="armar aunque no llegue la batería")
    args = ap.parse_args(argv)
    if not 0.01 <= args.velocidad <= K.MAX_ARM_SPEED:
        ap.error(f"--velocidad 0,01..{K.MAX_ARM_SPEED}")
    if not 0.01 <= args.envolvente <= K.MAX_ENVELOPE:
        ap.error(f"--envolvente 0,01..{K.MAX_ENVELOPE}")
    if not 0.05 <= args.seguimiento <= 0.3:
        ap.error("--seguimiento 0,05..0,3")
    if not K.MIN_PRIORITY <= args.prioridad <= K.MAX_PRIORITY:
        ap.error(f"--prioridad {K.MIN_PRIORITY}..{K.MAX_PRIORITY}: el mando (80) debe poder retomar")
    target = require_target()
    stop = stop_on_signals()
    log = SessionLog("brazos")
    link = RobotLink("brazos", publish_upper_body=True)
    ctl = ArmsController(link, envelope=args.envolvente, speed=args.velocidad, tracking=args.seguimiento,
                         priority=args.prioridad, stamp_mode=args.sello, ignore_battery=args.ignorar_bateria, log=log)
    log.write("inicio", destino=target, args=vars(args))
    print(f"X2 real · brazos ({target}); registro en {log.path}")
    try:
        link.wait_ready()
        ctl.start()
        run_window(ctl, stop)
    finally:
        ctl.shutdown()
        link.close()
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
