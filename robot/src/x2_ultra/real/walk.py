"""WASD/QE para el X2 Ultra REAL: marcha con la locomoción oficial del MC por AimDK.

MUEVE EL ROBOT. Lo lanza una persona que mira el robot, con el mando a mano (prioridad 80: siempre retoma el
control) y espacio libre alrededor: ``scripts/robot_real.sh andar``. Guía: docs/09-robot-real.md.

Ventana Tk (debe tener el foco):

    Intro armar · W/S avanzar/retroceder · A/D lateral · Q/E girar (mantener) · Mayús más rápido
    + / - subir o bajar el nivel de velocidad · O entrar/salir del modo offroad (LOCOMOTION_STEP, parado)
    F / R agacharse / levantarse en cuclillas (mantener, parado; necesita scripts/build_aimdk_overlay.sh)
    Espacio parar y desarmar · 1 pedir STAND_DEFAULT (parado) · Esc salir (si se usó offroad, lo quita)

Cómo manda: fuente ``sdk_x2_ultra_andar_*`` registrada con ``SetMcInputSource`` al armar, ``McLocomotionVelocity``
a 50 Hz (ceros cuando no hay tecla), cada eje en 0 o por encima del umbral de arranque y con rampa
(``control.VelocityShaper``). Se desarma solo, mandando ceros, si: se pierde el estado del MC (>0,5 s), el MC sale
de STAND/LOCOMOTION_DEFAULT, deja de informar de pie, el reproductor de movimientos arranca, el MC no selecciona
nuestra fuente en 1 s de marcha (o la cambia por otra, p. ej. el mando), la batería baja del 20 %, se pierde la hora
del robot o se acaba ``--duracion``. Si la ventana se congela o pierde el foco, se manda 0.

Cuclillas: ``McBodyPose`` en ``/aima/mc/body_pose`` con la misma fuente, como la app: ``stand_height`` es una velocidad
(±0,5 → 0,1/s), así que soltar la tecla deja el robot a esa altura. Sólo parado en STAND_DEFAULT estable; agachado no
anda ni cambia de modo, y al salir se levanta antes de soltar el control.

Nunca pide DAMPING, PASSIVE ni JOINT_DEFAULT: sólo puede pedir STAND_DEFAULT, LOCOMOTION_STEP (offroad) y
LOCOMOTION_DEFAULT (quitar el offroad).
"""
from __future__ import annotations

import argparse
import threading
import time

from x2_ultra.real import control as K
from x2_ultra.real.link import STAMP_MODES, RobotLink, require_target
from x2_ultra.real.ui import ARMED_BG, BG, FG, FONT, HeldKeys, SessionLog, fmt, stop_on_signals

TICK_S = 0.02
UI_TIMEOUT_S = 0.25
SOURCE_GRACE_S = 1.0
STABLE_WAIT_S = 6.0         # tras andar el MC tarda ≈2-4 s en volver a STABLE (gemelo, 20/10)
LOW_BATTERY_STOP = 20


class WalkController:
    def __init__(self, link: RobotLink, limits: K.WalkLimits, *, priority: int, stamp_mode: str,
                 ignore_battery: bool, max_s: float, log: SessionLog):
        self.link, self.log = link, log
        self.shaper = K.VelocityShaper(limits)
        self.priority, self.stamp_mode = priority, stamp_mode
        self.ignore_battery, self.max_s = ignore_battery, max_s
        self.lock = threading.Lock()
        self.keys: set[str] = set()
        self.fast = False
        self.ui_beat = time.monotonic()
        self.armed = False
        self.armed_at = 0.0
        self.busy = False
        self.changing = False
        self.message = ""
        self.cmd = (0.0, 0.0, 0.0)
        self.obs = K.Observed()
        self.problems: list[str] = ["comprobando el robot…"]
        self._nonzero_since: float | None = None
        self.squat = 0                        # cuclilla: -1 bajando, +1 subiendo, 0 quieto (teclas F/R)
        self.offroad = False                  # offroad pedido por la persona (tecla O) y aceptado por el MC
        self.gait: str | None = None          # modo con el que se vio andar al MC por última vez
        self._last_check = self._last_log = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    # --- órdenes de la persona -------------------------------------------------------------------------------
    def set_input(self, keys: set[str], fast: bool) -> None:
        with self.lock:
            self.keys, self.fast = set(keys), fast

    def beat(self) -> None:
        self.ui_beat = time.monotonic()

    def arm(self) -> None:
        if self.armed or self.busy:
            return
        self.busy = True
        threading.Thread(target=self._arm, daemon=True).start()

    def _arm(self) -> None:
        try:
            problems = K.preflight(self.link.observed(), "andar", need_clock=self.stamp_mode == "robot",
                                   ignore_battery=self.ignore_battery)
            if problems:
                self.message = "no se arma: " + problems[0]
                return
            self.message = "registrando la fuente de entrada…"
            self.link.register_source(self.priority)
            with self.lock:
                self.keys.clear()
                self.shaper.stop()
                self._nonzero_since = None
                self.armed, self.armed_at = True, time.monotonic()
            self.message = f"ARMADO (fuente {self.link.source}, prioridad {self.priority})"
            self.log.write("armado", fuente=self.link.source, prioridad=self.priority, sello=self.stamp_mode)
        except RuntimeError as exc:
            self.message = f"no se arma: {exc}"
            self.log.write("error", donde="armar", error=str(exc))
        finally:
            self.busy = False

    def disarm(self, reason: str) -> None:
        with self.lock:
            was = self.armed
            self.armed = False
            self.keys.clear()
            self.shaper.stop()
        if was:
            self.message = f"DESARMADO: {reason}"
            self.log.write("desarmado", motivo=reason)

    def change_level(self, step: int) -> None:
        with self.lock:
            level = self.shaper.change_level(step)
        v = self.shaper.axis_speeds()
        self.message = (f"nivel {level + 1}/{len(K.SPEED_LEVELS)}: adelante {v['vx']:.2f} · atrás {v['vx_back']:.2f}"
                        f" · lateral {v['vy']:.2f} m/s · giro {v['wz']:.2f} rad/s")
        self.log.write("nivel", nivel=level + 1, velocidades=v)

    def request_stand(self) -> None:
        self._request_modes([K.MODE_STAND])

    def toggle_offroad(self) -> None:
        """O: offroad sí/no, con el robot quieto.

        Visto en el MC (gemelo, 20/10): pedir LOCOMOTION_STEP deja memorizado ese estilo de marcha; en reposo el MC
        sigue informando STAND_DEFAULT y la próxima marcha sale en LOCOMOTION_STEP. Pedir STAND_DEFAULT no lo quita;
        LOCOMOTION_DEFAULT sí, pero deja el MC en ese modo también en reposo, y desde ahí rechaza LOCOMOTION_STEP
        ("Current state is moving"). Por eso: activar = [STAND_DEFAULT] + LOCOMOTION_STEP; desactivar =
        LOCOMOTION_DEFAULT + STAND_DEFAULT.
        """
        if self.offroad:
            self._request_modes([K.MODE_LOCOMOTION, K.MODE_STAND], offroad=False)
        else:
            stand = [] if self.obs.mode == K.MODE_STAND else [K.MODE_STAND]
            self._request_modes(stand + [K.MODE_OFFROAD], offroad=True)

    def _request_modes(self, modes: list[str], offroad: bool | None = None) -> None:
        if self.busy or any(self.cmd):
            self.message = "los cambios de modo sólo con el robot parado"
            return
        if self.obs.mode not in K.WALK_MODES or self.obs.body_status != K.BODY_STAND:
            self.message = f"cambios de modo sólo de pie en {'/'.join(K.WALK_MODES)}"
            return
        if K.crouched(self.obs) or self.squat:
            self.message = "agachado: R para levantarse antes de cambiar de modo"
            return
        self.busy = self.changing = True     # sin marcha mientras cambia de modo
        with self.lock:
            self.keys.clear()
        threading.Thread(target=self._run_modes, args=(modes, offroad), daemon=True).start()

    def _run_modes(self, modes: list[str], offroad: bool | None) -> None:
        try:
            for desc in modes:
                self._change_mode(desc)
            if offroad is not None:
                self.offroad, self.gait = offroad, None
                self.message = f"offroad {'ACTIVADO' if offroad else 'desactivado'}"
                self.log.write("offroad", activado=offroad)
            else:
                self.message = f"{modes[-1]} pedido"
        except RuntimeError as exc:
            self.message = str(exc)
        finally:
            self.busy = self.changing = False

    def _change_mode(self, desc: str) -> None:
        """Pide un modo; STAND_DEFAULT se espera hasta verlo (LOCOMOTION_* no se ve en reposo).

        Antes se espera a que el MC esté STABLE: tras andar pasa ≈2-4 s en MOVING y ahí rechaza el cambio
        ("Current state is moving")."""
        self.message = f"esperando a que el MC esté estable para pedir {desc}…"
        if not self.link.wait_stable(STABLE_WAIT_S):
            raise RuntimeError(f"el MC no se estabilizó en {STABLE_WAIT_S:.0f} s; {desc} no pedido")
        self.message = f"pidiendo {desc}…"
        try:
            self.link.request_mode(desc, busy_retry_s=12.0 if desc == K.MODE_STAND else 0.0)
            result = "aceptado"
        except RuntimeError as exc:
            # La respuesta puede perderse aunque el MC lo aplique: manda el modo observado.
            if not self.link.wait_mode(desc, 3.0):
                self.log.write("modo", pedido=desc, resultado=str(exc))
                raise
            result = "observado (respuesta perdida)"
        self.log.write("modo", pedido=desc, resultado=result)
        if desc == K.MODE_STAND and not self.link.wait_mode(K.MODE_STAND, 3.0):
            raise RuntimeError("el MC no volvió a STAND_DEFAULT en 3 s")

    # --- bucle de 50 Hz ------------------------------------------------------------------------------------
    def _safety(self, obs: K.Observed, now: float) -> str | None:
        if obs.mc_age_s is None or obs.mc_age_s > K.MC_STATE_TIMEOUT_S:
            return "se perdió el estado del MC"
        if obs.mode not in K.WALK_MODES:
            return f"el MC pasó a {obs.mode}"
        if obs.fsm == K.FSM_SAFE:
            return "el MC entró en su protección de seguridad (fsm SAFE)"
        if not K.standing(obs):
            return f"el MC ya no informa de pie (body_status={obs.body_status})"
        if obs.player_state != K.PLAYER_IDLE:
            return f"el reproductor de movimientos del MC arrancó ({obs.motion!r})"
        if obs.battery is not None and obs.battery < LOW_BATTERY_STOP:
            return f"batería al {obs.battery} %"
        if now - self.armed_at > self.max_s:
            return f"fin de --duracion ({self.max_s:.0f} s)"
        return None

    def _squat_gate(self, keys: set[str], obs: K.Observed) -> set[str]:
        """F/R → self.squat (-1 bajar, +1 subir, 0 quieto). Devuelve las teclas de marcha permitidas."""
        direction = ("r" in keys) - ("f" in keys)
        walk_keys = keys & {"w", "a", "s", "d", "q", "e"}
        if direction and self.link.body_pose_pub is None:
            self.message = "cuclillas no disponibles: falta McBodyPose (scripts/build_aimdk_overlay.sh)"
            direction = 0
        elif direction and (walk_keys or any(self.cmd)):
            self.message = "cuclillas sólo con el robot parado"
            direction = 0
        elif direction and (obs.mode != K.MODE_STAND or obs.fsm != K.FSM_STABLE):
            self.message = f"cuclillas sólo en STAND_DEFAULT estable (MC en {obs.mode}, fsm {obs.fsm}); espera"
            direction = 0
        elif direction < 0 and obs.squat_offset is not None and obs.squat_offset <= K.SQUAT_LOW + 0.005:
            direction = 0                                   # ya abajo del todo
        elif direction > 0 and not K.crouched(obs):
            direction = 0                                   # ya de pie
        if direction != self.squat:
            self.log.write("cuclilla", sentido=direction, offset=obs.squat_offset)
        self.squat = direction
        if walk_keys and (K.crouched(obs) or direction):
            self.message = "agachado: R (mantener) para levantarse antes de andar"
            return keys - walk_keys
        return keys

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
                self.problems = K.preflight(obs, "andar", need_clock=self.stamp_mode == "robot",
                                            ignore_battery=self.ignore_battery)
        if self.armed:
            reason = self._safety(obs, now)
            if reason:
                self.disarm(reason)
        with self.lock:
            ui_ok = now - self.ui_beat <= UI_TIMEOUT_S
            keys = self.keys if (self.armed and ui_ok and not self.changing) else set()
            keys = self._squat_gate(keys, obs)
            cmd = self.shaper.step(keys, self.fast, dt)
        if any(cmd):
            if obs.mode in (K.MODE_LOCOMOTION, K.MODE_OFFROAD) and obs.mode != self.gait:
                self.gait = obs.mode              # el estilo de marcha sólo se ve mientras anda
                if self.offroad != (obs.mode == K.MODE_OFFROAD):
                    self.message = (f"aviso: offroad {'activado' if self.offroad else 'desactivado'} "
                                    f"pero el MC anda en {obs.mode}")
                    self.log.write("aviso", offroad=self.offroad, marcha=obs.mode)
            self._nonzero_since = self._nonzero_since or now
            if now - self._nonzero_since > SOURCE_GRACE_S and obs.input_source != self.link.source:
                self.disarm(f"el MC no sigue nuestra fuente: manda {obs.input_source or '?'} "
                            "(¿mando/app activos? ver --prioridad)")
                cmd = self.shaper.step(set(), False, dt)
        else:
            self._nonzero_since = None
        self.cmd = cmd
        # Cambiando de modo no se publican ceros: con ellos el MC sigue ≈2 s más en MOVING antes de estabilizarse.
        if self.link.registered and not (self.changing and not any(cmd)):
            try:
                self.link.publish_velocity(*cmd, self.stamp_mode)
                if self.link.body_pose_pub is not None:
                    self.link.publish_body_pose(K.SQUAT_RATE * self.squat, self.stamp_mode)
            except RuntimeError as exc:          # sin hora del robot
                self.disarm(str(exc))
        if now - self._last_log >= 0.1:
            self._last_log = now
            self.log.write("muestra", armado=self.armed, orden=[round(v, 3) for v in cmd],
                           odom=self.link.odometry(), modo=obs.mode, fuente=obs.input_source,
                           cuerpo=obs.body_status, bateria=obs.battery, ui_ok=ui_ok)

    def _stand_up_before_exit(self) -> None:
        """Si quedó agachado, subir (+0,5) hasta estar de pie, como mucho 8 s, con el MC sano y en STAND_DEFAULT."""
        obs = self.link.observed(graph=False)
        if self.link.body_pose_pub is None or not self.link.registered or not K.crouched(obs):
            return
        print(f"levantándolo antes de salir (cuclilla {obs.squat_offset:+.2f})…")
        self.log.write("cuclilla", sentido=1, motivo="salida", offset=obs.squat_offset)
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            obs = self.link.observed(graph=False)
            if (not K.crouched(obs) or obs.mode != K.MODE_STAND or obs.mc_age_s is None
                    or obs.mc_age_s > K.MC_STATE_TIMEOUT_S):
                break
            try:
                self.link.publish_body_pose(K.SQUAT_RATE, self.stamp_mode)
            except RuntimeError:
                self.link.publish_body_pose(K.SQUAT_RATE, "cero")
            time.sleep(TICK_S)
        for _ in range(5):
            self.link.publish_body_pose(0.0, "cero")
            time.sleep(TICK_S)
        obs = self.link.observed(graph=False)
        self.log.write("cuclilla", sentido=0, motivo="salida", offset=obs.squat_offset)
        if K.crouched(obs):
            print(f"aviso: sigue agachado (cuclilla {obs.squat_offset:+.2f}); levantarlo con la app o el mando")

    def shutdown(self) -> None:
        """Ceros durante 0,3 s, salir del offroad (a STAND_DEFAULT) si hace falta y borrar la fuente."""
        self.disarm("salida")
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self.link.registered:
            for _ in range(15):
                try:
                    self.link.publish_velocity(0.0, 0.0, 0.0, self.stamp_mode)
                except RuntimeError:
                    self.link.publish_velocity(0.0, 0.0, 0.0, "cero")
                time.sleep(TICK_S)
        self._stand_up_before_exit()
        if self.offroad or self.gait == K.MODE_OFFROAD:
            # Dejar la marcha normal para el mando y la app: LOCOMOTION_DEFAULT quita el offroad memorizado.
            try:
                for desc in (K.MODE_LOCOMOTION, K.MODE_STAND):
                    self._change_mode(desc)
            except RuntimeError as exc:
                print(f"aviso: no se pudo quitar el offroad ({exc}); quitarlo con el mando o la app")
        error = self.link.delete_source()
        self.log.write("salida", borrar_fuente=error or "ok")
        if error:
            print(f"aviso: {error} (la fuente caduca sola; se borra al reiniciar el MC)")


def run_window(ctl: WalkController, stop) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("X2 REAL · marcha WASD")
    root.geometry("860x460")
    root.configure(bg=BG)
    label = tk.Label(root, justify="left", font=FONT, fg=FG, bg=BG, anchor="nw")
    label.pack(padx=14, pady=10, fill="both", expand=True)
    held = HeldKeys(root, {"w", "a", "s", "d", "q", "e", "f", "r"}, lambda: ctl.set_input(held.down, held.shift))

    def press(event):
        if held.press(event):
            return
        k = held.name(event)
        if k in ("return", "kp_enter"):
            ctl.arm()
        elif k == "space":
            ctl.disarm("Espacio")
        elif k == "1":
            ctl.request_stand()
        elif k in ("plus", "equal", "kp_add"):
            ctl.change_level(+1)
        elif k in ("minus", "kp_subtract", "underscore"):
            ctl.change_level(-1)
        elif k == "o":
            ctl.toggle_offroad()
        elif k == "escape":
            root.destroy()

    def tick():
        if stop.is_set():
            root.destroy()
            return
        ctl.beat()
        o, (vx, vy, wz) = ctl.obs, ctl.cmd
        odom = ctl.link.odometry()
        lines = [
            "Intro armar · W/S avanzar/atrás · A/D lateral · Q/E girar · Mayús rápido",
            "+ / - nivel de velocidad · O offroad sí/no (parado) · F / R agacharse / levantarse (mantener)",
            "Espacio parar y desarmar · 1 pedir STAND_DEFAULT · Esc salir",
            "",
            f"estado: {ctl.message}",
            f"orden : vx {vx:+.2f} m/s  vy {vy:+.2f} m/s  wz {wz:+.2f} rad/s",
            f"nivel {ctl.shaper.level + 1}/{len(K.SPEED_LEVELS)} (×{K.SPEED_LEVELS[ctl.shaper.level]:.2f})   "
            "offroad " + ("SÍ" if ctl.offroad else "no") +
            (f" · última marcha vista: {ctl.gait}" if ctl.gait else ""),
            "cuclilla: " + ("no disponible (scripts/build_aimdk_overlay.sh)" if ctl.link.body_pose_pub is None else
                            "—" if o.squat_offset is None else
                            f"{o.squat_offset:+.3f} (0 de pie, {K.SQUAT_LOW} abajo)"
                            + {-1: "  ▼ bajando", 1: "  ▲ subiendo", 0: ""}[ctl.squat]),
            "odom  : " + ("—" if odom is None else f"vx {fmt(odom[0], 5, 2)}  vy {fmt(odom[1], 5, 2)}  "
                                                    f"wz {fmt(odom[2], 5, 2)}"),
            f"MC    : {o.mode or '?'} ({o.action_status})  fuente {o.input_source or '?'}  "
            f"de pie {o.body_status == K.BODY_STAND}",
            f"batería {o.battery if o.battery is not None else '?'} %   reloj robot "
            f"{'—' if o.clock_offset_ms is None else f'{o.clock_offset_ms / 1000:+.3f} s'}   "
            f"otros publicadores de velocidad {o.velocity_publishers}",
        ]
        if not ctl.armed:
            lines += ["", "falta:"] + [f"  · {p}" for p in ctl.problems[:5]] if ctl.problems else \
                ["", "todo en orden: Intro para armar"]
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
    ap.add_argument("--vx", type=float, default=0.3, help="adelante [m/s] en el nivel 3, 0,2..0,8")
    ap.add_argument("--vx-atras", type=float, default=0.2, help="atrás [m/s] en el nivel 3, 0,2..0,5")
    ap.add_argument("--vy", type=float, default=0.5,
                    help="lateral [m/s] en el nivel 3, 0,2..0,8 (desde parado hace falta ≈0,5)")
    ap.add_argument("--wz", type=float, default=0.6,
                    help="giro [rad/s] en el nivel 3, 0,1..1,0 (0,45 no arrancó en el sitio)")
    ap.add_argument("--rapido", type=float, default=1.5, help="factor con Mayús (sin pasar de los topes)")
    ap.add_argument("--prioridad", type=int, default=65,
                    help="prioridad de la fuente, 20..69 (app 60, mando 80); 65 es la de los ensayos de brazos")
    ap.add_argument("--sello", choices=STAMP_MODES, default="robot",
                    help="sello de las órdenes: robot (hora estimada del robot, como los ejemplos), cero o pc")
    ap.add_argument("--duracion", type=float, default=600.0, help="segundos armado como máximo")
    ap.add_argument("--ignorar-bateria", action="store_true", help="armar aunque no llegue la batería")
    args = ap.parse_args(argv)
    limits = K.WalkLimits(vx=args.vx, vx_back=args.vx_atras, vy=args.vy, wz=args.wz, fast=args.rapido)
    try:
        limits.validate()
    except ValueError as exc:
        ap.error(str(exc))
    if not K.MIN_PRIORITY <= args.prioridad <= K.MAX_PRIORITY:
        ap.error(f"--prioridad {K.MIN_PRIORITY}..{K.MAX_PRIORITY}: el mando (80) debe poder retomar")
    if not 5.0 <= args.duracion <= 3600.0:
        ap.error("--duracion 5..3600 s")
    target = require_target()
    stop = stop_on_signals()
    log = SessionLog("andar")
    link = RobotLink("andar", publish_velocity=True, publish_body_pose=True)
    ctl = WalkController(link, limits, priority=args.prioridad, stamp_mode=args.sello,
                         ignore_battery=args.ignorar_bateria, max_s=args.duracion, log=log)
    log.write("inicio", destino=target, args=vars(args))
    print(f"X2 real · marcha ({target}); registro en {log.path}")
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
