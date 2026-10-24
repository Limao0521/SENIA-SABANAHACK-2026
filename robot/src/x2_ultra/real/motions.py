"""Gestos, bailes de fábrica y movimientos de LinkCraft del X2 Ultra REAL.

MUEVE EL ROBOT. Lo lanza una persona que mira el robot, con el mando a mano y espacio libre alrededor (los bailes y
los LinkCraft de cuerpo entero mueven también las piernas): ``scripts/robot_real.sh movimientos``. Guía:
docs/09-robot-real.md.

Ventana Tk: lista de movimientos (↑/↓ o ratón) · Intro o doble clic reproducir (los de cuerpo entero piden Intro otra
vez en 5 s) · 1 pedir STAND_DEFAULT (sin nada reproduciéndose) · Esc salir.

Tres fuentes, cada una con su servicio:
  - Gestos (``SetMcPresetMotion``): los 29 combinados movimiento + zona documentados en AimDK (§5.1.6).
  - LinkCraft (``GetRobotResources`` / ``ExecuteActionResource``, app_proxy): lo cargado en el robot; si la clave
    contiene ``onnx`` es de cuerpo entero (``BODY_MONTION``), si no de brazos (``ARM_MONTION``).
  - Bailes de fábrica (``GetMcMotions`` / ``SetMcMotion``, tipo MIMIC, de cuerpo entero): no están en el AimDK
    público; hacen falta del overlay (scripts/build_aimdk_overlay.sh). La voltereta y los saltos no se ofrecen.

Sólo se reproduce con el robot de pie en STAND_DEFAULT estable, sin otro movimiento en curso, sin cuclilla y con
batería ≥30 %. Las peticiones van sin ``interrupt``: si una copia reenviada llega mientras el robot ya se mueve, el MC
la rechaza en vez de empezar otra vez. No hay tecla de parar: un movimiento de cuerpo entero cortado a medias puede
dejar el robot en una postura inestable, así que termina solo; el mando sigue teniendo prioridad.

Nunca pide DAMPING, PASSIVE ni JOINT_DEFAULT: el único modo que puede pedir es STAND_DEFAULT (tecla 1).
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import threading
import time

from x2_ultra.real import control as K
from x2_ultra.real.link import RobotLink, require_target
from x2_ultra.real.ui import BG, FG, FONT, SessionLog, stop_on_signals

SRV_PRESET = "/aimdk_5Fmsgs/srv/SetMcPresetMotion"
SRV_RESOURCES = "/aimdk_5Fmsgs/srv/GetRobotResources"
SRV_EXECUTE = "/aimdk_5Fmsgs/srv/ExecuteActionResource"
SRV_MOTIONS = "/aimdk_5Fmsgs/srv/GetMcMotions"
SRV_MOTION = "/aimdk_5Fmsgs/srv/SetMcMotion"
MIMIC = 2
CONFIRM_S = 5.0
TICK_S = 0.05
LINKCRAFT_OK = "Play success"

# Tabla de AimDK §5.1.6 (combinaciones movimiento + zona admitidas desde v0.8.0). Zona: 1 izq., 2 der., 3 ambas,
# 11 cuerpo superior.
PRESETS = (
    ("Saludar (mano derecha)", 1002, 2), ("Saludar (mano izquierda)", 1002, 1),
    ("Dar la mano (derecha)", 1003, 2), ("Dar la mano (izquierda)", 1003, 1),
    ("Levantar la mano (derecha)", 1001, 2), ("Levantar la mano (izquierda)", 1001, 1),
    ("Lanzar un beso (derecha)", 1004, 2), ("Lanzar un beso (izquierda)", 1004, 1),
    ("Aplaudir", 3017, 11),
    ("Saludo militar (derecha)", 1013, 2), ("Saludo militar (izquierda)", 1013, 1),
    ("Corazón (dos manos)", 1007, 3), ("Corazón (derecha)", 1007, 2), ("Corazón (izquierda)", 1007, 1),
    ("Abrazo", 3008, 11), ("Animar", 3011, 11),
    ("Levantar las dos manos", 1010, 3), ("Levantar la mano derecha", 1010, 2), ("Levantar la mano izquierda", 1010, 1),
    ("Decir adiós", 3031, 11), ("Ola dinámica", 3007, 11),
    ("Chocar los cinco (derecha)", 1008, 2), ("Chocar los cinco (izquierda)", 1008, 1),
    ("Cruzar los brazos", 3009, 11),
    ("Saludar a la altura del pecho (derecha)", 1011, 2), ("Saludar a la altura del pecho (izquierda)", 1011, 1),
    ("Reverencia", 3001, 11), ("Rascarse la cabeza", 3024, 11), ("Agarrarse el trasero", 3025, 11),
)
# Bailes de fábrica (GetMcMotions, tipo MIMIC) que NO se ofrecen: el robot sale del suelo o rueda por él.
BLOCKED_MIMIC = {
    "M_ROLL_FORWARD": "voltereta hacia delante (rueda por el suelo)",
    "M_LONG_JUMP": "salto de longitud",
    "M_HIGH_JUMP": "salto de altura",
}
MIMIC_NAMES = {
    "M_CHARLESTON_DANCE": "Charlestón", "M_FAN_DANCE": "Baile del abanico", "M_NIUNIU_DANCE": "Baile Niuniu",
    "M_KUAILECHONGBAI": "Kuaile Chongbai (canción)", "M_KUAILECHONGBAI_VIDEO": "Kuaile Chongbai (versión vídeo)",
    "M_DINGDING": "Dingding", "M_BOXING": "Boxeo", "M_FOOTBALL": "Fútbol (patada)",
}


@dataclass(frozen=True)
class Motion:
    kind: str                 # "gesto" | "linkcraft" | "baile"
    name: str
    full_body: bool
    key: str = ""             # tag del baile o clave del recurso LinkCraft
    version: str = ""
    motion_id: int = 0
    area: int = 0

    @property
    def label(self) -> str:
        return f"{self.name}{'   [CUERPO ENTERO]' if self.full_body else ''}"


def catalog(resources: list[tuple[str, str, str]], mimic_tags: list[str] | None) -> list[tuple[str, list[Motion]]]:
    """Secciones de la lista. ``resources``: (clave, nombre, versión) de LinkCraft; ``mimic_tags``: tags MIMIC de
    GetMcMotions (None si el tipo no está disponible). Los LinkCraft de cuerpo entero también aparecen como MIMIC con
    su clave + versión: se listan sólo una vez, como LinkCraft."""
    gestures = [Motion("gesto", n, False, motion_id=m, area=a) for n, m, a in PRESETS]
    linkcraft = sorted((Motion("linkcraft", name or key, "onnx" in key, key=key, version=version)
                        for key, name, version in resources), key=lambda m: (not m.full_body, m.name.lower()))
    sections = [("Gestos (SetMcPresetMotion)", gestures), ("LinkCraft (cargados en el robot)", linkcraft)]
    if mimic_tags is not None:
        dances = [Motion("baile", MIMIC_NAMES.get(t, t), True, key=t) for t in sorted(set(mimic_tags))
                  if t not in BLOCKED_MIMIC and not t.startswith("linkcraft_resource")]
        sections.append(("Bailes de fábrica (SetMcMotion)", dances))
    return sections


def ready_problems(obs: K.Observed, *, ignore_battery: bool = False) -> list[str]:
    """Motivos para no reproducir ahora (lista vacía = se puede)."""
    problems = []
    if obs.mc_age_s is None or obs.mc_age_s > K.MC_STATE_TIMEOUT_S:
        return ["no llega el estado del MC: ¿cable y 'nmcli con up \"X2 robot\"'?"]
    if obs.mode != K.MODE_STAND or obs.action_status != K.ACTION_RUNNING:
        problems.append(f"el MC está en {obs.mode or '?'}: hace falta STAND_DEFAULT (tecla 1)")
    if obs.fsm != K.FSM_STABLE:
        problems.append(f"el MC no está estable (fsm {obs.fsm}); espera unos segundos")
    if obs.body_status != K.BODY_STAND:
        problems.append(f"el MC no informa de pie (body_status={obs.body_status})")
    if K.crouched(obs):
        problems.append("el robot está en cuclillas: levantarlo antes")
    if obs.player_state != K.PLAYER_IDLE:
        problems.append(f"ya se está reproduciendo un movimiento ({obs.motion!r})")
    if obs.battery is None:
        if not ignore_battery:
            problems.append("batería desconocida; comprobarla y usar --ignorar-bateria")
    elif obs.battery < K.MIN_BATTERY:
        problems.append(f"batería al {obs.battery} % (< {K.MIN_BATTERY} %)")
    return problems


def judge_preset(response) -> tuple[bool, str]:
    r = response.response
    code, state = int(r.header.code), int(r.state.value)
    if code == 0 or state == 400:          # 400 = RUNNING: ya se está ejecutando
        return True, f"gesto aceptado (tarea {r.task_id})"
    return False, f"gesto rechazado (code={code}, state={state})"


def judge_linkcraft(response) -> tuple[bool, str]:
    # La guía avisa: header.status es siempre FAILURE en el firmware actual; manda header.message.
    message = str(response.header.message)
    return message == LINKCRAFT_OK, message or f"code={response.header.header.code}"


def judge_mimic(response) -> tuple[bool, str]:
    r = response.response
    code, status = int(r.header.code), int(r.status.value)
    ok = code == 0 and status in (0, 1)
    return ok, (r.message or ("baile aceptado" if ok else f"baile rechazado (code={code}, status={status})"))


class MotionsController:
    def __init__(self, link: RobotLink, *, priority: int, ignore_battery: bool, log: SessionLog):
        self.link, self.log = link, log
        self.priority, self.ignore_battery = priority, ignore_battery
        self.sections: list[tuple[str, list[Motion]]] = []
        self.blocked: list[str] = []
        self.obs = K.Observed()
        self.problems: list[str] = ["comprobando el robot…"]
        self.message = ""
        self.busy = False
        self.pending: tuple[Motion, float] | None = None     # cuerpo entero esperando confirmación
        self.playing: str = ""
        self.alert = ""
        self._last_log = 0.0
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)

    # --- catálogo --------------------------------------------------------------------------------------------
    def load(self) -> None:
        S = self.link.S
        resources = []
        try:
            reply = self.link.send(self.link.client(S.GetRobotResources, SRV_RESOURCES),
                                   S.GetRobotResources.Request, "GetRobotResources", wait_s=1.0)
            resources = [(str(r.resource_key), str(r.current_version.name), str(r.current_version.version))
                         for r in reply.robot_resources]
        except RuntimeError as exc:
            self.message = f"no se pudo leer LinkCraft: {exc}"
        mimic = None
        if getattr(S, "GetMcMotions", None) is not None and getattr(S, "SetMcMotion", None) is not None:
            try:
                reply = self.link.send(self.link.client(S.GetMcMotions, SRV_MOTIONS),
                                       S.GetMcMotions.Request, "GetMcMotions", wait_s=1.0)
                tags = [(str(m.tag), int(m.type.value)) for m in reply.motion]
                mimic = [t for t, kind in tags if kind == MIMIC]
                self.blocked = [f"{t}: {why}" for t, why in BLOCKED_MIMIC.items() if t in mimic]
            except RuntimeError as exc:
                self.message = f"no se pudo leer la lista de bailes: {exc}"
        else:
            self.blocked = ["bailes de fábrica no disponibles: falta SetMcMotion (scripts/build_aimdk_overlay.sh)"]
        self.sections = catalog(resources, mimic)
        self.log.write("catalogo", linkcraft=[r[1] for r in resources], bailes=mimic, bloqueados=list(BLOCKED_MIMIC))

    def items(self) -> list[Motion | str]:
        """Lista plana para la ventana: cabeceras (str) y movimientos."""
        out: list[Motion | str] = []
        for title, motions in self.sections:
            out.append(f"── {title} ──")
            out.extend(motions)
        return out

    # --- reproducir ------------------------------------------------------------------------------------------
    def request(self, motion: Motion, now: float | None = None) -> None:
        """Intro sobre un movimiento. Los de cuerpo entero necesitan un segundo Intro sobre el mismo en 5 s."""
        now = time.monotonic() if now is None else now
        if self.busy:
            return
        problems = ready_problems(self.obs, ignore_battery=self.ignore_battery)
        if problems:
            self.message = "no se reproduce: " + problems[0]
            self.pending = None
            return
        if motion.full_body and not (self.pending and self.pending[0] == motion and now - self.pending[1] <= CONFIRM_S):
            self.pending = (motion, now)
            self.message = (f"CUERPO ENTERO: «{motion.name}» mueve también las piernas. Deja ≥2 m libres alrededor y "
                            f"pulsa Intro otra vez en {CONFIRM_S:.0f} s para confirmar")
            return
        self.pending = None
        self.busy = True
        threading.Thread(target=self._play, args=(motion,), daemon=True).start()

    def _play(self, motion: Motion) -> None:
        S, M = self.link.S, self.link.M
        try:
            if not self.link.registered:
                self.link.register_source(self.priority)

            def source(target):
                target.name, target.priority, target.timeout = self.link.source, self.priority, 1000

            if motion.kind == "gesto":
                def make():
                    req = S.SetMcPresetMotion.Request()
                    # En este servicio el sello es la hora de reproducción (0 = ya); la del PC podría ir por delante.
                    req.header.stamp.sec, req.header.stamp.nanosec = 0, 0
                    source(req.input_source)
                    req.motion.value, req.area.value, req.interrupt = motion.motion_id, motion.area, False
                    return req
                ok, text = judge_preset(self.link.send(self.link.client(S.SetMcPresetMotion, SRV_PRESET), make,
                                                       "SetMcPresetMotion"))
            elif motion.kind == "linkcraft":
                def make():
                    req = S.ExecuteActionResource.Request()
                    req.resource_key, req.resource_version = motion.key, motion.version
                    req.meta = json.dumps({"resource_type": "BODY_MONTION" if motion.full_body else "ARM_MONTION"})
                    return req
                ok, text = judge_linkcraft(self.link.send(self.link.client(S.ExecuteActionResource, SRV_EXECUTE),
                                                          make, "ExecuteActionResource", wait_s=1.0))
            else:
                def make():
                    req = S.SetMcMotion.Request()
                    req.header.stamp = self.link.node.get_clock().now().to_msg()
                    source(req.input_source)
                    req.motion = M.McMotion()
                    req.motion.tag, req.motion.type.value = motion.key, MIMIC
                    req.interrupt, req.play_timestamp = False, 0
                    return req
                ok, text = judge_mimic(self.link.send(self.link.client(S.SetMcMotion, SRV_MOTION), make,
                                                      "SetMcMotion", wait_s=1.0))
            self.message = f"{'▶' if ok else '✗'} {motion.name}: {text}"
            self.log.write("reproducir", tipo=motion.kind, nombre=motion.name, clave=motion.key,
                           id=motion.motion_id, zona=motion.area, ok=ok, respuesta=text)
        except RuntimeError as exc:
            self.message = f"✗ {motion.name}: {exc}"
            self.log.write("error", nombre=motion.name, error=str(exc))
        finally:
            self.busy = False

    def request_stand(self) -> None:
        if self.busy or self.obs.player_state != K.PLAYER_IDLE:
            self.message = "STAND_DEFAULT sólo sin nada reproduciéndose"
            return
        self.busy = True

        def work():
            try:
                if not self.link.wait_stable(6.0):
                    raise RuntimeError("el MC no se estabilizó en 6 s")
                self.message = "pidiendo STAND_DEFAULT (si el MC está ocupado, se reintenta 12 s)…"
                self.link.request_mode(K.MODE_STAND, busy_retry_s=12.0)
                self.message = "STAND_DEFAULT pedido"
            except RuntimeError as exc:
                self.message = f"{exc} → usar el mando o la app para volver a STAND_DEFAULT"
            finally:
                self.log.write("modo", pedido=K.MODE_STAND, resultado=self.message)
                self.busy = False
        threading.Thread(target=work, daemon=True).start()

    # --- vigilancia --------------------------------------------------------------------------------------------
    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            self.tick(time.monotonic())
            time.sleep(TICK_S)

    def tick(self, now: float) -> None:
        obs = self.link.observed(graph=False)
        self.obs = obs
        self.problems = ready_problems(obs, ignore_battery=self.ignore_battery)
        playing = obs.motion if obs.player_state not in (None, K.PLAYER_IDLE) else ""
        if playing != self.playing:
            self.log.write("reproductor", movimiento=playing, estado=obs.player_state, modo=obs.mode, fsm=obs.fsm)
            self.playing = playing
        if obs.body_status in (2, 3, 6) or obs.fsm == K.FSM_SAFE:
            alert = f"ALERTA: el MC informa body_status={obs.body_status}, fsm={obs.fsm} (¿caída?)"
            if alert != self.alert:
                self.log.write("alerta", cuerpo=obs.body_status, fsm=obs.fsm, modo=obs.mode)
            self.alert = alert
        if now - self._last_log >= 0.1:
            self._last_log = now
            self.log.write("muestra", modo=obs.mode, fsm=obs.fsm, cuerpo=obs.body_status, reproductor=obs.player_state,
                           movimiento=obs.motion, bateria=obs.battery)

    def shutdown(self) -> None:
        self._running = False
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        if self.obs.player_state not in (None, K.PLAYER_IDLE):
            print(f"aviso: el robot sigue reproduciendo {self.obs.motion!r}; termina solo")
        error = self.link.delete_source()
        self.log.write("salida", borrar_fuente=error or "ok")


def run_window(ctl: MotionsController, stop) -> None:
    import tkinter as tk

    root = tk.Tk()
    root.title("X2 REAL · movimientos")
    root.geometry("900x700")
    root.configure(bg=BG)
    header = tk.Label(root, justify="left", font=FONT, fg=FG, bg=BG, anchor="nw")
    header.pack(padx=14, pady=(10, 4), fill="x")
    items = ctl.items()
    listbox = tk.Listbox(root, font=FONT, fg=FG, bg="#1c2432", selectbackground="#2f5d8a", activestyle="none",
                         highlightthickness=0, borderwidth=0)
    for item in items:
        listbox.insert("end", f"  {item}" if isinstance(item, str) else f"     {item.label}")
        if isinstance(item, str):
            listbox.itemconfig("end", fg="#8fa3bf")
    listbox.pack(padx=14, pady=4, fill="both", expand=True)
    footer = tk.Label(root, justify="left", font=FONT, fg="#8fa3bf", bg=BG, anchor="w", wraplength=860,
                      text="No se ofrecen: " + ("; ".join(ctl.blocked) if ctl.blocked else "—"))
    header.config(wraplength=860)
    footer.pack(padx=14, pady=(0, 10), fill="x")
    first = next((i for i, item in enumerate(items) if not isinstance(item, str)), 0)
    listbox.selection_set(first)
    listbox.activate(first)

    def selected() -> Motion | None:
        sel = listbox.curselection()
        item = items[sel[0]] if sel else None
        return item if isinstance(item, Motion) else None

    def play(_event=None):
        motion = selected()
        if motion is not None:
            ctl.request(motion)
        return "break"

    def press(event):
        k = event.keysym.lower()
        if k == "1":
            ctl.request_stand()
        elif k == "escape":
            root.destroy()

    def tick():
        if stop.is_set():
            root.destroy()
            return
        o = ctl.obs
        lines = [
            "↑/↓ elegir · Intro o doble clic reproducir (cuerpo entero: Intro otra vez para confirmar)",
            "1 pedir STAND_DEFAULT · Esc salir",
            "Sin tecla de parar: el movimiento termina solo; ante un problema, el mando.",
            "",
            f"estado: {ctl.message}",
            f"MC: {o.mode or '?'} (fsm {o.fsm})  de pie {o.body_status == K.BODY_STAND}  batería "
            f"{o.battery if o.battery is not None else '?'} %  reproductor: "
            + (f"REPRODUCIENDO {o.motion!r}" if o.player_state not in (None, K.PLAYER_IDLE) else "libre"),
        ]
        if ctl.alert:
            lines.append(ctl.alert)
        lines.append("listo: elige y pulsa Intro" if not ctl.problems else "ahora no: " + ctl.problems[0])
        header.config(text="\n".join(lines))
        root.after(100, tick)

    listbox.bind("<Return>", play)
    listbox.bind("<KP_Enter>", play)
    listbox.bind("<Double-Button-1>", play)
    root.bind("<KeyPress>", press)
    root.after(100, tick)
    listbox.focus_set()
    root.focus_force()
    root.mainloop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prioridad", type=int, default=65, help="prioridad de la fuente, 20..69 (mando 80)")
    ap.add_argument("--ignorar-bateria", action="store_true", help="reproducir aunque no llegue la batería")
    ap.add_argument("--listar", action="store_true", help="sólo imprimir el catálogo (consulta; no mueve nada)")
    args = ap.parse_args(argv)
    if not K.MIN_PRIORITY <= args.prioridad <= K.MAX_PRIORITY:
        ap.error(f"--prioridad {K.MIN_PRIORITY}..{K.MAX_PRIORITY}: el mando (80) debe poder retomar")
    target = require_target()
    stop = stop_on_signals()
    log = SessionLog("movimientos")
    link = RobotLink("movimientos")
    ctl = MotionsController(link, priority=args.prioridad, ignore_battery=args.ignorar_bateria, log=log)
    log.write("inicio", destino=target, args=vars(args))
    try:
        link.wait_ready()
        ctl.load()
        if args.listar:
            for title, motions in ctl.sections:
                print(f"== {title} ({len(motions)})")
                for m in motions:
                    print(f"   {m.label}")
            print("== no se ofrecen:", "; ".join(ctl.blocked) or "—")
            return 0
        print(f"X2 real · movimientos ({target}); registro en {log.path}")
        ctl.start()
        run_window(ctl, stop)
    finally:
        ctl.shutdown()
        link.close()
        log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
