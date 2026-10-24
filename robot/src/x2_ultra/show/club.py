"""Decorado del reto 2: recepción del Club N al estilo de un hotel, un pasillo y el vestíbulo de ascensores.

Inspirado en los clubes clásicos de Bogotá (el Club El Nogal, en particular): paredes de nogal, piso de mármol
crema, latón, cuero verde y luz cálida. No es una réplica: el club no publica fotos de su interior. Todo es
procedural (``show.textures``) salvo los logos de los patrocinadores (``show.brand``).

Planta (x hacia la entrada, y hacia el pasillo)::

                         ┌──▌ASC 1▐────▌ASC 2▐──┐ y=20   vestíbulo de ascensores (directorio de pisos)
                         │                      │
                         └──────┐      ┌────────┘ y=16
                                │pasillo│  2,4 m de ancho: cuadros, apliques, alfombra
       y=6  ┌─────────────────────┘      └──────────────────────────────┐
            │N                                                              ║
            │C   ▓▓ mostrador ▓▓   X2  ←  el visitante entra de frente      ║ entrada
            │L   ▓▓ (de cara a ▓▓                                           ║ (puertas
            │U   ▓▓ la entrada)▓▓               sala de espera              ║  de vidrio)
            │B                                                              ║
       y=-6 └─────────┤PERSONAL├──────────────────────────────────────────┘
                   vestidores                      (zona de servicio: al norte, junto a la entrada,
                                                    tras la puerta ACCESO DE SERVICIO)
           x=0                                                             x=12

Las puertas (dos hojas por ascensor y las de vidrio de la entrada) son cuerpos con articulación de deslizamiento y
actuador de posición; ``ClubDoors`` las abre cuando un robot se acerca. Los techos van en el grupo 2 para poder
ocultarlos en la vista de planta.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from x2_ultra.show import brand as B
from x2_ultra.show import guest as G
from x2_ultra.show import spaces as S
from x2_ultra.show import textures as T

WALL_T = 0.2
LOBBY = (0.0, 12.0, -6.0, 6.0)          # x0, x1, y0, y1 del vestíbulo de entrada
LOBBY_H = 4.5
CORRIDOR = (1.4, 3.8, 6.0, 16.0)        # pasillo hacia los ascensores
CORRIDOR_H = 3.0
HALL = (-0.4, 5.6, 16.0, 20.0)          # vestíbulo de ascensores
HALL_H = 3.4
ELEVATOR_X = (1.0, 4.2)                 # centros de los dos ascensores (pared norte del vestíbulo)
ELEVATOR_W, ELEVATOR_H = 1.2, 2.4
ENTRANCE_W = 2.4                        # hueco de la entrada (pared este)
STAFF_ROOM = (1.0, 5.0, -10.0, -6.0)    # vestidores del personal, detrás de la pared sur
SERVICE_ROOM = (8.0, 12.0, 6.0, 10.0)   # zona de servicio (contratistas), detrás de la pared norte
SIDE_DOOR_W, SIDE_DOOR_H = 1.2, 2.3     # puertas de personal y de servicio
STAFF_DOOR_X = 2.6                      # en la pared sur del vestíbulo
SERVICE_DOOR_X = 10.8                   # en la pared norte del vestíbulo
BACK_H = 3.0                            # techo de las zonas interiores
DESK = {"center": (3.0, 0.0), "length": 4.0, "depth": 0.8, "height": 1.08}   # el frente mira a +x
SCREEN_SIZE = (2.8, 1.575)              # pantalla de bienvenida detrás de la recepción
SCREEN_TEX = (1280, 720)
DOOR_OPEN_RADIUS = 2.2                  # m: un robot más cerca que esto del hueco abre las puertas
ROBOT_START = (4.4, 0.8, 0.0)           # x, y, rumbo: delante del mostrador, mirando a la entrada
DOG_HOME = (4.0, -1.3, 0.0)             # el perro guía D1, tumbado delante del mostrador
GUEST_START = (15.0, 0.0, math.pi)      # el huésped empieza fuera, en el andén, mirando a la entrada

# Pisos del club según la biblioteca de espacios del HRI (``show.spaces``): los del directorio del vestíbulo de
# ascensores (de 6 a S2) y, de 2 a 6, los destinos de la recepción guiada de la ventana.
FLOORS = S.floor_names()

CEILING_GROUP = 2

# Vistas de la cámara libre: (lookat, distance, azimuth, elevation, techo visible).
CAMERAS = {
    "Entrada": ((3.6, 0.4, 1.3), 7.4, 180.0, -8.0, True),
    "Recepción": ((3.7, 0.6, 1.25), 4.2, 205.0, -7.0, True),
    "Pasillo": ((2.6, 11.0, 1.3), 8.6, 90.0, -5.0, True),
    "Ascensores": ((2.6, 19.2, 1.4), 3.6, 90.0, -8.0, True),
    "Puerta de personal": ((2.6, -5.8, 1.3), 4.0, 250.0, -8.0, True),
    "Acceso de servicio": ((10.6, 6.2, 1.3), 4.5, 70.0, -8.0, True),
    "Planta (sin techo)": ((5.5, 7.0, 0.0), 26.0, 180.0, -68.0, False),
}


# ----------------------------------------------------------------------------------------------
# Materiales
# ----------------------------------------------------------------------------------------------
def _texture(spec: mujoco.MjSpec, name: str, img: np.ndarray) -> None:
    h, w = img.shape[:2]
    tex = spec.add_texture(name=name, type=mujoco.mjtTexture.mjTEXTURE_2D, width=w, height=h, nchannel=3)
    tex.data = np.ascontiguousarray(img[..., :3]).tobytes()


def _material(spec: mujoco.MjSpec, name: str, texture: str | None = None, **kw) -> None:
    mat = spec.add_material(name=name, **kw)
    if texture:
        mat.textures[mujoco.mjtTextureRole.mjTEXROLE_RGB] = texture


def _materials(spec: mujoco.MjSpec) -> None:
    spec.add_texture(name="cielo", type=mujoco.mjtTexture.mjTEXTURE_SKYBOX,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_GRADIENT, rgb1=[0.55, 0.7, 0.88], rgb2=[0.92, 0.93, 0.95],
                     width=256, height=1536)
    _texture(spec, "marmol", T.marble(1024))
    _texture(spec, "nogal", T.walnut(1024, 512))
    _texture(spec, "alfombra", T.rug(512, 768))
    _texture(spec, "alfombra_azul", T.rug(512, 768, field=(24, 40, 78), border=(196, 160, 92), inner=(14, 20, 40),
                                          seed=5))
    _texture(spec, "corredor", T.rug(1536, 256, field=(24, 40, 78), border=(196, 160, 92), inner=(14, 20, 40),
                                     seed=6))
    for i in range(4):
        _texture(spec, f"cuadro{i}", T.painting(512, 384, seed=4 + 5 * i))
    _texture(spec, "rotulo_club", T.text_sign("CLUB N", 1024, 192, tracking=0.25))
    ident = S.identity()
    _texture(spec, "valores", T.plaque("NUESTROS VALORES", ident["lema"], [v["nombre"] for v in ident["valores"]],
                                       640, 480))
    _texture(spec, "indicador", T.text_sign("1", 128, 128, fg=(255, 176, 60), bg=(10, 8, 6), serif=False))
    _texture(spec, "directorio", T.directory("PISOS", S.directory_rows(), 512, 640))
    _texture(spec, "rotulo_personal", T.text_sign("SOLO PERSONAL", 768, 128, tracking=0.12))
    _texture(spec, "rotulo_servicio", T.text_sign("ACCESO DE SERVICIO", 1024, 128, fg=(20, 20, 20),
                                                  bg=(242, 192, 20), serif=False, tracking=0.08))
    _texture(spec, "rotulo_vestidores", T.text_sign("VESTIDORES", 768, 128, fg=(240, 240, 240), bg=(52, 62, 76),
                                                    serif=False, tracking=0.12))
    _texture(spec, "rotulo_registro", T.text_sign("REGISTRO DE CONTRATISTAS", 1024, 128, fg=(240, 240, 240),
                                                  bg=(40, 40, 40), serif=False, tracking=0.05))
    for logo in ("unisabana", "ansrobex"):
        _texture(spec, f"logo_{logo}", B.sign_image(logo, 640, 400, fill=0.55))
    w, h = SCREEN_TEX
    spec.add_texture(name="pantalla", type=mujoco.mjtTexture.mjTEXTURE_2D, width=w, height=h, nchannel=3,
                     builtin=mujoco.mjtBuiltin.mjBUILTIN_FLAT, rgb1=[0.05, 0.04, 0.03])

    # Baldosas de 0,8 m (texuniform: repeticiones por metro).
    _material(spec, "marmol", "marmol", texrepeat=[1.25, 1.25], texuniform=True, reflectance=0.12, specular=0.6,
              shininess=0.8)
    _material(spec, "nogal", "nogal", texrepeat=[0.8, 0.5], texuniform=True, specular=0.35, shininess=0.5)
    _material(spec, "nogal_oscuro", "nogal", rgba=[0.62, 0.6, 0.6, 1], texrepeat=[0.8, 0.5], texuniform=True,
              specular=0.4, shininess=0.6)
    _material(spec, "yeso", rgba=[0.93, 0.9, 0.84, 1], specular=0.05)
    _material(spec, "laton", rgba=[0.80, 0.63, 0.33, 1], specular=0.9, shininess=0.9, reflectance=0.15)
    _material(spec, "acero", rgba=[0.64, 0.65, 0.67, 1], specular=0.8, shininess=0.7, reflectance=0.1)
    _material(spec, "vidrio", rgba=[0.75, 0.88, 0.92, 0.16], specular=1.0, shininess=1.0)
    _material(spec, "bronce", rgba=[0.24, 0.19, 0.14, 1], specular=0.6, shininess=0.6)
    _material(spec, "cuero", rgba=[0.13, 0.24, 0.18, 1], specular=0.35, shininess=0.5)
    _material(spec, "cojin", rgba=[0.80, 0.70, 0.52, 1], specular=0.1)
    _material(spec, "hoja", rgba=[0.17, 0.38, 0.16, 1], specular=0.2)
    _material(spec, "maceta", rgba=[0.12, 0.11, 0.10, 1], specular=0.5, shininess=0.6)
    _material(spec, "piedra", rgba=[0.55, 0.55, 0.53, 1], specular=0.1)
    _material(spec, "luz_calida", rgba=[1.0, 0.88, 0.66, 1], emission=1.0, specular=0)
    _material(spec, "luz_tenue", rgba=[1.0, 0.82, 0.58, 1], emission=0.6, specular=0)
    for name in ("alfombra", "alfombra_azul", "corredor"):
        _material(spec, name, name, specular=0.0)
    for name in ("cuadro0", "cuadro1", "cuadro2", "cuadro3", "rotulo_club", "directorio", "valores"):
        _material(spec, name, name, specular=0.2)
    for name in ("logo_unisabana", "logo_ansrobex"):
        _material(spec, name, name, emission=0.25, specular=0.2)
    _material(spec, "indicador", "indicador", emission=1.0, specular=0)
    _material(spec, "pantalla", "pantalla", emission=1.0, specular=0.0)
    _material(spec, "pintura", rgba=[0.86, 0.86, 0.84, 1], specular=0.05)
    _material(spec, "vinilo", rgba=[0.62, 0.66, 0.70, 1], specular=0.3, reflectance=0.05)
    _material(spec, "concreto", rgba=[0.55, 0.55, 0.54, 1], specular=0.1)
    _material(spec, "amarillo", rgba=[0.95, 0.75, 0.05, 1], specular=0.3)
    _material(spec, "negro_mate", rgba=[0.08, 0.08, 0.08, 1], specular=0.1)
    _material(spec, "taquilla", rgba=[0.35, 0.42, 0.50, 1], specular=0.6, shininess=0.6)
    _material(spec, "carton", rgba=[0.66, 0.50, 0.32, 1], specular=0.05)
    _material(spec, "corcho", rgba=[0.72, 0.56, 0.38, 1], specular=0.05)
    for name in ("rotulo_personal", "rotulo_servicio", "rotulo_vestidores", "rotulo_registro"):
        _material(spec, name, name, emission=0.4, specular=0.1)


# ----------------------------------------------------------------------------------------------
# Piezas
# ----------------------------------------------------------------------------------------------
def _box(body, name: str, center, half, material: str, collide: bool = True, group: int = 1, **kw) -> None:
    body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_BOX, pos=center, size=half, material=material,
                  contype=1 if collide else 0, conaffinity=1 if collide else 0, density=0, group=group, **kw)


def _cyl(body, name: str, center, radius: float, half_h: float, material: str, collide: bool = True,
         group: int = 1) -> None:
    body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_CYLINDER, pos=center, size=[radius, half_h, 0],
                  material=material, contype=1 if collide else 0, conaffinity=1 if collide else 0, density=0,
                  group=group)


def _quad(spec: mujoco.MjSpec, name: str, width: float, height: float) -> str:
    """Rectángulo con coordenadas de textura, de cara a +x (como la pantalla del escenario)."""
    w, h = width / 2, height / 2
    spec.add_mesh(name=name, uservert=[0, -w, -h, 0, w, -h, 0, w, h, 0, -w, h], userface=[0, 1, 2, 0, 2, 3],
                  usertexcoord=[0, 1, 1, 1, 1, 0, 0, 0], inertia=mujoco.mjtMeshInertia.mjMESH_INERTIA_SHELL)
    return name


def _picture(body, name: str, mesh: str, pos, yaw: float, material: str) -> None:
    """Rectángulo texturizado (cuadro, rótulo, logo, pantalla) girado ``yaw`` alrededor de z (0 = mira a +x)."""
    body.add_geom(name=name, type=mujoco.mjtGeom.mjGEOM_MESH, meshname=mesh, pos=pos,
                  euler=[0, 0, yaw], material=material, contype=0, conaffinity=0, density=0, group=1)


def _wall(body, name: str, start, end, height: float, inside: int, openings=(), cladding: str | None = "wainscot",
          material: str = "yeso") -> None:
    """Pared recta de yeso entre dos puntos de un mismo eje, con huecos ``(centro, ancho, alto)`` a lo largo de ella.

    ``inside`` (+1/−1) es el lado de la habitación según la normal (+y para paredes a lo largo de x, +x para las de
    y); por ese lado se reviste de nogal: ``"full"`` hasta el techo, ``"wainscot"`` un zócalo de 1,1 m con filete
    de latón, ``None`` nada."""
    (x0, y0), (x1, y1) = start, end
    along_x = abs(y1 - y0) < 1e-9
    a0, a1 = (min(x0, x1), max(x0, x1)) if along_x else (min(y0, y1), max(y0, y1))
    c = y0 if along_x else x0
    t = WALL_T

    def piece(tag, lo, hi, z0, z1, half_thick, offset, material, collide=True):
        mid, half = (lo + hi) / 2, (hi - lo) / 2
        pos = [mid, c + offset, (z0 + z1) / 2] if along_x else [c + offset, mid, (z0 + z1) / 2]
        size = [half, half_thick, (z1 - z0) / 2] if along_x else [half_thick, half, (z1 - z0) / 2]
        _box(body, f"{name}_{tag}", pos, size, material, collide=collide)

    cuts = sorted(openings)
    spans, lo = [], a0
    for center, width, _ in cuts:
        spans.append((lo, center - width / 2))
        lo = center + width / 2
    spans.append((lo, a1))
    clad_top = {"full": height, "wainscot": 1.1, None: 0.0}[cladding]
    for i, (s0, s1) in enumerate(spans):
        if s1 - s0 < 1e-6:
            continue
        piece(f"tramo{i}", s0, s1, 0, height, t / 2, 0, material)
        if clad_top:
            piece(f"nogal{i}", s0, s1, 0, clad_top, 0.012, inside * (t / 2 + 0.012), "nogal", collide=False)
            piece(f"filete{i}", s0, s1, clad_top - 0.02, clad_top + 0.02, 0.02, inside * (t / 2 + 0.02), "laton",
                  collide=False)
    for k, (center, width, h) in enumerate(cuts):
        if h < height:
            piece(f"dintel{k}", center - width / 2, center + width / 2, h, height, t / 2, 0, material)
            if clad_top > h:
                piece(f"dintel_nogal{k}", center - width / 2, center + width / 2, h, clad_top, 0.012,
                      inside * (t / 2 + 0.012), "nogal", collide=False)


def _ceiling(spec: mujoco.MjSpec, name: str, rect, height: float, beams: bool = False) -> None:
    x0, x1, y0, y1 = rect
    g = CEILING_GROUP
    ceil = spec.worldbody.add_body(name=f"techo_{name}")
    cx, cy, hx, hy = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    _box(ceil, f"techo_{name}", [cx, cy, height + 0.05], [hx + WALL_T, hy + WALL_T, 0.05], "yeso", collide=False,
         group=g)
    if beams:
        for i, x in enumerate(np.arange(x0 + 2.0, x1, 4.0)):
            _box(ceil, f"viga_{name}_x{i}", [x, cy, height - 0.08], [0.09, hy, 0.08], "nogal_oscuro", collide=False,
                 group=g)
        for i, y in enumerate(np.arange(y0 + 2.0, y1, 4.0)):
            _box(ceil, f"viga_{name}_y{i}", [cx, y, height - 0.08], [hx, 0.09, 0.08], "nogal_oscuro", collide=False,
                 group=g)
    k = 0
    step = 2.0 if beams else 1.6
    for x in np.arange(x0 + step / 2, x1, step):
        for y in np.arange(y0 + step / 2, y1, step):
            _cyl(ceil, f"foco_{name}{k}", [x, y, height - 0.005], 0.07, 0.005, "luz_calida", collide=False, group=g)
            k += 1


def _structure(spec: mujoco.MjSpec) -> None:
    wb = spec.worldbody
    walls = wb.add_body(name="paredes")
    lx0, lx1, ly0, ly1 = LOBBY
    cx0, cx1, cy0, cy1 = CORRIDOR
    hx0, hx1, hy0, hy1 = HALL
    corridor_cut = ((cx0 + cx1) / 2, cx1 - cx0, CORRIDOR_H)
    # Vestíbulo de entrada. La pared oeste, detrás de la recepción, toda de nogal.
    _wall(walls, "lobby_oeste", (lx0, ly0), (lx0, ly1), LOBBY_H, +1, cladding="full")
    _wall(walls, "lobby_sur", (lx0, ly0), (lx1, ly0), LOBBY_H, +1,
          openings=[(STAFF_DOOR_X, SIDE_DOOR_W, SIDE_DOOR_H)])
    _wall(walls, "lobby_norte", (lx0, ly1), (lx1, ly1), LOBBY_H, -1,
          openings=[corridor_cut, (SERVICE_DOOR_X, SIDE_DOOR_W, SIDE_DOOR_H)])
    # Pasillo y vestíbulo de ascensores.
    _wall(walls, "pasillo_oeste", (cx0, cy0), (cx0, cy1), CORRIDOR_H, +1)
    _wall(walls, "pasillo_este", (cx1, cy0), (cx1, cy1), CORRIDOR_H, -1)
    _wall(walls, "hall_sur", (hx0, hy0), (hx1, hy0), HALL_H, +1, openings=[corridor_cut])
    _wall(walls, "hall_oeste", (hx0, hy0), (hx0, hy1), HALL_H, +1)
    _wall(walls, "hall_este", (hx1, hy0), (hx1, hy1), HALL_H, -1)
    _wall(walls, "hall_norte", (hx0, hy1), (hx1, hy1), HALL_H, -1, cladding="full",
          openings=[(x, ELEVATOR_W, ELEVATOR_H) for x in ELEVATOR_X])
    for k, x in enumerate(ELEVATOR_X):                        # marco de latón alrededor de cada hueco
        f, y = 0.05, hy1 - WALL_T / 2 - 0.035
        _box(walls, f"marco{k}_arriba", [x, y, ELEVATOR_H + f], [ELEVATOR_W / 2 + 2 * f, 0.015, f], "laton",
             collide=False)
        for s in (-1, 1):
            _box(walls, f"marco{k}_{s:+d}", [x + s * (ELEVATOR_W / 2 + f), y, ELEVATOR_H / 2],
                 [f, 0.015, ELEVATOR_H / 2], "laton", collide=False)
    # Este: fachada de vidrio con montantes de bronce y el hueco de la entrada.
    x = lx1 + WALL_T / 2
    for s in (-1, 1):
        a, b = s * ENTRANCE_W / 2, s * (ly1 + WALL_T)
        lo, hi = min(a, b), max(a, b)
        _box(walls, f"vidrio{s:+d}", [x, (lo + hi) / 2, LOBBY_H / 2], [0.012, (hi - lo) / 2, LOBBY_H / 2], "vidrio")
        n = int(round((hi - lo) / 1.5))
        for i in range(n + 1):
            _box(walls, f"montante{s:+d}_{i}", [x, lo + i * (hi - lo) / n, LOBBY_H / 2], [0.04, 0.03, LOBBY_H / 2],
                 "bronce", collide=False)
    _box(walls, "dintel_entrada", [x, 0, (2.6 + LOBBY_H) / 2], [0.05, ENTRANCE_W / 2, (LOBBY_H - 2.6) / 2],
         "bronce")
    # Exterior: andén de piedra y un seto, para que no se vea vacío a través del vidrio.
    out = wb.add_body(name="exterior")
    _box(out, "anden", [lx1 + 4.0, 0, -0.05 + 0.002], [4.0, ly1 + 3, 0.05], "piedra")
    _box(out, "jardinera", [lx1 + 6.5, 0, 0.35], [0.4, ly1 + 2, 0.35], "piedra", collide=False)
    for i, y in enumerate(np.linspace(ly0, ly1, 9)):
        out.add_geom(name=f"seto{i}", type=mujoco.mjtGeom.mjGEOM_ELLIPSOID, pos=[lx1 + 6.5, y, 0.95],
                     size=[0.45, 0.8, 0.45], material="hoja", contype=0, conaffinity=0, density=0, group=1)
    _ceiling(spec, "lobby", LOBBY, LOBBY_H, beams=True)
    _ceiling(spec, "pasillo", CORRIDOR, CORRIDOR_H)
    _ceiling(spec, "hall", HALL, HALL_H)
    # Vestidores (sur) y zona de servicio (norte): paredes pintadas, sin nogal.
    sx0, sx1, sy0, sy1 = STAFF_ROOM
    _wall(walls, "personal_oeste", (sx0, sy0), (sx0, sy1), BACK_H, +1, cladding=None, material="pintura")
    _wall(walls, "personal_este", (sx1, sy0), (sx1, sy1), BACK_H, -1, cladding=None, material="pintura")
    _wall(walls, "personal_sur", (sx0, sy0), (sx1, sy0), BACK_H, +1, cladding=None, material="pintura")
    _ceiling(spec, "personal", STAFF_ROOM, BACK_H)
    vx0, vx1, vy0, vy1 = SERVICE_ROOM
    _wall(walls, "servicio_oeste", (vx0, vy0), (vx0, vy1), BACK_H, +1, cladding=None, material="pintura")
    _wall(walls, "servicio_este", (vx1, vy0), (vx1, vy1), BACK_H, -1, cladding=None, material="pintura")
    _wall(walls, "servicio_norte", (vx0, vy1), (vx1, vy1), BACK_H, -1, cladding=None, material="pintura")
    _ceiling(spec, "servicio", SERVICE_ROOM, BACK_H)


def _chandelier(spec: mujoco.MjSpec, name: str, x: float, y: float, z: float, ceiling: float,
                radius: float = 0.55) -> None:
    """Lámpara de araña: aro de latón con brazos y luces, colgada del techo."""
    body = spec.worldbody.add_body(name=name, pos=[x, y, z])
    body.add_geom(name=f"{name}_cadena", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[0.012, 0, 0],
                  fromto=[0, 0, 0, 0, 0, ceiling - z], material="laton", contype=0, conaffinity=0, density=0,
                  group=1)
    n = 16
    for i in range(n):
        a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
        body.add_geom(name=f"{name}_aro{i}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[0.018, 0, 0],
                      fromto=[radius * math.cos(a0), radius * math.sin(a0), 0,
                              radius * math.cos(a1), radius * math.sin(a1), 0],
                      material="laton", contype=0, conaffinity=0, density=0, group=1)
    for i in range(8):
        a = 2 * math.pi * i / 8
        p = [radius * math.cos(a), radius * math.sin(a), 0]
        body.add_geom(name=f"{name}_brazo{i}", type=mujoco.mjtGeom.mjGEOM_CAPSULE, size=[0.01, 0, 0],
                      fromto=[0, 0, 0.25] + p, material="laton", contype=0, conaffinity=0, density=0, group=1)
        body.add_geom(name=f"{name}_vela{i}", type=mujoco.mjtGeom.mjGEOM_ELLIPSOID, size=[0.045, 0.045, 0.07],
                      pos=[p[0], p[1], 0.09], material="luz_calida", contype=0, conaffinity=0, density=0, group=1)


def _plant(spec: mujoco.MjSpec, name: str, x: float, y: float, scale: float = 1.0) -> None:
    pot = spec.worldbody.add_body(name=name, pos=[x, y, 0])
    _cyl(pot, f"{name}_maceta", [0, 0, 0.3 * scale], 0.26 * scale, 0.3 * scale, "maceta")
    for k, (dz, r) in enumerate(((0.9, 0.42), (1.35, 0.34), (1.7, 0.22))):
        pot.add_geom(name=f"{name}_hojas{k}", type=mujoco.mjtGeom.mjGEOM_ELLIPSOID,
                     size=[r * scale, r * scale, r * 0.9 * scale], pos=[0, 0, dz * scale], material="hoja",
                     contype=0, conaffinity=0, density=0, group=1)


def _framed_picture(spec: mujoco.MjSpec, name: str, pos, yaw: float, material: str, size=(1.6, 1.2)) -> None:
    """Cuadro con marco de latón colgado en una pared; ``yaw`` es hacia dónde mira."""
    wb = spec.worldbody
    nx, ny = math.cos(yaw), math.sin(yaw)
    w, h = size
    frame = wb.add_body(name=f"{name}_marco", pos=[pos[0] - 0.01 * nx, pos[1] - 0.01 * ny, pos[2]],
                        euler=[0, 0, yaw])
    _box(frame, f"{name}_marco", [0, 0, 0], [0.02, w / 2 + 0.08, h / 2 + 0.08], "laton", collide=False)
    _picture(wb, name, f"cuadro_{w:.1f}x{h:.1f}", [pos[0] + 0.015 * nx, pos[1] + 0.015 * ny, pos[2]], yaw, material)


def _sconce(spec: mujoco.MjSpec, name: str, pos, yaw: float) -> None:
    nx, ny = math.cos(yaw), math.sin(yaw)
    body = spec.worldbody.add_body(name=name, pos=[pos[0] + 0.05 * nx, pos[1] + 0.05 * ny, pos[2]],
                                   euler=[0, 0, yaw])
    _box(body, f"{name}_placa", [-0.03, 0, 0], [0.01, 0.06, 0.12], "laton", collide=False)
    _box(body, f"{name}_luz", [0.02, 0, 0.02], [0.05, 0.07, 0.11], "luz_tenue", collide=False)


def _reception(spec: mujoco.MjSpec) -> None:
    """Mostrador de cara a la entrada; detrás, en la pared oeste, el letrero, la pantalla y los logos."""
    wb = spec.worldbody
    cx, cy = DESK["center"]
    L, D, Hd = DESK["length"], DESK["depth"], DESK["height"]
    desk = wb.add_body(name="recepcion", pos=[cx, cy, 0])
    _box(desk, "mostrador", [0, 0, Hd / 2], [D / 2, L / 2, Hd / 2], "nogal")
    _box(desk, "mostrador_tapa", [0.04, 0, Hd + 0.025], [D / 2 + 0.06, L / 2 + 0.06, 0.025], "marmol")
    _box(desk, "mostrador_zocalo_luz", [D / 2 + 0.004, 0, 0.07], [0.004, L / 2 - 0.05, 0.012], "luz_calida",
         collide=False)
    _box(desk, "mostrador_filete", [D / 2 + 0.006, 0, Hd - 0.12], [0.006, L / 2, 0.012], "laton", collide=False)
    _box(desk, "mostrador_trasero", [-D / 2 - 0.3, 0, 0.4], [0.3, L / 2, 0.4], "nogal_oscuro")
    _cyl(desk, "lampara_pie", [0.1, -L / 2 + 0.35, Hd + 0.2], 0.015, 0.2, "laton", collide=False)
    desk.add_geom(name="lampara_pantalla", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.11, 0.08, 0],
                  pos=[0.1, -L / 2 + 0.35, Hd + 0.45], material="luz_tenue", contype=0, conaffinity=0, density=0,
                  group=1)
    _box(desk, "campana", [0.15, L / 2 - 0.4, Hd + 0.07], [0.05, 0.05, 0.02], "laton", collide=False)
    # Pared de fondo (oeste, mira a +x).
    xw = LOBBY[0] + WALL_T / 2 + 0.03
    _quad(spec, "rotulo_club", 3.2, 0.6)
    _picture(wb, "rotulo_club", "rotulo_club", [xw, cy, 3.75], 0.0, "rotulo_club")
    sw, sh = SCREEN_SIZE
    _box(wb.add_body(name="marco_pantalla"), "marco_pantalla", [xw - 0.01, cy, 2.35],
         [0.02, sw / 2 + 0.06, sh / 2 + 0.06], "bronce", collide=False)
    _quad(spec, "pantalla", sw, sh)
    _picture(wb, "pantalla", "pantalla", [xw + 0.015, cy, 2.35], 0.0, "pantalla")
    _quad(spec, "logo", 0.9, 0.5625)
    for logo, dy in (("unisabana", -2.45), ("ansrobex", 2.45)):
        _picture(wb, f"logo_{logo}", "logo", [xw, cy + dy, 2.35], 0.0, f"logo_{logo}")
    for s in (-1, 1):
        _plant(spec, f"planta_recepcion{s:+d}", cx - 0.2, cy + s * (L / 2 + 0.6))


def _lounge(spec: mujoco.MjSpec) -> None:
    """Sala de espera junto a la pared sur, a un lado del camino entrada → recepción."""
    wb = spec.worldbody
    lounge = wb.add_body(name="sala", pos=[8.0, -3.6, 0])
    _box(lounge, "alfombra_sala", [0, 0, 0.003], [2.1, 1.4, 0.003], "alfombra", collide=False)
    sofa = lounge.add_body(name="sofa", pos=[0, -1.75, 0])
    _box(sofa, "sofa_base", [0, 0, 0.22], [1.2, 0.45, 0.22], "cuero")
    _box(sofa, "sofa_respaldo", [0, -0.36, 0.62], [1.2, 0.09, 0.2], "cuero")
    for s in (-1, 1):
        _box(sofa, f"sofa_brazo{s:+d}", [s * 1.12, 0, 0.5], [0.09, 0.45, 0.12], "cuero")
    for i, x in enumerate((-0.68, 0.0, 0.68)):
        _box(sofa, f"sofa_cojin{i}", [x, 0.03, 0.5], [0.32, 0.36, 0.06], "cuero")
        _box(sofa, f"sofa_almohada{i}", [x, -0.22, 0.7], [0.2, 0.06, 0.16], "cojin", collide=False)
    for s in (-1, 1):
        chair = lounge.add_body(name=f"butaca{s:+d}", pos=[s * 1.7, 0.2, 0], euler=[0, 0, -s * math.pi / 2])
        _box(chair, f"butaca{s:+d}_base", [0, 0, 0.22], [0.42, 0.42, 0.22], "cuero")
        _box(chair, f"butaca{s:+d}_respaldo", [0, -0.34, 0.62], [0.42, 0.08, 0.22], "cuero")
        for k in (-1, 1):
            _box(chair, f"butaca{s:+d}_brazo{k:+d}", [k * 0.36, 0, 0.5], [0.06, 0.42, 0.1], "cuero")
        _box(chair, f"butaca{s:+d}_cojin", [0, 0.04, 0.48], [0.3, 0.32, 0.05], "cuero")
    _box(lounge, "mesa_tapa", [0, -0.25, 0.42], [0.65, 0.35, 0.025], "nogal")
    for i, (x, y) in enumerate(((-0.55, -0.5), (0.55, -0.5), (-0.55, 0.0), (0.55, 0.0))):
        _cyl(lounge, f"mesa_pata{i}", [x, y, 0.2], 0.02, 0.2, "laton")
    _cyl(lounge, "jarron", [0.25, -0.25, 0.55], 0.06, 0.1, "acero", collide=False)
    lounge.add_geom(name="flores", type=mujoco.mjtGeom.mjGEOM_ELLIPSOID, size=[0.14, 0.14, 0.1],
                    pos=[0.25, -0.25, 0.72], material="hoja", contype=0, conaffinity=0, density=0, group=1)
    _cyl(lounge, "lampara_pie_sala", [1.55, -1.8, 0.75], 0.015, 0.75, "laton", collide=False)
    lounge.add_geom(name="lampara_sala", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.2, 0.14, 0],
                    pos=[1.55, -1.8, 1.6], material="luz_tenue", contype=0, conaffinity=0, density=0, group=1)


def _decor(spec: mujoco.MjSpec) -> None:
    wb = spec.worldbody
    lx0, lx1, ly0, ly1 = LOBBY
    cx0, cx1, cy0, cy1 = CORRIDOR
    hx0, hx1, hy0, hy1 = HALL
    # Vestíbulo de entrada: columnas a los lados del camino, plantas, cuadros con apliques y una lámpara de araña.
    for i, (x, y) in enumerate(((7.0, 3.2), (7.0, -2.4), (10.4, 3.2), (10.4, -2.4))):
        col = wb.add_body(name=f"columna{i}", pos=[x, y, 0])
        _cyl(col, f"columna{i}_fuste", [0, 0, LOBBY_H / 2], 0.28, LOBBY_H / 2, "marmol")
        for k, z in enumerate((0.06, LOBBY_H - 0.3)):
            _cyl(col, f"columna{i}_anillo{k}", [0, 0, z], 0.33, 0.06, "laton", collide=False)
    for i, (x, y) in enumerate(((7.6, 5.3), (11.3, -5.3), (5.6, 5.3), (0.8, -5.3))):
        _plant(spec, f"planta{i}", x, y)
    _quad(spec, "cuadro_1.6x1.2", 1.6, 1.2)
    _quad(spec, "cuadro_1.2x0.9", 1.2, 0.9)
    _framed_picture(spec, "cuadro_sur", (4.7, ly0 + WALL_T / 2 + 0.03, 2.3), math.pi / 2, "cuadro0")
    _framed_picture(spec, "placa_valores", (8.4, ly1 - WALL_T / 2 - 0.03, 2.3), -math.pi / 2, "valores")
    for x in (1.6, 6.0):
        _sconce(spec, f"aplique_sur{x:.0f}", (x, ly0 + WALL_T / 2, 2.6), math.pi / 2)
    for x in (6.9, 9.55):
        _sconce(spec, f"aplique_norte{x:.0f}", (x, ly1 - WALL_T / 2, 2.6), -math.pi / 2)
    _chandelier(spec, "arana_lobby", 7.0, 0.4, 3.5, LOBBY_H, radius=0.8)
    # Pasillo: alfombra larga, cuadros alternados y apliques.
    _box(wb.add_body(name="alfombra_pasillo"), "alfombra_pasillo", [(cx0 + cx1) / 2, (cy0 + cy1) / 2, 0.003],
         [0.75, (cy1 - cy0) / 2 - 0.2, 0.003], "corredor", collide=False)
    for i, y in enumerate((8.4, 11.0, 13.6)):
        side = 1 if i % 2 == 0 else -1          # +1: pared oeste (mira a +x); −1: pared este (mira a −x)
        x = cx0 + WALL_T / 2 + 0.03 if side > 0 else cx1 - WALL_T / 2 - 0.03
        _framed_picture(spec, f"cuadro_pasillo{i}", (x, y, 1.75), 0.0 if side > 0 else math.pi, f"cuadro{2 + i % 2}",
                        size=(1.2, 0.9))
    for y in (7.2, 9.7, 12.3, 14.9):
        _sconce(spec, f"aplique_po{y:.0f}", (cx0 + WALL_T / 2, y, 2.1), 0.0)
        _sconce(spec, f"aplique_pe{y:.0f}", (cx1 - WALL_T / 2, y, 2.1), math.pi)
    # Vestíbulo de ascensores: alfombra, plantas, banco, botonera, indicadores y el directorio de pisos.
    _box(wb.add_body(name="alfombra_hall"), "alfombra_hall", [(hx0 + hx1) / 2, 18.3, 0.003], [2.2, 1.2, 0.003],
         "alfombra_azul", collide=False)
    for i, (x, y) in enumerate(((hx0 + 0.45, hy1 - 0.45), (hx1 - 0.45, hy1 - 0.45))):
        _plant(spec, f"planta_hall{i}", x, y, scale=0.85)
    bench = wb.add_body(name="banco", pos=[hx0 + WALL_T / 2 + 0.3, 17.6, 0])
    _box(bench, "banco_asiento", [0, 0, 0.42], [0.25, 0.7, 0.05], "cuero")
    _box(bench, "banco_base", [0, 0, 0.19], [0.22, 0.65, 0.19], "nogal")
    _quad(spec, "directorio", 0.8, 1.0)
    _picture(wb, "directorio", "directorio", [hx1 - WALL_T / 2 - 0.03, 17.9, 1.6], math.pi, "directorio")
    _box(wb.add_body(name="marco_directorio"), "marco_directorio", [hx1 - WALL_T / 2 - 0.015, 17.9, 1.6],
         [0.012, 0.44, 0.54], "laton", collide=False)
    yw = hy1 - WALL_T / 2 - 0.02
    panel = wb.add_body(name="botonera", pos=[(ELEVATOR_X[0] + ELEVATOR_X[1]) / 2, yw, 1.15])
    _box(panel, "botonera_placa", [0, 0, 0], [0.09, 0.012, 0.16], "laton", collide=False)
    for k, z in enumerate((0.05, -0.05)):
        panel.add_geom(name=f"boton{k}", type=mujoco.mjtGeom.mjGEOM_CYLINDER, size=[0.025, 0.006, 0],
                       pos=[0, -0.012, z], euler=[math.pi / 2, 0, 0], material="luz_calida", contype=0,
                       conaffinity=0, density=0, group=1)
    _quad(spec, "indicador", 0.22, 0.22)
    for k, x in enumerate(ELEVATOR_X):
        _box(wb.add_body(name=f"indicador_caja{k}"), f"indicador_caja{k}", [x, yw, ELEVATOR_H + 0.42],
             [0.16, 0.012, 0.14], "bronce", collide=False)
        _picture(wb, f"indicador{k}", "indicador", [x, yw - 0.015, ELEVATOR_H + 0.42], -math.pi / 2, "indicador")
    _chandelier(spec, "arana_hall", (hx0 + hx1) / 2, 18.2, 2.75, HALL_H, radius=0.45)


def _elevators(spec: mujoco.MjSpec) -> None:
    """Cabinas de acero detrás de los huecos y puertas de dos hojas; y las puertas de vidrio de la entrada."""
    wb = spec.worldbody
    y_in = HALL[3] + WALL_T / 2
    depth = 1.6
    for k, x in enumerate(ELEVATOR_X):
        cab = wb.add_body(name=f"cabina{k}", pos=[x, y_in + depth / 2, 0])
        hw = ELEVATOR_W / 2 + 0.25
        _box(cab, f"cabina{k}_fondo", [0, depth / 2 + 0.03, ELEVATOR_H / 2 + 0.1], [hw, 0.03, ELEVATOR_H / 2 + 0.1],
             "acero")
        for s in (-1, 1):
            _box(cab, f"cabina{k}_lado{s:+d}", [s * (hw + 0.03), 0, ELEVATOR_H / 2 + 0.1],
                 [0.03, depth / 2, ELEVATOR_H / 2 + 0.1], "acero")
        _box(cab, f"cabina{k}_techo", [0, 0, ELEVATOR_H + 0.22], [hw, depth / 2, 0.02], "acero", collide=False)
        _box(cab, f"cabina{k}_luz", [0, 0, ELEVATOR_H + 0.195], [hw - 0.2, depth / 2 - 0.2, 0.005], "luz_calida",
             collide=False)
        _box(cab, f"cabina{k}_espejo", [0, depth / 2 - 0.001, 1.5], [hw - 0.15, 0.002, 0.6], "vidrio", collide=False)
        _box(cab, f"cabina{k}_pasamanos", [0, depth / 2 - 0.06, 0.95], [hw - 0.1, 0.02, 0.02], "laton",
             collide=False)
        _door_pair(spec, f"ascensor{k}", (x, HALL[3] - WALL_T / 2 - 0.03), "x", ELEVATOR_W, ELEVATOR_H, "acero")
    _door_pair(spec, "entrada", (LOBBY[1] + WALL_T / 2 - 0.05, 0.0), "y", ENTRANCE_W, 2.6, "vidrio", frame="bronce")
    _door_pair(spec, "personal", (STAFF_DOOR_X, LOBBY[2] + WALL_T / 2 + 0.03), "x", SIDE_DOOR_W, SIDE_DOOR_H,
               "nogal_oscuro", frame="laton")
    _door_pair(spec, "servicio", (SERVICE_DOOR_X, LOBBY[3] - WALL_T / 2 - 0.03), "x", SIDE_DOOR_W, SIDE_DOOR_H,
               "acero", frame="amarillo")


def _door_pair(spec: mujoco.MjSpec, name: str, at, axis: str, width: float, height: float, material: str,
               frame: str | None = None) -> None:
    """Dos hojas que se deslizan a lo largo de la pared (``axis`` "x" o "y") hacia fuera del hueco. Chocan con los
    robots (grupo de contacto 2), no con las paredes."""
    leaf = width / 2
    for s in (-1, 1):
        offset = [s * leaf / 2, 0] if axis == "x" else [0, s * leaf / 2]
        slide = [s, 0, 0] if axis == "x" else [0, s, 0]
        size = [leaf / 2, 0.02, height / 2] if axis == "x" else [0.02, leaf / 2, height / 2]
        body = spec.worldbody.add_body(name=f"{name}_hoja{s:+d}",
                                       pos=[at[0] + offset[0], at[1] + offset[1], height / 2 + 0.005])
        body.add_joint(name=f"{name}_hoja{s:+d}", type=mujoco.mjtJoint.mjJNT_SLIDE, axis=slide,
                       range=[0, leaf * 0.97], damping=40.0, armature=1.0)
        body.add_geom(name=f"{name}_hoja{s:+d}", type=mujoco.mjtGeom.mjGEOM_BOX, size=size, material=material,
                      mass=25.0, contype=2, conaffinity=2, group=1)
        if frame:
            for k, dz in enumerate((-1, 1)):
                fsize = [size[0] + 0.005, size[1] + 0.005, 0.03]
                body.add_geom(name=f"{name}_hoja{s:+d}_perfil{k}", type=mujoco.mjtGeom.mjGEOM_BOX, size=fsize,
                              pos=[0, 0, dz * (height / 2 - 0.03)], material=frame, contype=0, conaffinity=0,
                              density=0, group=1)
        a = spec.add_actuator(name=f"{name}_hoja{s:+d}", target=f"{name}_hoja{s:+d}",
                              trntype=mujoco.mjtTrn.mjTRN_JOINT)
        a.set_to_position(kp=600.0, kv=0.0)
        a.ctrlrange = [0, leaf * 0.97]
        a.ctrllimited = mujoco.mjtLimited.mjLIMITED_TRUE
        a.forcerange = [-300, 300]
        a.forcelimited = mujoco.mjtLimited.mjLIMITED_TRUE


def _back_rooms(spec: mujoco.MjSpec) -> None:
    """Vestidores del personal y zona de servicio: suelos, muebles, rótulos sobre las puertas y luz."""
    wb = spec.worldbody
    ly0, ly1 = LOBBY[2], LOBBY[3]
    # Rótulos sobre las puertas, del lado del vestíbulo.
    _quad(spec, "rotulo_lateral", 1.5, 0.25)
    _quad(spec, "rotulo_largo", 2.0, 0.25)
    _picture(wb, "rotulo_personal", "rotulo_lateral", [STAFF_DOOR_X, ly0 + WALL_T / 2 + 0.03, SIDE_DOOR_H + 0.3],
             math.pi / 2, "rotulo_personal")
    _picture(wb, "rotulo_servicio", "rotulo_largo", [SERVICE_DOOR_X, ly1 - WALL_T / 2 - 0.03, SIDE_DOOR_H + 0.3],
             -math.pi / 2, "rotulo_servicio")

    # Vestidores: suelo de vinilo, taquillas, banco, tablón y luz.
    x0, x1, y0, y1 = STAFF_ROOM
    room = wb.add_body(name="vestidores")
    _box(room, "vestidores_suelo", [(x0 + x1) / 2, (y0 + y1) / 2, 0.002], [(x1 - x0) / 2, (y1 - y0) / 2, 0.002],
         "vinilo", collide=False)
    for i, x in enumerate(np.arange(x0 + 0.5, x1 - 0.3, 0.5)):
        _box(room, f"taquilla{i}", [x, y0 + WALL_T / 2 + 0.25, 0.95], [0.24, 0.24, 0.95], "taquilla")
        _box(room, f"taquilla{i}_ranura", [x, y0 + WALL_T / 2 + 0.495, 1.7], [0.08, 0.004, 0.01], "negro_mate",
             collide=False)
    _box(room, "vestidores_banco", [(x0 + x1) / 2, y0 + 1.6, 0.23], [1.2, 0.18, 0.04], "nogal")
    for s in (-1, 1):
        _box(room, f"vestidores_banco_pata{s:+d}", [(x0 + x1) / 2 + s * 1.0, y0 + 1.6, 0.1], [0.04, 0.15, 0.1],
             "acero")
    _box(room, "tablon", [x1 - WALL_T / 2 - 0.02, (y0 + y1) / 2, 1.5], [0.015, 0.6, 0.4], "corcho", collide=False)
    _picture(wb, "rotulo_vestidores", "rotulo_lateral", [(x0 + x1) / 2, y0 + WALL_T / 2 + 0.51, 2.3], math.pi / 2,
             "rotulo_vestidores")
    _box(room, "vestidores_luz", [(x0 + x1) / 2, (y0 + y1) / 2, BACK_H - 0.01], [1.2, 0.15, 0.01], "luz_calida",
         collide=False)

    # Zona de servicio: suelo de concreto con franja de seguridad, estanterías con cajas, carro y mesa de registro.
    x0, x1, y0, y1 = SERVICE_ROOM
    room = wb.add_body(name="servicio")
    _box(room, "servicio_suelo", [(x0 + x1) / 2, (y0 + y1) / 2, 0.002], [(x1 - x0) / 2, (y1 - y0) / 2, 0.002],
         "concreto", collide=False)
    for k in range(10):                                        # franja amarilla y negra en el umbral
        _box(room, f"franja{k}", [SERVICE_DOOR_X - 0.9 + 0.2 * k, y0 + 0.45, 0.004], [0.1, 0.06, 0.002],
             "amarillo" if k % 2 == 0 else "negro_mate", collide=False)
    for r, y in enumerate((y1 - WALL_T / 2 - 0.3, )):
        for i, x in enumerate((x0 + 0.9, x0 + 2.2)):
            rack = room.add_body(name=f"estanteria{r}{i}", pos=[x, y, 0])
            for s in (-1, 1):
                _box(rack, f"estanteria{r}{i}_lado{s:+d}", [s * 0.58, 0, 0.9], [0.02, 0.25, 0.9], "acero")
            for k, z in enumerate((0.3, 0.9, 1.5)):
                _box(rack, f"estanteria{r}{i}_balda{k}", [0, 0, z], [0.6, 0.25, 0.015], "acero")
                for j, dx in enumerate((-0.35, 0.05, 0.38)):
                    _box(rack, f"caja{r}{i}{k}{j}", [dx, 0, z + 0.13], [0.15, 0.18, 0.12], "carton", collide=False)
    cart = room.add_body(name="carro", pos=[x0 + 1.0, y0 + 2.0, 0])
    _box(cart, "carro_base", [0, 0, 0.35], [0.45, 0.28, 0.03], "acero")
    _box(cart, "carro_balda", [0, 0, 0.75], [0.45, 0.28, 0.02], "acero")
    _cyl(cart, "carro_cubo", [0.2, 0, 0.5], 0.15, 0.13, "amarillo")
    desk = room.add_body(name="mesa_registro", pos=[x1 - WALL_T / 2 - 0.45, (y0 + y1) / 2 + 0.3, 0])
    _box(desk, "mesa_registro", [0, 0, 0.73], [0.35, 0.7, 0.025], "concreto")
    for s in (-1, 1):
        _box(desk, f"mesa_registro_pata{s:+d}", [0, s * 0.62, 0.36], [0.3, 0.03, 0.36], "acero")
    _box(desk, "portapapeles", [-0.05, 0.1, 0.765], [0.12, 0.16, 0.008], "corcho", collide=False)
    _picture(wb, "rotulo_registro", "rotulo_largo", [x1 - WALL_T / 2 - 0.02, (y0 + y1) / 2 + 0.3, 1.6], math.pi,
             "rotulo_registro")
    _box(room, "servicio_luz", [(x0 + x1) / 2, (y0 + y1) / 2, BACK_H - 0.01], [1.2, 0.15, 0.01], "luz_calida",
         collide=False)


def _lights(spec: mujoco.MjSpec) -> None:
    wb = spec.worldbody
    spot, directional = mujoco.mjtLightType.mjLIGHT_SPOT, mujoco.mjtLightType.mjLIGHT_DIRECTIONAL
    wb.add_light(name="general", type=directional, pos=[6, 4, 8], dir=[-0.2, 0.25, -1], diffuse=[0.40, 0.37, 0.32],
                 specular=[0.15, 0.15, 0.15], castshadow=True)
    for name, pos, target, diffuse, cutoff in (
            ("foco_recepcion", (6.0, 0.0, 4.4), (2.8, 0.0, 1.2), (0.75, 0.66, 0.52), 38),
            ("foco_pasillo", (2.6, 6.5, 2.9), (2.6, 15.0, 0.4), (0.62, 0.55, 0.44), 55),
            ("foco_ascensores", (2.6, 16.4, 3.3), (2.6, 20.0, 1.0), (0.38, 0.34, 0.28), 60),
            ("foco_sala", (8.0, -1.6, 4.4), (8.0, -3.8, 0.4), (0.55, 0.48, 0.38), 45)):
        wb.add_light(name=name, type=spot, pos=pos, dir=np.subtract(target, pos), cutoff=cutoff, exponent=3,
                     diffuse=diffuse, specular=[0.2, 0.2, 0.2], castshadow=False)
    wb.add_light(name="exterior", type=directional, pos=[18, 0, 6], dir=[-1, 0.1, -0.5], diffuse=[0.18, 0.2, 0.24],
                 specular=[0, 0, 0], castshadow=False)


def add_club(spec: mujoco.MjSpec, robots=()) -> None:
    vis = spec.visual
    vis.headlight.ambient = [0.24, 0.23, 0.22]
    vis.headlight.diffuse = [0.22, 0.21, 0.2]
    vis.headlight.specular = [0.0, 0.0, 0.0]
    vis.global_.offwidth = 2560
    vis.global_.offheight = 1440
    vis.quality.shadowsize = 4096
    _materials(spec)
    spec.worldbody.add_geom(name="floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[0, 0, 0.05], material="marmol",
                            condim=3, friction=[1.0, 0.005, 0.0001])
    _structure(spec)
    _reception(spec)
    _lounge(spec)
    _decor(spec)
    _elevators(spec)
    _back_rooms(spec)
    _lights(spec)
    G.add_guest(spec, *GUEST_START)


def make_guest(model: mujoco.MjModel, data: mujoco.MjData) -> G.Guest:
    doors = [f"{name}_hoja{s:+d}" for name in door_centers() for s in (-1, 1)]
    return G.Guest(model, data, GUEST_START, door_geoms=doors)


# ----------------------------------------------------------------------------------------------
# Puertas automáticas
# ----------------------------------------------------------------------------------------------
def door_centers() -> dict[str, tuple[float, float]]:
    centers = {f"ascensor{k}": (x, HALL[3]) for k, x in enumerate(ELEVATOR_X)}
    centers["entrada"] = (LOBBY[1], 0.0)
    centers["personal"] = (STAFF_DOOR_X, LOBBY[2])
    centers["servicio"] = (SERVICE_DOOR_X, LOBBY[3])
    return centers


class ClubDoors:
    """Abre las puertas de cada ascensor y de la entrada cuando un robot se acerca a su hueco."""

    def __init__(self, model: mujoco.MjModel):
        self.doors = {}
        for name, center in door_centers().items():
            ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_hoja{s:+d}") for s in (-1, 1)]
            self.doors[name] = (np.array(center), np.array(ids), model.actuator_ctrlrange[ids[0], 1])
        self.hold_open = False

    def update(self, data: mujoco.MjData, robot_xy) -> None:
        for center, ids, open_ in self.doors.values():
            near = any(np.linalg.norm(np.asarray(p) - center) < DOOR_OPEN_RADIUS for p in robot_xy)
            data.ctrl[ids] = open_ if (near or self.hold_open) else 0.0

    def is_open(self, data: mujoco.MjData, name: str, model: mujoco.MjModel) -> float:
        """Apertura media de las hojas (0 cerrada, 1 abierta)."""
        _, ids, open_ = self.doors[name]
        q = [data.qpos[model.jnt_qposadr[model.actuator_trnid[i, 0]]] for i in ids]
        return float(np.mean(q) / open_)


def club_card(width: int, height: int) -> np.ndarray:
    """Pantalla de bienvenida del club: CLUB N, su lema y los dos patrocinadores."""
    from PIL import Image
    v = np.linspace(0, 1, height)[:, None, None]
    grad = ((1 - v) * np.array([40, 26, 18.0]) + v * np.array([18, 12, 8.0])) * np.ones((1, width, 1))
    img = grad.astype(np.uint8)
    title = T.text_sign("CLUB N", width, int(height * 0.28), bg=(0, 0, 0), tracking=0.25)
    hello = T.text_sign("Nuestro lugar de encuentro", width, int(height * 0.12), fg=(236, 226, 206), bg=(0, 0, 0),
                        fill=0.7)
    for part, y in ((title, int(height * 0.12)), (hello, int(height * 0.42))):
        mask = part.max(axis=2, keepdims=True) > 30
        region = img[y:y + part.shape[0]]
        img[y:y + part.shape[0]] = np.where(mask, part, region)
    img[int(height * 0.62):int(height * 0.62) + 3, int(width * 0.3):int(width * 0.7)] = (196, 160, 92)
    canvas = Image.fromarray(img)
    for logo, cx in (("unisabana", 0.33), ("ansrobex", 0.67)):
        sign = Image.fromarray(B.sign_image(logo, int(width * 0.24), int(height * 0.2), fill=0.7))
        canvas.paste(sign, (int(width * cx - sign.width / 2), int(height * 0.72)))
    return np.asarray(canvas)
