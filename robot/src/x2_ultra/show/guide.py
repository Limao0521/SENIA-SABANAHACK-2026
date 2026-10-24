"""Recepción guiada del Club N: el robot saluda, acompaña por el pasillo hasta el ascensor y dice a qué piso subir.

Un ``GuideTour`` es una lista de pasos que ``ShowEngine`` ejecuta en cada ciclo de control (50 Hz) sobre un robot:

- ``Say``: pone una pose de brazos, dice una frase (``engine.say``: subtítulo en la ventana y voz si hay
  ``espeak-ng``) y espera ``seconds``;
- ``Walk``: sigue una lista de puntos con la policy de marcha: gira en el sitio si el rumbo se desvía más de
  ``TURN_IN_PLACE`` y si no avanza corrigiendo el rumbo; los umbrales de arranque de la policy obligan a pedir al
  menos ~0,25 m/s;
- ``Face``: gira en el sitio hasta un rumbo;
- ``WaitForGuest``: espera en su sitio, siguiendo con la cabeza al huésped, hasta que lo tiene delante;
- ``Pause``: espera sin hablar (el reconocimiento facial, simulado de momento);
- ``FaceToward``: gira en el sitio hacia un punto (o hacia el huésped), para señalar;
- ``SendDog``: manda al perro guía Draco (``show.dog``) a acompañar al huésped por una ruta; el perro la hace solo.

El robot no siempre acompaña (``ESCORT``): ofrece y hace el acompañamiento al socio VIP, manda al perro D1 con los
socios e invitados, al personal le señala hacia dónde ir y al trabajador externo le dice que llamará a personal
humano del club.

Con huésped (``guest``), el robot lo mira mientras habla y al andar lo espera si se queda atrás ("Por aquí, por
favor"). Mientras dura, el teclado no mueve a ese robot. ``stop()`` lo deja parado donde esté.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from x2_ultra.robot import joints as J
from x2_ultra.show import club as C
from x2_ultra.show import spaces as S

TURN_IN_PLACE = 0.35      # rad: por encima, girar sin avanzar
TURN_RATE = 0.9           # rad/s al girar en el sitio
WALK_SPEED = 0.5          # m/s de crucero
REACHED = 0.3             # m: punto final alcanzado
PASS_THROUGH = 0.5        # m: puntos intermedios (no hace falta pisarlos)
SEE_DISTANCE = 6.0        # m: a partir de aquí el robot sigue al huésped con la cabeza
GREET_DISTANCE = 2.6      # m: el huésped está "delante" (y dentro de GREET_ANGLE) y el robot lo saluda
GREET_ANGLE = 0.7         # rad
WAIT_FAR, WAIT_NEAR = 3.2, 2.2   # m: al andar, parar si el huésped está más lejos; seguir cuando se acerque
REST_ARMS = np.array([J.DEFAULT_POSE[name] for name in J.ARM_JOINTS])
REST_ARM_CMD_TOL = 0.08          # rad: objetivo de brazo ya enviado a reposo
REST_ARM_ACTUAL_TOL = 0.18       # rad: articulaciones medidas cerca de reposo
REST_ARM_TIMEOUT = 4.0          # s: detener la visita si no puede recoger los brazos


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


@dataclass
class Say:
    text: str
    seconds: float
    pose: str | None = None


@dataclass
class Walk:
    points: list
    label: str = ""
    escort: bool = True              # acompañando al huésped: se para si se queda atrás


@dataclass
class FaceToward:
    target: tuple | None = None      # punto (x, y); None = el huésped
    tolerance: float = 0.12


@dataclass
class SendDog:
    points: list                     # ruta del perro desde su sitio
    yaw: float                       # rumbo al llegar (mirando al destino)
    wait: bool = False               # True: el paso dura hasta que el perro llega al destino


@dataclass
class Face:
    yaw: float
    tolerance: float = 0.12


@dataclass
class WaitForGuest:
    status: str = "esperando al huésped"


@dataclass
class Pause:
    seconds: float
    status: str = ""


@dataclass
class GuideTour:
    key: str                         # robot que guía
    steps: list
    label: str = ""
    guest: object = None             # show.guest.Guest, o None (recepción sin huésped)
    index: int = 0
    t_step: float | None = None      # inicio del paso actual (tiempo de simulación)
    point: int = 0
    done: bool = False
    status: str = ""
    spoken: list = field(default_factory=list)
    waiting: bool = False            # parado esperando a que el huésped lo alcance
    no_wait_from: int = 1 << 30      # desde este paso ya no se espera al huésped (la vuelta a la recepción)

    def update(self, engine, t: float) -> None:
        if self.done:
            return
        rig = engine.rigs[self.key]
        if rig.state != "activo" or rig.dance is not None:
            self.stop(engine)
            self.status = "interrumpida"
            return
        if self.index >= len(self.steps):
            rig.stop_walking()
            self.done, self.status = True, "terminada"
            return
        step = self.steps[self.index]
        if self.t_step is None:
            self.t_step, self.point = t, 0
            if isinstance(step, Say):
                rig.stop_walking()
                if step.pose:
                    rig.play_pose(step.pose, t)
                if step.text:
                    engine.say(step.text, self.key)
                    self.spoken.append(step.text)
            elif isinstance(step, Walk):
                rig.stop_walking()
                rig.play_pose("reposo", t)
        finished = False
        looking = self.guest is not None and isinstance(step, (Say, WaitForGuest, Pause, SendDog))
        rig.set_head_targets(*(self._look_at_guest(rig) if looking else (0.0, 0.0)))
        if isinstance(step, Say):
            self.status = f"«{step.text}»" if step.text else self.status
            finished = t - self.t_step >= step.seconds
        elif isinstance(step, Pause):
            self.status = step.status or self.status
            finished = t - self.t_step >= step.seconds
        elif isinstance(step, WaitForGuest):
            self.status = step.status
            rig.stop_walking()
            finished = self.guest is None or self._guest_in_front(rig)
        elif isinstance(step, Walk):
            if not self._arms_at_rest(engine, rig):
                rig.stop_walking()
                self.status = "brazos a reposo antes de caminar"
                if t - self.t_step >= REST_ARM_TIMEOUT:
                    self.stop(engine)
                    self.status = "detenida: brazos no llegaron a reposo"
                return
            if self._guest_left_behind(engine, rig):
                self.status = "esperando a que el huésped lo alcance"
                return
            self.status = step.label or "caminando"
            finished = self._walk(rig, step)
        elif isinstance(step, (Face, FaceToward)):
            self.status = "girando"
            err = _wrap(self._yaw_of(step, rig) - rig.info.yaw)
            if abs(err) < step.tolerance:
                rig.stop_walking()
                finished = True
            else:
                rig.set_velocity_target(0, 0, TURN_RATE * np.sign(err))
        elif isinstance(step, SendDog):
            dog = getattr(engine, "dog", None)
            if dog is None:                     # escenario sin perro: nada que esperar
                finished = True
            else:
                if self.point == 0:
                    dog.guide(step.points, step.yaw, self.guest)
                    self.point = 1
                self.status = dog.status
                finished = not step.wait or dog.arrived
        if finished:
            self.index += 1
            self.t_step = None

    def _yaw_of(self, step, rig) -> float:
        if isinstance(step, Face):
            return step.yaw
        if step.target is None:
            if self.guest is None:
                return C.ROBOT_START[2]
            target = self.guest.xy
        else:
            target = np.asarray(step.target, float)
        d = target - rig.info.base_pos[:2]
        return math.atan2(d[1], d[0])

    @staticmethod
    def _arms_at_rest(engine, rig) -> bool:
        commanded = np.max(np.abs(rig.arm_cmd - REST_ARMS))
        measured = np.max(np.abs(engine.data.qpos[rig.q_arms] - REST_ARMS))
        return commanded <= REST_ARM_CMD_TOL and measured <= REST_ARM_ACTUAL_TOL

    def _guest_relative(self, rig) -> tuple[float, float]:
        """Distancia y ángulo (respecto al rumbo del robot) del huésped."""
        d = self.guest.xy - rig.info.base_pos[:2]
        return float(np.linalg.norm(d)), _wrap(math.atan2(d[1], d[0]) - rig.info.yaw)

    def _look_at_guest(self, rig) -> tuple[float, float]:
        dist, ang = self._guest_relative(rig)
        if dist > SEE_DISTANCE:
            return 0.0, 0.0
        pitch = math.atan2(1.15 - 1.55, max(dist, 0.5))          # de la cabeza del robot a la cara del huésped
        return float(np.clip(ang, -0.48, 0.48)), float(np.clip(-pitch, -0.4, 0.48))

    def _guest_in_front(self, rig) -> bool:
        dist, ang = self._guest_relative(rig)
        return dist < GREET_DISTANCE and abs(ang) < GREET_ANGLE

    def _guest_left_behind(self, engine, rig) -> bool:
        """Al acompañar: parar si el huésped se queda a más de WAIT_FAR y seguir cuando esté a menos de WAIT_NEAR."""
        step = self.steps[self.index]
        if self.guest is None or self.index >= self.no_wait_from or not getattr(step, "escort", False):
            return False
        dist, _ = self._guest_relative(rig)
        if not self.waiting and dist > WAIT_FAR:
            self.waiting = True
            rig.stop_walking()
            engine.say("Por aquí, por favor.", self.key)
        elif self.waiting and dist < WAIT_NEAR:
            self.waiting = False
        return self.waiting

    def _walk(self, rig, step: Walk) -> bool:
        pos = rig.info.base_pos[:2]
        while True:
            target = np.asarray(step.points[self.point], float)
            last = self.point == len(step.points) - 1
            d = target - pos
            dist = float(np.linalg.norm(d))
            if dist < (REACHED if last else PASS_THROUGH):
                if last:
                    rig.stop_walking()
                    return True
                self.point += 1
                continue
            break
        err = _wrap(math.atan2(d[1], d[0]) - rig.info.yaw)
        if abs(err) > TURN_IN_PLACE:
            rig.set_velocity_target(0, 0, TURN_RATE * np.sign(err))
        else:
            speed = min(WALK_SPEED, 0.8 * dist + 0.25) if last else WALK_SPEED
            rig.set_velocity_target(speed, 0, float(np.clip(1.5 * err, -0.6, 0.6)))
        return False

    def stop(self, engine) -> None:
        engine.rigs[self.key].stop_walking()
        self.done = True


# ----------------------------------------------------------------------------------------------
# Recorrido del Club N
# ----------------------------------------------------------------------------------------------
# Las cinco categorías del reto y la ruta de cada una: socios, VIP e invitados suben a los pisos del club (pasillo
# y ascensores); el personal va a los vestidores (puerta SOLO PERSONAL, al sur) y los trabajadores externos a la
# zona de servicio (puerta ACCESO DE SERVICIO, al norte, junto a la entrada).
CATEGORIES = {
    "vip": "Socio VIP",
    "socio": "Socio",
    "invitado": "Invitado",
    "personal": "Personal del club",
    "externo": "Trabajador externo",
}
ROUTES = {"vip": "ascensores", "socio": "ascensores", "invitado": "ascensores", "personal": "personal",
          "externo": "servicio"}
NEEDS_FLOOR = {k for k, route in ROUTES.items() if route == "ascensores"}


def elevator_for(floor) -> int:
    """Pisos pares por el ascensor de la izquierda (0), impares por el de la derecha (1); S1 y S2 como 1 y 2."""
    return S.elevator_for(floor)


def _where(floor, place: str | None) -> str:
    """Cómo se nombra el destino de los ascensores: el espacio de la KB si se da, si no lo que hay en ese piso."""
    return place or C.FLOORS[floor]


# Quién acompaña a cada categoría: "robot" (el X2 lo acompaña), "perro" (el X2 manda al perro guía Draco), "señalar"
# (el X2 le indica hacia dónde ir, sin moverse) o "llamar" (el X2 avisa a personal humano del club).
ESCORT = {"vip": "robot", "socio": "perro", "invitado": "perro", "personal": "señalar", "externo": "llamar"}
# Salida del perro desde su sitio (C.DOG_HOME) hasta la ruta de los ascensores: por detrás del X2, entre él y el
# mostrador, para no cruzarse con el huésped que espera delante.
DOG_EXIT = [(3.9, -0.5), (3.9, 2.6)]


def _greeting(category: str, floor: int | None) -> list:
    """Saludo y anuncio del destino (y quién le acompaña), distintos para cada categoría."""
    if category in NEEDS_FLOOR:
        name = C.FLOORS[floor]
        where = f"{name}, en el piso {floor}"
    if category == "vip":
        return [Say("¡Bienvenido de nuevo a Club N! Es un honor recibirle.", 3.5, "saludar"),
                Say(f"Su destino es {where}. Si me lo permite, le acompaño personalmente hasta los ascensores.",
                    4.5, "reposo")]
    if category == "socio":
        return [Say("¡Bienvenido a Club N! Es un gusto recibirle.", 3.5, "saludar"),
                Say(f"Su destino es {where}. Nuestro perro guía Draco le acompañará hasta los ascensores.", 4.5,
                    "reposo")]
    if category == "invitado":
        return [Say("¡Bienvenido a Club N! Usted es nuestro invitado.", 3.5, "saludar"),
                Say(f"Su anfitrión le espera en {where}. Nuestro perro guía Draco le acompañará hasta los "
                    "ascensores.", 5.0, "reposo")]
    if category == "personal":
        return [Say("¡Hola! Bienvenido a tu turno.", 3.0, "saludar"),
                Say("Te indico la entrada de personal.", 2.5, "reposo")]
    return [Say("Buenos días, bienvenido a Club N.", 3.0, "saludar"),
            Say("Para trabajos de servicio le atenderá una persona del equipo del club.", 4.0, "reposo")]


def dog_route(points: list) -> list:
    """Ruta del perro hasta los ascensores a partir de la del robot (sin su primer punto, delante del X2)."""
    return DOG_EXIT + list(points[1:])


def escort_steps(category: str, floor, come_back: bool = True, route: str | None = None,
                 mode: str | None = None, place: str | None = None) -> tuple:
    """Pasos tras el saludo según quién acompaña (``ESCORT[category]``, o ``mode`` si se da: "señalar" cuando el
    visitante rechaza el acompañamiento) y la frase de cierre (el ``Say`` donde va la indicación final, para
    sustituirla por la de un guion). ``route`` es la categoría cuya ruta se sigue (por defecto la del visitante) o
    directamente una ruta del club (``show.spaces``: ascensores, personal, servicio, aqui); ``place`` es el
    espacio de la KB al que va (su nombre), y ``floor`` su piso (2–6, o "1", "S1", "S2"). Con ``come_back`` el
    robot vuelve a su puesto."""
    route = route or category
    sx, sy, syaw = C.ROBOT_START
    if ROUTES.get(route, route) == "aqui":
        closing = Say(f"Ya está usted en {place or 'la recepción'}: este es el vestíbulo del club. Con gusto le "
                      "atiendo aquí mismo.", 5.0, "reposo")
        return [closing, Say("", 1.0, "reposo")], closing
    points, face, arrival = _route(route, floor, place, who=category)
    mode = mode or ESCORT[category]
    if mode == "perro" and ROUTES.get(route, route) != "ascensores":
        mode = "robot"                          # el perro solo conoce el camino a los ascensores
    back = [Walk([(sx, sy)], "volviendo a su puesto", escort=False), Face(syaw)]
    if mode == "robot":
        steps = [Walk(points, "acompañando al visitante"), Face(face)] + arrival + [Say("", 1.0, "reposo")]
        back = [Walk(list(reversed(points[:-1])) + [(sx, sy)], "volviendo a la recepción", escort=False),
                Face(syaw)]
        closing = arrival[0]
    elif mode == "perro":
        k = elevator_for(floor)
        side = "izquierda" if k == 0 else "derecha"
        if str(floor) == "1":
            text = f"Draco le lleva hasta el vestíbulo de ascensores: allí está {_where(floor, place)}. "
        else:
            text = f"Al llegar, tome el ascensor de la {side} y marque el piso {floor}: {_where(floor, place)}. "
        closing = Say(text + "Por favor, siga a Draco.", 5.5, "señalar")
        steps = [closing, SendDog(dog_route(points), face), Say("", 1.0, "reposo")]
    elif mode == "señalar":
        if ROUTES.get(route, route) == "personal":
            door = (C.STAFF_DOOR_X, C.LOBBY[2])
            text = "Es por allí: la puerta de SOLO PERSONAL, al fondo. Los vestidores están detrás. ¡Buen turno!"
        else:
            door = points[1]
            text = "Es por allí: siga el pasillo hasta el final" + (
                f" y tome el ascensor hasta el piso {floor}." if floor is not None and str(floor) != "1" else ".")
        closing = Say(text, 5.0, "señalar")
        steps = [FaceToward(door), closing, Say("", 1.0, "reposo"), FaceToward(None)]
    else:
        closing = Say("Un momento, por favor: voy a avisar a personal del club para que le acompañe.", 4.5, "reposo")
        steps = [closing, Pause(3.0, "llamando a personal humano del club…"),
                 Say("Ya he avisado: enseguida viene una persona de nuestro equipo. Mientras tanto, está en su casa; "
                     "puede esperar cómodamente en la sala de espera.", 6.0, "reposo")]
    return steps + (back if come_back else []), closing


def _route(category: str, floor, place: str | None = None, who: str | None = None) -> tuple[list, float, list]:
    """Puntos de paso desde la recepción, rumbo final (mirando a la puerta o al ascensor) y lo que dice al llegar.
    ``category`` es una categoría (su ruta, ``ROUTES``) o directamente una ruta; ``who``, la categoría del
    visitante (el tono de la despedida), si no es la misma."""
    sx, sy, _ = C.ROBOT_START
    route = ROUTES.get(category, category)
    if route == "ascensores":
        k = elevator_for(floor)
        side = "izquierda" if k == 0 else "derecha"     # mirando a los ascensores (+y), +x queda a la derecha
        cx = (C.CORRIDOR[0] + C.CORRIDOR[1]) / 2
        desk_end = C.DESK["center"][1] + C.DESK["length"] / 2
        points = [(sx, desk_end + 1.0), (cx, C.CORRIDOR[2] - 0.8), (cx, C.CORRIDOR[3] - 0.3),
                  (C.ELEVATOR_X[k], C.HALL[3] - 1.35)]
        name = _where(floor, place)
        if str(floor) == "1":
            closing = f"Hemos llegado: aquí, en el vestíbulo de ascensores, está {name}. ¡Que lo disfrute!"
        else:
            closing = {"vip": f"Tome el ascensor de la {side} y marque el piso {floor}: {name}. Ha sido un placer.",
                       "invitado": f"Tome el ascensor de la {side} hasta el piso {floor}, {name}: allí le espera "
                                   "su anfitrión. ¡Bienvenido!"}.get(
                who or category, f"Tome el ascensor de la {side} y marque el piso {floor}: {name}. ¡Que disfrute su visita!")
        return points, math.pi / 2, [Say(closing, 5.5, "señalar")]
    if route == "personal":
        desk_south = C.DESK["center"][1] - C.DESK["length"] / 2
        points = [(sx, desk_south - 0.9), (C.STAFF_DOOR_X, C.LOBBY[2] + 1.2)]
        text = (f"Esta es la entrada de personal: por la escalera se baja a {place.lower()}, en el piso {floor}."
                if place else "Esta es la entrada de personal: los vestidores están al fondo. ¡Buen turno!")
        return points, -math.pi / 2, [Say(text, 4.5, "señalar")]
    door_x = C.SERVICE_DOOR_X
    points = [(8.7, sy + 0.8), (8.7, C.LOBBY[3] - 1.6), (door_x, C.LOBBY[3] - 1.2)]
    text = (f"Este es el acceso de servicio: por aquí se llega a {place.lower()}. Registre su entrada en la mesa de "
            "contratistas y lleve su credencial visible." if place else
            "Este es el acceso de servicio. Registre su entrada en la mesa de contratistas y lleve su credencial "
            "visible. Gracias.")
    return points, math.pi / 2, [Say(text, 6.0, "señalar")]


def club_tour(key: str, category: str = "socio", floor: int | None = 2, come_back: bool = True,
              guest=None, *, script=None, identified: bool = False,
              greeting: bool = True) -> GuideTour:
    """Recepción de un visitante de ``category``: saludo propio y lo que toque según ``ESCORT`` (el robot lo
    acompaña por su ruta, manda al perro D1, le señala el camino o llama a personal) y, si ``come_back``, volver
    a la recepción. Las rutas: ascensores para socios, VIP e invitados, con ``floor``; vestidores para el
    personal; zona de servicio para los trabajadores externos.

    Con ``guest`` el robot espera a que el huésped llegue al mostrador. ``identified`` indica que Face ya confirmó
    su identidad; ``script`` aporta habla y gestos validados por HRIEGO para los tres momentos del recorrido."""
    if category not in CATEGORIES:
        raise ValueError(f"categoría desconocida: {category}")
    floor = floor if category in NEEDS_FLOOR else None
    intro = _greeting(category, floor)
    escort, closing = escort_steps(category, floor, come_back)
    if script:
        for step, intervention in zip((intro[0], intro[1], closing),
                                      (script["GREETING"], script["GUIDANCE"], script["FAREWELL"])):
            step.text = intervention.speech
            step.pose = intervention.pose
            step.seconds = max(step.seconds, len(step.text) / 13.0)
    steps = []
    if guest is not None:
        steps.append(WaitForGuest())
        if not identified:
            steps.append(Pause(1.5, "reconocimiento facial (simulado): visitante identificado"))
    if greeting:
        steps += intro
    steps += escort
    label = CATEGORIES[category] + (f" · {C.FLOORS[floor]} (piso {floor})" if floor is not None else "")
    return GuideTour(key, steps, label=label, guest=guest)
