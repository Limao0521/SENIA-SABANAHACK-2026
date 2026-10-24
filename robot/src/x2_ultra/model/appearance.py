"""Colores del X2 como los del robot real: carcasa blanca, articulaciones y cabeza en gris oscuro, pies amarillos.

El MJCF oficial v1.4.0 deja todas las piezas en blanco; el v1.3.0 (``X2_URDF-v1.3.0/x2_ultra.xml``) sí colorea cada
una, y de ahí sale ``PART_COLORS`` (los nombres de cuerpo no cambiaron entre versiones). Se pinta solo la geometría
visual del cuerpo (mallas sin colisión) antes de injertar las manos, que conservan sus colores.

El X2 EDU se pinta igual que el Ultra-N, pero su cabeza (fija) viene dentro de la malla del torso
(``torso_lite_link``): se separa en dos mallas para pintar la cabeza de gris oscuro como la del Ultra-N.
"""
from __future__ import annotations

from pathlib import Path

import mujoco
import numpy as np

WHITE = (0.93, 0.94, 0.95, 1.0)
LIGHT = (0.84, 0.85, 0.87, 1.0)        # cuello (head_yaw_link): gris claro en el v1.3.0
DARK = (0.17, 0.17, 0.18, 1.0)
FOOT = (1.0, 0.62, 0.04, 1.0)
LOGO = (0.02, 0.02, 0.02, 1.0)         # AGIBOT del pecho (malla ``agibot``; en el MJCF oficial, negro al 50 %)

# Por cuerpo, sin el prefijo ``left_``/``right_``. Lo que no está aquí es blanco.
PART_COLORS = {
    "pelvis": DARK, "waist_yaw_link": DARK, "waist_pitch_link": DARK,
    "hip_pitch_link": DARK, "ankle_pitch_link": DARK, "ankle_roll_link": FOOT,
    "shoulder_pitch_link": DARK, "wrist_pitch_link": DARK,
    "head_yaw_link": LIGHT, "head_pitch_link": DARK,
}
DARK_MESHES = ("rgbd_head_front_link", "stereo_head_front_link", "rgb_head_rear_link", "rgb_head_center_link",
               "lidar_chest_front_link")

# Cabeza del EDU en el marco de ``torso_link``: triángulos por encima del cuello y dentro del contorno de la cabeza
# (sin el asa de la espalda, que sube a la misma altura por detrás). Medido sobre la malla de v1.4.0.
EDU_HEAD_MESH = "torso_lite_link"
EDU_HEAD_BOX = {"z_min": 0.335, "x_min": -0.08, "y_abs_max": 0.078}

MATERIALS = {"x2_blanco": (WHITE, 0.55, 0.6), "x2_claro": (LIGHT, 0.4, 0.5), "x2_oscuro": (DARK, 0.35, 0.4),
             "x2_pie": (FOOT, 0.3, 0.4), "x2_logo": (LOGO, 0.2, 0.2)}           # color, specular, shininess
_BY_COLOR = {c: name for name, (c, _, _) in MATERIALS.items()}


def _part(body_name: str) -> str:
    for side in ("left_", "right_"):
        if body_name.startswith(side):
            return body_name[len(side):]
    return body_name


def paint(spec: mujoco.MjSpec) -> None:
    """Pinta las mallas visuales del cuerpo del X2 (Ultra o EDU) con los colores del robot real."""
    for name, (rgba, specular, shininess) in MATERIALS.items():
        spec.add_material(name=name, rgba=rgba, specular=specular, shininess=shininess)
    for g in spec.geoms:
        if g.type != mujoco.mjtGeom.mjGEOM_MESH or g.contype != 0 or g.conaffinity != 0:
            continue                                   # solo visuales
        if g.meshname == "agibot":
            color = LOGO
        elif g.meshname in DARK_MESHES:
            color = DARK
        else:
            color = PART_COLORS.get(_part(g.parent.name), WHITE)
        g.rgba = color                                 # el rgba de la geometría manda sobre el del material
        g.material = _BY_COLOR[color]
    for g in list(spec.geoms):
        if g.meshname == EDU_HEAD_MESH and g.contype == 0 and g.conaffinity == 0:
            _split_edu_head(spec, g)


def _stl_triangles(path: Path) -> np.ndarray:
    """Triángulos (n × 3 × 3) de un STL binario."""
    raw = path.read_bytes()
    n = int(np.frombuffer(raw, np.uint32, 1, 80)[0])
    rec = np.frombuffer(raw, np.dtype([("normal", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")]), n, 84)
    return rec["v"].astype(float)


def _split_edu_head(spec: mujoco.MjSpec, geom) -> None:
    tri = _stl_triangles(Path(spec.meshdir) / f"{EDU_HEAD_MESH}.stl")
    c = tri.mean(axis=1)
    b = EDU_HEAD_BOX
    head = (c[:, 2] > b["z_min"]) & (c[:, 0] > b["x_min"]) & (np.abs(c[:, 1]) < b["y_abs_max"])
    for name, sel in (("edu_torso", ~head), ("edu_cabeza", head)):
        # Vértices compartidos (el STL repite cada vértice por triángulo): sin esto MuJoCo sombrea plano, facetado.
        verts, faces = np.unique(tri[sel].reshape(-1, 3).round(7), axis=0, return_inverse=True)
        spec.add_mesh(name=name, uservert=verts.reshape(-1), userface=faces.reshape(-1),
                      inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL)
    geom.meshname = "edu_torso"
    geom.parent.add_geom(name="edu_cabeza", type=mujoco.mjtGeom.mjGEOM_MESH, meshname="edu_cabeza",
                         pos=geom.pos, quat=geom.quat, rgba=DARK, material=_BY_COLOR[DARK], contype=0,
                         conaffinity=0, density=0, group=1)
