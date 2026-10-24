"""Cámaras y LiDAR del gemelo, sin ROS: intrínsecos, render de color/profundidad y nube PointXYZIRT.

Las cámaras son estenopeicas (MuJoCo no simula distorsión): ``camera_info`` lleva ``plumb_bob`` con D = 0 y la
K/P que corresponde al campo de visión vertical de cada cámara. El LiDAR lanza un haz por celda de 0,625° en su
marco propio (x adelante, y izquierda, z arriba) contra las superficies visibles (grupos 0-2: mundo y mallas del
robot, como ve un sensor real, incluidos los propios brazos), con zona ciega de 0,1 m.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import mujoco
import numpy as np

from x2_ultra.twin import contract as C

VISIBLE_GROUPS = np.array([1, 1, 1, 0, 0, 0], dtype=np.uint8)


@dataclass
class CameraSetup:
    key: str
    spec: C.CameraSpec
    cam: str               # nombre de la cámara MuJoCo
    width: int
    height: int
    fx: float
    fy: float
    cx: float
    cy: float
    tx: float = 0.0        # −fx·baseline para la derecha del estéreo (P[0, 3])

    @property
    def K(self) -> list[float]:
        return [self.fx, 0.0, self.cx, 0.0, self.fy, self.cy, 0.0, 0.0, 1.0]

    @property
    def P(self) -> list[float]:
        return [self.fx, 0.0, self.cx, self.tx, 0.0, self.fy, self.cy, 0.0, 0.0, 0.0, 1.0, 0.0]


def camera_setups(quality: str) -> list[CameraSetup]:
    """Cámaras a la resolución pedida ('real', 'media', 'reducida'), con intrínsecos de la estenopeica de MuJoCo
    (píxeles cuadrados, eje óptico en el centro de la imagen)."""
    out = []
    for key, spec in C.CAMERAS.items():
        s = (C.CAMERA_SCALE_RGBD if key.startswith("rgbd") else C.CAMERA_SCALE)[quality]
        w, h = max(2, round(spec.width * s / 2) * 2), max(2, round(spec.height * s / 2) * 2)
        f = (h / 2) / math.tan(math.radians(spec.fovy_deg) / 2)
        setup = CameraSetup(key, spec, f"cam_{key}", w, h, f, f, (w - 1) / 2, (h - 1) / 2)
        if key == "stereo_right":
            setup.tx = -f * C.STEREO_BASELINE
        out.append(setup)
    return out


class CameraRenderer:
    """Render fuera de pantalla (EGL en la GPU) de las cámaras del gemelo."""

    def __init__(self, model: mujoco.MjModel, setups: list[CameraSetup]):
        self.model = model
        self.rgb, self.depth = {}, {}
        for s in setups:
            size = (s.height, s.width)
            if s.spec.depth:
                if size not in self.depth:
                    r = mujoco.Renderer(model, s.height, s.width)
                    r.enable_depth_rendering()
                    self.depth[size] = r
            elif size not in self.rgb:
                self.rgb[size] = mujoco.Renderer(model, s.height, s.width)

    def render(self, data: mujoco.MjData, setup: CameraSetup) -> np.ndarray:
        """RGB uint8 (h, w, 3), o profundidad uint16 en mm (h, w) con 0 fuera de ``DEPTH_RANGE``."""
        size = (setup.height, setup.width)
        if setup.spec.depth:
            r = self.depth[size]
            r.update_scene(data, camera=setup.cam)
            z = r.render()
            lo, hi = C.DEPTH_RANGE
            mm = np.where((z >= lo) & (z <= hi), z * 1000.0, 0.0)
            return mm.astype(np.uint16)
        r = self.rgb[size]
        r.update_scene(data, camera=setup.cam)
        return r.render()

    def close(self) -> None:
        for r in list(self.rgb.values()) + list(self.depth.values()):
            r.close()


# --- LiDAR ------------------------------------------------------------------------------------------
# PointXYZIRT de RoboSense (rslidar_sdk + pcl::toROSMsg): x, y, z, relleno, intensity, ring, relleno, timestamp.
POINT_DTYPE = np.dtype({"names": ["x", "y", "z", "intensity", "ring", "timestamp"],
                        "formats": ["<f4", "<f4", "<f4", "<f4", "<u2", "<f8"],
                        "offsets": [0, 4, 8, 16, 20, 24], "itemsize": 32})
# (nombre, offset, datatype de sensor_msgs/PointField: FLOAT32 = 7, UINT16 = 4, FLOAT64 = 8)
POINT_FIELDS = (("x", 0, 7), ("y", 4, 7), ("z", 8, 7), ("intensity", 16, 7), ("ring", 20, 4), ("timestamp", 24, 8))


class LidarScanner:
    def __init__(self, model: mujoco.MjModel, res_deg: float = C.LIDAR_RES_DEG):
        self.model = model
        hfov, vfov = C.LIDAR_FOV_DEG
        az = np.radians(np.arange(-hfov / 2 + res_deg / 2, hfov / 2, res_deg))
        el = np.radians(np.arange(-vfov / 2 + res_deg / 2, vfov / 2, res_deg))
        A, E = np.meshgrid(az, el)                         # filas = elevación (ring 0 abajo)
        self.dirs = np.stack([np.cos(E) * np.cos(A), np.cos(E) * np.sin(A), np.sin(E)], -1).reshape(-1, 3)
        self.ring = np.repeat(np.arange(len(el), dtype=np.uint16), len(az))
        self.n = len(self.dirs)
        self.site = model.site("lidar").id
        self.exclude = model.body("torso_link").id         # el sensor está dentro de la carcasa del pecho
        self.geomid = np.zeros(self.n, dtype=np.int32)
        self.dist = np.zeros(self.n)
        # Intensidad aproximada: luminancia del color de cada geometría (0-255).
        rgba = model.geom_rgba.copy()
        mat = model.geom_matid
        rgba[mat >= 0] = model.mat_rgba[mat[mat >= 0]]
        self.intensity = (255.0 * (0.2126 * rgba[:, 0] + 0.7152 * rgba[:, 1] + 0.0722 * rgba[:, 2])).astype(np.float32)

    def scan(self, data: mujoco.MjData) -> np.ndarray:
        """Una nube (array estructurado ``POINT_DTYPE``) en el marco ``lidar_chest_front``."""
        R = data.site_xmat[self.site].reshape(3, 3)
        vec = (self.dirs @ R.T).reshape(-1)
        lo, hi = C.LIDAR_RANGE
        mujoco.mj_multiRay(self.model, data, data.site_xpos[self.site].copy(), vec, VISIBLE_GROUPS, 1,
                           self.exclude, self.geomid, self.dist, None, self.n, hi)
        hit = (self.geomid >= 0) & (self.dist >= lo) & (self.dist <= hi)
        d = np.round(self.dist[hit] / C.LIDAR_RESOLUTION_M) * C.LIDAR_RESOLUTION_M
        pts = np.zeros(int(hit.sum()), dtype=POINT_DTYPE)
        xyz = self.dirs[hit] * d[:, None]
        pts["x"], pts["y"], pts["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
        pts["intensity"] = self.intensity[self.geomid[hit]]
        pts["ring"] = self.ring[hit]
        return pts
