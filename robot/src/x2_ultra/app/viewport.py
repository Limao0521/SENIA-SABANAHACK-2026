"""Vista 3D: MuJoCo renderiza fuera de pantalla (EGL/GLFW) y Qt muestra la imagen con un HUD encima.

Ratón: arrastrar con el botón izquierdo = orbitar, derecho = desplazar, rueda = zoom,
doble clic = alternar cámara que sigue al robot.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


class MujocoViewport(QWidget):
    def __init__(self, model: mujoco.MjModel, parent=None):
        super().__init__(parent)
        self.model = model
        self.data = mujoco.MjData(model)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(480, 320)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.cam = mujoco.MjvCamera()
        self.cam.type = mujoco.mjtCamera.mjCAMERA_FREE
        self.cam.distance, self.cam.azimuth, self.cam.elevation = 3.2, 135.0, -18.0
        self.cam.lookat[:] = [0, 0, 0.75]
        self.follow = True
        self.follow_qadr = 0                   # qpos de la base que sigue la cámara (escenas con varios cuerpos libres)
        self.opt = mujoco.MjvOption()
        self.opt.geomgroup[:] = [1, 1, 1, 0, 0, 0]     # visual sí, colisión (3) y sitios (4) no
        self.opt.sitegroup[:] = [0, 0, 0, 0, 0, 0]       # sitios (tacto, agarre) ocultos por defecto
        self.show_contacts = False
        self.renderer: mujoco.Renderer | None = None
        self._size = (0, 0)
        self._image: QImage | None = None
        self._frame = None
        self._last_mouse = None
        self.hud_lines: list[tuple[str, QColor]] = []
        self.banner: str = ""
        self.subtitle: str = ""                # lo que dice el robot, abajo en el centro
        self.help_text: str = ""
        self.show_help = False
        self.markers: list[tuple] = []        # (pos(3), rgba, radio, mat(3x3)|None)
        self.arrow = None                      # (origen, vector, rgba)

    # ------------------------------------------------------------------ opciones
    def set_collision_visible(self, on: bool) -> None:
        self.opt.geomgroup[3] = 1 if on else 0

    def set_sites_visible(self, on: bool) -> None:
        self.opt.sitegroup[4] = 1 if on else 0

    def set_contacts_visible(self, on: bool) -> None:
        self.show_contacts = on
        self.opt.flags[mujoco.mjtVisFlag.mjVIS_CONTACTPOINT] = on
        self.opt.flags[mujoco.mjtVisFlag.mjVIS_CONTACTFORCE] = on

    # ------------------------------------------------------------------ render
    def _ensure_renderer(self) -> None:
        ratio = self.devicePixelRatioF()
        w = int(min(self.width() * ratio, self.model.vis.global_.offwidth))
        h = int(min(self.height() * ratio, self.model.vis.global_.offheight))
        w, h = max(w - w % 4, 64), max(h, 64)
        if self.renderer is None or (w, h) != self._size:
            if self.renderer is not None:
                self.renderer.close()
            self.renderer = mujoco.Renderer(self.model, height=h, width=w)
            self._size = (w, h)

    def render_state(self, qpos: np.ndarray, mocap: tuple | None = None) -> None:
        self._ensure_renderer()
        d = self.data
        d.qpos[:] = qpos
        if mocap is not None:
            d.mocap_pos[:], d.mocap_quat[:] = mocap
        if self.show_contacts:
            mujoco.mj_forward(self.model, d)
        else:
            mujoco.mj_kinematics(self.model, d)
            mujoco.mj_camlight(self.model, d)     # posiciones de luces y cámaras (sin esto la escena sale oscura)
        if self.follow:
            a = self.follow_qadr
            target = np.array([d.qpos[a], d.qpos[a + 1], d.qpos[a + 2] + 0.1])
            self.cam.lookat[:] = self.cam.lookat + 0.15 * (target - self.cam.lookat)
        self.renderer.update_scene(d, camera=self.cam, scene_option=self.opt)
        scn = self.renderer.scene
        for pos, rgba, radius, mat in self.markers:
            self._add_geom(scn, mujoco.mjtGeom.mjGEOM_SPHERE, [radius, 0, 0], pos, np.eye(3), rgba)
            if mat is not None:
                for k, color in enumerate(((1, 0.2, 0.2, 1), (0.2, 1, 0.2, 1), (0.3, 0.4, 1, 1))):
                    self._add_connector(scn, pos, pos + 0.07 * mat[:, k], color, 0.004)
        if self.arrow is not None:
            origin, vec, rgba = self.arrow
            if np.linalg.norm(vec) > 0.02:
                self._add_connector(scn, origin, origin + vec, rgba, 0.012, arrow=True)
        self._frame = self.renderer.render()
        h, w, _ = self._frame.shape
        self._image = QImage(self._frame.data, w, h, 3 * w, QImage.Format.Format_RGB888)
        self.update()

    @staticmethod
    def _add_geom(scn, gtype, size, pos, mat, rgba) -> None:
        if scn.ngeom >= scn.maxgeom:
            return
        mujoco.mjv_initGeom(scn.geoms[scn.ngeom], gtype, np.asarray(size, float), np.asarray(pos, float),
                            np.asarray(mat, float).reshape(-1), np.asarray(rgba, np.float32))
        scn.ngeom += 1

    @staticmethod
    def _add_connector(scn, a, b, rgba, width, arrow=False) -> None:
        if scn.ngeom >= scn.maxgeom:
            return
        g = scn.geoms[scn.ngeom]
        mujoco.mjv_initGeom(g, mujoco.mjtGeom.mjGEOM_ARROW if arrow else mujoco.mjtGeom.mjGEOM_CAPSULE,
                            np.zeros(3), np.zeros(3), np.eye(3).reshape(-1), np.asarray(rgba, np.float32))
        mujoco.mjv_connector(g, mujoco.mjtGeom.mjGEOM_ARROW if arrow else mujoco.mjtGeom.mjGEOM_CAPSULE,
                             width, np.asarray(a, float), np.asarray(b, float))
        scn.ngeom += 1

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt)
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(18, 22, 30))
        if self._image is not None:
            p.drawImage(QRectF(self.rect()), self._image)
        self._paint_hud(p)
        p.end()

    def _paint_hud(self, p: QPainter) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont("DejaVu Sans Mono", 10)
        p.setFont(font)
        y = 22
        for text, color in self.hud_lines:
            p.setPen(QColor(0, 0, 0, 160))
            p.drawText(QPointF(13, y + 1), text)
            p.setPen(color)
            p.drawText(QPointF(12, y), text)
            y += 18
        if self.banner:
            big = QFont("DejaVu Sans", 20, QFont.Weight.Bold)
            p.setFont(big)
            rect = QRectF(0, self.height() * 0.38, self.width(), 50)
            p.fillRect(rect, QColor(120, 20, 20, 170))
            p.setPen(QColor(255, 240, 240))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.banner)
        if self.subtitle:
            p.setFont(QFont("DejaVu Sans", 15))
            metrics = p.fontMetrics()
            width = min(self.width() - 40, metrics.horizontalAdvance(self.subtitle) + 40)
            rect = QRectF((self.width() - width) / 2, self.height() - 120, width, 0)
            text_rect = p.boundingRect(rect.adjusted(20, 12, -20, 200), Qt.AlignmentFlag.AlignHCenter
                                       | Qt.TextFlag.TextWordWrap, self.subtitle)
            box = QRectF(rect.left(), self.height() - text_rect.height() - 52, width, text_rect.height() + 24)
            p.fillRect(box, QColor(10, 12, 18, 200))
            p.setPen(QColor(255, 236, 200))
            p.drawText(box.adjusted(20, 12, -20, -12), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                       self.subtitle)
        if self.show_help and self.help_text:
            p.setFont(QFont("DejaVu Sans Mono", 10))
            lines = self.help_text.splitlines()
            w = max(len(line) for line in lines) * 8 + 30
            h = len(lines) * 17 + 24
            rect = QRectF(self.width() - w - 14, 14, w, h)
            p.fillRect(rect, QColor(10, 14, 22, 215))
            p.setPen(QPen(QColor(90, 140, 220), 1))
            p.drawRect(rect)
            p.setPen(QColor(225, 232, 245))
            for i, line in enumerate(lines):
                p.drawText(QPointF(rect.left() + 14, rect.top() + 22 + 17 * i), line)

    # ------------------------------------------------------------------ ratón
    def mousePressEvent(self, e) -> None:  # noqa: N802
        self._last_mouse = e.position()
        self.setFocus()

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        self._last_mouse = None

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        self.follow = not self.follow

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._last_mouse is None:
            return
        dx = (e.position().x() - self._last_mouse.x()) / max(self.height(), 1)
        dy = (e.position().y() - self._last_mouse.y()) / max(self.height(), 1)
        self._last_mouse = e.position()
        buttons = e.buttons()
        if buttons & Qt.MouseButton.LeftButton and not (e.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.cam.azimuth -= 180 * dx
            self.cam.elevation = float(np.clip(self.cam.elevation - 120 * dy, -89, 89))
        elif buttons & (Qt.MouseButton.RightButton | Qt.MouseButton.MiddleButton) or buttons & Qt.MouseButton.LeftButton:
            az = math.radians(self.cam.azimuth)
            right = np.array([math.sin(az), -math.cos(az), 0.0])
            up = np.array([0.0, 0.0, 1.0])
            scale = self.cam.distance
            self.cam.lookat[:] = self.cam.lookat - scale * dx * right + scale * dy * up
            self.follow = False

    def wheelEvent(self, e) -> None:  # noqa: N802
        steps = e.angleDelta().y() / 120.0
        self.cam.distance = float(np.clip(self.cam.distance * (0.9 ** steps), 0.4, 15.0))

    def upload_texture(self, texid: int) -> None:
        """Sube a la GPU una textura cuyo contenido cambió en ``model.tex_data`` (la pantalla de vídeo). Sin
        renderer no hace falta: al crearse sube todas las texturas del modelo."""
        if self.renderer is None:
            return
        if self.renderer._gl_context:
            self.renderer._gl_context.make_current()
        mujoco.mjr_uploadTexture(self.model, self.renderer._mjr_context, texid)

    def close_renderer(self) -> None:
        if self.renderer is not None:
            self.renderer.close()
            self.renderer = None
