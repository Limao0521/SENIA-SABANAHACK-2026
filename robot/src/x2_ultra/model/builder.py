"""Genera el modelo MuJoCo del X2 Ultra-N con OmniHand a partir de los archivos oficiales de AgiBot.

AgiBot publica (github.com/AgibotTech/agibot_x2_urdf, v1.4.0):
  - ``X2-Ultra.xml``: MJCF del cuerpo afinado para MuJoCo (colisiones simplificadas, pie con 12 esferas,
    sensores IMU), pero **sin manos**.
  - ``X2-Ultra_omnihand.urdf``: URDF con las OmniHand (mallas, inercias, 10 ejes activos + 6 mimic por mano),
    pero sin nada específico de MuJoCo.

Este módulo parte del MJCF oficial y le injerta las manos del URDF:
  1. cambia la inercia y la malla de ``*_wrist_roll_link`` por las de la variante con OmniHand;
  2. cuelga palma y dedos (cuerpos, articulaciones, mallas y colisiones del URDF);
  3. convierte los ``<mimic>`` del URDF en restricciones ``<equality joint>``;
  4. sustituye los motores de par por actuadores de posición (PD interno de MuJoCo) con las ganancias de
     ``x2_ultra.robot.joints`` y los límites de par del Ultra-N, y añade 20 actuadores de dedos;
  5. añade sitios y sensores (IMU, pies, tacto simulado en palma y dedos).

``variant="full"`` es el modelo del simulador; ``variant="train"`` es el que usa el entrenamiento RL
(manos rígidas sin articulaciones, sin mallas visuales) para poder simular miles de robots en paralelo.
"""
from __future__ import annotations

import argparse
import math
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import mujoco
import numpy as np

from x2_ultra import paths
from x2_ultra.model.appearance import paint
from x2_ultra.robot import joints as J
from x2_ultra.robot import omnihand as H

# Grupos de colisión (contype/conaffinity): el cuerpo choca con todo; las manos chocan con el cuerpo,
# el suelo y los objetos, pero no entre sus propios dedos (evita contactos espurios en la mano cerrada).
BODY_CONTYPE, BODY_CONAFFINITY = 1, 3
HAND_CONTYPE, HAND_CONAFFINITY = 2, 1

HAND_KP, HAND_KV, HAND_FORCE = 2.0, 0.06, 1.5      # PD de los dedos [N·m/rad], [N·m·s/rad], [N·m]
HAND_ARMATURE, HAND_DAMPING = 0.001, 0.02

URDF_COLORS = {"silver": (0.78, 0.78, 0.80, 1.0), "blue": (0.22, 0.36, 0.62, 1.0),
               "black": (0.10, 0.10, 0.11, 1.0)}
DEFAULT_HAND_RGBA = (0.26, 0.27, 0.30, 1.0)

TOUCH_REGIONS = ("palm", "thumb", "index", "middle", "ring", "pinky")
GRASP_OFFSET = (0.045, 0.0, 0.09)   # en el marco de la palma: delante de la palma, a media altura de los dedos


@dataclass
class BuildOptions:
    variant: str = "full"          # "full" (simulador) | "train" (RL: manos rígidas, sin visuales)
    scene: str = "mesa"            # "vacia" | "mesa" (mesa con objetos para probar las manos)
    timestep: float = 0.002
    omnihand: bool = True          # False: muñeca de serie del MJCF oficial, sin manos (como el X2 EDU)


# ----------------------------------------------------------------------------------------------
# Utilidades geométricas
# ----------------------------------------------------------------------------------------------
def rpy_to_quat(r: float, p: float, y: float) -> np.ndarray:
    """URDF rpy (ejes fijos X, Y, Z: R = Rz(y)·Ry(p)·Rx(r)) -> cuaternión MuJoCo (w, x, y, z)."""
    cr, sr = math.cos(r / 2), math.sin(r / 2)
    cp, sp = math.cos(p / 2), math.sin(p / 2)
    cy, sy = math.cos(y / 2), math.sin(y / 2)
    return np.array([cr * cp * cy + sr * sp * sy,
                     sr * cp * cy - cr * sp * sy,
                     cr * sp * cy + sr * cp * sy,
                     cr * cp * sy - sr * sp * cy])


def _vec(text: str | None, default=(0.0, 0.0, 0.0)) -> np.ndarray:
    return np.array([float(v) for v in text.split()]) if text else np.array(default, dtype=float)


def _origin(el) -> tuple[np.ndarray, np.ndarray]:
    o = el.find("origin") if el is not None else None
    if o is None:
        return np.zeros(3), np.array([1.0, 0, 0, 0])
    return _vec(o.get("xyz")), rpy_to_quat(*_vec(o.get("rpy")))


def _quat_to_mat(q: np.ndarray) -> np.ndarray:
    m = np.zeros(9)
    mujoco.mju_quat2Mat(m, q)
    return m.reshape(3, 3)


# ----------------------------------------------------------------------------------------------
# Lectura del URDF de la OmniHand
# ----------------------------------------------------------------------------------------------
class OmniHandUrdf:
    def __init__(self, path: Path):
        root = ET.parse(path).getroot()
        self.links = {l.get("name"): l for l in root.findall("link")}
        self.joints = root.findall("joint")
        self.children: dict[str, list] = {}
        for j in self.joints:
            self.children.setdefault(j.find("parent").get("link"), []).append(j)

    def palm_joint(self, side: str):
        return next(j for j in self.joints if j.get("name") == f"{side}_palm_joint")

    def wrist_link(self, side: str):
        return self.links["left_wrist_roll_link" if side == "L" else "right_wrist_roll_link"]


def _inertial(body, link) -> None:
    inertial = link.find("inertial")
    if inertial is None:
        return
    pos, quat = _origin(inertial)
    i = inertial.find("inertia")
    tensor = np.array([[float(i.get("ixx")), float(i.get("ixy")), float(i.get("ixz"))],
                       [float(i.get("ixy")), float(i.get("iyy")), float(i.get("iyz"))],
                       [float(i.get("ixz")), float(i.get("iyz")), float(i.get("izz"))]])
    rot = _quat_to_mat(quat)
    tensor = rot @ tensor @ rot.T
    body.explicitinertial = True
    body.mass = float(inertial.find("mass").get("value"))
    body.ipos = pos
    body.iquat = [1, 0, 0, 0]
    body.fullinertia = [tensor[0, 0], tensor[1, 1], tensor[2, 2], tensor[0, 1], tensor[0, 2], tensor[1, 2]]
    body.inertia = [0, 0, 0]


class HandGrafter:
    """Cuelga una OmniHand del URDF en el cuerpo ``*_wrist_roll_link`` del MJCF oficial."""

    def __init__(self, spec: mujoco.MjSpec, urdf: OmniHandUrdf, opts: BuildOptions):
        self.spec, self.urdf, self.opts = spec, urdf, opts
        self.meshes: dict[str, str] = {}

    def mesh(self, filename: str) -> str:
        stem = Path(filename).stem
        name = f"omni_{stem}"
        if name not in self.meshes:
            self.spec.add_mesh(name=name, file=Path(filename).name)
            self.meshes[name] = filename
        return name

    def graft(self, side: str, wrist_body) -> None:
        pj = self.urdf.palm_joint(side)
        pos, quat = _origin(pj)
        self._add_link(wrist_body, pj.find("child").get("link"), pos, quat, joint=None)
        # Punto de agarre (lado interior de la palma, +x en ambas manos): objetivo de la IK.
        palm = self.spec.body(f"{side}_palm")
        palm.add_site(name=f"{side}_grasp", pos=GRASP_OFFSET, size=[0.012, 0, 0], group=4,
                      rgba=(1.0, 0.85, 0.1, 0.8))

    def _add_link(self, parent, link_name: str, pos, quat, joint) -> None:
        link = self.urdf.links[link_name]
        body = parent.add_body(name=link_name, pos=pos, quat=quat)
        _inertial(body, link)
        if joint is not None and joint.get("type") == "revolute" and self.opts.variant == "full":
            lim = joint.find("limit")
            body.add_joint(name=joint.get("name"), type=mujoco.mjtJoint.mjJNT_HINGE,
                           axis=_vec(joint.find("axis").get("xyz")),
                           range=[float(lim.get("lower")), float(lim.get("upper"))],
                           limited=mujoco.mjtLimited.mjLIMITED_TRUE,
                           armature=HAND_ARMATURE, damping=HAND_DAMPING, frictionloss=0.0)
        if self.opts.variant == "full":
            for vis in link.findall("visual"):
                geo = vis.find("geometry")[0]
                if geo.tag != "mesh":
                    continue
                vpos, vquat = _origin(vis)
                mat = vis.find("material")
                rgba = URDF_COLORS.get(mat.get("name") if mat is not None else "", DEFAULT_HAND_RGBA)
                body.add_geom(type=mujoco.mjtGeom.mjGEOM_MESH, meshname=self.mesh(geo.get("filename")),
                              pos=vpos, quat=vquat, rgba=rgba, group=1, contype=0, conaffinity=0,
                              density=0)
        palm_only = self.opts.variant == "train"
        if not palm_only or link_name.endswith("_palm"):
            for col in link.findall("collision"):
                geo = col.find("geometry")[0]
                cpos, cquat = _origin(col)
                g = body.add_geom(pos=cpos, quat=cquat, group=3, contype=HAND_CONTYPE,
                                  conaffinity=HAND_CONAFFINITY, condim=4, friction=[1.2, 0.02, 0.0005],
                                  rgba=(0.9, 0.5, 0.2, 0.4), density=0)
                if geo.tag == "box":
                    g.type = mujoco.mjtGeom.mjGEOM_BOX
                    g.size = _vec(geo.get("size")) / 2
                elif geo.tag == "cylinder":
                    g.type = mujoco.mjtGeom.mjGEOM_CYLINDER
                    g.size = [float(geo.get("radius")), float(geo.get("length")) / 2, 0]
                elif geo.tag == "sphere":
                    g.type = mujoco.mjtGeom.mjGEOM_SPHERE
                    g.size = [float(geo.get("radius")), 0, 0]
                elif geo.tag == "mesh":
                    g.type = mujoco.mjtGeom.mjGEOM_MESH
                    g.meshname = self.mesh(geo.get("filename"))
        for cj in self.urdf.children.get(link_name, []):
            child = cj.find("child").get("link")
            cpos, cquat = _origin(cj)
            if child.endswith("_tip"):
                # Las puntas no tienen masa: en MuJoCo son sitios (útiles para IK y para el tacto).
                body.add_site(name=child, pos=cpos, quat=cquat, size=[0.006, 0, 0], group=4,
                              rgba=(0.1, 0.8, 0.3, 0.6))
                continue
            self._add_link(body, child, cpos, cquat, joint=cj)


# ----------------------------------------------------------------------------------------------
# Construcción del modelo
# ----------------------------------------------------------------------------------------------
def _find_body(spec: mujoco.MjSpec, name: str):
    b = spec.body(name)
    if b is None:
        raise KeyError(f"cuerpo {name} no encontrado en el MJCF oficial")
    return b


def set_options(spec: mujoco.MjSpec, opts: BuildOptions) -> None:
    spec.option.timestep = opts.timestep
    spec.option.integrator = mujoco.mjtIntegrator.mjINT_IMPLICITFAST
    if opts.variant == "full":
        # Agarre: con el contacto blando por defecto los objetos resbalan de los dedos. Cono elíptico + impratio 10 +
        # noslip lo resuelven (agarre cilíndrico 9/9 en la prueba de docs/02); la marcha no cambia (docs/07).
        spec.option.cone = mujoco.mjtCone.mjCONE_ELLIPTIC
        spec.option.impratio = 10.0
        spec.option.noslip_iterations = 5
    spec.compiler.autolimits = True


def _arm_gravcomp(spec: mujoco.MjSpec, roots) -> None:
    """Compensación de gravedad de brazos, cabeza y manos a través de sus actuadores (como el feedforward de
    gravedad del control real): sin ella el brazo extendido se descuelga varios cm con los PD de AgiBot.
    actuatorgravcomp hace que el par lo den los motores (con su límite) y la reacción llegue al torso."""
    for root in roots:
        stack = [_find_body(spec, root)]
        while stack:
            b = stack.pop()
            b.gravcomp = 1.0
            for j in b.joints:
                j.actgravcomp = True
            stack.extend(b.bodies)


def _body_actuators(spec: mujoco.MjSpec, names) -> None:
    """Sustituye los motores de par del MJCF oficial por actuadores de posición con las ganancias de AgiBot."""
    for act in list(spec.actuators):
        spec.delete(act)
    for name in names:
        a = spec.add_actuator(name=name, target=name, trntype=mujoco.mjtTrn.mjTRN_JOINT)
        a.set_to_position(kp=J.KP[name], kv=J.KD[name])
        a.ctrlrange = list(J.JOINT_RANGE[name])
        a.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE
        a.forcerange = [-J.EFFORT_LIMIT[name], J.EFFORT_LIMIT[name]]
        a.forcelimited = mujoco.mjtLimited.mjLIMITED_TRUE


def _foot_sensors(spec: mujoco.MjSpec) -> None:
    for s in list(spec.sensors):
        if s.type in (mujoco.mjtSensor.mjSENS_JOINTPOS, mujoco.mjtSensor.mjSENS_JOINTVEL):
            spec.delete(s)
    for side in ("left", "right"):
        foot = _find_body(spec, f"{side}_ankle_roll_link")
        foot.add_site(name=f"{side}_foot", pos=[0.03, 0, -0.068], size=[0.01, 0, 0], group=4)
        spec.add_sensor(name=f"{side}_foot_pos", type=mujoco.mjtSensor.mjSENS_FRAMEPOS,
                        objtype=mujoco.mjtObj.mjOBJ_SITE, objname=f"{side}_foot")
        spec.add_sensor(name=f"{side}_foot_vel", type=mujoco.mjtSensor.mjSENS_FRAMELINVEL,
                        objtype=mujoco.mjtObj.mjOBJ_SITE, objname=f"{side}_foot")


def build_robot_spec(opts: BuildOptions | None = None) -> mujoco.MjSpec:
    opts = opts or BuildOptions()
    spec = mujoco.MjSpec.from_file(str(paths.official_mjcf()))
    spec.modelname = "x2_ultra_n_omnihand" if opts.omnihand else "x2_ultra_n"
    spec.meshdir = str(paths.meshes_dir())
    set_options(spec, opts)
    paint(spec)
    hands = opts.omnihand
    if hands:
        urdf = OmniHandUrdf(paths.omnihand_urdf())

        # --- 1) Muñeca de la variante OmniHand (brida en lugar de la mano genérica) ----------------
        for side, wrist in (("L", "left_wrist_roll_link"), ("R", "right_wrist_roll_link")):
            body = _find_body(spec, wrist)
            link = urdf.wrist_link(side)
            _inertial(body, link)
            mesh_file = link.find("visual").find("geometry")[0].get("filename")
            mesh_name = f"{wrist}_omnihand"
            spec.add_mesh(name=mesh_name, file=Path(mesh_file).name)
            for g in body.geoms:
                if g.type == mujoco.mjtGeom.mjGEOM_MESH:
                    g.meshname = mesh_name

        # --- 2) Manos ---------------------------------------------------------------------------
        grafter = HandGrafter(spec, urdf, opts)
        grafter.graft("L", _find_body(spec, "left_wrist_roll_link"))
        grafter.graft("R", _find_body(spec, "right_wrist_roll_link"))

    # --- Colisiones del cuerpo: también chocan con las manos ---------------------------------------
    for g in spec.geoms:
        if g.contype == 1 and g.conaffinity == 1:
            g.conaffinity = BODY_CONAFFINITY

    # Pares que se solapan en la postura por defecto sin ser padre-hijo (cabeza con torso).
    spec.add_exclude(bodyname1="torso_link", bodyname2="head_pitch_link")
    if opts.variant == "train":
        spec.compiler.fusestatic = True

    if opts.variant == "full":
        _arm_gravcomp(spec, ("left_shoulder_pitch_link", "right_shoulder_pitch_link", "head_yaw_link"))

    # --- 3) Mimic -> equality -----------------------------------------------------------------
    if opts.variant == "full" and hands:
        for mimic, (driver, mult) in H.MIMIC.items():
            spec.add_equality(type=mujoco.mjtEq.mjEQ_JOINT, name=f"mimic_{mimic}",
                              objtype=mujoco.mjtObj.mjOBJ_JOINT, name1=mimic, name2=driver,
                              data=[0.0, mult, 0, 0, 0, 0, 0, 0, 0, 0, 0],
                              solref=[0.005, 1.0])

    # --- 4) Actuadores de posición --------------------------------------------------------------
    _body_actuators(spec, J.BODY_JOINTS)
    if opts.variant == "full" and hands:
        for name in H.HAND_JOINTS:
            a = spec.add_actuator(name=name, target=name, trntype=mujoco.mjtTrn.mjTRN_JOINT)
            a.set_to_position(kp=HAND_KP, kv=HAND_KV)
            a.ctrlrange = list(H.HAND_RANGE[name])
            a.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE
            a.forcerange = [-HAND_FORCE, HAND_FORCE]
            a.forcelimited = mujoco.mjtLimited.mjLIMITED_TRUE

    # --- 5) Sitios y sensores --------------------------------------------------------------------
    _foot_sensors(spec)
    if opts.variant == "full":
        if hands:
            _add_touch_sensors(spec)
    else:
        _strip_for_training(spec)
    return spec


def build_edu_spec(opts: BuildOptions | None = None) -> mujoco.MjSpec:
    """X2 EDU desde su MJCF oficial: el mismo cuerpo que el Ultra-N sin cuello y con la muñeca sin mano (la pinza
    del robot no está en los modelos de AgiBot). Mismos actuadores de posición, ganancias y sensores de pie."""
    opts = opts or BuildOptions()
    spec = mujoco.MjSpec.from_file(str(paths.edu_mjcf()))
    spec.modelname = "x2_edu"
    spec.meshdir = str(paths.meshes_dir())
    set_options(spec, opts)
    paint(spec)
    _arm_gravcomp(spec, ("left_shoulder_pitch_link", "right_shoulder_pitch_link"))
    _body_actuators(spec, J.EDU_BODY_JOINTS)
    _foot_sensors(spec)
    return spec


def _strip_for_training(spec: mujoco.MjSpec) -> None:
    """Modelo ligero para RL: sin geometría visual y con las colisiones de malla sustituidas por cápsulas
    ajustadas por MuJoCo (cada malla de colisión tiene hasta 45k vértices y se copian 128 variantes)."""
    for g in list(spec.geoms):
        if g.contype == 0 and g.conaffinity == 0:
            spec.delete(g)
    mesh_geoms = [g for g in spec.geoms if g.type == mujoco.mjtGeom.mjGEOM_MESH]
    probe = spec.copy()
    for g in probe.geoms:
        if g.type == mujoco.mjtGeom.mjGEOM_MESH:
            g.type = mujoco.mjtGeom.mjGEOM_CAPSULE
    pm = probe.compile()
    fitted = {}
    for i, g in enumerate(probe.geoms):
        if g.type == mujoco.mjtGeom.mjGEOM_CAPSULE and g.meshname:
            fitted[(g.parent.name, g.meshname)] = (pm.geom_size[i].copy(), pm.geom_pos[i].copy(),
                                                   pm.geom_quat[i].copy())
    for g in mesh_geoms:
        size, pos, quat = fitted[(g.parent.name, g.meshname)]
        g.type = mujoco.mjtGeom.mjGEOM_CAPSULE
        g.meshname = ""
        g.size, g.pos, g.quat = size, pos, quat
    for mesh in list(spec.meshes):
        spec.delete(mesh)


def _add_touch_sensors(spec: mujoco.MjSpec) -> None:
    """Tacto simulado: un sensor ``touch`` por región (palma + 5 dedos) y mano, como las regiones de AimDK."""
    for side in ("L", "R"):
        palm = _find_body(spec, f"{side}_palm")
        palm.add_site(name=f"{side}_touch_palm", type=mujoco.mjtGeom.mjGEOM_BOX, pos=[0, 0, 0.057],
                      size=[0.024, 0.045, 0.047], group=4, rgba=(0.2, 0.6, 1.0, 0.15))
        tips = {"thumb": f"{side}_thumb_dip", "index": f"{side}_index_dip", "middle": f"{side}_middle_dip",
                "ring": f"{side}_ring_dip", "pinky": f"{side}_pinky_dip"}
        for region, body_name in tips.items():
            body = _find_body(spec, body_name)
            pos = [0, -0.0134, -0.0026] if region == "thumb" else [0, 0, 0.018]
            body.add_site(name=f"{side}_touch_{region}", type=mujoco.mjtGeom.mjGEOM_SPHERE, pos=pos,
                          size=[0.016, 0, 0], group=4, rgba=(0.2, 0.6, 1.0, 0.15))
        for region in TOUCH_REGIONS:
            spec.add_sensor(name=f"{side}_touch_{region}", type=mujoco.mjtSensor.mjSENS_TOUCH,
                            objtype=mujoco.mjtObj.mjOBJ_SITE, objname=f"{side}_touch_{region}")


def _add_world(spec: mujoco.MjSpec, opts: BuildOptions) -> None:
    spec.visual.headlight.diffuse = [0.55, 0.55, 0.55]
    spec.visual.headlight.ambient = [0.32, 0.32, 0.34]
    spec.visual.headlight.specular = [0.1, 0.1, 0.1]
    spec.visual.global_.offwidth = 1920
    spec.visual.global_.offheight = 1080
    spec.visual.quality.shadowsize = 4096
    spec.add_texture(name="skybox", type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT, rgb1=[0.42, 0.58, 0.75],
                     rgb2=[0.06, 0.08, 0.12], width=512, height=3072)
    spec.add_texture(name="floor", type=mujoco.mjtTexture.mjTEXTURE_2D,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER, rgb1=[0.46, 0.50, 0.56],
                     rgb2=[0.38, 0.42, 0.48], mark=mujoco.mjtMark.mjMARK_EDGE, markrgb=[0.65, 0.70, 0.76],
                     width=300, height=300)
    mat = spec.add_material(name="floor", texrepeat=[4, 4], texuniform=True, reflectance=0.05)
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = "floor"
    wb = spec.worldbody
    wb.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.05], material="floor",
                condim=3, friction=[1.0, 0.005, 0.0001])
    wb.add_light(name="sun", pos=[2, -2, 6], dir=[-0.3, 0.3, -1], type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL,
                 diffuse=[0.65, 0.65, 0.62], specular=[0.2, 0.2, 0.2], castshadow=False)
    wb.add_light(name="fill", pos=[-3, 3, 4], dir=[0.4, -0.4, -1], type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL,
                 diffuse=[0.25, 0.27, 0.3], specular=[0, 0, 0], castshadow=False)
    if opts.scene == "mesa":
        _add_table(spec)


def _add_table(spec: mujoco.MjSpec) -> None:
    """Mesa a la izquierda del robot (gira 90° con Q y avanza ~1 m) con objetos para agarrar."""
    wb = spec.worldbody
    top_h, x0, y0 = 0.72, 0.0, 1.75
    table = wb.add_body(name="mesa", pos=[x0, y0, 0])
    wood = (0.55, 0.40, 0.26, 1.0)
    table.add_geom(name="mesa_tablero", type=mujoco.mjtGeom.mjGEOM_BOX, pos=[0, 0, top_h - 0.02],
                   size=[0.30, 0.45, 0.02], rgba=wood, friction=[1.0, 0.01, 0.001])
    for i, (dx, dy) in enumerate(((0.26, 0.40), (0.26, -0.40), (-0.26, 0.40), (-0.26, -0.40))):
        table.add_geom(name=f"mesa_pata{i}", type=mujoco.mjtGeom.mjGEOM_BOX, pos=[dx, dy, (top_h - 0.04) / 2],
                       size=[0.025, 0.025, (top_h - 0.04) / 2], rgba=(0.35, 0.25, 0.16, 1))
    # objetos cerca del borde de la mesa que mira al robot (lado -y)
    objects = (
        ("cubo", mujoco.mjtGeom.mjGEOM_BOX, [0.025, 0.025, 0.025], [0.15, -0.30], 0.08, (0.85, 0.2, 0.2, 1)),
        ("botella", mujoco.mjtGeom.mjGEOM_CYLINDER, [0.03, 0.09, 0], [-0.15, -0.30], 0.25, (0.2, 0.55, 0.85, 1)),
        ("pelota", mujoco.mjtGeom.mjGEOM_SPHERE, [0.035, 0, 0], [0.0, -0.25], 0.06, (0.95, 0.75, 0.15, 1)),
    )
    for name, gtype, size, (dx, dy), mass, rgba in objects:
        z = top_h + (size[2] if gtype != mujoco.mjtGeom.mjGEOM_SPHERE else size[0]) + 0.002
        if gtype == mujoco.mjtGeom.mjGEOM_CYLINDER:
            z = top_h + size[1] + 0.002
        b = wb.add_body(name=name, pos=[x0 + dx, y0 + dy, z])
        b.add_freejoint(name=f"{name}_libre")
        b.add_geom(name=name, type=gtype, size=size, mass=mass, rgba=rgba, condim=4,
                   friction=[1.2, 0.02, 0.0005], solref=[0.004, 1])


def _add_world_sensors(spec: mujoco.MjSpec) -> None:
    for side in ("left", "right"):
        spec.add_sensor(name=f"{side}_foot_contact", type=mujoco.mjtSensor.mjSENS_CONTACT,
                        objtype=mujoco.mjtObj.mjOBJ_BODY, objname=f"{side}_ankle_roll_link",
                        reftype=mujoco.mjtObj.mjOBJ_GEOM, refname="floor",
                        intprm=[_contact_data_found_force(), _contact_reduce_netforce(), 1])


def _contact_data_found_force() -> int:
    # bits de "data" del sensor contact: found(1) | force(2)
    return 1 | 2


def _contact_reduce_netforce() -> int:
    return 3  # reduce="netforce"


def build_scene_spec(opts: BuildOptions | None = None) -> mujoco.MjSpec:
    opts = opts or BuildOptions()
    spec = build_robot_spec(opts)
    _add_world(spec, opts)
    _add_world_sensors(spec)
    home = default_qpos(spec)
    spec.add_key(name="home", qpos=home, ctrl=default_ctrl(spec))
    cam = spec.worldbody.add_camera(name="seguimiento", mode=mujoco.mjtCamLight.mjCAMLIGHT_TRACKCOM,
                                    pos=[2.2, -2.2, 1.6], xyaxes=[0.707, 0.707, 0, -0.3, 0.3, 0.9])
    del cam
    return spec


def default_qpos(spec: mujoco.MjSpec) -> np.ndarray:
    """qpos de pie en la postura por defecto con los pies apoyados (cubre objetos libres si los hay)."""
    m = spec.compile()
    d = mujoco.MjData(m)
    mujoco.mj_resetData(m, d)
    for name, val in J.DEFAULT_POSE.items():
        d.qpos[m.jnt_qposadr[mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_JOINT, name)]] = val
    d.qpos[2] = 1.0
    mujoco.mj_kinematics(m, d)
    foot_z = min(d.geom_xpos[g][2] - m.geom_size[g][0] for g in range(m.ngeom)
                 if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_SPHERE
                 and m.geom_bodyid[g] in (mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "left_ankle_roll_link"),
                                          mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "right_ankle_roll_link")))
    d.qpos[2] -= foot_z - 0.001
    return d.qpos.copy()


def default_ctrl(spec: mujoco.MjSpec) -> np.ndarray:
    m = spec.compile()
    ctrl = np.zeros(m.nu)
    for i in range(m.nu):
        name = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_ACTUATOR, i)
        ctrl[i] = J.DEFAULT_POSE.get(name, 0.0)
    return ctrl


def build_model(opts: BuildOptions | None = None) -> mujoco.MjModel:
    return build_scene_spec(opts).compile()


def export_xml(out_dir: Path, opts: BuildOptions) -> Path:
    """Escribe el MJCF (para abrirlo con ``python -m mujoco.viewer``). Las mallas se referencian en
    ``third_party/agibot_x2_urdf`` (enlace que crea scripts/setup.sh)."""
    spec = build_scene_spec(opts)
    out_dir.mkdir(parents=True, exist_ok=True)
    repo_meshes = paths.REPO_ROOT / "third_party" / "agibot_x2_urdf" / paths.URDF_VERSION_DIR / "meshes"
    xml = spec.to_xml()   # to_xml compila: necesita la ruta absoluta; luego se deja relativa al archivo
    rel = os.path.relpath(repo_meshes, out_dir)
    xml = xml.replace(f'meshdir="{spec.meshdir}', f'meshdir="{rel}', 1)
    out = out_dir / f"x2_ultra_n_omnihand_{opts.scene}.xml"
    out.write_text(xml)
    return out


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Genera el MJCF del X2 Ultra-N + OmniHand")
    ap.add_argument("--scene", choices=("vacia", "mesa"), default="mesa")
    ap.add_argument("--out", type=Path, default=paths.MODELS_DIR)
    args = ap.parse_args(argv)
    opts = BuildOptions(scene=args.scene)
    m = build_model(opts)
    out = export_xml(args.out, opts)
    pelvis = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
    robot_mass = sum(m.body_mass[b] for b in range(m.nbody) if m.body_rootid[b] == pelvis)
    print(f"{out}  (nq={m.nq} nv={m.nv} nu={m.nu} nbody={m.nbody} masa del robot={robot_mass:.2f} kg)")


if __name__ == "__main__":
    main()
