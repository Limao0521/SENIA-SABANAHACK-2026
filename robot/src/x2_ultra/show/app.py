"""Ventana de los escenarios de los retos (Qt + MuJoCo).

    scripts/run_show.sh                       # reto 1: X2 Ultra-N y X2 EDU en la tarima con pantalla de vídeo
    scripts/run_show.sh --video presentacion.mp4 --sin-audio
    scripts/run_club.sh                       # reto 2: el X2 Ultra-N en la recepción del Club N (--escena club)

En el club, T (o «Recibir y guiar») hace la recepción guiada: saludo, acompañar hasta el ascensor y decir el piso.

Teclado (H en la ventana): Tab robot activo (Ultra-N → EDU → los dos) · WASD/QE marcha · 0-9 poses ·
G puño (con OmniHand) · L bailar · V vídeo reproducir/pausa · B vídeo al principio · O abrir vídeo ·
C siguiente vista de cámara (en el club: entrada, recepción, ascensores, sala, planta, seguir robot).
"""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "egl")   # render fuera de pantalla; con MUJOCO_GL=glfw también funciona

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import queue  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import Future, ThreadPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
from PySide6.QtCore import QEvent, QObject, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFileDialog,  # noqa: E402
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QRadioButton,
                               QSlider, QSplitter, QVBoxLayout, QWidget)

from x2_ultra.app.main import MOVE_KEYS, _preload_xcb_cursor  # noqa: E402
from x2_ultra.app.viewport import MujocoViewport  # noqa: E402
from x2_ultra.control.poses import ARM_POSES, POSE_BY_KEY  # noqa: E402
from x2_ultra.robot.joints import ARM_JOINTS, DEFAULT_POSE  # noqa: E402
from x2_ultra.show.commercial import CommercialShow  # noqa: E402
from x2_ultra.show.dance import MOTIONS_DIR, available_dances  # noqa: E402
from x2_ultra.show.engine import ShowEngine  # noqa: E402
from x2_ultra.show.guest import GROUP as GUEST_GROUP  # noqa: E402
from x2_ultra.show.guest import BACK_SPEED, RUN_SPEED, SIDE_SPEED, TURN_SPEED, WALK_SPEED  # noqa: E402
from x2_ultra.show.screen import VideoScreen  # noqa: E402
from x2_ultra.show.stage import DECORS  # noqa: E402

HELP = """ESCENARIO · RETO 1 (H para ocultar)

Robot activo (si hay dos)
  Tab        Ultra-N -> EDU -> los dos

Marcha del robot activo (mantener)
  W / S      avanzar / retroceder
  A / D      paso lateral izq. / der.
  Q / E      girar izq. / der.
  Mayús      más rápido
  Espacio    parar en seco

Brazos y manos del robot activo
  0 - 9      poses de brazos
  G          puño / abrir (solo con OmniHand)

Bailes de LinkCraft (cinemáticos)
  L          bailar el elegido / parar

Recepción guiada (club)
  T          empezar / cancelar (destino en el panel)
  M          modo huésped: tú eres la persona que entra
             W/S andar · A/D girar · Q/E de lado · Mayús rápido
  F          terminar la respuesta de voz o volver a grabar

Pantalla
  V          reproducir / pausa
  B          volver al principio
  O          abrir vídeo

Otros
  C          siguiente vista de cámara
  Y          puertas abiertas siempre / automáticas (club)
  R          reiniciar        P  pausa
  Ratón      izq. orbitar · der. mover · rueda zoom"""

FOLLOW_VIEW = "Seguir robot"
GUEST_VIEW = "Huésped: detrás"
FIRST_PERSON_VIEW = "Huésped: primera persona"
SUBTITLE_MIN_S = 3.0          # s que se ve una frase como mínimo (más si es larga)


def selections(rigs) -> tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]:
    """Grupos de robots que se pueden manejar (cada uno y, si hay varios, todos) y sus nombres."""
    keys = tuple(rigs)
    groups = tuple((k,) for k in keys) + ((keys,) if len(keys) > 1 else ())
    labels = tuple(rigs[k].robot.label for k in keys) + (("Los dos",) if len(keys) > 1 else ())
    return groups, labels


def _fmt(seconds: float) -> str:
    return f"{int(seconds // 60)}:{seconds % 60:04.1f}"


class ShowPanel(QWidget):
    def __init__(self, win: "ShowWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)

        box = QGroupBox("Robot activo (Tab)")
        row = QHBoxLayout(box)
        self.robot_group = QButtonGroup(self)
        for i, label in enumerate(win.selection_labels):
            b = QRadioButton(label)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.robot_group.addButton(b, i)
            row.addWidget(b)
        self.robot_group.button(0).setChecked(True)
        self.robot_group.idClicked.connect(win.select)
        lay.addWidget(box)

        if win.engine.decor.tour is None:
            box = QGroupBox("Presentación")
            v = QVBoxLayout(box)
            self.commercial_button = QPushButton("▶ Show completo (19 parlamentos)")
            self.commercial_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.commercial_button.clicked.connect(win.toggle_commercial_show)
            v.addWidget(self.commercial_button)
            if win.hri_enabled:
                b = QPushButton("▶ Bienvenida nueva con LLM y gesto")
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.clicked.connect(win.start_ai_intro)
                v.addWidget(b)
            lay.addWidget(box)

        box = QGroupBox("Poses (0-9)")
        grid = QGridLayout(box)
        for i, (name, pose) in enumerate(ARM_POSES.items()):
            b = QPushButton(f"{pose.key} · {pose.label}")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, n=name: win.play_pose(n))
            grid.addWidget(b, i // 2, i % 2)
        lay.addWidget(box)

        box = QGroupBox("Pantalla")
        v = QVBoxLayout(box)
        self.video_label = QLabel("sin vídeo: título del reto")
        self.video_label.setWordWrap(True)
        v.addWidget(self.video_label)
        row = QHBoxLayout()
        for text, slot in (("Abrir… (O)", win.open_video), ("▶ / ⏸ (V)", win.toggle_video),
                           ("⏮ (B)", win.restart_video)):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek.sliderReleased.connect(lambda: win.seek_fraction(self.seek.value() / 1000))
        v.addWidget(self.seek)
        self.time_label = QLabel("")
        v.addWidget(self.time_label)
        row = QHBoxLayout()
        self.audio_box = QCheckBox("Audio")
        self.audio_box.setChecked(win.screen.audio)
        self.audio_box.toggled.connect(win.set_audio)
        self.loop_box = QCheckBox("Repetir")
        self.loop_box.toggled.connect(lambda on: setattr(win.screen, "loop", on))
        for w in (self.audio_box, self.loop_box):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(w)
        v.addLayout(row)
        lay.addWidget(box)

        if win.engine.decor.tour is not None:
            box = QGroupBox("Recepción guiada (T)")
            v = QVBoxLayout(box)
            decor = win.engine.decor
            self.category = QComboBox()
            self.category.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            for key, label in decor.categories.items():
                self.category.addItem(f"Visitante: {label}", key)
            self.category.setCurrentIndex(self.category.findData("invitado"))
            v.addWidget(self.category)
            self.destination = QComboBox()
            self.destination.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            for floor, label in decor.destinations.items():
                self.destination.addItem(label, floor)
            v.addWidget(self.destination)
            self.category.currentIndexChanged.connect(self._category_changed)
            row = QHBoxLayout()
            for text, slot in (("▶ Recibir y guiar", win.start_tour), ("■ Cancelar", win.stop_tour)):
                b = QPushButton(text)
                b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                b.clicked.connect(slot)
                row.addWidget(b)
            v.addLayout(row)
            row = QHBoxLayout()
            self.come_back = QCheckBox("Volver a la recepción")
            self.come_back.setChecked(True)
            self.voice = QCheckBox("Voz")
            self.voice.setChecked(win.tts is not None)
            self.voice.setEnabled(win.tts is not None)
            for w in (self.come_back, self.voice):
                w.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                row.addWidget(w)
            v.addLayout(row)
            self.tour_label = QLabel("")
            self.tour_label.setWordWrap(True)
            v.addWidget(self.tour_label)
            lay.addWidget(box)

            box = QGroupBox("Hablar con el robot")
            v = QVBoxLayout(box)
            self.conversation_button = QPushButton("Iniciar conversación")
            self.conversation_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.conversation_button.clicked.connect(win.start_conversation)
            v.addWidget(self.conversation_button)
            self.mic_button = QPushButton("🎙 Grabar respuesta (F)")
            self.mic_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.mic_button.clicked.connect(win.toggle_recording)
            v.addWidget(self.mic_button)
            self.visitor_text = QLineEdit()
            self.visitor_text.setPlaceholderText("O escribe aquí lo que dice el visitante")
            self.visitor_text.returnPressed.connect(win.send_visitor_text)
            text_row = QHBoxLayout()
            text_row.addWidget(self.visitor_text)
            send_button = QPushButton("Enviar")
            send_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            send_button.clicked.connect(win.send_visitor_text)
            text_row.addWidget(send_button)
            v.addLayout(text_row)
            self.conversation_label = QLabel("El robot preguntará adónde vas y abrirá el micrófono al terminar de hablar.")
            self.conversation_label.setWordWrap(True)
            v.addWidget(self.conversation_label)
            lay.addWidget(box)

        if win.engine.guest is not None:
            box = QGroupBox("Modo huésped (M)")
            v = QVBoxLayout(box)
            self.guest_button = QPushButton("▶ Entrar como huésped")
            self.guest_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.guest_button.clicked.connect(win.toggle_guest_mode)
            v.addWidget(self.guest_button)
            note = QLabel("Eres la persona que llega (en primera persona; C cambia la cámara): entra, ve al "
                          "mostrador y sigue al robot. W/S andar · A/D girar · Q/E de lado · Mayús rápido. "
                          + ("Face confirma tu categoría; di tu destino cuando el robot te pregunte."
                             if win.hri_enabled else "Di tu destino cuando el robot te pregunte; elige la categoría arriba."))
            note.setWordWrap(True)
            v.addWidget(note)
            lay.addWidget(box)

        box = QGroupBox("Bailes de LinkCraft (L)")
        v = QVBoxLayout(box)
        self.dances = available_dances()
        self.dance_combo = QComboBox()
        self.dance_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.dance_combo.addItems(list(self.dances))
        v.addWidget(self.dance_combo)
        row = QHBoxLayout()
        for text, slot in (("▶ Bailar", win.start_dance), ("■ Parar", win.stop_dance)):
            b = QPushButton(text)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)
        self.with_video = QCheckBox("Empezar también el vídeo desde el inicio")
        self.with_video.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        v.addWidget(self.with_video)
        note = QLabel("Reproducción cinemática: el robot sigue el clip sin control de equilibrio."
                      if self.dances else f"No hay clips en {MOTIONS_DIR}: "
                      "scripts/fetch_linkcraft_dance.py --all-requested")
        note.setWordWrap(True)
        v.addWidget(note)
        lay.addWidget(box)

        box = QGroupBox("Cámara (C)")
        v = QVBoxLayout(box)
        self.view_combo = QComboBox()
        self.view_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.view_combo.addItems(win.view_names)
        self.view_combo.currentTextChanged.connect(win.set_view)
        v.addWidget(self.view_combo)
        lay.addWidget(box)
        lay.addStretch(1)

    def _category_changed(self) -> None:
        """El piso solo cuenta para las rutas a los ascensores (socios, VIP, invitados)."""
        from x2_ultra.show.guide import NEEDS_FLOOR
        self.destination.setEnabled(self.category.currentData() in NEEDS_FLOOR)

    def refresh(self) -> None:
        tour = self.win.engine.tour
        if hasattr(self, "tour_label"):
            self.tour_label.setText(f"{tour.label}: {tour.status}" if tour is not None else "")
        scr = self.win.screen
        if scr.path is None:
            return
        self.video_label.setText(scr.path.name)
        pos = scr.position()
        self.time_label.setText(f"{'▶' if scr.playing else '⏸'}  {_fmt(pos)} / {_fmt(scr.duration)}")
        if not self.seek.isSliderDown() and scr.duration > 0:
            self.seek.setValue(int(1000 * pos / scr.duration))


class KeyFilter(QObject):
    """Captura el teclado en toda la ventana (también con el foco en un botón)."""

    def __init__(self, window: "ShowWindow"):
        super().__init__(window)
        self.w = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        t = event.type()
        if t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and self.w.isActiveWindow():
            if isinstance(QApplication.focusWidget(), QLineEdit):
                return False
            return self.w.handle_key(event, t == QEvent.Type.KeyPress)
        if t == QEvent.Type.ApplicationDeactivate:
            self.w.keys.clear()
        return False


class ShowWindow(QMainWindow):
    def __init__(self, engine: ShowEngine, video: Path | None = None, audio: bool = True,
                 hri: bool = False):
        super().__init__()
        decor = engine.decor
        self.setWindowTitle(decor.title)
        self.engine = engine
        self.hri_enabled = hri
        self._hri_queue: queue.Queue[dict] = queue.Queue()
        self._hri_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sabana-hri") if hri else None
        self._hri_future: Future | None = None
        self._hri_task: tuple[str, object] | None = None
        self._hri_profile: dict | None = None
        self._hri_qr: str | None = None   # último QR que Face leyó en la webcam (la credencial del check-in)
        self._hri_face_proc: subprocess.Popen | None = None
        self.keep_profile = False       # con gafas VR: el check-in vale aunque Face deje de ver la cara
        self.conversation_enabled = True   # False: la recepción la lleva otro (la HRI pregrabada con VR)
        self._voice_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="club-voice") if decor.tour else None
        self._voice_future: Future | None = None
        self._voice_task: str | None = None
        self._recording: tuple[subprocess.Popen, Path] | None = None
        self._conversation_started = False
        self._guest_prompted = False
        self._conversation_history: list[dict] = []
        self._pending_tour = None
        self._pending_tour_until = 0.0
        self._auto_listen_pending = False
        self._auto_listen_after = 0.0
        self._auto_record_deadline = 0.0
        self._commercial_show: CommercialShow | None = None
        self._commercial_started = 0.0
        self._commercial_proc: subprocess.Popen | None = None
        self._commercial_outro_pending = False
        self._commercial_outro_until = 0.0
        self._commercial_outro_line = None
        self.selection_groups, self.selection_labels = selections(engine.rigs)
        self.view_names = list(decor.cameras) + [FOLLOW_VIEW]
        self.guest_mode = False
        if engine.guest is not None:
            self.view_names += [GUEST_VIEW, FIRST_PERSON_VIEW]
        self.screen = VideoScreen(engine.model)
        self.screen.audio = audio
        if decor.card is not None:
            self.screen.set_image(decor.card(self.screen.width, self.screen.height))
        self.viewport = MujocoViewport(engine.model)
        self.viewport.help_text = HELP
        self.viewport.opt.geomgroup[GUEST_GROUP] = 1
        self.keys: set = set()
        self.selection = 0
        self.tts = _tts_command()
        self._tts_proc: subprocess.Popen | None = None
        self._speech_seen = 0
        self._subtitle_until = 0.0
        self.grasp = False
        self.message, self.message_until = "", 0.0
        self.panel = ShowPanel(self)
        self.set_view(self.view_names[0])
        split = QSplitter()
        split.addWidget(self.viewport)
        split.addWidget(self.panel)
        split.setStretchFactor(0, 1)
        split.setSizes([1180, 380])
        self.setCentralWidget(split)
        self.statusBar().showMessage("Tab robot · WASD mover · QE girar · 0-9 poses · L bailar · V vídeo · "
                                     "B inicio · O abrir · C vista · F hablar · H ayuda")
        self.resize(1560, 900)
        self._filter = KeyFilter(self)
        QApplication.instance().installEventFilter(self._filter)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)
        if video is not None:
            self.load_video(video)
        self._tick_n = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)
        if hri and decor.tour is not None:
            self._start_face_bridge()
        self.viewport.setFocus()

    # --------------------------------------------------------------- acciones
    @property
    def active(self) -> tuple[str, ...]:
        return self.selection_groups[self.selection]

    def flash(self, text: str, seconds: float = 2.0) -> None:
        self.message, self.message_until = text, time.time() + seconds

    def select(self, index: int) -> None:
        for key in self.active:
            if key not in self.selection_groups[index]:
                self.engine.rigs[key].stop_walking()
        self.selection = index
        self.panel.robot_group.button(index).setChecked(True)
        self.viewport.follow_qadr = self.engine.rigs[self.active[0]].qbase

    def play_pose(self, name: str) -> None:
        for key in self.active:
            self.engine.play_pose(key, name)

    def toggle_grasp(self) -> None:
        self.grasp = not self.grasp
        for key in self.active:
            self.engine.rigs[key].set_hand_pose("LR", "puño" if self.grasp else "abierta")

    def start_dance(self) -> None:
        """El robot activo (o los dos) baila el clip elegido en el panel; si ya bailaba, para."""
        self.stop_commercial_show()
        self._cancel_commercial_outro()
        panel = self.panel
        if not panel.dances:
            self.flash(f"No hay bailes en {MOTIONS_DIR}")
            return
        name = panel.dance_combo.currentText()
        for key in self.active:
            self.engine.start_dance(key, panel.dances[name], name)
        if panel.with_video.isChecked() and self.screen.path is not None:
            self.screen.seek(0.0, play=True)
        self.flash(f"Bailando: {name}", 2.0)

    def start_tour(self) -> None:
        """El robot activo recibe al visitante y lo guía hasta el ascensor del destino elegido. En modo huésped,
        espera a que el huésped llegue al mostrador."""
        decor, panel = self.engine.decor, self.panel
        if decor.tour is None:
            return
        category, floor = panel.category.currentData(), panel.destination.currentData()
        self.keys.clear()
        guest = self.engine.guest if self.guest_mode else None
        tour = decor.tour(self.active[0], category, floor, panel.come_back.isChecked(), guest)
        self._pending_tour = None
        self._auto_listen_pending = False
        self.engine.start_tour(tour)
        self._conversation_started = False
        self._guest_prompted = True
        self.flash(f"Recepción guiada: {tour.label}", 2.0)

    def start_conversation(self) -> None:
        """Ask for the destination before any guided walk begins."""
        if self.engine.decor.tour is None or self._voice_future is not None or self._recording is not None:
            return
        self.engine.stop_tour()
        self._pending_tour = None
        self._auto_listen_pending = True
        self._auto_listen_after = time.monotonic() + 0.7
        self._conversation_started = True
        self._guest_prompted = True
        self._conversation_history.clear()
        from x2_ultra.show.hri import welcome_text
        welcome = welcome_text(self._hri_profile)
        self.panel.conversation_label.setText(f"Robot: {welcome}")
        self.engine.play_pose("ultra", "saludar")
        self.engine.say(welcome, "ultra")

    def toggle_recording(self) -> None:
        """Click or press F once to record, again to transcribe."""
        if self.engine.decor.tour is None:
            return
        self._auto_listen_pending = False
        self._auto_record_deadline = 0.0
        if self._recording is not None:
            process, path = self._recording
            self._recording = None
            self.panel.mic_button.setText("🎙 Grabar respuesta (F)")
            from x2_ultra.show.conversation import finish_recording
            self._voice_task = "asr"
            self._voice_future = self._voice_executor.submit(finish_recording, process, path)
            self.panel.conversation_label.setText("Transcribiendo lo que dijiste…")
            return
        if self._voice_future is not None:
            self.flash("Espera la respuesta del robot", 3.0)
            return
        if not self._conversation_started:
            self.start_conversation()
        if self._tts_proc is not None and self._tts_proc.poll() is None:
            self._tts_proc.terminate()
        try:
            from x2_ultra.show.conversation import start_recording
            self._recording = start_recording()
        except Exception as exc:
            self.flash(f"Micrófono: {exc}", 8.0)
            return
        self.panel.mic_button.setText("■ Terminar respuesta (F)")
        self.panel.conversation_label.setText("Escuchando… di adónde vas y vuelve a pulsar F")

    def send_visitor_text(self) -> None:
        text = self.panel.visitor_text.text().strip()
        self.panel.visitor_text.clear()
        self._auto_listen_pending = False
        if self._recording is not None:
            process, path = self._recording
            self._recording = None
            process.terminate()
            path.unlink(missing_ok=True)
            self.panel.mic_button.setText("🎙 Grabar respuesta (F)")
        self._visitor_turn(text)

    def _visitor_turn(self, text: str) -> None:
        if not text or self._voice_future is not None or self._recording is not None:
            return
        if not self._conversation_started:
            self.start_conversation()
        from x2_ultra.show.conversation import answer
        category = self.panel.category.currentData()
        self._voice_task = "qwen"
        self._voice_future = self._voice_executor.submit(answer, text, list(self._conversation_history), category)
        self._conversation_history.append({"role": "user", "content": text})
        self.panel.conversation_label.setText(f"Visitante: {text}\nQwen está respondiendo…")

    def _poll_conversation(self) -> None:
        if self._voice_future is None or not self._voice_future.done():
            return
        future, task = self._voice_future, self._voice_task
        self._voice_future = self._voice_task = None
        try:
            result = future.result()
        except Exception as exc:
            self.panel.conversation_label.setText(f"Error de voz o Qwen: {exc}")
            self.flash(f"Conversación: {exc}", 8.0)
            return
        if task == "asr":
            if not result:
                self.panel.conversation_label.setText("No escuché una frase clara. Vuelve a grabar.")
                return
            self._visitor_turn(result)
            return
        from x2_ultra.show.conversation import route_floor
        if result.destination is None:
            self._conversation_history.append({"role": "assistant", "content": result.text})
            self.panel.conversation_label.setText(f"Robot: {result.text}")
            self.engine.say(result.text, "ultra")
            self._auto_listen_pending = True
            self._auto_listen_after = time.monotonic() + 0.7
            return
        key = result.destination
        category = self.panel.category.currentData()
        if self._hri_profile is not None:
            from x2_ultra.show.hri import category_for_profile
            category = category_for_profile(self._hri_profile)
        try:
            floor = route_floor(key, category)
        except ValueError as exc:
            clarification = str(exc)
            self._conversation_history.append({"role": "assistant", "content": clarification})
            self.panel.conversation_label.setText(f"Robot: {clarification}")
            self.engine.say(clarification, "ultra")
            return
        self._conversation_history.append({"role": "assistant", "content": result.text})
        self.panel.conversation_label.setText(f"Robot: {result.text}")
        if floor is not None:
            self.panel.destination.setCurrentIndex(self.panel.destination.findData(floor))
        self.panel.category.setCurrentIndex(self.panel.category.findData(category))
        self.engine.say(result.text, "ultra")
        guest = self.engine.guest if self.guest_mode else None
        tour = self.engine.decor.tour("ultra", category, floor, self.panel.come_back.isChecked(), guest,
                                      identified=True, greeting=False)
        self._pending_tour = tour
        self._auto_listen_pending = False
        self._pending_tour_until = time.monotonic() + max(1.8, len(result.text) / 13.0)
        self._conversation_started = False

    def _poll_auto_listen(self) -> None:
        now = time.monotonic()
        if self._auto_listen_pending and now >= self._auto_listen_after:
            if self._tts_proc is None or self._tts_proc.poll() is not None:
                self.toggle_recording()
                if self._recording is not None:
                    self._auto_record_deadline = now + 8.0
        elif self._recording is not None and self._auto_record_deadline and now >= self._auto_record_deadline:
            self.toggle_recording()

    def _poll_pending_tour(self) -> None:
        if self._pending_tour is None or time.monotonic() < self._pending_tour_until:
            return
        if self._tts_proc is not None and self._tts_proc.poll() is None:
            return
        tour, self._pending_tour = self._pending_tour, None
        self.engine.start_tour(tour)
        self.panel.conversation_label.setText("Recepción: " + tour.label)

    def start_ai_intro(self) -> None:
        if not self.hri_enabled or self._hri_future is not None:
            return
        self.stop_commercial_show()
        self._cancel_commercial_outro()
        from x2_ultra.show.hri import generate_stage_intro
        self._hri_task = ("stage", self.active[0])
        self._hri_future = self._hri_executor.submit(generate_stage_intro)
        self.flash("Generando presentación con el LLM…", 3.0)

    def toggle_commercial_show(self) -> None:
        if self._commercial_show is not None:
            self.stop_commercial_show()
            return
        if self._hri_future is not None:
            self.flash("Espere a que termine la bienvenida con LLM", 5.0)
            return
        self._cancel_commercial_outro()
        try:
            show = CommercialShow()
        except (OSError, ValueError, KeyError) as exc:
            self.flash(f"Show comercial: {exc}", 8.0)
            return
        if not (MOTIONS_DIR / "chen_style_tai_chi.json").is_file():
            self.flash("Falta el clip Tai Chi estilo Chen", 8.0)
            return
        self.engine.reset()
        self._commercial_show = show
        self._commercial_started = time.monotonic()
        self.panel.commercial_button.setText("■ Parar show")
        self.flash("Show completo: Ultra-N y EDU; voz y gestos sincronizados", 5.0)

    def stop_commercial_show(self) -> None:
        if self._commercial_show is None:
            return
        self._commercial_show = None
        if self._commercial_proc is not None and self._commercial_proc.poll() is None:
            self._commercial_proc.terminate()
        self._commercial_proc = None
        for key in ("ultra", "edu"):
            if key in self.engine.rigs:
                self.engine.set_arm_targets(key, np.array([DEFAULT_POSE[n] for n in ARM_JOINTS]))
        self.panel.commercial_button.setText("▶ Show completo (19 parlamentos)")

    def _cancel_commercial_outro(self) -> None:
        was_waving = self._commercial_outro_until > 0
        self._commercial_outro_pending = False
        self._commercial_outro_until = 0.0
        self._commercial_outro_line = None
        if self._commercial_proc is not None and self._commercial_proc.poll() is None:
            self._commercial_proc.terminate()
        self._commercial_proc = None
        if was_waving:
            for key in ("ultra", "edu"):
                self.engine.play_pose(key, "reposo")

    def _play_commercial_audio(self, path: Path) -> None:
        if self._commercial_proc is not None and self._commercial_proc.poll() is None:
            self._commercial_proc.terminate()
        player = shutil.which("pw-play") or shutil.which("aplay")
        if player is None:
            self.flash("No hay reproductor de audio (pw-play o aplay)", 6.0)
            return
        try:
            self._commercial_proc = subprocess.Popen(
                [player, str(path)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.flash(f"Audio comercial: {exc}", 6.0)

    def _poll_commercial_show(self) -> None:
        show = self._commercial_show
        if show is None:
            return
        elapsed = time.monotonic() - self._commercial_started
        for line in show.due(elapsed):
            self._play_commercial_audio(line.audio)
            speaker = "Ultra-N" if line.robot == "ultra" else "EDU"
            self.viewport.subtitle = f"{speaker}: {line.text}"
            self._subtitle_until = time.time() + max(1.0, line.end - elapsed)
            self.flash(f"{line.scene} · {line.number}/19", 2.0)
        for key, target in show.targets(elapsed).items():
            self.engine.set_arm_targets(key, target)
        if show.finished(elapsed):
            self.stop_commercial_show()
            chen = MOTIONS_DIR / "chen_style_tai_chi.json"
            try:
                self.engine.start_dance_many(("ultra", "edu"), chen, "Tai Chi estilo Chen")
            except (OSError, ValueError) as exc:
                self.flash(f"No se pudo iniciar Tai Chi estilo Chen: {exc}", 8.0)
                return
            both = next((i for i, keys in enumerate(self.selection_groups)
                         if set(keys) == {"ultra", "edu"}), None)
            if both is not None:
                self.select(both)
            self.panel.dance_combo.setCurrentText("Tai Chi estilo Chen")
            self._commercial_outro_line = show.lines[17]  # «Muchas gracias por acompañarnos»
            self._commercial_outro_pending = True
            self.flash("Discurso terminado · ambos robots: Tai Chi estilo Chen", 6.0)

    def _poll_commercial_outro(self) -> None:
        if self._commercial_outro_pending:
            if any(self.engine.rigs[key].dance is not None for key in ("ultra", "edu")):
                return
            self._commercial_outro_pending = False
            line = self._commercial_outro_line
            for key in ("ultra", "edu"):
                self.engine.play_pose(key, "saludar")
            self._play_commercial_audio(line.audio)
            self.viewport.subtitle = f"Ultra-N: {line.text}"
            self._subtitle_until = time.time() + 4.5
            self._commercial_outro_until = time.monotonic() + 4.5
            self.flash("Despedida: ambos robots saludan con la mano", 4.5)
        elif self._commercial_outro_until and time.monotonic() >= self._commercial_outro_until:
            self._commercial_outro_until = 0.0
            self._commercial_outro_line = None
            for key in ("ultra", "edu"):
                self.engine.play_pose(key, "reposo")

    def _start_face_bridge(self) -> None:
        root = Path(__file__).resolve().parents[3]
        python = Path(os.getenv("HRIEGO_FACE_PYTHON", root / "Face/.venv-face/bin/python"))
        if not python.is_file():
            self.flash("Falta Face/.venv-face; ejecute scripts/setup_hriego_v2.sh", 8.0)
            return
        env = os.environ.copy()
        paths = (root / "Face/thirdparty/uniface", root / "Face", root)
        env["PYTHONPATH"] = os.pathsep.join(map(str, paths))
        env["UNIFACE_CACHE_DIR"] = str(root / "Face/thirdparty/uniface-models")
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        try:
            self._hri_face_proc = subprocess.Popen(
                [str(python), "-u", "-m", "hriego_v2.show_face_stream"], cwd=root, env=env,
                stdout=subprocess.PIPE, stderr=None, text=True, bufsize=1)
        except OSError as exc:
            self.flash(f"No se pudo iniciar Face: {exc}", 8.0)
            return

        def read_face() -> None:
            assert self._hri_face_proc is not None and self._hri_face_proc.stdout is not None
            for line in self._hri_face_proc.stdout:
                try:
                    self._hri_queue.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            self._hri_queue.put({"event": "stopped"})

        threading.Thread(target=read_face, name="sabana-face-events", daemon=True).start()

    def _poll_hri(self) -> None:
        if not self.hri_enabled:
            return
        from x2_ultra.show.hri import category_for_profile
        while True:
            try:
                event = self._hri_queue.get_nowait()
            except queue.Empty:
                break
            kind = event.get("event")
            if kind == "ready":
                self.flash("Cámara Face lista: espere una identidad confirmada", 4.0)
            elif kind == "stopped":
                self.flash("Face se detuvo; revise el error en la terminal", 8.0)
            elif kind == "absent":
                if not self.keep_profile:
                    self._hri_profile = None
            elif kind == "qr":
                if event.get("text") and event["text"] != self._hri_qr:
                    self._hri_qr = str(event["text"])
                    print(f"Face: QR leído {self._hri_qr[:12]}…", flush=True)
                    self.flash("Credencial QR leída", 3.0)
            elif kind == "profile":
                profile = event.get("profile") or {}
                try:
                    category = category_for_profile(profile)
                    if (self._hri_profile or {}).get("person_id") != profile.get("person_id"):
                        self._hri_qr = None       # otra persona: su QR todavía no se ha visto
                    print(f"Face: {profile.get('display_name')} ({profile.get('rank_code')})", flush=True)
                    self._hri_profile = profile
                    self.panel.category.setCurrentIndex(self.panel.category.findData(category))
                    self.flash("Identidad confirmada; ahora el QR de la credencial a la cámara" if self.keep_profile
                               else "Identidad confirmada; pregunte al visitante su destino", 5.0)
                except ValueError as exc:
                    self._hri_profile = None
                    self.flash(str(exc), 5.0)
        if self._hri_future is None or not self._hri_future.done():
            return
        future, task = self._hri_future, self._hri_task
        self._hri_future = self._hri_task = None
        try:
            result = future.result()
        except Exception as exc:
            self.flash(f"HRI: {type(exc).__name__}: {exc}", 8.0)
            return
        key = task[1]
        if result.pose:
            self.engine.play_pose(key, result.pose)
        self.engine.say(result.speech, key)
        self.flash(f"Gesto: {result.gesture}", 4.0)

    def toggle_guest_mode(self) -> None:
        """Modo huésped: la escena vuelve al principio, el usuario maneja a la persona que entra y el robot la
        espera en la recepción para guiarla."""
        eng = self.engine
        if eng.guest is None:
            return
        self.guest_mode = not self.guest_mode
        self.keys.clear()
        eng.guest.cmd[:] = 0.0
        if self.guest_mode:
            eng.reset()
            self._conversation_started = False
            self._guest_prompted = False
            self.set_view(FIRST_PERSON_VIEW)
            self.flash("Modo huésped: acércate al mostrador; el robot preguntará adónde vas", 5.0)
        else:
            eng.stop_tour()
            self._pending_tour = None
            self._auto_listen_pending = False
            self._conversation_started = False
            self._guest_prompted = False
            self.set_view(self.view_names[0])
        self.panel.guest_button.setText("■ Salir del modo huésped" if self.guest_mode else "▶ Entrar como huésped")

    def stop_tour(self) -> None:
        self._pending_tour = None
        self._auto_listen_pending = False
        self.engine.stop_tour()

    def toggle_tour(self) -> None:
        tour = self.engine.tour
        if tour is not None and not tour.done:
            self.stop_tour()
        else:
            self.start_tour()

    def _speak(self, text: str) -> None:
        self.viewport.subtitle = text
        self._subtitle_until = time.time() + max(SUBTITLE_MIN_S, 0.075 * len(text))
        voice = getattr(self.panel, "voice", None)
        if not text or self.tts is None or voice is None or not voice.isChecked():
            return
        if self._tts_proc is not None and self._tts_proc.poll() is None:
            self._tts_proc.terminate()
        self._tts_proc = subprocess.Popen(self.tts + [text], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)

    def stop_dance(self) -> None:
        self._cancel_commercial_outro()
        for key in self.active:
            self.engine.stop_dance(key)

    def toggle_dance(self) -> None:
        if self._commercial_show is not None:
            self.stop_commercial_show()
        if any(self.engine.rigs[k].dance is not None for k in self.active):
            self.stop_dance()
        else:
            self.start_dance()

    def set_view(self, name: str) -> None:
        """Vista de cámara del decorado, o seguir al primer robot activo."""
        vp = self.viewport
        vp.opt.geomgroup[GUEST_GROUP] = 0 if name == FIRST_PERSON_VIEW else 1
        if name in (GUEST_VIEW, FIRST_PERSON_VIEW):
            vp.follow = False
            vp.opt.geomgroup[2] = 1
        elif name == FOLLOW_VIEW:
            vp.follow = True
            rig = self.engine.rigs[self.active[0]]
            vp.follow_qadr = rig.qbase
            vp.cam.distance, vp.cam.elevation = 4.0, -15.0
            vp.cam.azimuth = math.degrees(rig.info.yaw)       # detrás del robot, mirando hacia donde va
            vp.opt.geomgroup[2] = 1
        else:
            view = self.engine.decor.cameras[name]
            vp.follow = False
            vp.cam.lookat[:] = view.lookat
            vp.cam.distance, vp.cam.azimuth, vp.cam.elevation = view.distance, view.azimuth, view.elevation
            vp.opt.geomgroup[2] = 1 if view.ceiling else 0
        self.view = name
        combo = self.panel.view_combo
        if combo.currentText() != name:
            combo.blockSignals(True)
            combo.setCurrentText(name)
            combo.blockSignals(False)

    def _guest_camera(self) -> None:
        """Cámara detrás del huésped (o en sus ojos) que gira con él, suavizada."""
        g, cam = self.engine.guest, self.viewport.cam
        yaw = math.degrees(g.yaw)
        cam.azimuth += ((yaw - cam.azimuth + 180) % 360 - 180) * 0.12
        if self.view == FIRST_PERSON_VIEW:
            fwd = np.array([math.cos(g.yaw), math.sin(g.yaw), 0.0])
            target = np.array([g.x, g.y, 1.62]) + 0.6 * fwd
            cam.distance, cam.elevation = 0.6, -4.0
        else:
            target = np.array([g.x, g.y, 1.35])
            cam.distance, cam.elevation = 3.4, -13.0
        cam.lookat[:] = cam.lookat + 0.25 * (target - cam.lookat)

    def next_view(self) -> None:
        names = self.view_names
        self.set_view(names[(names.index(self.view) + 1) % len(names)])
        self.flash(f"Cámara: {self.view}", 1.5)

    def load_video(self, path: Path) -> None:
        try:
            self.screen.load(path)
        except Exception as e:  # noqa: BLE001 (se informa en la ventana)
            self.flash(f"No se pudo abrir {path}: {e}", 6.0)
            return
        self.flash(f"Vídeo: {Path(path).name} ({_fmt(self.screen.duration)}) · V para reproducir", 4.0)

    def open_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Vídeo para la pantalla", str(Path.home()),
                                              "Vídeo (*.mp4 *.mkv *.mov *.webm *.avi);;Todos (*)")
        if path:
            self.load_video(Path(path))

    def toggle_video(self) -> None:
        if self.screen.path is None:
            self.flash("No hay vídeo: O o «Abrir…»")
        self.screen.toggle()

    def restart_video(self) -> None:
        self.screen.restart()

    def seek_fraction(self, f: float) -> None:
        self.screen.seek(f * self.screen.duration)

    def set_audio(self, on: bool) -> None:
        self.screen.audio = on
        if self.screen.playing:          # aplicar ya: reanudar con o sin audio
            self.screen.pause()
            self.screen.play()

    # --------------------------------------------------------------- teclado
    def handle_key(self, e, pressed: bool) -> bool:
        key = Qt.Key(e.key())
        if e.isAutoRepeat():
            return key in MOVE_KEYS
        if key in MOVE_KEYS:
            (self.keys.add if pressed else self.keys.discard)(key)
            return True
        if not pressed:
            return False
        text = e.text()
        eng = self.engine
        if key == Qt.Key.Key_Tab:
            self.select((self.selection + 1) % len(self.selection_groups))
        elif key == Qt.Key.Key_Space:
            for k in self.active:
                eng.rigs[k].stop_walking()
        elif text and text in POSE_BY_KEY:
            self.play_pose(POSE_BY_KEY[text])
        elif key == Qt.Key.Key_G:
            self.toggle_grasp()
        elif key == Qt.Key.Key_L:
            self.toggle_dance()
        elif key == Qt.Key.Key_T and self.engine.decor.tour is not None:
            self.toggle_tour()
        elif key == Qt.Key.Key_M and self.engine.guest is not None:
            self.toggle_guest_mode()
        elif key == Qt.Key.Key_F and self.engine.decor.tour is not None:
            self.toggle_recording()
        elif key == Qt.Key.Key_V:
            self.toggle_video()
        elif key == Qt.Key.Key_B:
            self.restart_video()
        elif key == Qt.Key.Key_O:
            self.keys.clear()
            self.open_video()
        elif key == Qt.Key.Key_C:
            self.next_view()
        elif key == Qt.Key.Key_Y and eng.props is not None and hasattr(eng.props, "hold_open"):
            eng.props.hold_open = not eng.props.hold_open
            self.flash("Puertas: " + ("abiertas" if eng.props.hold_open else "automáticas"))
        elif key == Qt.Key.Key_R:
            self.stop_commercial_show()
            self._cancel_commercial_outro()
            eng.reset()
        elif key == Qt.Key.Key_P:
            eng.paused = not eng.paused
        elif key in (Qt.Key.Key_H, Qt.Key.Key_F1):
            self.viewport.show_help = not self.viewport.show_help
        elif key == Qt.Key.Key_Escape:
            self.viewport.show_help = False
        else:
            return False
        return True

    def _velocity_from_keys(self) -> None:
        k = self.keys
        if self.guest_mode:
            run = Qt.Key.Key_Shift in k
            fwd = (RUN_SPEED if run else WALK_SPEED) * (Qt.Key.Key_W in k) - BACK_SPEED * (Qt.Key.Key_S in k)
            side = SIDE_SPEED * ((Qt.Key.Key_Q in k) - (Qt.Key.Key_E in k))
            turn = TURN_SPEED * ((Qt.Key.Key_A in k) - (Qt.Key.Key_D in k))
            self.engine.guest.cmd[:] = (fwd, side, turn)
            return
        for key in self.active:
            if self.engine.touring(key):
                continue                 # lo lleva la recepción guiada
            rig = self.engine.rigs[key]
            sp = rig.speed
            run = sp.run_factor if Qt.Key.Key_Shift in k else 1.0
            vx = (sp.vx_forward * (Qt.Key.Key_W in k) - sp.vx_backward * (Qt.Key.Key_S in k)) * run
            vy = sp.vy * ((Qt.Key.Key_A in k) - (Qt.Key.Key_D in k)) * run
            wz = sp.wz * ((Qt.Key.Key_Q in k) - (Qt.Key.Key_E in k)) * run
            rig.set_velocity_target(vx, vy, wz)

    # --------------------------------------------------------------- ciclo
    def tick(self) -> None:
        eng = self.engine
        self._poll_commercial_show()
        self._poll_commercial_outro()
        self._poll_hri()
        self._poll_conversation()
        if self.guest_mode and self.conversation_enabled and not self._guest_prompted and eng.guest is not None:
            rig = eng.rigs["ultra"]
            if math.hypot(eng.guest.x - rig.info.base_pos[0],
                          eng.guest.y - rig.info.base_pos[1]) < 2.6:
                self.start_conversation()
        if not eng.paused:
            self._velocity_from_keys()
        vp = self.viewport
        if self.screen.poll():
            vp.upload_texture(self.screen.texid)
        speech = eng.speech
        while self._speech_seen < len(speech):
            self._speak(speech[self._speech_seen][2])
            self._speech_seen += 1
        self._poll_auto_listen()
        self._poll_pending_tour()
        if vp.subtitle and time.time() > self._subtitle_until:
            vp.subtitle = ""
        white, dim, warn, hi = QColor(235, 240, 250), QColor(170, 185, 205), QColor(255, 200, 90), \
            QColor(120, 200, 255)
        lines = []
        for key, rig in eng.rigs.items():
            inf = rig.info
            state = {"activo": "ACTIVO", "caido": "CAÍDO (R reinicia)", "bailando": "BAILA"}.get(inf.state, inf.state)
            c = inf.cmd
            mark = "▶ " if key in self.active else "  "
            detail = (f" · {inf.dance}" if inf.dance else
                      f" vx {c[0]:+.2f} vy {c[1]:+.2f} wz {c[2]:+.2f}" + (f" · {inf.pose}" if inf.pose else ""))
            lines.append((f"{mark}{rig.robot.label:<11} {state:<8}{detail}", hi if key in self.active else white))
        if eng.tour is not None and not eng.tour.done:
            lines.append((f"Recepción guiada · {eng.tour.label} · {eng.tour.status}", hi))
        lines.append((f"t {eng.sim_time:6.1f} s · {eng.rtf:.2f}× tiempo real" + (" · PAUSA" if eng.paused else "")
                      + " · H ayuda", dim))
        if self.message and time.time() < self.message_until:
            lines.append((self.message, warn))
        vp.hud_lines = lines
        if self.view in (GUEST_VIEW, FIRST_PERSON_VIEW) and eng.guest is not None:
            self._guest_camera()
        vp.render_state(eng.snapshot(), eng.snapshot_mocap())
        self._tick_n += 1
        if self._tick_n % 6 == 0:
            self.panel.refresh()

    def closeEvent(self, e) -> None:  # noqa: N802
        self.timer.stop()
        self.stop_commercial_show()
        self._cancel_commercial_outro()
        if self._hri_face_proc is not None and self._hri_face_proc.poll() is None:
            self._hri_face_proc.terminate()
        if self._hri_executor is not None:
            self._hri_executor.shutdown(wait=False, cancel_futures=True)
        if self._recording is not None:
            process, path = self._recording
            process.terminate()
            path.unlink(missing_ok=True)
        if self._voice_executor is not None:
            self._voice_executor.shutdown(wait=False, cancel_futures=True)
        self.engine.stop()
        self.screen.close()
        if self._tts_proc is not None and self._tts_proc.poll() is None:
            self._tts_proc.terminate()
        self.viewport.close_renderer()
        super().closeEvent(e)


def _tts_command() -> list[str] | None:
    """Voz en español con espeak-ng (latinoamericana si está), o None si no hay sintetizador."""
    if shutil.which("espeak-ng") is None:
        return None
    try:
        voices = subprocess.run(["espeak-ng", "--voices=es"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    voice = "es-419" if "es-419" in voices else "es"
    return ["espeak-ng", "-v", voice, "-s", "150"]


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Escenarios de los retos: tarima con pantalla (reto 1) o recepción "
                                             "del Club N (reto 2)")
    ap.add_argument("--escena", choices=tuple(DECORS), default="escenario")
    ap.add_argument("--video", type=Path, help="vídeo para la pantalla (también se abre desde la ventana)")
    ap.add_argument("--sin-audio", action="store_true", help="no reproducir el audio del vídeo")
    ap.add_argument("--policy-path", type=Path, help="otra policy 'brazos libres' (.onnx)")
    ap.add_argument("--hri", action="store_true", help="Face + LLM local + gestos validados (requiere setup_hriego_v2)")
    ap.add_argument("--show-completo", action="store_true", help="iniciar automáticamente el show comercial de 19 parlamentos")
    ap.add_argument("--vr", action="store_true", help="gafas Meta Quest 3 (WebXR): quien las lleva es el huésped del club")
    ap.add_argument("--vr-puerto", type=int, default=8443, help="puerto HTTPS de la página VR (por defecto 8443)")
    ap.add_argument("--vr-api-puerto", type=int, default=8766, help="puerto de la API local ws://127.0.0.1")
    ap.add_argument("--vr-micro", choices=("defecto", "virtual", "no"), default="defecto",
                    help="micrófono de las gafas en PipeWire: predeterminado del PC (defecto), solo como fuente "
                         "virtual (virtual) o nada (no)")
    ap.add_argument("--vr-grabar", type=Path, help="guardar el micrófono de las gafas en WAV en esta carpeta")
    ap.add_argument("--vr-recepcion", choices=("hri", "hri-qwen", "guion", "conversacion"), default="hri",
                    help="con las gafas: recepción HRI del reto 2 con la máquina de estados de Luis (hri: cara + QR, "
                         "accesos por rango y biblioteca de espacios), la conversacional anterior (hri-qwen: saludo y "
                         "despedida pregrabados, el visitante dice su destino en vivo), su guion pregrabado completo "
                         "(guion) o la conversación libre de la ventana (conversacion)")
    ap.add_argument("--sin-qr", action="store_true",
                    help="recepción hri sin credencial QR: el rango sale solo de la cara (identidad simulada)")
    args = ap.parse_args(argv)
    if args.show_completo and args.escena != "escenario":
        ap.error("--show-completo solo está disponible en el reto 1")
    if args.vr and args.escena != "club":
        ap.error("--vr solo está disponible en el club (--escena club)")
    _preload_xcb_cursor()
    app = QApplication.instance() or QApplication(sys.argv)
    engine = ShowEngine(DECORS[args.escena], policy_path=args.policy_path)
    win = ShowWindow(engine, args.video, audio=not args.sin_audio, hri=args.hri)
    if args.vr:
        from x2_ultra.vr.bridge import VRBridge
        bridge = VRBridge(win, Path(__file__).resolve().parents[3] / "runs/vr", args.vr_puerto, args.vr_api_puerto,
                          args.vr_micro, args.vr_grabar, reception=args.vr_recepcion, require_qr=not args.sin_qr)
        app.aboutToQuit.connect(bridge.close)
    engine.start()
    win.show()
    if args.show_completo:
        QTimer.singleShot(1500, win.toggle_commercial_show)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
