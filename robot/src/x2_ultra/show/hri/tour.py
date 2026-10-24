"""Recepción HRI del reto 2: llegada del huésped → cara → rango → punto de recepción → turnos HRIEGO.

Extiende ``GuideTour`` con tres pasos nuevos (sin tocar ``guide.py``):

- ``GuestArrive``: el huésped cinemático entra desde la puerta hasta su sitio delante del mostrador
  (la «cámara simulada»: la foto ya se identificó con ``face_id``);
- ``Settle``: el robot parado, con la velocidad comandada en cero, se asienta antes de gesticular;
- ``HriTurns``: la secuencia de turnos HRIEGO del rango (``player.HriPlayer`` a 50 Hz).

Orden no negociable: caminar → parar → asentarse → gestos → brazos a reposo → caminar de nuevo
(el paso ``Walk`` heredado ya exige brazos en reposo antes de andar). Durante ``HriTurns`` el
reproductor exige robot parado cada ciclo (interlock en ``player.tick``).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from x2_ultra.show import club as C
from x2_ultra.show.guide import (Face, GuideTour, Pause, SendDog, Walk, _wrap, TURN_RATE, escort_steps)
from x2_ultra.show.hri.player import HriPlayer

RECEPTION_POINT = (5.2, 0.8, 0.0)     # x, y, rumbo: delante del mostrador, mirando a la entrada
GUEST_SPOT = (6.6, 0.6)               # dónde se para el huésped recién llegado
GUEST_SPEED = 0.9                     # m/s
FOLLOW_FAR, FOLLOW_NEAR = 2.2, 1.3    # m: el huésped sigue al robot durante la ruta


@dataclass
class GuestArrive:
    spot: tuple = GUEST_SPOT
    status: str = "el huésped entra al club"


@dataclass
class Settle:
    seconds: float = 0.8
    status: str = "parado, asentándose (velocidad cero)"


@dataclass
class HriTurns:
    plans: list = field(default_factory=list)
    audio_fn: object = None
    status: str = "recepción HRIEGO"


def _drive_guest(guest, target_xy, stop_dist: float = 0.2) -> bool:
    """Lleva al huésped cinemático hacia ``target_xy``; True cuando ya está."""
    d = np.asarray(target_xy, float) - guest.xy
    dist = float(np.linalg.norm(d))
    if dist < stop_dist:
        guest.cmd[:] = 0.0
        return True
    err = _wrap(math.atan2(d[1], d[0]) - guest.yaw)
    turn = float(np.clip(2.0 * err, -TURN_RATE * 2, TURN_RATE * 2))
    fwd = 0.0 if abs(err) > 0.7 else min(GUEST_SPEED, 0.35 + 0.5 * dist)
    guest.cmd[:] = (fwd, 0.0, turn)
    return False


class HriReceptionTour(GuideTour):
    follow_from: int = 1 << 30        # desde este paso el huésped sigue al robot
    log_event: object = None          # callable(**evento) opcional para el registro

    def update(self, engine, t: float) -> None:
        if self.done:
            return
        step = self.steps[self.index] if self.index < len(self.steps) else None
        if isinstance(step, HriTurns):
            self._update_hri(engine, t, step)
            return
        if isinstance(step, GuestArrive):
            self._update_arrive(engine, t, step)
            return
        if isinstance(step, Settle):
            self._update_settle(engine, t, step)
            return
        rig = engine.rigs[self.key]
        if (self.guest is not None and isinstance(step, Walk) and step.escort and self.follow_from <= self.index
                and rig.state == "activo"):
            _drive_guest(self.guest, rig.info.base_pos[:2], stop_dist=FOLLOW_NEAR)
        if (self.guest is not None and isinstance(step, SendDog) and self.follow_from <= self.index
                and getattr(engine, "dog", None) is not None and engine.dog.busy):
            _drive_guest(self.guest, engine.dog.xy, stop_dist=FOLLOW_NEAR)     # el huésped sigue al perro
        super().update(engine, t)

    # ---------------------------------------------------------------- pasos propios
    def _finish_step(self) -> None:
        self.index += 1
        self.t_step = None

    def _update_arrive(self, engine, t: float, step: GuestArrive) -> None:
        rig = engine.rigs[self.key]
        if self.t_step is None:
            self.t_step = t
            rig.stop_walking()
        rig.set_head_targets(*(self._look_at_guest(rig) if self.guest is not None else (0.0, 0.0)))
        if self.guest is None or _drive_guest(self.guest, step.spot):
            self.status = "huésped delante del mostrador"
            self._finish_step()
        else:
            self.status = step.status

    def _update_settle(self, engine, t: float, step: Settle) -> None:
        rig = engine.rigs[self.key]
        if self.t_step is None:
            self.t_step = t
        rig.stop_walking()
        rig.set_head_targets(*(self._look_at_guest(rig) if self.guest is not None else (0.0, 0.0)))
        quiet = float(np.max(np.abs(rig.cmd))) < 0.05
        self.status = step.status
        if quiet and t - self.t_step >= step.seconds:
            self._finish_step()

    def _update_hri(self, engine, t: float, step: HriTurns) -> None:
        rig = engine.rigs[self.key]
        if rig.state != "activo":
            self.stop(engine)
            self.status = "interrumpida"
            return
        if self.t_step is None:
            self.t_step = t
            rig.stop_walking()
        if self.player is None:
            self.player = HriPlayer(self.key, step.plans, audio_fn=step.audio_fn,
                                    log_event=self.log_event or (lambda **kw: None))
        active = self.player.tick(t, rig)
        self.status = self.player.status or step.status
        self.waiting = False
        if not active and self.player.done:
            self._finish_step()

    player: object = None             # HriPlayer (se crea al entrar en HriTurns)

    def stop(self, engine) -> None:
        if self.player is not None and not self.player.done:
            self.player.abort_to_rest(engine.rigs[self.key])
        super().stop(engine)


def hri_reception(key: str, category: str, plans: list, floor: int | None = None, guest=None,
                  audio_fn=None, come_back: bool = False, log_event=None,
                  face_status: str = "reconocimiento facial (foto): visitante identificado",
                  label: str = "") -> HriReceptionTour:
    """Recepción completa con los turnos HRIEGO precomputados ``plans`` (ver ``data.visit_plan``):
    llega el huésped → pausa de reconocimiento → punto de recepción → parar y asentarse → turnos
    (saludo del rango, guía señalando la ruta, diálogo, despedida) → caminar la ruta → cierre."""
    from x2_ultra.show.guide import CATEGORIES
    if category not in CATEGORIES:
        raise ValueError(f"categoría desconocida: {category}")
    floor = floor if category in {"vip", "socio", "invitado"} else None
    escort, _ = escort_steps(category, floor, come_back)
    for step in escort:
        if isinstance(step, SendDog):
            step.wait = True                     # el huésped sigue al perro hasta el destino
    steps = [
        GuestArrive(),
        Pause(1.5, face_status),
        Walk([RECEPTION_POINT[:2]], "acercándose al punto de recepción"),
        Face(RECEPTION_POINT[2]),
        Settle(0.8),
        HriTurns(plans=list(plans), audio_fn=audio_fn),
    ] + escort
    if not label:
        label = CATEGORIES[category] + (f" · {C.FLOORS[floor]} (piso {floor})" if floor is not None else "")
    tour = HriReceptionTour(key, steps, label=label, guest=guest)
    tour.follow_from = 6                     # tras los turnos: el huésped sigue al robot o al perro
    tour.log_event = log_event
    return tour
