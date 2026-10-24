"""Paneles laterales de la app: marcha, brazos (sliders), IK, poses y manos."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QRadioButton, QScrollArea, QSlider, QVBoxLayout,
                               QWidget)

from x2_ultra.control.poses import ARM_POSES
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H

if TYPE_CHECKING:
    from x2_ultra.app.main import Controller

ARM_LABELS = {"shoulder_pitch": "Hombro · cabeceo", "shoulder_roll": "Hombro · abrir", "shoulder_yaw": "Hombro · giro",
              "elbow": "Codo", "wrist_yaw": "Muñeca · giro", "wrist_pitch": "Muñeca · cabeceo",
              "wrist_roll": "Muñeca · alabeo"}


def _no_focus(w):
    w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return w


class LabeledSlider(QWidget):
    """Slider con etiqueta y valor; trabaja en unidades reales con resolución fija."""

    def __init__(self, label: str, lo: float, hi: float, value: float, on_change, unit: str = "°",
                 scale: float = 180 / math.pi, decimals: int = 0, resolution: int = 1000):
        super().__init__()
        self.lo, self.hi, self.scale, self.unit, self.decimals = lo, hi, scale, unit, decimals
        self.resolution = resolution
        self.on_change = on_change
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setMinimumWidth(130)
        self.slider = _no_focus(QSlider(Qt.Orientation.Horizontal))
        self.slider.setRange(0, resolution)
        self.value_label = QLabel()
        self.value_label.setMinimumWidth(58)
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.value_label)
        self.set_value(value)
        self.slider.valueChanged.connect(self._changed)

    def value(self) -> float:
        return self.lo + (self.hi - self.lo) * self.slider.value() / self.resolution

    def set_value(self, v: float) -> None:
        if self.slider.isSliderDown():
            return
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((np.clip(v, self.lo, self.hi) - self.lo) / (self.hi - self.lo) * self.resolution)))
        self.slider.blockSignals(False)
        self._update_label()

    def _update_label(self) -> None:
        self.value_label.setText(f"{self.value() * self.scale:.{self.decimals}f}{self.unit}")

    def _changed(self) -> None:
        self._update_label()
        self.on_change(self.value())


def _scroll(widget: QWidget) -> QScrollArea:
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setWidget(widget)
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return area


# ------------------------------------------------------------------------------------------- Marcha
class WalkPanel(QWidget):
    def __init__(self, ctl: "Controller"):
        super().__init__()
        self.ctl = ctl
        lay = QVBoxLayout(self)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setStyleSheet("font-family: monospace;")
        lay.addWidget(self.status)

        box = QGroupBox("Velocidades máximas (teclado)")
        bl = QVBoxLayout(box)
        sp = ctl.engine.speed
        bl.addWidget(LabeledSlider("Avance (W)", 0.1, 1.0, sp.vx_forward, lambda v: setattr(sp, "vx_forward", v),
                                   " m/s", 1.0, 2))
        bl.addWidget(LabeledSlider("Retroceso (S)", 0.1, 0.6, sp.vx_backward, lambda v: setattr(sp, "vx_backward", v),
                                   " m/s", 1.0, 2))
        bl.addWidget(LabeledSlider("Lateral (A/D)", 0.05, 0.4, sp.vy, lambda v: setattr(sp, "vy", v), " m/s", 1.0, 2))
        bl.addWidget(LabeledSlider("Giro (Q/E)", 0.1, 1.0, sp.wz, lambda v: setattr(sp, "wz", v), " rad/s", 1.0, 2))
        twin = getattr(ctl.engine, "is_twin", False)
        note = QLabel("Gemelo: anda el MC oficial; las órdenes por debajo de 0,2 m/s o 0,1 rad/s se suben a ese mínimo "
                      "(umbral de arranque del MC)." if twin else
                      "Umbral de arranque de la policy: por debajo de ~0,25 m/s no da pasos y en el sitio gira poco "
                      "por debajo de ~0,6 rad/s.")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray; font-size: 11px;")
        bl.addWidget(note)
        lay.addWidget(box)

        opts = QGroupBox("Opciones")
        ol = QVBoxLayout(opts)
        hold = _no_focus(QCheckBox("Mantener rumbo al caminar recto"))
        hold.setChecked(ctl.engine.heading_hold)
        hold.toggled.connect(lambda v: setattr(ctl.engine, "heading_hold", v))
        follow = _no_focus(QCheckBox("Cámara sigue al robot (doble clic en la vista)"))
        follow.setChecked(True)
        follow.toggled.connect(lambda v: setattr(ctl.viewport, "follow", v))
        self.follow_box = follow
        contacts = _no_focus(QCheckBox("Mostrar contactos y fuerzas"))
        contacts.toggled.connect(ctl.viewport.set_contacts_visible)
        collision = _no_focus(QCheckBox("Mostrar geometría de colisión"))
        collision.toggled.connect(ctl.viewport.set_collision_visible)
        sites = _no_focus(QCheckBox("Mostrar sensores de tacto y puntos de agarre"))
        sites.toggled.connect(ctl.viewport.set_sites_visible)
        for w in ((follow, contacts, collision, sites) if twin else (hold, follow, contacts, collision, sites)):
            ol.addWidget(w)
        if not twin:      # en el gemelo el tiempo es real y el rumbo lo lleva el MC
            ol.addWidget(LabeledSlider("Velocidad sim.", 0.1, 2.0, 1.0,
                                       lambda v: setattr(ctl.engine, "realtime_factor", v), "×", 1.0, 2))
        lay.addWidget(opts)

        row = QHBoxLayout()
        for text, fn in (("Reiniciar (R)", ctl.reset), ("Pausa (P)", ctl.toggle_pause),
                         ("Empujón", lambda: ctl.engine.push())):
            b = _no_focus(QPushButton(text))
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)
        lay.addStretch(1)

    def refresh(self) -> None:
        inf = self.ctl.engine.info
        c, t = inf.cmd, inf.cmd_target
        yaw = inf.yaw
        v = inf.base_vel
        vb = (math.cos(yaw) * v[0] + math.sin(yaw) * v[1], -math.sin(yaw) * v[0] + math.cos(yaw) * v[1])
        state = {"activo": "<span style='color:#7fdc8a'>ACTIVO</span>",
                 "caido": "<span style='color:#ff7b7b'>CAÍDO — pulsa R</span>",
                 "pausa": "<span style='color:#f5c26b'>PAUSA</span>"}.get(inf.state, inf.state)
        self.status.setText(
            f"<b>Estado:</b> {state}<br>"
            f"<b>Policy:</b> {inf.policy}<br>"
            f"Orden &nbsp;vx {c[0]:+.2f} vy {c[1]:+.2f} wz {c[2]:+.2f}<br>"
            f"Teclas vx {t[0]:+.2f} vy {t[1]:+.2f} wz {t[2]:+.2f}<br>"
            f"Medida vx {vb[0]:+.2f} vy {vb[1]:+.2f}<br>"
            f"Altura pelvis {inf.base_pos[2]:.3f} m · rumbo {math.degrees(yaw):+.0f}°<br>"
            f"t = {inf.sim_time:.1f} s · {inf.rtf:.2f}× tiempo real · caídas {inf.falls}")


# ------------------------------------------------------------------------------------------- Brazos
class ArmsPanel(QWidget):
    def __init__(self, ctl: "Controller"):
        super().__init__()
        self.ctl = ctl
        inner = QWidget()
        lay = QVBoxLayout(inner)
        self.mirror = _no_focus(QCheckBox("Espejo: mover el otro brazo de forma simétrica"))
        lay.addWidget(self.mirror)
        self.sliders: dict[str, LabeledSlider] = {}
        for side, title in (("left", "Brazo izquierdo"), ("right", "Brazo derecho")):
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            for j in ("shoulder_pitch", "shoulder_roll", "shoulder_yaw", "elbow", "wrist_yaw", "wrist_pitch", "wrist_roll"):
                name = f"{side}_{j}_joint"
                lo, hi = J.JOINT_RANGE[name]
                s = LabeledSlider(ARM_LABELS[j], lo, hi, J.DEFAULT_POSE[name],
                                  lambda v, n=name: ctl.set_arm_joint(n, v, self.mirror.isChecked()))
                self.sliders[name] = s
                bl.addWidget(s)
            lay.addWidget(box)
        head = QGroupBox("Cabeza (2 ejes)")
        hl = QVBoxLayout(head)
        self.head = {}
        for name, label in (("head_yaw_joint", "Cabeza · giro"), ("head_pitch_joint", "Cabeza · cabeceo")):
            lo, hi = J.JOINT_RANGE[name]
            s = LabeledSlider(label, lo, hi, 0.0, lambda v, n=name: ctl.set_head_joint(n, v))
            self.head[name] = s
            hl.addWidget(s)
        lay.addWidget(head)
        row = QHBoxLayout()
        b1 = _no_focus(QPushButton("Postura por defecto"))
        b1.clicked.connect(lambda: ctl.play_pose("reposo"))
        b2 = _no_focus(QPushButton("Cabeza al frente"))
        b2.clicked.connect(lambda: ctl.set_head(0.0, 0.0))
        row.addWidget(b1)
        row.addWidget(b2)
        lay.addLayout(row)
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(inner))

    def refresh(self) -> None:
        q = self.ctl.engine.arm_target
        for name, v in zip(J.ARM_JOINTS, q):
            self.sliders[name].set_value(v)
        for name, v in zip(J.HEAD_JOINTS, self.ctl.engine.head_target):
            self.head[name].set_value(v)


# ------------------------------------------------------------------------------------------- IK
class IKPanel(QWidget):
    AXES = (("x", "Adelante / atrás (I/K)"), ("y", "Izquierda / derecha (J/L)"), ("z", "Arriba / abajo (U/O)"),
            ("roll", "Alabeo (Z/X)"), ("pitch", "Cabeceo (C/V)"), ("yaw", "Guiñada (B/N)"))

    def __init__(self, ctl: "Controller"):
        super().__init__()
        self.ctl = ctl
        lay = QVBoxLayout(self)
        info = QLabel("La IK mueve el <b>punto de agarre</b> de la mano en el marco del torso. "
                      "Usa las teclas (se activan al pulsarlas) o estos sliders. "
                      "<b>Tab</b> cambia de brazo.")
        info.setWordWrap(True)
        lay.addWidget(info)
        sel = QHBoxLayout()
        self.group = QButtonGroup(self)
        for i, (key, text) in enumerate((("L", "Izquierdo"), ("R", "Derecho"), ("LR", "Ambos (espejo)"))):
            rb = _no_focus(QRadioButton(text))
            self.group.addButton(rb, i)
            sel.addWidget(rb)
            rb.toggled.connect(lambda on, k=key: on and ctl.select_arm(k))
        self.group.button(0).setChecked(True)
        lay.addLayout(sel)
        box = QGroupBox("Objetivo de la mano seleccionada (relativo a la postura al activar la IK)")
        bl = QVBoxLayout(box)
        self.sliders = {}
        for axis, label in self.AXES:
            lin = axis in ("x", "y", "z")
            s = LabeledSlider(label, -0.4 if lin else -math.pi / 2, 0.4 if lin else math.pi / 2, 0.0,
                              lambda v, a=axis: ctl.set_ik_offset(a, v),
                              unit=" cm" if lin else "°", scale=100.0 if lin else 180 / math.pi)
            self.sliders[axis] = s
            bl.addWidget(s)
        lay.addWidget(box)
        row = QHBoxLayout()
        b = _no_focus(QPushButton("Fijar objetivo = postura actual"))
        b.clicked.connect(ctl.reset_ik_targets)
        row.addWidget(b)
        lay.addLayout(row)
        self.readout = QLabel()
        self.readout.setStyleSheet("font-family: monospace;")
        lay.addWidget(self.readout)
        lay.addStretch(1)

    def refresh(self) -> None:
        ctl = self.ctl
        sel = "L" if ctl.selected_arm == "LR" else ctl.selected_arm
        off = ctl.ik_offsets.get(sel)
        if off is not None:
            for axis, s in self.sliders.items():
                s.set_value(off[axis])
        lines = []
        for side, name in (("L", "Izq"), ("R", "Der")):
            t = ctl.ik_targets.get(side)
            err = ctl.ik_errors.get(side)
            if t is None:
                lines.append(f"{name}: IK inactiva")
                continue
            p = t[0] * 100
            e = f"{err[0] * 1000:.0f} mm / {math.degrees(err[1]):.0f}°" if err else "-"
            lines.append(f"{name}: x {p[0]:+5.1f} y {p[1]:+5.1f} z {p[2]:+5.1f} cm · error {e}")
        self.readout.setText("<br>".join(lines))


# ------------------------------------------------------------------------------------------- Poses
class PosesPanel(QWidget):
    def __init__(self, ctl: "Controller"):
        super().__init__()
        lay = QVBoxLayout(self)
        box = QGroupBox("Poses de brazos (teclas 0-9)")
        grid = QGridLayout(box)
        for i, (name, pose) in enumerate(ARM_POSES.items()):
            b = _no_focus(QPushButton(f"{pose.key} · {pose.label}"))
            b.clicked.connect(lambda _=False, n=name: ctl.play_pose(n))
            grid.addWidget(b, i // 2, i % 2)
        lay.addWidget(box)
        hbox = QGroupBox("Poses de manos")
        hg = QGridLayout(hbox)
        hg.addWidget(QLabel("Izquierda"), 0, 0)
        hg.addWidget(QLabel("Derecha"), 0, 1)
        hg.addWidget(QLabel("Ambas"), 0, 2)
        for i, pose in enumerate(H.HAND_POSES):
            for j, side in enumerate(("L", "R", "LR")):
                b = _no_focus(QPushButton(pose.replace("_", " ")))
                b.clicked.connect(lambda _=False, s=side, p=pose: ctl.set_hand_pose(s, p))
                hg.addWidget(b, i + 1, j)
        lay.addWidget(hbox)
        lay.addStretch(1)


# ------------------------------------------------------------------------------------------- Manos
class HandsPanel(QWidget):
    def __init__(self, ctl: "Controller"):
        super().__init__()
        self.ctl = ctl
        inner = QWidget()
        lay = QVBoxLayout(inner)
        info = QLabel("OmniHand: 10 ejes activos por mano (orden del SDK). 0 % = abierta, 100 % = extremo "
                      "del rango (dedo flexionado, pulgar oponiéndose, dedos separados). "
                      "Teclas: <b>G</b> puño/abrir, <b>T</b> pinza/abrir (mano del brazo seleccionado).")
        info.setWordWrap(True)
        lay.addWidget(info)
        self.sliders = {}
        self.grasp = {}
        for side, title in (("L", "Mano izquierda"), ("R", "Mano derecha")):
            box = QGroupBox(title)
            bl = QVBoxLayout(box)
            g = LabeledSlider("Cierre global", 0.0, 1.0, 0.0, lambda v, s=side: ctl.set_grasp(s, v),
                              unit=" %", scale=100.0)
            self.grasp[side] = g
            bl.addWidget(g)
            for k, j in enumerate(H.HAND_SIDE_JOINTS):
                s = LabeledSlider(H.JOINT_LABELS[j], 0.0, 1.0, 0.0,
                                  lambda v, sd=side, kk=k: ctl.set_hand_joint(sd, kk, v), unit=" %", scale=100.0)
                self.sliders[(side, k)] = s
                bl.addWidget(s)
            lay.addWidget(box)
        touch = QGroupBox("Tacto simulado (fuerza normal por región, N)")
        tl = QGridLayout(touch)
        self.bars = {}
        regions = ("palm", "thumb", "index", "middle", "ring", "pinky")
        names = {"palm": "Palma", "thumb": "Pulgar", "index": "Índice", "middle": "Corazón", "ring": "Anular",
                 "pinky": "Meñique"}
        tl.addWidget(QLabel("Izquierda"), 0, 1)
        tl.addWidget(QLabel("Derecha"), 0, 2)
        for i, r in enumerate(regions):
            tl.addWidget(QLabel(names[r]), i + 1, 0)
            for j, side in enumerate(("L", "R")):
                bar = QProgressBar()
                bar.setRange(0, 200)
                bar.setFormat("%v dN")
                self.bars[f"{side}_{r}"] = bar
                tl.addWidget(bar, i + 1, j + 1)
        lay.addWidget(touch)
        lay.addStretch(1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(_scroll(inner))

    def refresh(self) -> None:
        tgt = self.ctl.engine.hand_target
        for side, sl in (("L", slice(0, 10)), ("R", slice(10, 20))):
            u = H.to_normalized(side, tgt[sl])
            for k in range(10):
                self.sliders[(side, k)].set_value(u[k])
        for key, bar in self.bars.items():
            bar.setValue(int(min(self.ctl.engine.info.touch.get(key, 0.0) * 10, 200)))
