"""Ventana MuJoCo para reproducir un JSON de movimiento de LinkCraft."""
from __future__ import annotations

import os

os.environ.setdefault("MUJOCO_GL", "egl")

import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import mujoco  # noqa: E402
from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow  # noqa: E402

from x2_ultra.app.viewport import MujocoViewport  # noqa: E402
from x2_ultra.model.builder import BuildOptions, build_model  # noqa: E402
from x2_ultra.tools.linkcraft_dance import LinkCraftClip, LinkCraftPlayer  # noqa: E402


class DanceWindow(QMainWindow):
    def __init__(self, path: Path):
        super().__init__()
        self.clip = LinkCraftClip.load(path)
        self.model = build_model(BuildOptions(scene="vacia"))
        self.data = mujoco.MjData(self.model)
        self.player = LinkCraftPlayer(self.model, self.clip)
        self.viewport = MujocoViewport(self.model)
        self.setCentralWidget(self.viewport)
        self.resize(1100, 760)
        self.setWindowTitle(f"X2 Ultra-N · LinkCraft · {path.stem}")
        self.playhead = 0.0
        self.running = True
        self.last_wall = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self.tick()

    def tick(self) -> None:
        now = time.perf_counter()
        if self.running:
            self.playhead = min(self.clip.duration, self.playhead + max(0.0, now - self.last_wall))
            if self.playhead >= self.clip.duration:
                self.running = False
        self.last_wall = now
        self.player.pose(self.data, self.playhead)
        self.viewport.hud_lines = [
            (f"LinkCraft  {self.playhead:6.1f} / {self.clip.duration:.1f} s", QColor("white")),
            ("Espacio: pausa · R: inicio · ←/→: 5 s · Esc: salir", QColor(190, 205, 225)),
            ("Reproducción cinemática (sin control de equilibrio)", QColor(235, 195, 115)),
        ]
        self.viewport.render_state(self.data.qpos)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.running = not self.running
        elif key == Qt.Key.Key_R:
            self.playhead, self.running = 0.0, True
        elif key == Qt.Key.Key_Left:
            self.playhead = max(0.0, self.playhead - 5.0)
        elif key == Qt.Key.Key_Right:
            self.playhead = min(self.clip.duration, self.playhead + 5.0)
        elif key == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
        self.last_wall = time.perf_counter()


def run(path: Path) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    win = DanceWindow(path)
    win.show()
    sys.exit(app.exec())
