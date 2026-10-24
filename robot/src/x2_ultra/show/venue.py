"""Decorado del escenario de venta: tarima con borde LED, pared con pantalla LED, columnas de luz, truss y focos.

Ejes como en ``stage``: los robots miran a +x (al público), la pared y la pantalla quedan detrás, en x < 0.
Todo es estático (cuerpos sin articulaciones). Solo la tarima, el escalón y el suelo tienen colisión; el resto es
decorado visual. Los focos son luces ``spot`` reales de MuJoCo: dos cenitales que proyectan sombra sobre los robots
y dos contraluces azules desde atrás. Logos y colores de marca: ``show.brand``.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from x2_ultra.show import brand as B

# --- Tarima -----------------------------------------------------------------------------------
PLATFORM_H = 0.30                         # altura [m]
PLATFORM_X = (-1.9, 1.4)                  # fondo .. frente
PLATFORM_HALF_W = 3.0                     # medio ancho (y)
STEP = (0.40, 1.4, 0.15)                  # escalón delante: fondo, ancho, alto [m]

# --- Pared y pantalla ------------------------------------------------------------------------
WALL_X = -2.6
SCREEN_TEX = (1280, 720)                  # resolución de la textura de la pantalla (16:9)
SCREEN_SIZE = (6.4, 3.6)                  # ancho × alto [m]
SCREEN_X = WALL_X + 0.07                  # delante de la pared
SCREEN_BOTTOM = 1.25                      # borde inferior [m]: por encima de los robots en la tarima hasta el pecho

# --- Cámara del público (la de la app y la cámara "publico" del modelo) ------------------------
AUDIENCE_CAM = {"lookat": (-0.6, 0.0, 1.85), "distance": 8.2, "azimuth": 180.0, "elevation": -6.0}

# Rótulos del frontal de la tarima (logos de show.brand), de izquierda a derecha vistos desde el público (−y → +y).
FASCIA_LOGOS = ("autoriente", "ansrobex")
ROTULO_TEX = (1024, 154)                  # textura de cada rótulo [px]
ROTULO_W = 1.6                            # ancho de cada rótulo [m] (el alto sale de la proporción de la textura)
BANNER_TEX = (1040, 800)                  # paneles con los logos en la pared, a los lados de la pantalla
BANNER_W = 1.3
# Fondo de rótulos y paneles: Autoriente sobre su azul marino; Ansrobex (logo sobre blanco) en placa blanca sobre
# casi negro, como la tarima.
SIGN_BG = {"autoriente": None, "ansrobex": (8, 10, 16)}
COLUMN_Y = (5.0, 5.35)                    # columnas de luz (ancha, fina) [m] a cada lado


def audience_camera_pose() -> tuple[np.ndarray, np.ndarray]:
    """Posición y ``xyaxes`` de una cámara fija equivalente a la cámara libre ``AUDIENCE_CAM``."""
    c = AUDIENCE_CAM
    az, el = math.radians(c["azimuth"]), math.radians(c["elevation"])
    fwd = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    pos = np.array(c["lookat"]) - c["distance"] * fwd
    right = np.cross(fwd, [0, 0, 1])
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    return pos, np.concatenate([right, up])


def _quad(spec: mujoco.MjSpec, name: str, width: float, height: float) -> None:
    """Rectángulo de cara a +x centrado en su origen (columna 0 de la textura a la izquierda vista de frente)."""
    w, h = width / 2, height / 2
    spec.add_mesh(name=name, uservert=[0, -w, -h, 0, w, -h, 0, w, h, 0, -w, h], userface=[0, 1, 2, 0, 2, 3],
                  usertexcoord=[0, 1, 1, 1, 1, 0, 0, 0], inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL)


def _materials(spec: mujoco.MjSpec) -> None:
    spec.add_texture(name="cielo", type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT, rgb1=[0.03, 0.04, 0.07], rgb2=[0, 0, 0],
                     width=256, height=1536)
    spec.add_texture(name="suelo", type=mujoco.mjtTexture.mjTEXTURE_2D, builtin=mujoco.mjtBuiltin.mjBUILTIN_CHECKER,
                     rgb1=[0.075, 0.078, 0.09], rgb2=[0.06, 0.062, 0.072], width=256, height=256)
    # Sin reflejo en el suelo: MuJoCo refleja a través de la tarima y se veían los robots debajo de ella.
    mat = spec.add_material(name="suelo", texrepeat=[6, 6], texuniform=True, specular=0.3)
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = "suelo"
    spec.add_material(name="tarima", rgba=[0.11, 0.11, 0.13, 1], reflectance=0.12, specular=0.5, shininess=0.6)
    navy = np.array(B.rgba01(B.AUTORIENTE_NAVY))
    spec.add_material(name="pared", rgba=navy * [0.45, 0.45, 0.45, 1], specular=0.05)
    spec.add_material(name="negro", rgba=[0.02, 0.02, 0.025, 1], specular=0.3)
    spec.add_material(name="metal", rgba=[0.32, 0.33, 0.36, 1], specular=0.7, shininess=0.8)
    # LED con los colores de las marcas: Autoriente a la izquierda y en la pantalla, Ansrobex a la derecha.
    for name, rgb in (("led_azul", B.AUTORIENTE_BLUE), ("led_oro", B.AUTORIENTE_GOLD),
                      ("led_naranja", B.ANSROBEX_ORANGE), ("led_cian", B.ANSROBEX_CYAN)):
        spec.add_material(name=name, rgba=B.rgba01(rgb), emission=1.0, specular=0)
    w, h = ROTULO_TEX
    for i, logo in enumerate(FASCIA_LOGOS):
        tex = spec.add_texture(name=f"rotulo{i}", type=mujoco.mjtTexture.mjTEXTURE_2D, width=w, height=h,
                               nchannel=3)
        tex.data = B.sign_image(logo, w, h, SIGN_BG[logo], fill=0.7).tobytes()
        mat = spec.add_material(name=f"rotulo{i}", emission=0.9, specular=0)
        mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = f"rotulo{i}"
        bw, bh = BANNER_TEX
        tex = spec.add_texture(name=f"banner{i}", type=mujoco.mjtTexture.mjTEXTURE_2D, width=bw, height=bh,
                               nchannel=3)
        tex.data = B.sign_image(logo, bw, bh, SIGN_BG[logo], fill=0.6).tobytes()
        mat = spec.add_material(name=f"banner{i}", emission=0.9, specular=0)
        mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = f"banner{i}"
    w, h = SCREEN_TEX
    spec.add_texture(name="pantalla", type=mujoco.mjtTexture.mjTEXTURE_2D, width=w, height=h, nchannel=3,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_FLAT, rgb1=[0.04, 0.05, 0.08])
    mat = spec.add_material(name="pantalla", emission=1.0, specular=0.0, shininess=0.0, reflectance=0.0)
    mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = "pantalla"


def _deco(body, name: str, size, pos, material: str, gtype=mujoco.mjtGeom.mjGEOM_BOX, **kw) -> None:
    """Geometría solo visual (sin colisión ni masa)."""
    body.add_geom(name=name, type=gtype, size=size, pos=pos, material=material, contype=0, conaffinity=0,
                  density=0, group=1, **kw)


def _add_platform(spec: mujoco.MjSpec) -> None:
    wb = spec.worldbody
    x0, x1 = PLATFORM_X
    hw, h = PLATFORM_HALF_W, PLATFORM_H
    tarima = wb.add_body(name="tarima", pos=[(x0 + x1) / 2, 0, 0])
    depth = (x1 - x0) / 2
    tarima.add_geom(name="tarima", type=mujoco.mjtGeom.mjGEOM_BOX, size=[depth, hw, h / 2], pos=[0, 0, h / 2],
                    material="tarima", friction=[1.0, 0.005, 0.0001], condim=3)
    sd, sw, sh = STEP
    tarima.add_geom(name="escalon", type=mujoco.mjtGeom.mjGEOM_BOX, size=[sd / 2, sw / 2, sh / 2],
                    pos=[depth + sd / 2, 0, sh / 2], material="tarima", friction=[1.0, 0.005, 0.0001], condim=3)
    # Borde LED: frente y lados de la tarima, y canto del escalón.
    e = 0.006
    _deco(tarima, "led_frente", [e, hw, 0.012], [depth + e, 0, h - 0.016], "led_oro")
    for s in (-1, 1):
        _deco(tarima, f"led_lado{s:+d}", [depth, e, 0.012], [0, s * (hw + e), h - 0.016], "led_oro")
    _deco(tarima, "led_escalon", [e, sw / 2, 0.008], [depth + sd + e, 0, sh - 0.012], "led_oro")
    # Rótulos con los logos en el frontal, a los lados del escalón (FASCIA_LOGOS).
    _quad(spec, "rotulo", ROTULO_W, ROTULO_W * ROTULO_TEX[1] / ROTULO_TEX[0])
    for i, s in enumerate((-1, 1)):
        _deco(tarima, f"rotulo{i}", None, [depth + 0.002, s * (sw / 2 + 0.15 + ROTULO_W / 2), h * 0.45],
              f"rotulo{i}", gtype=mujoco.mjtGeom.mjGEOM_MESH, meshname="rotulo")


def _add_wall_and_screen(spec: mujoco.MjSpec) -> None:
    wb = spec.worldbody
    wall = wb.add_body(name="pared", pos=[WALL_X, 0, 0])
    _deco(wall, "pared", [0.05, 7.0, 3.4], [-0.05, 0, 3.4], "pared")
    sw, sh = SCREEN_SIZE
    zc = SCREEN_BOTTOM + sh / 2
    # Marco negro y halo LED alrededor de la pantalla.
    _deco(wall, "pantalla_marco", [0.03, sw / 2 + 0.08, sh / 2 + 0.08], [0.03, 0, zc], "negro")
    t = 0.025
    for name, size, pos in (("halo_arriba", [0.01, sw / 2 + 0.14, t], [0.02, 0, zc + sh / 2 + 0.14]),
                            ("halo_abajo", [0.01, sw / 2 + 0.14, t], [0.02, 0, zc - sh / 2 - 0.14]),
                            ("halo_izq", [0.01, t, sh / 2 + 0.14], [0.02, -sw / 2 - 0.14, zc]),
                            ("halo_der", [0.01, t, sh / 2 + 0.14], [0.02, sw / 2 + 0.14, zc])):
        _deco(wall, name, size, pos, "led_azul")
    _quad(spec, "pantalla", sw, sh)
    screen = wb.add_body(name="pantalla", pos=[SCREEN_X, 0, zc])
    _deco(screen, "pantalla", None, [0, 0, 0], "pantalla", gtype=mujoco.mjtGeom.mjGEOM_MESH, meshname="pantalla")
    # Columnas de luz de suelo a techo, una ancha y una fina por lado: Autoriente (azul y oro) a la izquierda,
    # Ansrobex (naranja y cian) a la derecha.
    for s, wide, thin in ((-1, "led_azul", "led_oro"), (1, "led_naranja", "led_cian")):
        _deco(wall, f"columna_ancha{s:+d}", [0.015, 0.07, 2.9], [0.02, s * COLUMN_Y[0], 2.9], wide)
        _deco(wall, f"columna_fina{s:+d}", [0.015, 0.025, 2.9], [0.02, s * COLUMN_Y[1], 2.9], thin)
        _deco(wall, f"panel{s:+d}", [0.04, 1.0, 2.9], [0.04, s * 6.5, 2.9], "negro")
    # Paneles con los logos entre la pantalla y las columnas (Autoriente a la izquierda, Ansrobex a la derecha).
    bw = BANNER_W
    _quad(spec, "banner", bw, bw * BANNER_TEX[1] / BANNER_TEX[0])
    y_banner = (sw / 2 + 0.14 + COLUMN_Y[0] - 0.07) / 2
    for i, s in enumerate((-1, 1)):
        _deco(wall, f"banner{i}", None, [0.012, s * y_banner, zc], f"banner{i}", gtype=mujoco.mjtGeom.mjGEOM_MESH,
              meshname="banner")


def _add_truss(spec: mujoco.MjSpec) -> None:
    """Truss delante de la tarima con los focos (las carcasas son decorado; la luz la dan las luces spot)."""
    wb = spec.worldbody
    x, z, half = 2.4, 4.7, 5.2
    truss = wb.add_body(name="truss", pos=[x, 0, 0])
    cyl = mujoco.mjtGeom.mjGEOM_CYLINDER
    for dz in (0.0, 0.3):
        for dx in (-0.15, 0.15):
            truss.add_geom(name=f"truss_{dz:.1f}_{dx:+.2f}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[0.025, 0, 0],
                           fromto=[dx, -half, z + dz, dx, half, z + dz], material="metal", contype=0,
                           conaffinity=0, density=0, group=1)
    for s in (-1, 1):
        truss.add_geom(name=f"poste{s:+d}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[0.05, 0, 0],
                       fromto=[0, s * half, 0, 0, s * half, z + 0.35], material="metal", contype=0, conaffinity=0,
                       density=0, group=1)
        _deco(truss, f"base_poste{s:+d}", [0.3, 0.3, 0.02], [0, s * half, 0.02], "negro")
    for i, y in enumerate((-2.6, -0.75, 0.75, 2.6)):
        _deco(truss, f"foco{i}", [0.11, 0.16, 0], [-0.05, y, z - 0.18], "negro", gtype=cyl,
              euler=[0, -0.6, 0])


def _add_lights(spec: mujoco.MjSpec, robot_ys) -> None:
    wb = spec.worldbody
    spot = mujoco.mjtLightType.mjLIGHT_SPOT
    target_z = PLATFORM_H + 0.9
    for i, y in enumerate(robot_ys):         # cenitales cálidos, con sombra
        pos = np.array([2.3, y * 1.2, 4.5])
        wb.add_light(name=f"cenital{i}", type=spot, pos=pos, dir=np.array([0.0, y, target_z]) - pos,
                     cutoff=24, exponent=6, diffuse=[1.0, 0.95, 0.88], specular=[0.3, 0.3, 0.3],
                     castshadow=True, attenuation=[1, 0, 0])
    for s in (-1, 1):                         # contraluces azules desde la pared
        pos = np.array([-2.2, s * 2.4, 4.3])
        wb.add_light(name=f"contraluz{s:+d}", type=spot, pos=pos, dir=np.array([0.2, -s * 1.2, target_z]) - pos,
                     cutoff=35, exponent=4, diffuse=[0.25, 0.42, 0.95], specular=[0.2, 0.3, 0.6],
                     castshadow=False)
    wb.add_light(name="relleno", type=mujoco.mjtLightType.mjLIGHT_DIRECTIONAL, pos=[6, 0, 5], dir=[-1, 0, -0.5],
                 diffuse=[0.22, 0.23, 0.26], specular=[0, 0, 0], castshadow=False)


def add_venue(spec: mujoco.MjSpec, robot_ys=(-0.75, 0.75)) -> None:
    vis = spec.visual
    vis.headlight.ambient = [0.10, 0.10, 0.12]
    vis.headlight.diffuse = [0.12, 0.12, 0.13]
    vis.headlight.specular = [0.0, 0.0, 0.0]
    vis.global_.offwidth = 2560
    vis.global_.offheight = 1440
    vis.quality.shadowsize = 4096
    _materials(spec)
    spec.worldbody.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.05], material="suelo",
                            condim=3, friction=[1.0, 0.005, 0.0001])
    _add_platform(spec)
    _add_wall_and_screen(spec)
    _add_truss(spec)
    _add_lights(spec, robot_ys)
    pos, xyaxes = audience_camera_pose()
    spec.worldbody.add_camera(name="publico", pos=pos, xyaxes=xyaxes)
