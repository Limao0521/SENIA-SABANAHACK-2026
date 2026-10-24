"""Modelo MuJoCo del perro robot AgiBot D1 (Edu) y su trote cinemático.

El modelo sale del URDF del D1 Edu (``scripts/fetch_d1.sh``): 4 patas de 3 ejes (abducción, cadera y rodilla),
muslo de 0,20 m y espinilla de 0,227 m hasta el centro de la esfera del pie. Aquí se le añade una articulación
libre ``root`` y se deja preparado para moverse **cinemáticamente**: no choca con nada (contype/conaffinity 0) y todos sus cuerpos
llevan compensación de gravedad, así que entre dos posturas impuestas no se mueve solo. No hay policy de marcha
del D1: el trote es una función del tiempo (``trot_pose``), como los bailes de LinkCraft del humanoide.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from x2_ultra import paths

LEGS = ("FL", "FR", "RR", "RL")               # orden de las articulaciones en el URDF
LEG_JOINTS = tuple(f"{leg}_{part}_JOINT" for leg in LEGS for part in ("ABAD", "HIP", "KNEE"))
THIGH, SHANK = 0.2, 0.22666                   # m: de la rodilla al centro de la esfera del pie
FOOT_RADIUS = 0.022                           # esfera del pie (la que deja el URDF al fusionar FOOT_LINK)
STAND_HIP, STAND_KNEE = 0.8, -1.5             # rad: de pie
SIT_HIP, SIT_KNEE = 1.35, -2.55               # rad: tumbado, esperando junto al mostrador
TROT_PAIRS = {"FL": 0.0, "RR": 0.0, "FR": 0.5, "RL": 0.5}   # fase de cada pata: diagonales a la vez
STEP_HEIGHT = 0.07                            # m: altura del pie en el vuelo
GAIT_HZ = 2.2                                 # ciclos/s al trotar

BODY_RGBA = (0.20, 0.21, 0.23, 1.0)           # carcasa gris grafito
LEG_RGBA = (0.11, 0.11, 0.12, 1.0)
FOOT_RGBA = (0.05, 0.05, 0.05, 1.0)


def _leg_fk(hip: float, knee: float) -> tuple[float, float]:
    """Pie (x, z) respecto al eje de la cadera para unos ángulos de cadera y rodilla (eje +y, URDF)."""
    x = -THIGH * math.sin(hip) - SHANK * math.sin(hip + knee)
    z = -THIGH * math.cos(hip) - SHANK * math.cos(hip + knee)
    return x, z


def leg_ik(x: float, z: float) -> tuple[float, float]:
    """Ángulos de cadera y rodilla (rodilla hacia atrás, negativa) que llevan el pie a (x, z)."""
    r2 = x * x + z * z
    c = np.clip((r2 - THIGH ** 2 - SHANK ** 2) / (2 * THIGH * SHANK), -1.0, 1.0)
    knee = -math.acos(c)
    hip = math.atan2(-x, -z) - math.atan2(SHANK * math.sin(knee), THIGH + SHANK * math.cos(knee))
    return hip, knee


STAND_FOOT = _leg_fk(STAND_HIP, STAND_KNEE)
STAND_HEIGHT = -STAND_FOOT[1] + FOOT_RADIUS   # m: altura de la base sobre el suelo, de pie


def stand_pose() -> np.ndarray:
    return np.array([0.0, STAND_HIP, STAND_KNEE] * 4)


def sit_pose() -> np.ndarray:
    return np.array([0.0, SIT_HIP, SIT_KNEE] * 4)


def sit_height() -> float:
    return -_leg_fk(SIT_HIP, SIT_KNEE)[1] + FOOT_RADIUS


def trot_pose(phase: float, stride: float, amount: float = 1.0) -> np.ndarray:
    """12 ángulos de un trote: ``phase`` en ciclos, ``stride`` lo que avanza el cuerpo durante un apoyo (medio
    ciclo; m, negativo hacia atrás) y ``amount`` (0-1) cuánto se levantan los pies (0 = de pie)."""
    q = np.zeros(12)
    x0, z0 = STAND_FOOT
    for i, leg in enumerate(LEGS):
        u = (phase + TROT_PAIRS[leg]) % 1.0
        if u < 0.5:                                    # vuelo: el pie adelanta y se levanta
            s = u / 0.5
            dx = stride * (s - 0.5)
            dz = STEP_HEIGHT * amount * math.sin(math.pi * s)
        else:                                          # apoyo: el pie queda en el suelo y el cuerpo avanza
            s = (u - 0.5) / 0.5
            dx = stride * (0.5 - s)
            dz = 0.0
        q[3 * i + 1], q[3 * i + 2] = leg_ik(x0 + dx, z0 + dz)
    return q


def build_d1_spec() -> mujoco.MjSpec:
    """El D1 como ``MjSpec`` listo para ``attach``: base libre ``root``, sin colisiones y con gravedad compensada."""
    root = paths.d1_root()
    xml = (root / "urdf" / "edu_mujoco.urdf").read_text()
    xml = xml.replace("package://edu_description/meshes/", f"{root / 'meshes'}/")     # rutas absolutas: attach
    urdf = mujoco.MjSpec.from_string(xml)
    urdf.compiler.discardvisual = False           # si no, el compilador de URDF tira las geometrías sin contactos
    urdf.compiler.fusestatic = False              # al fusionar FOOT_LINK, to_xml pierde dónde está el pie
    urdf.compile()
    # Como MJCF: editar la spec del URDF (gravcomp) la corrompe en MuJoCo 3.15 (segfault al liberarla).
    spec = mujoco.MjSpec.from_string(urdf.to_xml())
    spec.body("base_link").add_freejoint(name="root")
    for body in spec.worldbody.find_all(mujoco.mjtObj.mjOBJ_BODY):
        body.gravcomp = 1.0
    for geom in spec.geoms:
        geom.contype = geom.conaffinity = 0
        if geom.type == mujoco.mjtGeom.mjGEOM_SPHERE:
            geom.rgba = FOOT_RGBA
        elif geom.parent.name == "base_link":
            geom.rgba = BODY_RGBA
        else:
            geom.rgba = LEG_RGBA
    return spec
