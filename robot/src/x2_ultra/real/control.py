"""Lógica del X2 real sin ROS: velocidades de marcha, jog de brazos, reloj del robot y comprobación previa.

Todo lo que decide qué se manda al robot está aquí, separado del transporte (``link.py``) y de las ventanas
(``walk.py``, ``arms.py``), para poder probarlo sin robot (``tests/test_real_control.py``).

Valores tomados de los ensayos con el robot (g1_teleop, 19/10/2026) y de AimDK v1.1.0:
  - el MC sólo arranca la marcha con |vx|,|vy| ≥ 0,2 m/s o |wz| ≥ 0,1 rad/s (ejemplo ``mc_locomotion_velocity``);
  - sin manos instaladas hay que mandar ``hand_sub_mode=1`` con ``hand_pos=[0,0]``: con 0 el MC descarta todo;
  - en URS el MC sigue los brazos con un hundimiento estático de ≈0,03 rad bajo gravedad;
  - el reloj del robot va segundos por delante del PC: se estima con los sellos de los estados.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

from x2_ultra.robot.joints import ARM_JOINTS, JOINT_RANGE

MODE_STAND = "STAND_DEFAULT"
MODE_LOCOMOTION = "LOCOMOTION_DEFAULT"
MODE_URS = "UPPERBODY_REMOTE_SPLIT"
MODE_OFFROAD = "LOCOMOTION_STEP"      # "越野": el MC pasa solo al modo de velocidad STEP y lo deshace al salir
WALK_MODES = (MODE_STAND, MODE_LOCOMOTION, MODE_OFFROAD)
ACTION_RUNNING = 100
FSM_STABLE = 4          # fsm_state del MC; tras andar pasa ≈2-4 s en MOVING (5) y en él rechaza cambios de modo
FSM_SAFE = 6            # protección de seguridad del MC (caída u otra anomalía)
BODY_STAND = 1
BODY_UNKNOWN, BODY_SIT, BODY_SQUAT = 0, 4, 5
# En cuclillas el MC reclasifica la postura por la altura: 1 → 4 (sentado) → 5 (cuclilla) → 0 (desconocido)
# (robot real, 20/10). Tumbado (2, 3) o cayendo (6) nunca valen.
BODY_WHILE_SQUATTING = (BODY_STAND, BODY_SIT, BODY_SQUAT, BODY_UNKNOWN)
# Cuclilla (/aima/mc/body_pose, McBodyPose.stand_height): no es una altura sino una velocidad. Grabado del mando/app
# el 20/10: -0,5 mantenido baja servo_status.squat_offset a 0,1/s hasta -0,5 (abajo del todo, ~5 s); +0,5 lo sube
# hasta 0 (de pie); 0 lo deja donde está.
SQUAT_RATE = 0.5
SQUAT_LOW = -0.5
SQUAT_UP_TOL = 0.005      # offset por encima de esto = de pie
PLAYER_IDLE = 0
# Inventario de manos (tipo izq., tipo der., ejes izq., ejes der.) de /aima/hal/joint/hand/state.
HANDS_NONE = (0, 0, 0, 0)
HANDS_OMNIHAND = (1, 1, 10, 10)
HAND_SUB_MODE_CLAW = 1
HAND_SUB_MODE_DEXTEROUS = 2

MIN_LIN, MIN_ANG = 0.2, 0.1
# Por encima de 69 se compite con la fuente VR (70) y con el mando (80): el mando siempre debe poder retomar.
MIN_PRIORITY, MAX_PRIORITY = 20, 69
MIN_BATTERY = 30
MC_STATE_TIMEOUT_S = 0.5
FEEDBACK_TIMEOUT_S = 0.15


def wrong(value: float, low: float, high: float) -> bool:
    return not (math.isfinite(value) and low <= value <= high)


# --- marcha ------------------------------------------------------------------------------------------------------

# Topes duros. El MC en modo LOW admite adelante 0,8 m/s y lateral/giro ±1,0. En el robot (20/10/2026) lateral
# 0,375 m/s y giro 0,45 rad/s no dieron ni un paso desde parado: la guía de AimDK pide ≈0,5-0,7 m/s de lateral para
# el primer paso, y el giro en el sitio necesitó más de lo que dice (≈0,03-0,05 rad/s).
# Los topes son los límites del modo de velocidad LOW (el de fábrica): adelante 0,8 m/s, lateral y giro ±1,0.
WALK_CAPS = {"vx": 0.8, "vx_back": 0.5, "vy": 0.8, "wz": 1.0}
# Umbral de arranque desde parado medido en el robot el 20/10: lateral 0,375 y giro 0,45 no dieron un paso;
# 0,5 y 0,6 sí. Bajar el nivel de velocidad nunca deja un eje por debajo de esto (ni de su valor elegido).
WALK_START = {"vx": 0.2, "vx_back": 0.2, "vy": 0.5, "wz": 0.6}
# Niveles de velocidad (teclas + / -): factor sobre las velocidades elegidas, sin pasar de WALK_CAPS.
SPEED_LEVELS = (0.6, 0.8, 1.0, 1.25, 1.5, 1.75, 2.0)
DEFAULT_LEVEL = 2


@dataclass
class WalkLimits:
    vx: float = 0.3          # adelante [m/s]
    vx_back: float = 0.2     # atrás [m/s]
    vy: float = 0.5          # lateral [m/s]
    wz: float = 0.6          # giro [rad/s]
    fast: float = 1.5        # factor con Mayús (sin pasar de WALK_CAPS)
    accel_lin: float = 0.5   # rampa [m/s²] desde el umbral de arranque
    accel_ang: float = 1.0   # rampa [rad/s²]

    def validate(self) -> None:
        for name, minimum in (("vx", MIN_LIN), ("vx_back", MIN_LIN), ("vy", MIN_LIN), ("wz", MIN_ANG)):
            value = getattr(self, name)
            if wrong(value, minimum, WALK_CAPS[name]):
                raise ValueError(f"{name}={value} fuera de [{minimum}, {WALK_CAPS[name]}]")
        if wrong(self.fast, 1.0, 3.0) or wrong(self.accel_lin, 0.05, 2.0) or wrong(self.accel_ang, 0.05, 4.0):
            raise ValueError("fast en [1, 3], accel_lin en [0,05, 2] y accel_ang en [0,05, 4]")


class VelocityShaper:
    """Teclas pulsadas → (vx, vy, wz) para el MC.

    Cada eje vale 0 o al menos su umbral de arranque (por debajo el MC no se mueve), sube en rampa hasta el
    objetivo y se pone a 0 en cuanto se suelta la tecla. Un cambio de sentido pasa antes por 0.
    """
    AXES = (("vx", MIN_LIN), ("vy", MIN_LIN), ("wz", MIN_ANG))

    def __init__(self, limits: WalkLimits):
        limits.validate()
        self.limits = limits
        self.level = DEFAULT_LEVEL
        self.current = [0.0, 0.0, 0.0]

    def axis_speeds(self, fast: bool = False) -> dict[str, float]:
        """Velocidad de cada sentido con el nivel actual (y Mayús): entre el arranque y el tope."""
        k = SPEED_LEVELS[self.level] * (self.limits.fast if fast else 1.0)
        result = {}
        for name in ("vx", "vx_back", "vy", "wz"):
            base = getattr(self.limits, name)
            result[name] = min(WALK_CAPS[name], max(min(base, WALK_START[name]), base * k))
        return result

    def change_level(self, step: int) -> int:
        self.level = min(len(SPEED_LEVELS) - 1, max(0, self.level + step))
        return self.level

    def targets(self, keys: set[str], fast: bool) -> tuple[float, float, float]:
        v = self.axis_speeds(fast)

        def axis(pos: bool, neg: bool, v_pos: float, v_neg: float) -> float:
            if pos == neg:
                return 0.0
            return v_pos if pos else -v_neg

        return (axis("w" in keys, "s" in keys, v["vx"], v["vx_back"]),
                axis("a" in keys, "d" in keys, v["vy"], v["vy"]),
                axis("q" in keys, "e" in keys, v["wz"], v["wz"]))

    def step(self, keys: set[str], fast: bool, dt: float) -> tuple[float, float, float]:
        dt = min(max(dt, 0.0), 0.1)
        accel = (self.limits.accel_lin, self.limits.accel_lin, self.limits.accel_ang)
        for i, target in enumerate(self.targets(keys, fast)):
            cur, minimum = self.current[i], self.AXES[i][1]
            if target == 0.0 or (cur != 0.0 and math.copysign(1, cur) != math.copysign(1, target)):
                cur = 0.0
            elif cur == 0.0:
                cur = math.copysign(min(minimum, abs(target)), target)
            else:
                cur += max(-accel[i] * dt, min(accel[i] * dt, target - cur))
            self.current[i] = cur
        return tuple(self.current)

    def stop(self) -> None:
        self.current = [0.0, 0.0, 0.0]


# --- brazos ------------------------------------------------------------------------------------------------------

LIMIT_MARGIN = 0.05
DEFAULT_ENVELOPE, MAX_ENVELOPE = 0.4, 0.6
DEFAULT_ARM_SPEED, MAX_ARM_SPEED = 0.15, 0.35


class ArmJog:
    """Objetivo de los 14 ejes de brazo, movido eje a eje desde la postura medida al armar (``anchor``).

    Cada eje queda dentro de su rango URDF menos ``LIMIT_MARGIN`` y a no más de ``envelope`` del ancla. Si el ancla
    ya está dentro del margen de un tope (el hombro roll izquierdo suele quedar a ≈0,02 rad), el eje sólo puede
    alejarse de él: nunca se ordena un salto para "entrar" en el rango.
    """

    def __init__(self, anchor: dict[str, float], *, envelope: float = DEFAULT_ENVELOPE,
                 speed: float = DEFAULT_ARM_SPEED):
        if set(anchor) != set(ARM_JOINTS) or any(wrong(v, -4.0, 4.0) for v in anchor.values()):
            raise ValueError("el ancla necesita los 14 ejes de brazo con valores finitos")
        if wrong(envelope, 0.01, MAX_ENVELOPE):
            raise ValueError(f"envolvente fuera de (0, {MAX_ENVELOPE}] rad")
        if wrong(speed, 0.01, MAX_ARM_SPEED):
            raise ValueError(f"velocidad fuera de (0, {MAX_ARM_SPEED}] rad/s")
        self.anchor = dict(anchor)
        self.target = dict(anchor)
        self.speed = speed
        self.bounds = {}
        for name, a in anchor.items():
            low, high = JOINT_RANGE[name]
            self.bounds[name] = (min(a, max(low + LIMIT_MARGIN, a - envelope)),
                                 max(a, min(high - LIMIT_MARGIN, a + envelope)))

    def jog(self, name: str, direction: int, dt: float) -> None:
        if name not in self.target or direction not in (-1, 1):
            raise ValueError("eje o sentido inválido")
        low, high = self.bounds[name]
        step = self.speed * min(max(dt, 0.0), 0.1)
        self.target[name] = min(high, max(low, self.target[name] + direction * step))

    def home_step(self, dt: float) -> bool:
        """Acerca todos los ejes al ancla a la velocidad de jog; True cuando ya están en ella."""
        step = self.speed * min(max(dt, 0.0), 0.1)
        for name, a in self.anchor.items():
            self.target[name] += max(-step, min(step, a - self.target[name]))
        return self.distance_home() < 1e-9

    def distance_home(self) -> float:
        return max(abs(self.target[n] - self.anchor[n]) for n in ARM_JOINTS)

    def positions(self) -> list[float]:
        return [self.target[n] for n in ARM_JOINTS]


class TrackingMonitor:
    """Falla si algún eje medido se aparta del ordenado más de ``limit`` durante ``hold_s`` seguidos.

    El hundimiento normal es ≈0,03 rad; un error grande y sostenido significa que el MC no sigue las órdenes
    (descartadas, otra fuente manda, bloqueo físico) y seguir moviendo el objetivo sólo lo agrandaría.
    """

    def __init__(self, limit: float = 0.12, hold_s: float = 0.5):
        self.limit, self.hold_s = limit, hold_s
        self.since: float | None = None
        self.worst = ("", 0.0)

    def update(self, target: dict[str, float], measured: dict[str, float], now_s: float) -> bool:
        name = max(ARM_JOINTS, key=lambda n: abs(target[n] - measured[n]))
        self.worst = (name, abs(target[name] - measured[name]))
        if self.worst[1] <= self.limit:
            self.since = None
            return False
        if self.since is None:
            self.since = now_s
        return now_s - self.since >= self.hold_s


# --- reloj del robot ---------------------------------------------------------------------------------------------

class ClockOffsetEstimator:
    """Desfase reloj del robot − reloj del PC a partir de los sellos de los estados de articulación.

    Cada muestra es sello en origen − hora de recepción en el PC = desfase − retardo de entrega, así que el máximo
    de la ventana es la estimación menos retrasada. Sin estimación válida no hay hora del robot: los programas
    se niegan a sellar órdenes con ella. (Mismo método que el gateway de g1_teleop, probado con el robot.)
    """

    def __init__(self, *, window_ns: int = 2_000_000_000, min_samples: int = 20,
                 max_spread_ns: int = 50_000_000, max_age_ns: int = 200_000_000):
        self.window_ns, self.min_samples = window_ns, min_samples
        self.max_spread_ns, self.max_age_ns = max_spread_ns, max_age_ns
        self.samples: deque[tuple[int, int]] = deque()

    def add(self, source_wall_ns: int, receipt_wall_ns: int, receipt_mono_ns: int) -> None:
        self.samples.append((receipt_mono_ns, source_wall_ns - receipt_wall_ns))
        self._prune(receipt_mono_ns)

    def _prune(self, now_mono_ns: int) -> None:
        while self.samples and now_mono_ns - self.samples[0][0] > self.window_ns:
            self.samples.popleft()

    def estimate(self, now_mono_ns: int) -> dict:
        self._prune(now_mono_ns)
        deltas = [d for _, d in self.samples]
        spread = max(deltas) - min(deltas) if deltas else None
        valid = bool(len(deltas) >= self.min_samples and 0 <= now_mono_ns - self.samples[-1][0] <= self.max_age_ns
                     and spread <= self.max_spread_ns)
        return {"offset_ns": max(deltas) if valid else None, "spread_ns": spread, "samples": len(deltas)}


# --- comprobación previa -----------------------------------------------------------------------------------------

@dataclass
class Observed:
    """Lo último que se ha visto del robot (lo rellena ``link.RobotLink.observed``)."""
    mode: str = ""
    action_status: int | None = None
    input_source: str = ""
    body_status: int | None = None
    player_state: int | None = None
    motion: str = ""
    mc_age_s: float | None = None
    fsm: int | None = None
    squat_offset: float | None = None
    squat_enable: bool = False
    battery: int | None = None
    clock_offset_ms: float | None = None
    arm_age_s: float | None = None
    head_age_s: float | None = None
    hands: tuple[int, int, int, int] | None = None
    upper_body_publishers: int = 0     # sin contar el nuestro
    velocity_publishers: int = 0


def crouched(obs: Observed) -> bool:
    return obs.squat_offset is not None and obs.squat_offset < -SQUAT_UP_TOL


def standing(obs: Observed) -> bool:
    """De pie, o en una cuclilla del MC (durante la cual body_status pasa por 4, 5 y 0 según la altura)."""
    if obs.body_status == BODY_STAND:
        return True
    return (obs.squat_enable or crouched(obs)) and obs.body_status in BODY_WHILE_SQUATTING


def preflight(obs: Observed, program: str, *, need_clock: bool, ignore_battery: bool = False) -> list[str]:
    """Motivos para no armar (lista vacía = se puede). ``program`` es ``"andar"`` o ``"brazos"``."""
    problems = []
    allowed = WALK_MODES if program == "andar" else (MODE_STAND, MODE_URS)
    if obs.mc_age_s is None or obs.mc_age_s > MC_STATE_TIMEOUT_S:
        problems.append("no llega el estado del MC (/aima/mc/common/state): ¿cable y 'nmcli con up \"X2 robot\"'?")
    else:
        if obs.mode not in allowed:
            problems.append(f"MC en {obs.mode or '?'}; hace falta {' o '.join(allowed)}")
        elif obs.action_status != ACTION_RUNNING:
            problems.append(f"el modo {obs.mode} aún no está en RUNNING (estado {obs.action_status})")
        if not standing(obs):
            problems.append(f"el MC no informa de pie (body_status={obs.body_status}, hace falta 1)")
        if obs.player_state != PLAYER_IDLE:
            problems.append(f"el reproductor de movimientos del MC no está libre ({obs.player_state}, {obs.motion!r})")
    if obs.battery is None:
        if not ignore_battery:
            problems.append("batería desconocida (/aima/hal/pmu/state); comprobarla y usar --ignorar-bateria")
    elif obs.battery < MIN_BATTERY:
        problems.append(f"batería al {obs.battery} % (< {MIN_BATTERY} %)")
    if need_clock and obs.clock_offset_ms is None:
        problems.append("sin estimación del reloj del robot (faltan estados de brazo frescos y regulares)")
    if program == "brazos":
        if obs.arm_age_s is None or obs.arm_age_s > FEEDBACK_TIMEOUT_S:
            problems.append("no llegan estados frescos de los brazos")
        if obs.head_age_s is None or obs.head_age_s > FEEDBACK_TIMEOUT_S:
            problems.append("no llegan estados frescos de la cabeza")
        if crouched(obs):
            problems.append(f"el robot está en cuclillas ({obs.squat_offset:+.2f}): levantarlo antes de los brazos")
        if obs.hands not in (HANDS_NONE, HANDS_OMNIHAND):
            problems.append(f"inventario de manos inesperado {obs.hands} (esperado sin manos u OmniHand)")
        # El tren superior no tiene arbitraje de fuentes: dos escritores se pelearían por los brazos.
        # (La velocidad sí: el MC elige una fuente por prioridad, así que otros publicadores no impiden andar.)
        if obs.upper_body_publishers > 0:
            problems.append(f"hay otro publicador en /mc/upper_body_command ({obs.upper_body_publishers})")
    return problems
