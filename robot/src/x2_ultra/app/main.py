"""Simulador interactivo del AgiBot X2 Ultra-N + OmniHand (sin gafas VR).

    x2-sim                         # escena con mesa y objetos, policy "brazos libres"
    x2-sim --scene vacia
    x2-sim --policy agibot         # policy oficial de AgiBot (mueve también los brazos)

Teclado (pulsa H en la ventana para verlo):
  W/S avanzar/retroceder · A/D lateral · Q/E girar · Mayús más rápido · Espacio parar
  I/K J/L U/O mover la mano (IK) · Z/X C/V B/N girar la mano · Tab cambiar de brazo
  G puño/abrir · T pinza/abrir · 0-9 poses de brazos · flechas empujar · R reiniciar · P pausa
"""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "egl")   # render fuera de pantalla; con MUJOCO_GL=glfw también funciona

import argparse  # noqa: E402
import math  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QObject, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QTabWidget  # noqa: E402

from x2_ultra import paths  # noqa: E402
from x2_ultra.app.panels import ArmsPanel, HandsPanel, IKPanel, PosesPanel, WalkPanel  # noqa: E402
from x2_ultra.app.viewport import MujocoViewport  # noqa: E402
from x2_ultra.control import obs_spec as S  # noqa: E402
from x2_ultra.control.arm_ik import ArmIK, rot_exp  # noqa: E402
from x2_ultra.control.poses import ARM_POSES, POSE_BY_KEY  # noqa: E402
from x2_ultra.robot import joints as J  # noqa: E402
from x2_ultra.robot import omnihand as H  # noqa: E402
from x2_ultra.sim.engine import SimEngine  # noqa: E402

HELP = """CONTROLES (H para ocultar)

Marcha (mantener pulsado)
  W / S      avanzar / retroceder
  A / D      paso lateral izq. / der.
  Q / E      girar izq. / der.
  Mayús      más rápido
  Espacio    parar en seco

Brazos con IK (mano del brazo activo)
  I / K      adelante / atrás
  J / L      izquierda / derecha
  U / O      arriba / abajo
  Z / X      alabeo   C / V  cabeceo
  B / N      guiñada
  Tab        brazo: izq -> der -> ambos

Manos y poses
  G          puño / abrir
  T          pinza / abrir
  0 - 9      poses de brazos

Otros
  Flechas    empujar al robot
  R          reiniciar        P  pausa
  F          cámara: seguir / libre
  Ratón      izq. orbitar · der. mover · rueda zoom"""

MOVE_KEYS = {Qt.Key.Key_W, Qt.Key.Key_S, Qt.Key.Key_A, Qt.Key.Key_D, Qt.Key.Key_Q, Qt.Key.Key_E, Qt.Key.Key_Shift}
IK_KEYS = {Qt.Key.Key_I: ("x", 1), Qt.Key.Key_K: ("x", -1), Qt.Key.Key_J: ("y", 1), Qt.Key.Key_L: ("y", -1),
           Qt.Key.Key_U: ("z", 1), Qt.Key.Key_O: ("z", -1), Qt.Key.Key_Z: ("roll", -1), Qt.Key.Key_X: ("roll", 1),
           Qt.Key.Key_C: ("pitch", -1), Qt.Key.Key_V: ("pitch", 1), Qt.Key.Key_B: ("yaw", 1),
           Qt.Key.Key_N: ("yaw", -1)}
IK_LIN_SPEED, IK_ANG_SPEED = 0.25, 1.2          # m/s, rad/s mientras se mantiene la tecla
IK_LIMITS = {"x": 0.45, "y": 0.45, "z": 0.45, "roll": math.pi / 2, "pitch": math.pi / 2, "yaw": math.pi / 2}
ARM_SLICE = {"L": slice(0, 7), "R": slice(7, 14)}
MIRROR_SIGN = {"x": 1, "y": -1, "z": 1, "roll": -1, "pitch": 1, "yaw": -1}


def _rpy(roll: float, pitch: float, yaw: float) -> np.ndarray:
    return rot_exp(np.array([0, 0, yaw])) @ rot_exp(np.array([0, pitch, 0])) @ rot_exp(np.array([roll, 0, 0]))


class Controller:
    """Traduce teclado, sliders e IK en objetivos para el motor."""

    def __init__(self, engine: SimEngine, viewport: MujocoViewport):
        self.engine, self.viewport = engine, viewport
        self.ik = ArmIK(engine.model)
        self.keys: set = set()
        self.selected_arm = "L"
        self.ik_base: dict = {}
        self.ik_offsets: dict = {}
        self.ik_targets: dict = {}
        self.ik_errors: dict = {}
        self.ik_q: dict = {}
        self.grasp_preset = {"L": "puño", "R": "puño"}
        self.message = ""
        self.message_until = 0.0

    # --------------------------------------------------------------- estado general
    def flash(self, text: str, seconds: float = 2.0) -> None:
        self.message, self.message_until = text, time.time() + seconds

    def reset(self) -> None:
        self.engine.reset()
        self._clear_ik()
        self.flash("Robot reiniciado")

    def toggle_pause(self) -> None:
        self.engine.paused = not self.engine.paused

    def _clear_ik(self, sides=("L", "R")) -> None:
        for s in sides:
            for dct in (self.ik_base, self.ik_offsets, self.ik_targets, self.ik_errors, self.ik_q):
                dct.pop(s, None)

    def _sides(self):
        return ("L", "R") if self.selected_arm == "LR" else (self.selected_arm,)

    def select_arm(self, key: str) -> None:
        self.selected_arm = key
        self.flash({"L": "Brazo activo: izquierdo", "R": "Brazo activo: derecho", "LR": "Brazos: ambos (espejo)"}[key])

    def cycle_arm(self) -> None:
        self.select_arm({"L": "R", "R": "LR", "LR": "L"}[self.selected_arm])

    # --------------------------------------------------------------- brazos / cabeza
    def play_pose(self, name: str) -> None:
        self.engine.play_pose(name)
        self._clear_ik()
        self.flash(f"Pose: {ARM_POSES[name].label}")

    def set_arm_joint(self, name: str, value: float, mirror: bool) -> None:
        q = self.engine.arm_target.copy()
        q[J.ARM_JOINTS.index(name)] = value
        sides = ["L" if name.startswith("left") else "R"]
        if mirror:
            other = J.mirror_name(name)
            sign = -1 if any(k in name for k in ("roll", "yaw")) else 1
            q[J.ARM_JOINTS.index(other)] = sign * value
            sides = ["L", "R"]
        self.engine.set_arm_targets(q)
        self._clear_ik(sides)

    def set_head_joint(self, name: str, value: float) -> None:
        yaw, pitch = self.engine.head_target
        if name == "head_yaw_joint":
            yaw = value
        else:
            pitch = value
        self.engine.set_head_targets(yaw, pitch)

    def set_head(self, yaw: float, pitch: float) -> None:
        self.engine.set_head_targets(yaw, pitch)

    # --------------------------------------------------------------- IK
    def _ensure_ik(self, side: str) -> None:
        if side in self.ik_base:
            return
        q = self.engine.arm_target[ARM_SLICE[side]].copy()
        p, R = self.ik.fk(side, q)
        self.ik_base[side] = (p, R)
        self.ik_offsets[side] = {a: 0.0 for a in IK_LIMITS}
        self.ik_q[side] = q
        if self.engine.anim is not None:
            self.engine.set_arm_targets(self.engine.arm_target)    # congela la animación

    def reset_ik_targets(self) -> None:
        self._clear_ik(self._sides())
        for s in self._sides():
            self._ensure_ik(s)
        self.flash("Objetivo IK = postura actual")

    def set_ik_offset(self, axis: str, value: float) -> None:
        for s in self._sides():
            self._ensure_ik(s)
            sign = MIRROR_SIGN[axis] if (self.selected_arm == "LR" and s == "R") else 1
            self.ik_offsets[s][axis] = float(np.clip(sign * value, -IK_LIMITS[axis], IK_LIMITS[axis]))
        self._solve_ik(self._sides())

    def _nudge_ik(self, dt: float) -> None:
        active = [IK_KEYS[k] for k in self.keys if k in IK_KEYS]
        if not active:
            return
        for s in self._sides():
            self._ensure_ik(s)
            for axis, direction in active:
                speed = IK_LIN_SPEED if axis in ("x", "y", "z") else IK_ANG_SPEED
                sign = MIRROR_SIGN[axis] if (self.selected_arm == "LR" and s == "R") else 1
                off = self.ik_offsets[s]
                off[axis] = float(np.clip(off[axis] + sign * direction * speed * dt, -IK_LIMITS[axis], IK_LIMITS[axis]))
        self._solve_ik(self._sides())

    def _solve_ik(self, sides) -> None:
        q14 = self.engine.arm_target.copy()
        for s in sides:
            base_p, base_R = self.ik_base[s]
            o = self.ik_offsets[s]
            p = base_p + np.array([o["x"], o["y"], o["z"]])
            R = _rpy(o["roll"], o["pitch"], o["yaw"]) @ base_R
            res = self.ik.solve(s, self.ik_q[s], p, R, iters=25, rot_weight=0.2)
            self.ik_q[s] = res.q
            self.ik_targets[s] = (p, R)
            self.ik_errors[s] = (res.pos_err, res.rot_err)
            q14[ARM_SLICE[s]] = res.q
        self.engine.set_arm_targets(q14)

    # --------------------------------------------------------------- manos
    def set_hand_pose(self, side: str, pose: str) -> None:
        self.engine.set_hand_pose(side, pose)
        if pose != "abierta":
            for s in (("L", "R") if side == "LR" else (side,)):
                self.grasp_preset[s] = pose

    def set_grasp(self, side: str, g: float) -> None:
        u = g * np.array(H.HAND_POSES[self.grasp_preset[side]])
        self.engine.set_hand_targets(side, H.to_radians(side, u))

    def set_hand_joint(self, side: str, k: int, u: float) -> None:
        sl = slice(0, 10) if side == "L" else slice(10, 20)
        cur = H.to_normalized(side, self.engine.hand_target[sl])
        cur[k] = u
        self.engine.set_hand_targets(side, H.to_radians(side, cur))

    def toggle_hand(self, pose: str) -> None:
        for s in self._sides():
            sl = slice(0, 10) if s == "L" else slice(10, 20)
            closed = np.abs(self.engine.hand_target[sl]).sum() > 0.3
            self.set_hand_pose(s, "abierta" if closed else pose)

    # --------------------------------------------------------------- ciclo de la GUI
    def velocity_from_keys(self) -> None:
        k, sp = self.keys, self.engine.speed
        run = sp.run_factor if Qt.Key.Key_Shift in k else 1.0
        vx = (sp.vx_forward * (Qt.Key.Key_W in k) - sp.vx_backward * (Qt.Key.Key_S in k)) * run
        vy = sp.vy * ((Qt.Key.Key_A in k) - (Qt.Key.Key_D in k)) * run
        wz = sp.wz * ((Qt.Key.Key_Q in k) - (Qt.Key.Key_E in k)) * run
        self.engine.set_velocity_target(vx, vy, wz)

    def update(self, dt: float) -> None:
        self.velocity_from_keys()
        self._nudge_ik(dt)

    def markers(self, data) -> list:
        """Esferas de los objetivos IK en coordenadas del mundo (a partir del torso del último fotograma)."""
        out = []
        torso = self.engine.torso
        pt, Rt = data.xpos[torso], data.xmat[torso].reshape(3, 3)
        for s, (p, R) in self.ik_targets.items():
            color = (1.0, 0.85, 0.1, 0.9) if s in self._sides() else (0.6, 0.6, 0.6, 0.6)
            out.append((pt + Rt @ p, color, 0.022, Rt @ R))
        return out


class KeyFilter(QObject):
    """Captura el teclado en toda la ventana (también con el foco en un slider)."""

    def __init__(self, window: "MainWindow"):
        super().__init__(window)
        self.w = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and self.w.isActiveWindow():
            return self.w.handle_key(event, t == QEvent.Type.KeyPress)
        if t == QEvent.Type.ApplicationDeactivate:
            self.w.ctl.keys.clear()
        return False


class MainWindow(QMainWindow):
    def __init__(self, engine: SimEngine, note: str = ""):
        super().__init__()
        self.setWindowTitle("AgiBot X2 Ultra-N + OmniHand · simulador MuJoCo")
        self.engine = engine
        self.viewport = MujocoViewport(engine.model)
        self.viewport.help_text = HELP
        self.ctl = Controller(engine, self.viewport)
        if note:
            self.ctl.flash(note, 8.0)
        tabs = QTabWidget()
        tabs.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.panels = [WalkPanel(self.ctl), ArmsPanel(self.ctl), IKPanel(self.ctl), PosesPanel(self.ctl),
                       HandsPanel(self.ctl)]
        for panel, name in zip(self.panels, ("Marcha", "Brazos", "IK", "Poses", "Manos")):
            tabs.addTab(panel, name)
        self.walk_panel = self.panels[0]
        split = QSplitter()
        split.addWidget(self.viewport)
        split.addWidget(tabs)
        split.setStretchFactor(0, 1)
        split.setSizes([1100, 430])
        self.setCentralWidget(split)
        self.statusBar().showMessage("WASD mover · QE girar · Mayús rápido · Espacio parar · IJKL/UO mano · "
                                     "Tab brazo · G/T manos · 0-9 poses · H ayuda")
        self.resize(1560, 900)
        self._filter = KeyFilter(self)
        QApplication.instance().installEventFilter(self._filter)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        self._last = time.perf_counter()
        self._tick_n = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        self.viewport.setFocus()

    # --------------------------------------------------------------- teclado
    def handle_key(self, e, pressed: bool) -> bool:
        key = Qt.Key(e.key())
        if e.isAutoRepeat():
            return key in MOVE_KEYS or key in IK_KEYS
        ctl = self.ctl
        if key in MOVE_KEYS or key in IK_KEYS:
            (ctl.keys.add if pressed else ctl.keys.discard)(key)
            return True
        if not pressed:
            return False
        text = e.text()
        if key == Qt.Key.Key_Space:
            self.engine.stop_walking()
        elif key == Qt.Key.Key_R:
            ctl.reset()
        elif key == Qt.Key.Key_P:
            ctl.toggle_pause()
        elif key in (Qt.Key.Key_H, Qt.Key.Key_F1):
            self.viewport.show_help = not self.viewport.show_help
        elif key == Qt.Key.Key_Escape:
            self.viewport.show_help = False
        elif key == Qt.Key.Key_F:
            self.viewport.follow = not self.viewport.follow
            self.walk_panel.follow_box.setChecked(self.viewport.follow)
        elif key == Qt.Key.Key_Tab:
            ctl.cycle_arm()
            ip = self.panels[2]
            ip.group.button({"L": 0, "R": 1, "LR": 2}[ctl.selected_arm]).setChecked(True)
        elif key == Qt.Key.Key_G:
            ctl.toggle_hand("puño")
        elif key == Qt.Key.Key_T:
            ctl.toggle_hand("pinza")
        elif text and text in POSE_BY_KEY:
            ctl.play_pose(POSE_BY_KEY[text])
        elif key in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right):
            yaw = self.engine.info.yaw
            fwd = np.array([math.cos(yaw), math.sin(yaw), 0.0])
            left = np.array([-math.sin(yaw), math.cos(yaw), 0.0])
            d = {Qt.Key.Key_Up: fwd, Qt.Key.Key_Down: -fwd, Qt.Key.Key_Left: left, Qt.Key.Key_Right: -left}[key]
            self.engine.push(d)
            ctl.flash("¡Empujón!", 1.0)
        else:
            return False
        return True

    # --------------------------------------------------------------- ciclo
    def tick(self) -> None:
        now = time.perf_counter()
        dt = min(now - self._last, 0.1)
        self._last = now
        if not self.engine.paused:
            self.ctl.update(dt)
        qpos, info = self.engine.snapshot()
        vp = self.viewport
        vp.markers = self.ctl.markers(vp.data)
        yaw = info.yaw
        c = info.cmd
        vec = np.array([math.cos(yaw) * c[0] - math.sin(yaw) * c[1], math.sin(yaw) * c[0] + math.cos(yaw) * c[1], 0])
        vp.arrow = (np.array([qpos[0], qpos[1], 0.05]), 0.8 * vec, (0.2, 0.8, 1.0, 0.9))
        arm = {"L": "izquierdo", "R": "derecho", "LR": "ambos (espejo)"}[self.ctl.selected_arm]
        white, dim, warn = QColor(235, 240, 250), QColor(170, 185, 205), QColor(255, 200, 90)
        state = {"activo": "ACTIVO", "caido": "CAÍDO", "pausa": "PAUSA"}.get(info.state, info.state)
        lines = [
            (f"X2 Ultra-N + OmniHand · policy: {info.policy} · {state}", white),
            (f"orden  vx {c[0]:+.2f} m/s  vy {c[1]:+.2f} m/s  wz {c[2]:+.2f} rad/s", white),
            ((f"brazo activo: {arm}" + (f" · pose: {info.pose}" if info.pose else ""))
             if not self.engine.policy.controls_arms else
             "brazos: los mueve la policy de AgiBot (usa la policy 'brazos libres' para controlarlos)", dim),
            (f"t {info.sim_time:6.1f} s · {info.rtf:.2f}× tiempo real · H ayuda", dim),
        ]
        if self.ctl.message and time.time() < self.ctl.message_until:
            lines.append((self.ctl.message, warn))
        vp.hud_lines = lines
        vp.banner = "CAÍDO · pulsa R para reiniciar" if info.state == "caido" else (
            "PAUSA (P)" if info.state == "pausa" else "")
        vp.render_state(qpos)
        self._tick_n += 1
        if self._tick_n % 6 == 0:
            for p in self.panels:
                if hasattr(p, "refresh") and p.isVisible():
                    p.refresh()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.timer.stop()
        self.engine.stop()
        self.viewport.close_renderer()
        super().closeEvent(e)


def _preload_xcb_cursor() -> None:
    """Qt ≥ 6.5 necesita libxcb-cursor0 para abrir ventanas en X11. Si el sistema no la tiene, se carga la copia
    local que deja scripts/setup.sh en third_party/syslibs (sin sudo)."""
    import ctypes
    import ctypes.util
    if os.environ.get("QT_QPA_PLATFORM", "xcb") != "xcb" or ctypes.util.find_library("xcb-cursor"):
        return
    local = paths.REPO_ROOT / "third_party" / "syslibs" / "libxcb-cursor.so.0"
    if local.exists():
        ctypes.CDLL(str(local), mode=ctypes.RTLD_GLOBAL)
    else:
        print("Falta libxcb-cursor0 (Qt la necesita en X11): `sudo apt install libxcb-cursor0` o scripts/setup.sh",
              file=sys.stderr)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Simulador del AgiBot X2 Ultra-N + OmniHand")
    ap.add_argument("--scene", choices=("mesa", "vacia"), default="mesa")
    ap.add_argument("--policy", choices=("brazos_libres", "agibot"), default="brazos_libres")
    ap.add_argument("--policy-path", type=Path)
    ap.add_argument("--timestep", type=float, default=0.002)
    ap.add_argument("--dance", type=Path, help="reproducir un JSON de LinkCraft en modo cinemático")
    ap.add_argument("--gemelo", action="store_true",
                    help="manejar el gemelo digital (MC oficial) por la interfaz de AimDK; dentro del contenedor "
                         "x2-twin: official_sim/twin.sh gui")
    args = ap.parse_args(argv)
    if args.dance:
        _preload_xcb_cursor()
        from x2_ultra.tools.dance_viewer import run
        run(args.dance)
        return
    note = ""
    policy = args.policy
    if policy == "brazos_libres" and args.policy_path is None and not paths.default_policy().exists():
        policy = "agibot"
        note = "Aún no hay policy 'brazos libres' entrenada: uso la de AgiBot (mueve los brazos ella)"
        print(note, file=sys.stderr)
    _preload_xcb_cursor()
    app = QApplication.instance() or QApplication(sys.argv)
    if args.gemelo:
        from x2_ultra.twin.client import TwinClient
        engine = TwinClient(scene=args.scene)
        note = "Gemelo: marcha del MC oficial; brazos y manos por /mc/upper_body_command (URS emulado)"
    else:
        engine = SimEngine(scene=args.scene, policy=policy, policy_path=args.policy_path, timestep=args.timestep)
    win = MainWindow(engine, note)
    engine.start()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
