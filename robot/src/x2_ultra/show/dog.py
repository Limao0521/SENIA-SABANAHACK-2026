"""El perro guía AgiBot D1 del club: espera tumbado junto al mostrador y acompaña a socios e invitados.

El X2 decide quién acompaña (``show.guide.ESCORT``); cuando le toca al perro, la guía le pasa la ruta con
``DogRig.guide`` y el perro la hace solo, en paralelo a lo que siga haciendo el X2:

    tumbado → se levanta → trota por la ruta (se para si el huésped se queda atrás) → se gira hacia el destino
    → espera un momento → vuelve por el mismo camino → se tumba en su sitio.

El movimiento es cinemático (``model.d1``): la base sigue un modelo de uniciclo con aceleración limitada y las patas
el trote de ``d1.trot_pose`` con el avance que toca a la velocidad actual. En cada ciclo de control se impone la
postura (``apply``); el perro no choca con nada y tiene la gravedad compensada, así que la física no lo mueve.
"""
from __future__ import annotations

import math

import mujoco
import numpy as np

from x2_ultra.model import d1

PREFIX = "d1/"
SPEED = 0.6               # m/s de crucero
ACCEL = 0.8               # m/s²
TURN_RATE = 1.2           # rad/s al girar en el sitio
TURN_IN_PLACE = 0.5       # rad: por encima, girar sin avanzar
PASS_THROUGH, REACHED = 0.4, 0.1    # m
WAIT_FAR, WAIT_NEAR = 3.4, 2.4      # m: parar si el huésped está más lejos; seguir cuando se acerque
POSTURE_TIME = 0.9        # s para levantarse o tumbarse
LINGER = 4.0              # s esperando en el destino antes de volver


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


class DogRig:
    def __init__(self, model: mujoco.MjModel, home: tuple, floor_z: float = 0.0, prefix: str = PREFIX):
        self.home = tuple(float(v) for v in home)
        self.floor_z = floor_z
        root = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, prefix + "root")
        if root < 0:
            raise ValueError(f"el modelo no tiene el D1 ({prefix}root)")
        self.qbase = int(model.jnt_qposadr[root])
        self.vbase = int(model.jnt_dofadr[root])
        ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, prefix + n) for n in d1.LEG_JOINTS]
        self.q_legs = np.array([model.jnt_qposadr[j] for j in ids])
        self.v_legs = np.array([model.jnt_dofadr[j] for j in ids])
        self.reset()

    def reset(self, data: mujoco.MjData | None = None) -> None:
        self.x, self.y, self.yaw = self.home
        self.v = self.w = 0.0
        self.phase = 0.0
        self.gait = 0.0                 # 0 = pies quietos, 1 = trotando
        self.stand = 0.0                # 0 = tumbado, 1 = de pie
        self.mode = "tumbado"   # tumbado, levantandose, guiando, esperando, llegado, volviendo, tumbandose
        self.path: list = []
        self.point = 0
        self.face: float | None = None
        self.back: list = []
        self.guest = None
        self.t_mode = 0.0
        self.at_end = False
        self.status = "Draco tumbado junto al mostrador"
        if data is not None:
            self.apply(data)

    # ------------------------------------------------------------------ órdenes
    def guide(self, points, face: float, guest=None) -> None:
        """Acompañar al huésped por ``points`` hasta mirar a ``face`` y volver luego a casa por el mismo camino."""
        self.path = [tuple(p) for p in points]
        self.back = list(reversed(self.path[:-1])) + [self.home[:2]]
        self.point, self.face, self.guest = 0, face, guest
        self._set_mode("levantandose")

    @property
    def xy(self) -> np.ndarray:
        return np.array([self.x, self.y])

    @property
    def busy(self) -> bool:
        return self.mode != "tumbado"

    @property
    def arrived(self) -> bool:
        """Ya llevó al huésped (está en el destino o de vuelta)."""
        return self.mode in ("llegado", "volviendo", "tumbandose", "tumbado") and self.face is not None

    # ------------------------------------------------------------------ ciclo de control
    def _set_mode(self, mode: str) -> None:
        self.mode, self.t_mode = mode, 0.0
        self.at_end = False

    def _go(self, points, yaw: float, dt: float) -> bool:
        """Recorrer ``points``, frenar y girar hasta ``yaw``; True cuando ya está."""
        if not self.at_end:
            self.at_end = self._follow(points, dt)
        elif abs(self.v) > 0.05:
            self._drive(0.0, 0.0, dt)
        else:
            return self._turn_to(yaw, dt)
        return False

    def _guest_far(self) -> bool:
        if self.guest is None:
            return False
        dist = float(np.linalg.norm(self.guest.xy - self.xy))
        if self.mode == "esperando":
            return dist > WAIT_NEAR
        return dist > WAIT_FAR

    def _follow(self, points, dt: float) -> bool:
        """Velocidad hacia el siguiente punto de ``points``; True al llegar al último."""
        while True:
            target = np.asarray(points[self.point], float)
            last = self.point == len(points) - 1
            d = target - self.xy
            dist = float(np.linalg.norm(d))
            if dist < (REACHED if last else PASS_THROUGH):
                if last:
                    self._drive(0.0, 0.0, dt)
                    return True
                self.point += 1
                continue
            break
        err = _wrap(math.atan2(d[1], d[0]) - self.yaw)
        if abs(err) > TURN_IN_PLACE:
            self._drive(0.0, TURN_RATE * np.sign(err), dt)
        else:
            speed = min(SPEED, 0.9 * dist + 0.15) if last else SPEED
            self._drive(speed, float(np.clip(2.0 * err, -TURN_RATE, TURN_RATE)), dt)
        return False

    def _turn_to(self, yaw: float, dt: float) -> bool:
        err = _wrap(yaw - self.yaw)
        if abs(err) < 0.08:
            self._drive(0.0, 0.0, dt)
            return True
        self._drive(0.0, float(np.clip(2.5 * err, -TURN_RATE, TURN_RATE)), dt)
        return False

    def _drive(self, v: float, w: float, dt: float) -> None:
        self.v += float(np.clip(v - self.v, -ACCEL * dt, ACCEL * dt))
        self.w += float(np.clip(w - self.w, -3.0 * dt, 3.0 * dt))

    def tick(self, data: mujoco.MjData, dt: float) -> None:
        self.t_mode += dt
        mode = self.mode
        if mode == "tumbado":
            self._drive(0.0, 0.0, dt)
            self.status = "Draco tumbado junto al mostrador"
        elif mode == "levantandose":
            self.stand = min(1.0, self.stand + dt / POSTURE_TIME)
            self.status = "Draco se levanta"
            if self.stand >= 1.0:
                self._set_mode("guiando")
        elif mode in ("guiando", "esperando"):
            if self._guest_far():
                if mode == "guiando":
                    self.mode = "esperando"
                self._drive(0.0, 0.0, dt)
                self.status = "Draco espera al huésped"
            else:
                if mode == "esperando":
                    self.mode = "guiando"
                self.status = "Draco acompaña al huésped"
                if self._go(self.path, self.face, dt):
                    self._set_mode("llegado")
        elif mode == "llegado":
            self._turn_to(self.face, dt)
            self.status = "Draco en el destino"
            if self.t_mode >= LINGER:
                self.point = 0
                self._set_mode("volviendo")
        elif mode == "volviendo":
            self.status = "Draco vuelve al mostrador"
            if self._go(self.back, self.home[2], dt):
                self._set_mode("tumbandose")
        elif mode == "tumbandose":
            self._drive(0.0, 0.0, dt)
            self.stand = max(0.0, self.stand - dt / POSTURE_TIME)
            if self.stand <= 0.0:
                self.x, self.y = self.home[:2]
                self._set_mode("tumbado")
        # uniciclo
        self.yaw = _wrap(self.yaw + self.w * dt)
        self.x += self.v * math.cos(self.yaw) * dt
        self.y += self.v * math.sin(self.yaw) * dt
        moving = abs(self.v) > 0.02 or abs(self.w) > 0.05
        self.gait += float(np.clip((1.0 if moving else 0.0) - self.gait, -3.0 * dt, 3.0 * dt))
        if self.gait > 0.0:
            self.phase += d1.GAIT_HZ * dt
        self.apply(data)

    def leg_pose(self) -> np.ndarray:
        walk = d1.trot_pose(self.phase, self.v / (2 * d1.GAIT_HZ), self.gait)
        s = 0.5 - 0.5 * math.cos(math.pi * self.stand)          # suave al levantarse y tumbarse
        return (1 - s) * d1.sit_pose() + s * walk

    def height(self) -> float:
        s = 0.5 - 0.5 * math.cos(math.pi * self.stand)
        return (1 - s) * d1.sit_height() + s * d1.STAND_HEIGHT

    def apply(self, data: mujoco.MjData) -> None:
        q = data.qpos
        q[self.qbase:self.qbase + 3] = (self.x, self.y, self.floor_z + self.height())
        q[self.qbase + 3:self.qbase + 7] = (math.cos(self.yaw / 2), 0.0, 0.0, math.sin(self.yaw / 2))
        q[self.q_legs] = self.leg_pose()
        data.qvel[self.vbase:self.vbase + 6] = 0.0
        data.qvel[self.v_legs] = 0.0
