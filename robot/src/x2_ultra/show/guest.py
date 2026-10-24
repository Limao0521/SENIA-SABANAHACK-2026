"""El huésped: una persona que maneja el usuario (teclado) y que el robot recibe y guía.

Es un muñeco cinemático hecho de cuerpos *mocap* (torso con cabeza, dos piernas y dos brazos) que se colocan en
cada ciclo: no tiene física, así que no empuja al robot ni el robot a él. Para que no atraviese nada, ``Guest``
lleva un mapa de ocupación 2D del decorado (las geometrías estáticas con colisión entre 0,05 y 1,8 m de altura) y
comprueba además las puertas (que se mueven) y la distancia al robot. Al andar balancea piernas y brazos con una
fase que avanza con la distancia recorrida.

Controles (los pone la app): avanzar/retroceder y girar, más paso lateral. Con las gafas de VR (``x2_ultra.vr``) es el
visor el que mueve al huésped (``follow``): las Quest calculan el paso y las colisiones con este mismo mapa, para
que andar no tenga la latencia de la red, y la simulación solo coloca el muñeco donde le dicen.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

PREFIX = "huesped_"
GROUP = 5                  # grupo de geometría del huésped: la vista en primera persona lo oculta
RADIUS = 0.25              # m, huella del huésped
ROBOT_CLEARANCE = 0.65     # m, distancia mínima a la base de un robot
CELL = 0.05                # m, resolución del mapa de ocupación
WALK_SPEED, RUN_SPEED, BACK_SPEED, SIDE_SPEED = 0.9, 1.4, 0.5, 0.5   # m/s
TURN_SPEED = 1.8           # rad/s
STEP_LENGTH = 0.7          # m por zancada (un ciclo de piernas = dos zancadas)

SUIT = (0.13, 0.15, 0.22, 1)
TROUSERS = (0.10, 0.11, 0.14, 1)
SKIN = (0.80, 0.62, 0.50, 1)
HAIR = (0.15, 0.10, 0.07, 1)
SHIRT = (0.95, 0.95, 0.96, 1)
TIE = (0.55, 0.10, 0.12, 1)
SHOE = (0.06, 0.05, 0.05, 1)

PARTS = ("torso", "pierna_izq", "pierna_der", "brazo_izq", "brazo_der")
HIP = {"pierna_izq": (0.0, 0.10, 0.93), "pierna_der": (0.0, -0.10, 0.93)}
SHOULDER = {"brazo_izq": (0.0, 0.22, 1.40), "brazo_der": (0.0, -0.22, 1.40)}


def add_guest(spec: mujoco.MjSpec, x: float, y: float, yaw: float) -> None:
    """Cuerpos mocap del huésped (sin colisión), de pie en (x, y) mirando a ``yaw``."""
    wb = spec.worldbody

    def geom(body, name, gtype, rgba, **kw):
        body.add_geom(name=f"{PREFIX}{name}", type=gtype, rgba=rgba, contype=0, conaffinity=0, density=0,
                      group=GROUP, **kw)

    cap, sph, box, ell = (mujoco.mjtGeom.mjGEOM_CAPSULE, mujoco.mjtGeom.mjGEOM_SPHERE, mujoco.mjtGeom.mjGEOM_BOX,
                          mujoco.mjtGeom.mjGEOM_ELLIPSOID)
    torso = wb.add_body(name=f"{PREFIX}torso", mocap=True, pos=[x, y, 0])
    geom(torso, "cadera", ell, TROUSERS, size=[0.13, 0.18, 0.08], pos=[0, 0, 0.95])
    geom(torso, "tronco", ell, SUIT, size=[0.14, 0.21, 0.27], pos=[0, 0, 1.2])
    geom(torso, "camisa", box, SHIRT, size=[0.01, 0.05, 0.1], pos=[0.13, 0, 1.36])
    geom(torso, "corbata", box, TIE, size=[0.012, 0.022, 0.13], pos=[0.137, 0, 1.25])
    geom(torso, "cuello", cap, SKIN, size=[0.045, 0.04, 0], pos=[0, 0, 1.5])
    geom(torso, "cabeza", ell, SKIN, size=[0.095, 0.085, 0.115], pos=[0.01, 0, 1.64])
    geom(torso, "pelo", ell, HAIR, size=[0.1, 0.09, 0.07], pos=[-0.01, 0, 1.7])
    for s, name in ((1, "izq"), (-1, "der")):
        geom(torso, f"ojo_{name}", sph, (0.1, 0.08, 0.06, 1), size=[0.012, 0, 0], pos=[0.088, s * 0.035, 1.66])
    for part, (hx, hy, hz) in HIP.items():
        leg = wb.add_body(name=f"{PREFIX}{part}", mocap=True, pos=[x + hx, y + hy, hz])
        geom(leg, f"{part}_muslo", cap, TROUSERS, size=[0.075, 0, 0], fromto=[0, 0, -0.02, 0, 0, -0.45])
        geom(leg, f"{part}_espinilla", cap, TROUSERS, size=[0.06, 0, 0], fromto=[0, 0, -0.45, 0, 0, -0.84])
        geom(leg, f"{part}_zapato", box, SHOE, size=[0.13, 0.05, 0.04], pos=[0.05, 0, -0.89])
    for part, (sx, sy, sz) in SHOULDER.items():
        arm = wb.add_body(name=f"{PREFIX}{part}", mocap=True, pos=[x + sx, y + sy, sz])
        geom(arm, f"{part}_brazo", cap, SUIT, size=[0.05, 0, 0], fromto=[0, 0, 0, 0, 0, -0.55])
        geom(arm, f"{part}_mano", ell, SKIN, size=[0.04, 0.03, 0.06], pos=[0, 0, -0.62])
    del yaw   # la orientación inicial la pone Guest.reset


def _quat(yaw: float, pitch: float = 0.0) -> np.ndarray:
    """Rumbo (z) y luego cabeceo (y local)."""
    cy, sy, cp, sp = math.cos(yaw / 2), math.sin(yaw / 2), math.cos(pitch / 2), math.sin(pitch / 2)
    return np.array([cy * cp, -sy * sp, cy * sp, sy * cp])


class Occupancy:
    """Mapa 2D de lo que el huésped no puede atravesar: geometrías estáticas con colisión que ocupan la franja de
    altura del cuerpo (paredes, mostrador, muebles, columnas, macetas). Las puertas se comprueban aparte."""

    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, z_band=(0.05, 1.8)):
        body = model.geom_bodyid
        static = [g for g in range(model.ngeom)                 # soldadas al mundo, con colisión, sin el suelo
                  if model.body_weldid[body[g]] == 0 and model.body_mocapid[body[g]] < 0
                  and model.geom_contype[g] & 1 and int(model.geom_type[g]) != int(mujoco.mjtGeom.mjGEOM_PLANE)]
        boxes = [b for b in (self._aabb(model, data, g) for g in static) if b[5] > z_band[0] and b[2] < z_band[1]]
        lo = np.min([b[:2] for b in boxes], axis=0) - 1.0
        hi = np.max([b[3:5] for b in boxes], axis=0) + 1.0
        self.origin = lo
        self.shape = np.ceil((hi - lo) / CELL).astype(int)
        self.grid = np.zeros(self.shape, bool)
        for b in boxes:
            i0, j0 = np.floor((b[:2] - lo) / CELL).astype(int)
            i1, j1 = np.ceil((b[3:5] - lo) / CELL).astype(int)
            self.grid[i0:i1, j0:j1] = True

    @staticmethod
    def _aabb(model, data, g) -> np.ndarray:
        """Caja alineada con los ejes que contiene la geometría (x0, y0, z0, x1, y1, z1)."""
        t, size = int(model.geom_type[g]), model.geom_size[g]      # int: el enum no se compara bien con numpy
        R, p = data.geom_xmat[g].reshape(3, 3), data.geom_xpos[g]
        if t == int(mujoco.mjtGeom.mjGEOM_BOX):
            half = np.abs(R) @ size
        elif t in (int(mujoco.mjtGeom.mjGEOM_CYLINDER), int(mujoco.mjtGeom.mjGEOM_CAPSULE)):
            r, h = size[0], size[1] + (size[0] if t == int(mujoco.mjtGeom.mjGEOM_CAPSULE) else 0)
            half = np.abs(R) @ np.array([r, r, h])
        elif t == int(mujoco.mjtGeom.mjGEOM_MESH):
            half = np.abs(R) @ model.geom_aabb[g, 3:]
            p = p + R @ model.geom_aabb[g, :3]
        else:
            half = np.full(3, model.geom_rbound[g])
        return np.concatenate([p - half, p + half])

    def free(self, xy, radius: float = RADIUS) -> bool:
        c = (np.asarray(xy) - self.origin) / CELL
        r = int(math.ceil(radius / CELL))
        i, j = int(round(c[0])), int(round(c[1]))
        if i - r < 0 or j - r < 0 or i + r >= self.shape[0] or j + r >= self.shape[1]:
            return True                                       # fuera del mapa: exterior abierto
        ii, jj = np.mgrid[-r:r + 1, -r:r + 1]
        disk = ii ** 2 + jj ** 2 <= r * r
        return not self.grid[i - r:i + r + 1, j - r:j + r + 1][disk].any()


class Guest:
    def __init__(self, model: mujoco.MjModel, data: mujoco.MjData, start, door_geoms=()):
        self.model = model
        self.start = tuple(start)
        self.mocap = {p: model.body_mocapid[mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, PREFIX + p)]
                      for p in PARTS}
        mujoco.mj_forward(model, data)
        self.occupancy = Occupancy(model, data)
        self.door_geoms = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, n) for n in door_geoms]
        self.cmd = np.zeros(3)          # avance, lateral [m/s], giro [rad/s]
        self.target = None              # (x, y, yaw) que pone el visor de VR; manda sobre ``cmd``
        self.generation = 0             # sube en cada reset (el visor de VR vuelve a la salida)
        self.reset(data)

    def reset(self, data: mujoco.MjData) -> None:
        self.x, self.y, self.yaw = self.start
        self.phase = 0.0
        self.speed = 0.0
        self.cmd[:] = 0.0
        self.target = None
        self.generation += 1
        self.pose(data)

    @property
    def xy(self) -> np.ndarray:
        return np.array([self.x, self.y])

    def _blocked_by_doors(self, data, xy) -> bool:
        for g in self.door_geoms:
            b = Occupancy._aabb(self.model, data, g)
            if b[0] - RADIUS < xy[0] < b[3] + RADIUS and b[1] - RADIUS < xy[1] < b[4] + RADIUS:
                return True
        return False

    def _free(self, data, xy, robots_xy) -> bool:
        if not self.occupancy.free(xy) or self._blocked_by_doors(data, xy):
            return False
        return all(np.linalg.norm(xy - np.asarray(r)) >= ROBOT_CLEARANCE for r in robots_xy)

    def follow(self, x: float, y: float, yaw: float) -> None:
        """Pose que manda el visor de VR (ya con colisiones); ``update`` lleva el muñeco hasta ella."""
        self.target = (float(x), float(y), float(yaw))

    def release(self) -> None:
        """Devuelve el huésped al teclado."""
        self.target = None
        self.cmd[:] = 0.0

    def update(self, data: mujoco.MjData, dt: float, robots_xy=()) -> None:
        if self.target is not None:
            x, y, self.yaw = self.target
            dist = math.hypot(x - self.x, y - self.y)
            self.x, self.y = x, y
            self.speed = dist / dt if dt > 0 and dist < 1.0 else 0.0     # un salto (reaparecer) no es andar
            self.phase += math.pi * dist / STEP_LENGTH if dist < 1.0 else 0.0
            self.pose(data)
            return
        fwd, side, turn = self.cmd
        self.yaw = (self.yaw + turn * dt + math.pi) % (2 * math.pi) - math.pi
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        step = np.array([c * fwd - s * side, s * fwd + c * side]) * dt
        here = self.xy
        moved = np.zeros(2)
        for trial in (step, np.array([step[0], 0.0]), np.array([0.0, step[1]])):   # deslizar por las paredes
            if np.linalg.norm(trial) > 1e-9 and self._free(data, here + trial, robots_xy):
                moved = trial
                break
        self.x, self.y = here + moved
        dist = float(np.linalg.norm(moved))
        self.speed = dist / dt if dt > 0 else 0.0
        self.phase += math.pi * dist / STEP_LENGTH
        self.pose(data)

    def pose(self, data: mujoco.MjData) -> None:
        """Coloca las piezas: piernas y brazos balanceando según la fase; al pararse vuelven a su sitio."""
        amp = min(self.speed / WALK_SPEED, 1.4) * 0.45
        swing = amp * math.sin(self.phase)
        c, s = math.cos(self.yaw), math.sin(self.yaw)

        def place(part, local, pitch):
            p = np.array([self.x + c * local[0] - s * local[1], self.y + s * local[0] + c * local[1], local[2]])
            m = self.mocap[part]
            data.mocap_pos[m] = p
            data.mocap_quat[m] = _quat(self.yaw, pitch)

        place("torso", (0, 0, 0), 0.0)
        place("pierna_izq", HIP["pierna_izq"], swing)
        place("pierna_der", HIP["pierna_der"], -swing)
        place("brazo_izq", SHOULDER["brazo_izq"], -0.8 * swing)
        place("brazo_der", SHOULDER["brazo_der"], 0.8 * swing)
