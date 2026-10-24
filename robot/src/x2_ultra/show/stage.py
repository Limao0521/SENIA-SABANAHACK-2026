"""Modelo MuJoCo de los escenarios de los retos: robots X2 montados en un decorado.

Cada robot se construye con su builder y se monta con ``MjSpec.attach`` bajo un prefijo (``ultra/``, ``edu/``):
sus articulaciones, actuadores y cuerpos se llaman igual que en el modelo de un solo robot con el prefijo delante.

Un ``Decor`` reúne lo propio de cada reto: qué robots y dónde, el decorado (``show.venue`` para el reto 1, la tarima
con pantalla; ``show.club`` para el reto 2, el vestíbulo del club), la altura del suelo bajo los robots, las vistas
de cámara, la imagen de la pantalla sin vídeo y los elementos que se mueven solos (las puertas del club). El club
lleva además el perro guía AgiBot D1 (``model.d1``) bajo el prefijo ``d1/``.

La pantalla es un rectángulo (malla de dos triángulos con coordenadas de textura) con material emisivo y una
textura RGB ``pantalla``. ``show.screen.VideoScreen`` escribe los fotogramas en ``model.tex_data`` y la vista los
sube a la GPU con ``mjr_uploadTexture``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable

import mujoco
import numpy as np

from x2_ultra import paths
from x2_ultra.model import d1
from x2_ultra.model.builder import BuildOptions, build_edu_spec, build_robot_spec, default_qpos, set_options
from x2_ultra.robot import joints as J
from x2_ultra.show import brand, club, guide, venue


@dataclass(frozen=True)
class StageRobot:
    key: str            # prefijo en el modelo: "<key>/"
    label: str
    variant: str        # "ultra_n" (cuello 2 ejes) | "edu" (sin cuello)
    y: float            # posición lateral [m]
    omnihand: bool = False   # solo el Ultra-N puede llevarlas; sin ellas, la muñeca de serie (como el EDU)
    x: float = 0.0
    yaw: float = 0.0    # rumbo inicial [rad]; 0 = mirando a +x

    @property
    def prefix(self) -> str:
        return f"{self.key}/"

    @property
    def has_head(self) -> bool:
        return self.variant == "ultra_n"

    @property
    def has_hands(self) -> bool:
        return self.variant == "ultra_n" and self.omnihand


def _robot_spec(robot: StageRobot, opts: BuildOptions) -> mujoco.MjSpec:
    if robot.variant == "ultra_n":
        return build_robot_spec(replace(opts, omnihand=robot.omnihand))
    return build_edu_spec(opts)


@dataclass(frozen=True)
class CameraView:
    lookat: tuple
    distance: float
    azimuth: float
    elevation: float
    ceiling: bool = True        # False: ocultar el techo (grupo 2), para vistas desde arriba


@dataclass(frozen=True)
class Decor:
    key: str
    title: str
    robots: tuple
    build: Callable                     # build(spec, robots): añade el decorado
    floor_z: float = 0.0                # altura del suelo bajo los robots
    cameras: dict = field(default_factory=dict)   # nombre -> CameraView; la primera es la inicial
    card: Callable | None = None        # card(ancho, alto) -> imagen de la pantalla sin vídeo
    props: Callable | None = None       # props(model) -> objeto con update(data, [xy de cada robot])
    tour: Callable | None = None        # tour(robot, categoría, piso, volver, huésped) -> show.guide.GuideTour
    categories: dict = field(default_factory=dict)     # categoría de visitante -> nombre visible
    destinations: dict = field(default_factory=dict)   # piso -> nombre visible (rutas a los ascensores)
    guest: Callable | None = None       # guest(model, data) -> show.guest.Guest (huésped que maneja el usuario)
    dog: tuple | None = None            # (x, y, rumbo) donde espera el perro guía AgiBot D1 (show.dog), o sin perro


# Reto 1. Vistos desde el público (+x mirando hacia la pantalla), +y queda a la derecha: el Ultra-N a la izquierda.
# Los dos llevan la muñeca de serie, como los robots reales hoy (sin OmniHand).
ROBOTS = (
    StageRobot("ultra", "X2 Ultra-N", "ultra_n", -0.75),
    StageRobot("edu", "X2 EDU", "edu", 0.75),
)
ESCENARIO = Decor(
    "escenario", "X2 · escenario del reto 1 (Autoriente)", ROBOTS,
    build=lambda spec, robots: venue.add_venue(spec, [r.y for r in robots]),
    floor_z=venue.PLATFORM_H,
    cameras={"Público": CameraView(**venue.AUDIENCE_CAM)},
    card=brand.title_card,
)
# Reto 2: el Ultra-N recibe en el vestíbulo del club y guía hasta los ascensores.
CLUB = Decor(
    "club", "X2 · Club N, recepción (reto 2)",
    (StageRobot("ultra", "X2 Ultra-N", "ultra_n", club.ROBOT_START[1], x=club.ROBOT_START[0],
                yaw=club.ROBOT_START[2]),),
    build=club.add_club,
    cameras={name: CameraView(*v) for name, v in club.CAMERAS.items()},
    card=club.club_card,
    props=club.ClubDoors,
    tour=guide.club_tour,
    categories=guide.CATEGORIES,
    destinations={floor: f"Piso {floor} · {name}" for floor, name in club.FLOORS.items()},
    guest=club.make_guest,
    dog=club.DOG_HOME,
)
DOG_PREFIX = "d1/"
DECORS = {d.key: d for d in (ESCENARIO, CLUB)}


def build_stage_spec(decor: Decor = ESCENARIO, timestep: float = 0.002) -> mujoco.MjSpec:
    opts = BuildOptions(scene="vacia", timestep=timestep)
    robots = decor.robots
    spec = mujoco.MjSpec()
    spec.modelname = f"x2_{decor.key}"
    spec.compiler.degree = False
    spec.meshdir = str(paths.meshes_dir())
    set_options(spec, opts)
    decor.build(spec, robots)
    floor = decor.floor_z

    homes = []
    for robot in robots:
        child = _robot_spec(robot, opts)
        homes.append((robot, child.compile(), default_qpos(child)))   # antes de montarlo: luego ya no compila
        frame = spec.worldbody.add_frame(pos=[robot.x, robot.y, floor], quat=_yaw_quat(robot.yaw))
        spec.attach(child, prefix=robot.prefix, frame=frame)
    if decor.dog is not None:
        spec.attach(d1.build_d1_spec(), prefix=DOG_PREFIX, frame=spec.worldbody.add_frame())

    # Postura inicial: cada robot de pie en su sitio con la postura por defecto.
    m = spec.compile()
    qpos = m.qpos0.copy()
    for robot, cm, home in homes:
        for j in range(cm.njnt):
            name = mujoco.mj_id2name(cm, mujoco.mjtObj.mjOBJ_JOINT, j)
            sj = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, robot.prefix + name)
            a, ca = m.jnt_qposadr[sj], cm.jnt_qposadr[j]
            n = 7 if cm.jnt_type[j] == mujoco.mjtJoint.mjJNT_FREE else 1
            qpos[a:a + n] = home[ca:ca + n]
            if n == 7:
                qpos[a:a + 3] += [robot.x, robot.y, floor]
                qpos[a + 3:a + 7] = _yaw_quat(robot.yaw)
    if decor.dog is not None:                    # el perro, tumbado en su sitio
        x, y, yaw = decor.dog
        a = m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, DOG_PREFIX + "root")]
        qpos[a:a + 7] = [x, y, floor + d1.sit_height(), *_yaw_quat(yaw)]
        for name, q in zip(d1.LEG_JOINTS, d1.sit_pose()):
            qpos[m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, DOG_PREFIX + name)]] = q
    ctrl = np.zeros(m.nu)
    for i in range(m.nu):
        name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_ACTUATOR, i)
        ctrl[i] = J.DEFAULT_POSE.get(name.split("/", 1)[-1], 0.0) if "/" in name else 0.0   # puertas: cerradas
    spec.add_key(name="home", qpos=qpos, ctrl=ctrl)
    return spec


def build_stage_model(decor: Decor = ESCENARIO, timestep: float = 0.002) -> mujoco.MjModel:
    return build_stage_spec(decor, timestep).compile()


def _yaw_quat(yaw: float) -> np.ndarray:
    return np.array([math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2)])

