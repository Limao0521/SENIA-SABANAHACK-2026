"""AimdkBackend: X2 Ultra real vía AimDK ROS 2 (lecciones del robot + motor_fix aplicadas).

Diseño (spec T5):
- brazos vía URS (``UPPERBODY_REMOTE_SPLIT``) a 50 Hz por ``/mc/upper_body_command`` (fuente
  ``sdk_*``, prioridad 65 < mando 80); cintura/cabeza vía ``McBodyPose`` (control de signo con
  compensación de inflight, como c07/c12);
- emotes reproducidos **cinemáticamente por URS** desde los CSV de AgiBot (evita los cambios de
  modo URS↔preset, que en el robot dan ``MotionBusy`` code 9 al salir de URS); ruta opcional de
  preset del MC (``use_presets=True``) con el layout de petición v1.1.4 (``input_source`` incluido;
  el MC v1.0.0 del gemelo necesita el helper sin ``input_source`` → lo usa ``TwinBackend``);
- locomoción por ``/aima/mc/locomotion/velocity`` SOLO sin gestos activos (bloqueo mutuo) y por
  rutas abiertas (vx, wz, duración) inyectables: sin odometría en el PC no hay lazo cerrado;
- límite por articulación 10 rad/s fuera de emotes (emotes: brazos 20, cintura 20, cabeza 30 —
  spec), límite de aceleración 40 rad/s² (``Limiter`` de motor_fix);
- interlock de caída (motor_fix diag_fall): pelvis < 0.62 siempre; tilt > 12° salvo preset del MC
  reproduciéndose con pelvis ≥ 0.66; tilt > 25° siempre; debounce 3 ticks → parar, mantener,
  DELETE + STAND_DEFAULT en el worker y rehusar todo hasta reset;
- TODAS las llamadas bloqueantes a servicios del MC van en un hilo worker (``Svc``, fix 5): el
  bucle de 50 Hz sólo envía y consulta; tras un preset, re-ADD de la fuente URS sólo con el
  reproductor en idle + settle (fix 1); nada de cintura durante presets (fix 2);
- **DRY_RUN por defecto**: registra las órdenes en ``link.commands`` y NUNCA publica; sólo se
  arma con ``arm=True`` (CLI ``--arm``) y ``X2_REAL=robot``.
- Bring-up SIN grúa/pórtico (el usuario no dispone de gantry): dry-run → baja velocidad con
  e-stop a mano → completo (DEPLOY_X2_REAL.md). El interlock de caída está SIEMPRE activo.
"""
from __future__ import annotations

import argparse
import math
import os
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from x2_ultra import real as _real_pkg  # noqa: F401  (garantiza el paquete; rclpy NO se importa aquí)
from x2_ultra.hri_runtime.backends.base import (EMOTE_VMAX, JOINT_AMAX, JOINT_VMAX, TICK,
                                                WAIST_BOUNDS, WAIST_BOUNDS_GENTLE, WAIST_SLEW,
                                                WAIST_SLEW_GENTLE)
from x2_ultra.real import control as K
from x2_ultra.robot import joints as J

# Posturas (mismo convenio que show/hri/motion.py y emote_lib del gemelo).
D_ARMS = np.array([0.4, 0.0, 0.0, -1.2, 0.0, 0.0, 0.0, 0.4, 0.0, 0.0, -1.2, 0.0, 0.0, 0.0])
STREAM_HOLD_S = 0.3        # mantener el último cuadro SONIC si el stream se retrasa
SONIC_RAMP_IN_S = 0.6      # rampa min-jerk al entrar a SONIC desde cualquier otra fuente
SONIC_RAMP_OUT_S = 0.8     # rampa a READY al cesar el stream
READY_ARMS = np.array([0.2, 0.2, 0.0, -0.6, 0.0, 0.0, 0.0, 0.2, -0.2, 0.0, -0.6, 0.0, 0.0, 0.0])
REST_ARMS = np.array([J.DEFAULT_POSE[n] for n in J.ARM_JOINTS])
# Límites articulares URDF con margen 0.05 (emote_lib LIMS).
_MARGIN = 0.05
LO = np.array([J.JOINT_RANGE[n][0] + _MARGIN for n in J.ARM_JOINTS])
HI = np.array([J.JOINT_RANGE[n][1] - _MARGIN for n in J.ARM_JOINTS])
# Cintura/cabeza por McBodyPose: (twist_waist, bend_forward, bend_sideways, turn_head).
BP_FIELDS = ("twist_waist", "bend_forward", "bend_sideways", "turn_head")
BP_SPEED = np.array([0.81, 0.45, 0.92, 1.30])   # rad/s logrados (c07/c12)
BP_DEADBAND = BP_SPEED * 0.02
BP_U = 0.3                                      # amplitud de la orden de signo
BP_HEAD_KP = 6.0    # FIX HE: ganancia proporcional de turn_head (satura a ±BP_U; sin overshoot
                    # del bang-bang+inflight, que con planta integradora cancelaba RIGHT ⇒ u→0)
HEAD_YAW_LIM, HEAD_PITCH_LO, HEAD_PITCH_HI = 0.4887, -0.4014, 0.4887   # rad: límites URS/HAL
HEAD_RATE = 10.0    # rad/s: tasa máx. de la orden de posición HAL de cabeza
EMOTE_BLEND_IN, EMOTE_BLEND_OUT = 0.5, 0.8      # s: FIX B mezclas min-jerk entrada/recuperación
# Interlock de caída (motor_fix SAFETY FIXES 3/4).
TILT_FALL, PELVIS_FALL, FALL_DEB, TILT_HARD = 12.0, 0.62, 3, 25.0
PELVIS_NOMINAL = 0.72                           # pelvis de pie (gemelo); base de la altura relativa
PELVIS_PRESET_OK = 0.66                         # tilt>12 tolerado sólo con preset busy y pelvis≥0.66
TILT_GATE, ARM_GATE, SETTLE_S = 6.0, 0.05, 0.2  # puerta pre-preset y settle post-idle (fix 1/4)
W_RATE_MAX = 1.2                                # rad/s: slew global de la referencia de cintura (fix 2)


@dataclass
class AimdkConfig:
    arm: bool = False                  # True ⇒ publica de verdad (requiere X2_REAL=robot y --arm)
    ros_domain: int | None = None      # None ⇒ ROS_DOMAIN_ID del entorno (cable a 10.0.1.41)
    robot: str = "10.0.1.41"
    priority: int = 65                 # fuente sdk_* (20..69; mando 80, app 60)
    stamp_mode: str = "cero"           # sello 0: probado con los brazos el 19/10
    use_presets: bool = False          # ruta opcional: presets del MC en vez de CSV cinemático
    preset_layout: str = "v1.1.4"      # "v1.1.4" (con input_source) | "mc_v1.0.0" (helper gemelo)
    body_pose_domain: int | None = None  # gemelo: 77 (MC privado); robot: None (dominio normal)
    joint_vmax: float = JOINT_VMAX     # 10 rad/s fuera de emotes (spec)
    emote_vmax: float = EMOTE_VMAX["arms"]
    amax: float = JOINT_AMAX
    ignore_battery: bool = False
    min_battery: int = K.MIN_BATTERY
    routes: dict = field(default_factory=dict)   # área → [(vx, wz, dur_s), ...] en lazo abierto
    vx_cap: float = K.WALK_CAPS["vx"]
    wz_cap: float = K.WALK_CAPS["wz"]
    head_channel: str = "body_pose"    # FIX A: "body_pose" (turn_head en McBodyPose) | "hal"
                                       # (posición yaw+pitch a /aima/hal/joint/head/command)
    waist_mode: str = "off"            # "off" (defecto: cintura bloqueada) | "slew" | "lowpass"
    waist_lp_hz: float = 1.5

    def __post_init__(self):
        env = os.environ.get("HRI_HEAD_CHANNEL", "").strip().lower()
        if env in ("body_pose", "hal"):
            self.head_channel = env
        wm = os.environ.get("HRI_WAIST_MODE", "").strip().lower()
        if wm in ("slew", "lowpass", "off"):
            self.waist_mode = wm
        try:
            self.waist_lp_hz = float(os.environ.get("HRI_WAIST_LP_HZ", self.waist_lp_hz))
        except ValueError:
            pass


def _clamp_w(w, prev, bounds, rate):
    """fix 2: recorte de amplitud por eje + slew de la referencia de cintura contra la anterior."""
    w = np.clip(np.asarray(w, dtype=float).reshape(-1)[:4], -np.asarray(bounds), np.asarray(bounds))
    return prev + np.clip(w - prev, -np.asarray(rate) * TICK, np.asarray(rate) * TICK)


class Limiter:
    """Seguidor con límite de velocidad Y aceleración (motor_fix): v_des = clip(e/dt, ±vmax,
    ±sqrt(2·0.9·amax·|e|)); v += clip(v_des − v, ±amax·dt)."""

    def __init__(self, vmax: float, amax: float, n: int = 14):
        self.vmax, self.amax = float(vmax), float(amax)
        self.p: np.ndarray | None = None
        self.v = np.zeros(n)
        self.nlim = 0

    def reset(self, q) -> None:
        self.p = np.asarray(q, dtype=float).copy()
        self.v = np.zeros_like(self.p)

    def step(self, tgt, vmax: float | None = None):
        vmax = self.vmax if vmax is None else float(vmax)
        tgt = np.asarray(tgt, dtype=float)
        if self.p is None:
            self.reset(tgt)
        e = tgt - self.p
        vd = np.sign(e) * np.minimum(np.minimum(np.abs(e) / TICK, vmax),
                                     np.sqrt(2.0 * 0.9 * self.amax * np.abs(e)))
        self.v = self.v + np.clip(vd - self.v, -self.amax * TICK, self.amax * TICK)
        self.p = self.p + self.v * TICK
        hit = bool(np.any(np.abs(vd - self.v) > 1e-9) or np.any(np.abs(e / TICK) > vmax + 1e-9))
        self.nlim += hit
        return self.p.copy(), hit


class FallInterlock:
    """fix 3: firma de caída validada en c12 — la venia del fabricante llega a 21° con pelvis 0.693,
    así que tilt>12 solo cuenta si NO hay preset ocupado con pelvis≥0.66; pelvis<0.62 y tilt>25
    siempre. Debounce de 3 ticks. Siempre activo: no hay modo grúa/pórtico (el usuario no
    dispone de gantry; el bring-up es dry-run → baja velocidad con e-stop → completo)."""

    def __init__(self):
        self.tripped = False
        self.deb = 0

    def update(self, tilt: float | None, pelvis: float | None, preset_busy: bool) -> bool:
        if self.tripped:
            return self.tripped
        pel_hi = pelvis is not None and pelvis >= PELVIS_PRESET_OK
        fall_now = ((pelvis is not None and pelvis < PELVIS_FALL)
                    or (tilt is not None and tilt > TILT_HARD)
                    or (tilt is not None and tilt > TILT_FALL and not (preset_busy and pel_hi)))
        self.deb = self.deb + 1 if fall_now else 0
        if self.deb >= FALL_DEB:
            self.tripped = True
        return self.tripped


class Svc:
    """fix 5: un hilo worker ejecuta las llamadas bloqueantes al MC en orden de envío; el bucle de
    50 Hz sólo submit/poll. Cada clave lleva un presupuesto (svc_timeout al pasarse)."""

    def __init__(self, ev):
        self.ev = ev
        self.q: queue.Queue = queue.Queue()
        self.lk = threading.Lock()
        self.st: dict = {}
        self.t_sub: dict = {}
        self.to_logged: set = set()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            key, fn = self.q.get()
            t0, err = time.monotonic(), None
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - el worker nunca muere
                err = str(exc)
            with self.lk:
                self.st[key] = {"dur_s": time.monotonic() - t0, "err": err}

    def submit(self, key: str, fn, budget: float = 6.0) -> bool:
        with self.lk:
            if key in self.st or key in self.t_sub:
                return False
            self.t_sub[key] = (time.monotonic(), budget)
        self.q.put((key, fn))
        return True

    def get(self, key: str):
        with self.lk:
            return self.st.get(key)

    def tick(self, now: float) -> None:
        with self.lk:
            items = [(k, v) for k, v in self.t_sub.items() if k not in self.st and k not in self.to_logged]
        for k, (t0, budget) in items:
            if now - t0 > budget:
                self.to_logged.add(k)
                self.ev("svc_timeout", key=k, elapsed_s=now - t0, budget_s=budget)


class DryLink:
    """Enlace simulado: MISMA API que el enlace real pero NUNCA publica — anota cada orden en
    ``commands`` (lista de dicts {"kind", ...}). Estado del robot inyectable para pruebas."""

    def __init__(self):
        self.commands: list[dict] = []
        self.obs = K.Observed(mode=K.MODE_STAND, action_status=K.ACTION_RUNNING, fsm=K.FSM_STABLE,
                              body_status=K.BODY_STAND, player_state=K.PLAYER_IDLE, mc_age_s=0.0,
                              battery=100, input_source="", motion="")
        self.pose = {"z": 0.72, "tilt": 0.0}
        self._arms = {n: float(READY_ARMS[i]) for i, n in enumerate(J.ARM_JOINTS)}
        self._head = [0.0, 0.0]
        self.wh_state = {"waist": [0.0, 0.0, 0.0]}   # medida simulada de cintura (sign-control)
        self.source = "sdk_x2_ultra_hri_dry"

    def log(self, kind: str, **kw) -> None:
        self.commands.append({"t": time.monotonic(), "kind": kind, **kw})

    # --- API de publicación -------------------------------------------------------------
    def publish_upper_body(self, head, arms, hand_sub_mode, hand_pos, stamp_mode) -> None:
        self.log("upper_body", head=[float(v) for v in head], arms=[float(v) for v in arms],
                 hand_sub_mode=int(hand_sub_mode))
        self._arms = dict(zip(J.ARM_JOINTS, [float(v) for v in arms]))

    def publish_velocity(self, vx, vy, wz, stamp_mode) -> None:
        self.log("velocity", vx=float(vx), vy=float(vy), wz=float(wz))

    def send_body_pose(self, u4) -> None:
        u = np.asarray(u4, dtype=float).reshape(-1)[:4]
        self.log("body_pose", u=[float(v) for v in u])
        # FIX HE: modelo simple — la cabeza INTEGRA turn_head·BP_SPEED (la planta real es un
        # lazo de velocidad); sin esto la medida no se mueve y la prueba de signo no significa.
        self._head[0] = float(np.clip(self._head[0] + u[3] * BP_SPEED[3] * TICK,
                                      -HEAD_YAW_LIM, HEAD_YAW_LIM))

    def publish_head_cmd(self, yaw: float, pitch: float) -> None:
        """FIX A (head_channel=hal): en dry-run sólo registra la orden de posición."""
        self.log("head_hal", yaw=float(yaw), pitch=float(pitch))

    def preset_set(self, motion: int, area: int, layout: str) -> None:
        self.log("preset_set", motion=int(motion), area=int(area), layout=layout)

    # --- servicios (en el worker; aquí son instantáneos y anotados) -----------------------
    def register_source(self, priority: int) -> None:
        self.log("register_source", priority=int(priority))

    def delete_source(self):
        self.log("delete_source")
        return None

    def request_mode(self, desc: str, busy_retry_s: float = 0.0) -> None:
        self.log("request_mode", mode=desc)
        self.obs.mode = desc

    def wait_mode(self, desc: str, timeout_s: float) -> bool:
        return self.obs.mode == desc

    def wait_stable(self, timeout_s: float) -> bool:
        return True

    # --- lectura -------------------------------------------------------------------------
    def observed(self, graph: bool = True) -> K.Observed:
        return self.obs

    def latest(self, group: str, max_age_s: float = K.FEEDBACK_TIMEOUT_S):
        if group == "arms":
            return dict(self._arms)
        if group == "head":
            return dict(zip(J.HEAD_JOINTS, self._head))
        return None

    def hand_positions(self):
        return None

    def odometry(self):
        return None

    def close(self) -> None:
        self.log("close")


class _LiveLink:
    """Enlace real: RobotLink (teleop) + pose por odometría + McBodyPose (overlay) + preset.

    Sólo se construye con ``arm=True`` y dentro del entorno AimDK (contenedor Humble,
    ``X2_REAL=robot``). El overlay ``aimdk_msgs`` con McBodyPose hace falta para cintura/cabeza.
    """

    def __init__(self, cfg: AimdkConfig, name: str = "hri"):
        from x2_ultra.real.link import RobotLink
        import rclpy
        from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
        from nav_msgs.msg import Odometry
        from aimdk_msgs import msg as AM
        self.cfg = cfg
        self.link = RobotLink(name, publish_velocity=True, publish_upper_body=True)
        self.source = self.link.source
        self.pose = {"z": None, "tilt": None, "t": None, "src": None}
        self._t_hal_odom = -1e9
        self._leg_z = None                 # z crudo de /aima/mc/leg_odometry (lidar del pecho)
        self.leg_z_ref = None              # referencia de "de pie" (recalibrate_height)

        def tilt_of(q):
            return math.degrees(2 * math.asin(min(1.0, math.hypot(q.x, q.y))))

        def on_odom(m):
            p, q = m.pose.pose.position, m.pose.pose.orientation
            self._t_hal_odom = time.monotonic()
            self.pose.update(z=p.z, tilt=tilt_of(q), t=self._t_hal_odom, src="hal_odom")

        # Respaldo a bordo (firmware v1.1.4 no publica /aima/hal/odom/state, medido 2026-10-24):
        # inclinación = IMU del torso (base_link, ~390 Hz; ~3° de pie) y altura RELATIVA desde
        # /aima/mc/leg_odometry (marco del lidar del pecho, ~0.91 m de pie): pelvis estimada =
        # PELVIS_NOMINAL + (z − z_ref), con z_ref tomada de pie (recalibrate_height al armar).
        def hal_odom_fresh():
            return time.monotonic() - self._t_hal_odom < 0.5

        def on_imu(m):
            if not hal_odom_fresh():
                self.pose.update(tilt=tilt_of(m.orientation), t=time.monotonic(), src="imu_torso+leg_odom")

        def on_leg(m):
            self._leg_z = float(m.pose.pose.position.z)
            if self.leg_z_ref is None:
                self.leg_z_ref = self._leg_z
            if not hal_odom_fresh():
                self.pose.update(z=PELVIS_NOMINAL + self._leg_z - self.leg_z_ref)

        sensor = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT,
                            durability=DurabilityPolicy.VOLATILE)
        self.link.node.create_subscription(Odometry, "/aima/hal/odom/state", on_odom, sensor)
        from sensor_msgs.msg import Imu
        self.link.node.create_subscription(Imu, "/aima/hal/imu/torso/state", on_imu, sensor)
        self.link.node.create_subscription(Odometry, "/aima/mc/leg_odometry", on_leg, sensor)
        self.wh_state: dict = {}
        self.link.node.create_subscription(AM.JointStateArray, "/aima/hal/joint/waist/state",
                                           lambda m: self.wh_state.update(waist=[j.position for j in m.joints]),
                                           sensor)
        # McBodyPose: overlay (robot: dominio normal; gemelo: dominio privado 77 del MC).
        self.bp_pub = None
        body_pose = getattr(AM, "McBodyPose", None)
        if body_pose is not None:
            if cfg.body_pose_domain is None:
                self.bp_pub = self.link.node.create_publisher(body_pose, "/aima/mc/body_pose", sensor)
            else:
                self.ctx = rclpy.Context()
                rclpy.init(context=self.ctx, domain_id=int(cfg.body_pose_domain))
                self.node_bp = rclpy.create_node(f"{name}_waist", context=self.ctx)
                self.bp_pub = self.node_bp.create_publisher(body_pose, "/aima/mc/body_pose", sensor)
        # FIX A (head_channel=hal): posición de cabeza por el topic HAL público (JointCommandArray,
        # mismo estilo que los demás grupos: x2_ultra.robot.joints HEAD_JOINTS).
        self.head_pub = None
        jca = getattr(AM, "JointCommandArray", None)
        if jca is not None:
            self.head_pub = self.link.node.create_publisher(jca, "/aima/hal/joint/head/command", sensor)
        self.hand_mode, self.hand_pos = 1, [0.0, 0.0]
        self.head = [0.0, 0.0]

    def recalibrate_height(self) -> bool:
        """Toma la altura actual de leg_odometry como "de pie" (llamar solo con el robot de pie)."""
        if self._leg_z is None:
            return False
        self.leg_z_ref = self._leg_z
        if time.monotonic() - self._t_hal_odom >= 0.5:
            self.pose["z"] = PELVIS_NOMINAL
        return True

    def hand_init(self) -> None:
        h = self.link.hand_positions()
        self.hand_mode, self.hand_pos = (2, h) if h and len(h) == 20 else (1, [0.0, 0.0])
        hd = self.link.latest("head")
        if hd:
            self.head = [hd["head_yaw_joint"], hd["head_pitch_joint"]]

    # --- API espejo de DryLink -----------------------------------------------------------
    def log(self, kind, **kw):  # pragma: no cover - el enlace real no acumula comandos
        pass

    def publish_upper_body(self, head, arms, hand_sub_mode, hand_pos, stamp_mode):
        self.link.publish_upper_body(list(head), list(arms), int(hand_sub_mode), list(hand_pos), stamp_mode)

    def publish_velocity(self, vx, vy, wz, stamp_mode):
        self.link.publish_velocity(float(vx), float(vy), float(wz), stamp_mode)

    def send_body_pose(self, u4):
        if self.bp_pub is None:
            return
        from aimdk_msgs import msg as AM
        m = AM.McBodyPose()
        m.source = self.link.source
        for f, v in zip(BP_FIELDS, np.asarray(u4, dtype=float).reshape(-1)[:4]):
            setattr(m, f, float(v))
        self.bp_pub.publish(m)

    def publish_head_cmd(self, yaw, pitch):
        """FIX A: JointCommandArray{HEAD_JOINTS} → /aima/hal/joint/head/command (posición)."""
        if self.head_pub is None:
            return
        from aimdk_msgs.msg import JointCommandArray, JointState
        m = JointCommandArray()
        m.joints = [JointState(name=n, position=float(p), velocity=0.0, effort=0.0)
                    for n, p in zip(J.HEAD_JOINTS, (yaw, pitch))]
        self.head_pub.publish(m)

    def preset_set(self, motion, area, layout):
        # v1.1.4 (robot): SetMcPresetMotion con input_source; sello 0 = reproducir ya.
        S = self.link.S

        def make():
            req = S.SetMcPresetMotion.Request()
            req.header.stamp.sec, req.header.stamp.nanosec = 0, 0
            req.input_source.name, req.input_source.priority, req.input_source.timeout = \
                self.link.source, self.cfg.priority, 1000
            req.motion.value, req.area.value, req.interrupt = int(motion), int(area), False
            return req

        self.link.send(self.link.client(S.SetMcPresetMotion, "/aimdk_5Fmsgs/srv/SetMcPresetMotion"),
                       make, "SetMcPresetMotion")

    def register_source(self, priority):
        self.link.register_source(int(priority))

    def delete_source(self):
        return self.link.delete_source()

    def request_mode(self, desc, busy_retry_s=0.0):
        self.link.request_mode(desc, busy_retry_s=busy_retry_s)

    def wait_mode(self, desc, timeout_s):
        return self.link.wait_mode(desc, timeout_s)

    def wait_stable(self, timeout_s):
        return self.link.wait_stable(timeout_s)

    def observed(self, graph=True):
        return self.link.observed(graph=graph)

    def latest(self, group, max_age_s=K.FEEDBACK_TIMEOUT_S):
        return self.link.latest(group, max_age_s)

    def hand_positions(self):
        return self.link.hand_positions()

    def odometry(self):
        return self.link.odometry()

    def close(self):
        try:
            self.link.delete_source()
        except Exception:
            pass
        self.link.close()


def _pad(v, n: int) -> np.ndarray:
    v = np.asarray(v, dtype=float).reshape(-1)[:n]
    return np.concatenate([v, np.zeros(n - v.size)])


class _EmoteTrack:
    """Emote en curso: cinemático por URS (CSV) o preset del MC (use_presets)."""

    def __init__(self, motion_id: int, area: int, t_dispatch: float, csv=None, preset: bool = False):
        self.motion_id, self.area = int(motion_id), int(area)
        self.t_dispatch = float(t_dispatch)     # reloj monotónico del backend
        self.csv = csv
        self.preset = preset
        self.phase = "fade_out" if preset else "kinematic"
        self.t0 = time.monotonic()
        self.seen_busy = False
        self.t_idle: float | None = None
        self.p0 = READY_ARMS.copy()
        self.duration = float(csv.duration) if csv is not None else 6.0

    def kinematic_end(self) -> float:
        return self.t_dispatch + self.duration + EMOTE_BLEND_OUT   # FIX B: recuperación 0.8 s

    def sample(self, now: float):
        """(arms14, waist≤3, head≤2, gentle) en la ruta cinemática; None ⇒ usar READY."""
        if self.csv is None:
            return None
        from x2_ultra.show.hri.motion import min_jerk
        if now < self.t_dispatch:
            # FIX B: mezcla de ENTRADA (0.5 s min-jerk) de los brazos comandados (p0) al primer
            # cuadro del CSV — antes se iba a D_ARMS y en t_dispatch saltaba al cuadro 0.
            a0, w0, h0 = self.csv.sample(0.0)
            w0 = np.clip(w0, -0.35, 0.35)[:3] if np.asarray(w0).size >= 3 else np.zeros(3)
            h0 = np.asarray(h0, dtype=float).reshape(-1)
            h0 = h0[:2] if h0.size >= 2 else np.zeros(2)
            if now < self.t_dispatch - EMOTE_BLEND_IN:
                return self.p0.copy(), np.zeros(3), np.zeros(2), False
            w = min_jerk((now - (self.t_dispatch - EMOTE_BLEND_IN)) / EMOTE_BLEND_IN)
            arms = self.p0 + w * (np.asarray(a0, dtype=float) - self.p0)
            return arms, w * w0, w * h0, True
        tau = now - self.t_dispatch
        if tau < self.duration:
            arms, w3, h2 = self.csv.sample(tau)
            waist = np.clip(w3, -0.35, 0.35)[:3] if w3.size >= 3 else np.zeros(3)
            head = h2[:2] if h2.size >= 2 else np.zeros(2)
            return arms, waist, head, True
        # FIX B: fallback de recuperación a READY (la mezcla hacia el cuadro SONIC actual se hace
        # en _emote_tick, que ve la cola de stream).
        w = min_jerk(min(1.0, (tau - self.duration) / EMOTE_BLEND_OUT))
        la, lw, _ = self.csv.sample(self.duration)
        arms = READY_ARMS + (1.0 - w) * (la - READY_ARMS)
        waist = (1.0 - w) * (np.clip(lw, -0.35, 0.35)[:3] if lw.size >= 3 else np.zeros(3))
        return arms, waist, np.zeros(2), w < 1.0


class AimdkBackend:
    """Backend del X2 real. DRY_RUN por defecto: ``link.commands`` registra todo, nada se publica."""

    def __init__(self, config: AimdkConfig | None = None, *, arm: bool | None = None, store=None,
                 link=None, pump: bool = True, name: str = "hri"):
        self.cfg = config or AimdkConfig()
        self.arm = bool(self.cfg.arm if arm is None else arm)
        self.dry_run = not self.arm
        if self.arm:
            from x2_ultra.real.link import require_target
            require_target()      # X2_REAL=robot (real) o gemelo (twin); sin ello, SystemExit
        if store is None:
            try:
                from x2_ultra.show.hri.data import VisitStore
                store = VisitStore()
            except Exception:
                store = None
        self.store = store
        self._link = link if link is not None else (DryLink() if self.dry_run else _LiveLink(self.cfg, name))
        self.link = self._link
        self.events: list[dict] = []
        self.svc = Svc(self.log)
        self.interlock = FallInterlock()
        self.limiter = Limiter(self.cfg.joint_vmax, self.cfg.amax)
        self._lock = threading.Lock()
        self._q: deque = deque(maxlen=2)       # stream 50 Hz: último cuadro manda
        self._track: _EmoteTrack | None = None
        self._point = None                     # (direction, t_stroke, hold_s, targets, side, target14)
        self._streaming = False
        self._vel = (0.0, 0.0)                 # última velocidad comandada (vx, wz)
        self._walk: list | None = None         # ruta walk_to en lazo abierto [(vx, wz, t_end), ...]
        self._bp_prev = np.zeros(4)
        self._wlp_x = np.zeros(3)
        self._wlp_v = np.zeros(3)
        self._bp_inflight = [deque([0.0] * 15, maxlen=15) for _ in BP_FIELDS]
        self._href2 = np.zeros(2)                # FIX A: referencia de posición de cabeza (yaw, pitch)
        self._head_tgt = np.zeros(2)
        self._head_cmd_prev = np.zeros(2)        # FIX A hal: último yaw/pitch publicado (rate ≤10 rad/s)
        self.estopped = False
        self.stopped = False
        self.unsafe = False
        self._last_cmd_arms = READY_ARMS.copy()
        self._last_item = None                  # último cuadro SONIC consumido (hold-last-frame)
        self._last_item_t = -1e9                # instante (reloj del tick) en que se consumió
        self._src = "ready"                     # fuente del tick previo: stream/out/emote/point/ready
        self._ramp = None                       # (t0, arms0, wref0) rampa de entrada/salida
        self._post_walk = False                 # hay que re-sembrar rampa/limitador tras caminar
        self._thread: threading.Thread | None = None
        self._running = True
        if pump:
            self.start()

    # ----------------------------------------------------------------------------- registro
    def log(self, event: str, **kw) -> None:
        self.events.append({"event": event, "t": time.monotonic(), **kw})

    @property
    def commands(self) -> list[dict]:
        """Órdenes registradas (dry-run: la evidencia de qué se habría publicado)."""
        return getattr(self._link, "commands", [])

    # ----------------------------------------------------------------------------- Protocolo
    def state(self) -> dict:
        obs = self._link.observed(graph=False)
        pose = getattr(self._link, "pose", {"z": None, "tilt": None})
        walking = self.walking()
        fall = bool(self.interlock.tripped or obs.body_status in (2, 3, 6) or obs.fsm == 6)
        stable = bool(obs.fsm == K.FSM_STABLE and obs.action_status == K.ACTION_RUNNING
                      and obs.body_status == K.BODY_STAND and not (fall or self.estopped))
        with self._lock:
            kind = "emote" if self._track is not None else ("point" if self._point else
                   ("stream" if self._streaming else ""))
        mode = ("estop" if self.estopped else "fall" if fall else "walking" if walking
                else kind or obs.mode or "idle")
        return {"t": time.monotonic(), "pelvis_z": pose.get("z"), "tilt_deg": pose.get("tilt"),
                "mode": mode, "stable": stable, "walking": walking, "fall": fall}

    def stream_upper_body(self, t: float, arms14, waist3, head2) -> None:
        if self._refuse("stream_upper_body"):
            return
        arms, waist, head = self._vec(arms14, 14), self._vec(waist3, 3), self._vec(head2, 2)
        if arms is None or waist is None or head is None:
            self.log("stream_rejected", reason="malformed", t_cmd=float(t))
            return
        with self._lock:
            self._q.append((float(t), arms, waist, head))
            self._streaming = True

    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None:
        if self._refuse("play_emote"):
            return
        motion_id, area = int(motion_id), int(area)
        csv = self._csv_for(motion_id, area)
        source = "kinematic_csv"
        if csv is None and not self.cfg.use_presets:
            # Sin los CSV de AgiBot: emote cinemático propio (backends/emotes.py).
            from x2_ultra.hri_runtime.backends.emotes import procedural_for
            csv = procedural_for(motion_id, area)
            source = "procedural"
        if csv is None and not self.cfg.use_presets:
            self.log("emote_unavailable", motion_id=motion_id, area=area)
            return
        with self._lock:
            self._track = _EmoteTrack(motion_id, area, float(t_dispatch), csv=csv,
                                      preset=self.cfg.use_presets)
            self._track.p0 = self._last_cmd_arms.copy()
            self._point = None
            self._q.clear()
        self.log("emote_dispatch", motion_id=motion_id, area=area, t_dispatch=float(t_dispatch),
                 path=("mc_preset" if self.cfg.use_presets else source),
                 layout=self.cfg.preset_layout)

    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None:
        if self._refuse("point"):
            return
        direction = str(direction).upper()
        targets = self._point_targets()
        if direction not in ("LEFT", "RIGHT", "FRONT") or not targets:
            self.log("point_rejected", direction=direction, reason="sin objetivos o dirección inválida")
            return
        entry = targets[direction]
        q = np.full(14, np.nan)
        for n, v in entry.get("joints", {}).items():
            if n in J.ARM_JOINTS:
                q[J.ARM_JOINTS.index(n)] = float(v)
        with self._lock:
            self._point = {"direction": direction, "t_stroke": float(t_stroke),
                           "hold_s": float(np.clip(hold_s, 1.0, 2.0)),
                           "parm": np.array([n.startswith(entry.get("side", "right")) for n in J.ARM_JOINTS]),
                           "target": q}
            self._track = None
            self._q.clear()
        self.log("point_dispatch", direction=direction, t_stroke=float(t_stroke))

    def walk_to(self, area: str) -> None:
        if self._refuse("walk_to"):
            return
        self._reset_wlp()
        route = self.cfg.routes.get(area)
        if not route:
            # Sin odometría en el PC (/aima/hal/odom/state no llega al robot) no hay lazo cerrado:
            # walk_to sólo acepta rutas abiertas inyectadas; si no, se registra y no mueve nada.
            self.log("walk_refused", area=area, reason="sin ruta (cfg.routes) — usar navegación/app")
            return
        with self._lock:
            self._abort_gestures("walk_to")     # gestos → reposo ANTES de caminar
        now = time.monotonic()
        segs, t = [], now
        for vx, wz, dur in route:
            t += float(dur)
            segs.append((float(vx), float(wz), t))
        with self._lock:
            self._walk = segs
        self.log("walk_start", area=area, segments=len(segs))

    def set_velocity(self, vx: float, wz: float) -> None:
        if self._refuse("set_velocity"):
            return
        with self._lock:
            if self._track is not None or self._point is not None:
                self.log("velocity_refused", reason="gesture_active")   # locomoción sólo sin gestos
                return
        vx = float(np.clip(vx, -K.WALK_CAPS["vx_back"], self.cfg.vx_cap))
        wz = float(np.clip(wz, -self.cfg.wz_cap, self.cfg.wz_cap))
        self._vel = (vx, wz)
        self._link.publish_velocity(vx, 0.0, wz, self.cfg.stamp_mode)
        self.log("set_velocity", vx=vx, wz=wz)

    def dispatch_d1(self, area: str) -> None:
        """Stub: API del D1 desconocida — sólo registro."""
        self.log("dispatch_d1", area=str(area), stub=True)

    def stop(self) -> None:
        with self._lock:
            self._abort_gestures("stop")
            self._q.clear()
            self._streaming = False
            self._walk = None
        self._reset_wlp()
        self._vel = (0.0, 0.0)
        self._link.publish_velocity(0.0, 0.0, 0.0, self.cfg.stamp_mode)
        self.stopped = True
        self.svc.submit("stop_final", lambda: (self._link.delete_source(),
                                               self._link.request_mode(K.MODE_STAND, busy_retry_s=8.0)))
        self.log("stop")

    def _reset_wlp(self) -> None:
        self._wlp_x = np.zeros(3)
        self._wlp_v = np.zeros(3)

    def estop(self) -> None:
        with self._lock:
            self._abort_gestures("estop")
            self._q.clear()
            self._streaming = False
            self._walk = None
        self._reset_wlp()
        self._vel = (0.0, 0.0)
        self._link.publish_velocity(0.0, 0.0, 0.0, self.cfg.stamp_mode)   # ceros ya, sin worker
        self.estopped = True
        self.svc.submit("estop_final", lambda: (self._link.delete_source(),
                                                self._link.request_mode(K.MODE_STAND, busy_retry_s=8.0)))
        self.log("estop")

    # ----------------------------------------------------------------------------- bomba 50 Hz
    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="hri-aimdk", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            self._link.close()
        except Exception:
            pass

    def _loop(self) -> None:  # pragma: no cover - tiempo real
        nxt = time.monotonic()
        while self._running:
            self.pump_once()
            nxt += TICK
            delay = nxt - time.monotonic()
            if delay > 0:
                time.sleep(max(0.0, delay - 0.002))
                while time.monotonic() < nxt:
                    pass
            elif delay < -0.25:
                nxt = time.monotonic()

    def pump_once(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else float(now)
        self.svc.tick(now)
        obs = self._link.observed(graph=False)
        pose = getattr(self._link, "pose", {"z": None, "tilt": None})
        preset_busy = self._track is not None and self._track.preset \
            and self._track.phase in ("preset", "preset_wait") and obs.player_state in (1, 2)
        # --- fix 3: interlock de caída → parar, mantener, DELETE+SD, rehusar hasta reset
        if self.interlock.update(pose.get("tilt"), pose.get("z"), preset_busy):
            if not self.unsafe:
                self.unsafe = True
                self.log("fall_interlock", tilt=pose.get("tilt"), pelvis=pose.get("z"))
                with self._lock:
                    self._abort_gestures("fall")
                    self._q.clear()
                    self._walk = None
                self.svc.submit("fall_sd", lambda: (self._link.delete_source(),
                                                    self._link.request_mode(K.MODE_STAND, busy_retry_s=8.0)))
            return
        if self.estopped or self.unsafe:
            return
        # --- marcha en lazo abierto (walk_to) y fin de marcha
        with self._lock:
            walk = list(self._walk) if self._walk else None
        if walk is not None:
            if now >= walk[-1][2]:
                with self._lock:
                    self._walk = None
                self._vel = (0.0, 0.0)
                self._link.publish_velocity(0.0, 0.0, 0.0, self.cfg.stamp_mode)
                self.log("walk_done")
            else:
                vx, wz, _ = next(s for s in walk if now < s[2])
                if (vx, wz) != self._vel:
                    self._vel = (vx, wz)
                    self._link.publish_velocity(vx, 0.0, wz, self.cfg.stamp_mode)
        # --- gestos bloqueados mientras camina (interlock no negociable)
        if self.walking():
            with self._lock:
                had = self._track is not None or self._point is not None or self._streaming
                self._abort_gestures("walking")
                self._q.clear()
                self._streaming = False
                self._last_item, self._last_item_t = None, -1e9   # sin hold/rampa rancios tras la marcha
                self._ramp, self._src = None, "out"
                self._post_walk = True
            if had:
                self.log("safety_gesture_suppressed")
            return
        if self._post_walk:                      # 1er tick tras caminar: rampa desde lo MEDIDO
            self._post_walk = False
            meas = self._arms_now()
            base = np.clip(meas, LO, HI) if meas is not None else self._last_cmd_arms.copy()
            self._last_cmd_arms = base.copy()
            self.limiter.reset(base)
            self._ramp, self._src = (now, base.copy(), self._bp_prev.copy()), "out"
        # --- pista activa: emote (cinemático o preset) / point / stream / READY
        arms, wref, gentle = None, None, False
        with self._lock:
            track, pt = self._track, self._point
            item = None
            if track is None and pt is None:
                if self._q:
                    item = self._q.pop()
                    self._q.clear()
                    self._last_item, self._last_item_t = item, now
                elif self._last_item is not None and now - self._last_item_t <= STREAM_HOLD_S:
                    item = self._last_item
        if track is not None:
            arms, wref, gentle = self._emote_tick(track, obs, now)
            if track.phase == "done":
                with self._lock:
                    self._track = None        # último tick: READY y pista cerrada
        elif pt is not None:
            arms, wref = self._point_tick(pt, now)
            if arms is None:
                with self._lock:
                    self._point = None
        elif item is not None:
            _, arms, w3, h2 = item
            wref = np.concatenate([self._clamp_waist(w3), h2[:1]])
            self._href2 = self._pad2(h2)         # FIX A hal: yaw+pitch SONIC completos
            if self._src != "stream":            # entrada a SONIC: rampa desde lo comandado
                self._ramp = (now, self._last_cmd_arms.copy(), self._bp_prev.copy())
            r = self._ramp
            if r is not None:
                from x2_ultra.show.hri.motion import min_jerk
                k = min(1.0, (now - r[0]) / SONIC_RAMP_IN_S)
                if k >= 1.0:
                    self._ramp = None
                else:
                    wk = min_jerk(k)
                    arms = r[1] + wk * (arms - r[1])
                    wref = r[2] + wk * (wref - r[2])
            self._src = "stream"
        else:
            if self._src == "stream":            # stream detenido: rampa a READY desde lo comandado
                self._ramp = (now, self._last_cmd_arms.copy(), self._bp_prev.copy())
                self._src = "out"
            r = self._ramp if self._src == "out" else None
            if r is not None:
                from x2_ultra.show.hri.motion import min_jerk
                k = min(1.0, (now - r[0]) / SONIC_RAMP_OUT_S)
                wk = min_jerk(k)
                arms = r[1] + wk * (READY_ARMS - r[1])
                wref = r[2] * (1.0 - wk)
                if k >= 1.0:
                    self._src, self._ramp = "ready", None
            else:
                arms = READY_ARMS.copy()
                wref = np.zeros(4)
            if self._streaming:
                self._streaming = False
        if track is not None:
            self._src = "emote"
        elif pt is not None:
            self._src = "point"
        # --- publicación (brazos por el limitador; cintura/cabeza por McBodyPose)
        if arms is not None:
            out, hit = self.limiter.step(np.clip(arms, LO, HI),
                                         vmax=(self.cfg.emote_vmax if (track is not None and not track.preset)
                                               else self.cfg.joint_vmax))
            head = self._head_now()
            self._link.publish_upper_body(head, out.tolist(), *self._hands(), self.cfg.stamp_mode)
            self._last_cmd_arms = out
            if hit:
                self.log("limiter_hit", n=self.limiter.nlim)
        if wref is not None:
            bounds = WAIST_BOUNDS_GENTLE if gentle else WAIST_BOUNDS
            rate = WAIST_SLEW_GENTLE if gentle else WAIST_SLEW
            w4 = np.asarray(wref, dtype=float).reshape(-1)[:4]
            if w4.size < 4:
                w4 = np.concatenate([w4, np.zeros(4 - w4.size)])
            if self.cfg.head_channel == "hal":
                self._head_tgt = np.array([w4[3] if w4[3] != 0.0 else self._href2[0], self._href2[1]])
                w4[3] = 0.0                      # FIX A: en modo hal McBodyPose no lleva turn_head
            if self.cfg.waist_mode == "off":
                w4[:3] = 0.0
            elif self.cfg.waist_mode == "lowpass":
                bd = np.asarray(bounds, dtype=float)
                wn = 2.0 * np.pi * self.cfg.waist_lp_hz
                u = np.clip(w4[:3], -bd, bd)
                self._wlp_v = self._wlp_v + (wn * wn * (u - self._wlp_x) - 2.0 * wn * self._wlp_v) * TICK
                self._wlp_x = np.clip(self._wlp_x + self._wlp_v * TICK, -bd, bd)
                w4[:3] = self._wlp_x
            ref = _clamp_w(w4, self._bp_prev, list(bounds) + [0.6], list(rate) + [2.5])
            if self.cfg.waist_mode == "off":
                ref[:3] = 0.0
            self._bp_prev = ref
            self._send_body_pose(ref)
        else:
            # fix 2: durante las fases de preset del MC no se toca la cintura — ceros = mantener.
            self._link.send_body_pose(np.zeros(4))
        if self.cfg.head_channel == "hal":
            self._publish_head_hal()             # FIX A: posición 50 Hz (también = mantener)

    # ----------------------------------------------------------------------------- emote/point
    def _emote_tick(self, track: _EmoteTrack, obs, now: float):
        if not track.preset:
            if now >= track.kinematic_end():
                track.phase = "done"
                tgt = self._sonic_target()
                return tgt[0], np.concatenate([self._clamp_waist(tgt[1]), tgt[2][:1]]), False
            if track.csv is not None and now - track.t_dispatch >= track.duration:
                return self._emote_exit_blend(track, now)
            s = track.sample(now)
            if s is None:
                return READY_ARMS.copy(), np.zeros(4), False
            arms, w3, h2, gentle = s
            # McBodyPose lleva 4 campos (c12): waist 3 + turn_head (yaw); el pitch de cabeza no
            # es canalizable por McBodyPose en el firmware actual (blocker "head channel").
            return arms, np.concatenate([w3, h2[:1]]), gentle
        # --- ruta opcional de preset del MC (state machine simplificado de motor_fix)
        if track.phase == "fade_out":
            w = float(np.clip((now - (track.t_dispatch - 0.6)) / 0.6, 0.0, 1.0))
            w = w * w * w * (10 + w * (-15 + 6 * w))
            arms = D_ARMS + (1.0 - w) * (track.p0 - D_ARMS)
            if now >= track.t_dispatch - 1e-6 and abs(arms - D_ARMS).max() <= ARM_GATE + 0.05:
                track.phase = "preset_wait"
            return arms, np.zeros(4), False      # fix 2: sin cintura desde el handoff
        if track.phase == "preset_wait":
            gate_ok = (obs.mode == K.MODE_STAND and obs.fsm == K.FSM_STABLE
                       and obs.action_status == K.ACTION_RUNNING
                       and (obs.player_state in (None, K.PLAYER_IDLE)))
            tilt = getattr(self._link, "pose", {}).get("tilt")
            if gate_ok and (tilt is None or tilt <= TILT_GATE):
                self.svc.submit(f"preset_{track.motion_id}", lambda: self._preset_request(track))
                track.phase = "preset"
                self.log("preset_dispatch", motion_id=track.motion_id, area=track.area)
            elif now > track.t_dispatch + 1.0:
                track.phase = "urs_reentry"      # fix 4: puerta no abierta en 1 s → saltar el preset
                self.log("preset_gate_skip", motion_id=track.motion_id)
            return None, None, False
        if track.phase == "preset":
            if obs.player_state not in (None, K.PLAYER_IDLE):
                track.seen_busy = True
            idle_deb = track.seen_busy and obs.player_state == K.PLAYER_IDLE
            rejected = self.svc.get(f"preset_{track.motion_id}") or {}
            if idle_deb or rejected.get("err") or (now - track.t_dispatch > 8.0 and not track.seen_busy):
                track.t_idle = now
                track.phase = "settle"
                self.log("preset_idle", motion_id=track.motion_id)
            return None, None, False
        if track.phase == "settle":              # fix 1: URS sólo tras idle + settle
            if now - (track.t_idle or now) >= SETTLE_S:
                self.svc.submit("urs_reentry", lambda: self._link.request_mode(K.MODE_URS))
                track.phase = "urs_reentry"
            return None, None, False
        if track.phase == "urs_reentry":
            if obs.mode == K.MODE_URS and obs.action_status == K.ACTION_RUNNING:
                self.svc.submit("urs_readd", lambda: self._link.register_source(self.cfg.priority))
                meas = self._arms_now()
                self.limiter.reset(meas if meas is not None else D_ARMS)
                track.phase = "fade_in"
                track.t0 = now
                track.p0 = np.asarray(meas if meas is not None else D_ARMS, dtype=float)
            elif now - (track.t_idle or track.t_dispatch) > 8.0:
                track.phase = "done"             # URS no recuperado: terminar en READY
                self.log("urs_timeout")
            return None, None, False
        if track.phase == "fade_in":
            w = float(np.clip((now - track.t0) / 0.7, 0.0, 1.0))
            w = w * w * w * (10 + w * (-15 + 6 * w))
            arms = track.p0 + w * (READY_ARMS - track.p0)
            if w >= 1.0:
                track.phase = "done"
            return arms, np.zeros(4), False
        track.phase = "done"
        return READY_ARMS.copy(), np.zeros(4), False

    def _sonic_target(self):
        """Último cuadro SONIC encolado (arms14, waist3, head2); READY si no hay ninguno."""
        with self._lock:
            item = self._q[-1] if self._q else self._last_item
        if item is None:
            return READY_ARMS.copy(), np.zeros(3), np.zeros(2)
        return (np.asarray(item[1], dtype=float), np.asarray(item[2], dtype=float),
                _pad(item[3], 2))

    def _emote_exit_blend(self, track: _EmoteTrack, now: float):
        """Salida del emote: min-jerk (EMOTE_BLEND_OUT) del último cuadro del CSV al cuadro SONIC
        ACTUAL (se re-lee cada tick), para que SONIC reanude sin salto (limitador 10 rad/s)."""
        from x2_ultra.show.hri.motion import min_jerk
        w = min_jerk(min(1.0, (now - track.t_dispatch - track.duration) / EMOTE_BLEND_OUT))
        la, lw, lh = track.csv.sample(track.duration)
        lw = np.clip(lw, -0.35, 0.35)[:3] if np.asarray(lw).size >= 3 else np.zeros(3)
        lh = _pad(lh, 2)
        ta, tw, th = self._sonic_target()
        arms = la + w * (ta - la)
        waist = lw + w * (self._clamp_waist(tw) - lw)
        head = lh + w * (th - lh)
        self._href2 = head
        return arms, np.concatenate([waist, head[:1]]), w < 1.0

    def _preset_request(self, track: _EmoteTrack) -> None:
        layout = self.cfg.preset_layout
        if layout == "mc_v1.0.0":
            raise RuntimeError("layout mc_v1.0.0 (sin input_source) requiere el helper del gemelo: TwinBackend")
        self._link.preset_set(track.motion_id, track.area, layout)   # v1.1.4: con input_source

    def _point_tick(self, pt: dict, now: float):
        from x2_ultra.show.hri.motion import min_jerk
        prep, hold, recov = 0.8, pt["hold_s"], 1.65
        ts = pt["t_stroke"]
        if now < ts - prep:
            return READY_ARMS.copy(), np.zeros(4)
        if now > ts + hold + recov:
            return None, None                    # fin: la pista se cierra
        if now < ts:
            w = min_jerk((now - (ts - prep)) / prep)
        elif now <= ts + hold:
            w = 1.0
        else:
            w = 1.0 - min_jerk((now - ts - hold) / recov)
        arms = READY_ARMS.copy()
        m = pt["parm"] & np.isfinite(pt["target"])
        arms[m] += w * (pt["target"][m] - arms[m])
        sign = {"LEFT": 1.0, "RIGHT": -1.0, "FRONT": 0.0}[pt["direction"]]
        return arms, np.concatenate([np.zeros(3), [sign * 0.30 * w]])

    # ----------------------------------------------------------------------------- utilidades
    def walking(self) -> bool:
        with self._lock:
            if self._walk is not None:
                return True
        vx, wz = self._vel
        return abs(vx) > 1e-6 or abs(wz) > 1e-6

    def gesture_active(self) -> bool:
        with self._lock:
            return self._track is not None or self._point is not None

    def _abort_gestures(self, why: str) -> None:
        if self._track is not None or self._point is not None:
            self.log("gesture_aborted", why=why)
        self._track = None
        self._point = None

    def _refuse(self, what: str) -> bool:
        if self.estopped:
            self.log("refused", what=what, reason="estop")
            return True
        if self.unsafe:
            self.log("refused", what=what, reason="fall_interlock")
            return True
        if self.stopped:
            self.log("refused", what=what, reason="stopped")   # terminal hasta re-armar
            return True
        if what in ("play_emote", "point", "stream_upper_body") and self.walking():
            self.log("refused", what=what, reason="walking")   # nunca gestos mientras camina
            return True
        return False

    @staticmethod
    def _vec(v, n: int) -> np.ndarray | None:
        a = np.asarray(v, dtype=float).reshape(-1)
        return a if (a.size == n and np.all(np.isfinite(a))) else None

    @staticmethod
    def _clamp_waist(w3) -> np.ndarray:
        return np.clip(np.asarray(w3, dtype=float).reshape(-1)[:3],
                       -np.array(WAIST_BOUNDS), np.array(WAIST_BOUNDS))

    def _arms_now(self):
        d = self._link.latest("arms")
        return None if not d else np.array([d[n] for n in J.ARM_JOINTS])

    def _head_now(self):
        d = self._link.latest("head")
        return [0.0, 0.0] if not d else [d[J.HEAD_JOINTS[0]], d[J.HEAD_JOINTS[1]]]

    @staticmethod
    def _pad2(h) -> np.ndarray:
        h = np.asarray(h, dtype=float).reshape(-1)[:2]
        return np.concatenate([h, np.zeros(2 - h.size)])

    def _publish_head_hal(self) -> None:
        """FIX A: posición yaw/pitch recortada a límites y a ≤HEAD_RATE rad/s por tick."""
        tgt = np.clip(self._head_tgt, [-HEAD_YAW_LIM, HEAD_PITCH_LO], [HEAD_YAW_LIM, HEAD_PITCH_HI])
        step = HEAD_RATE * TICK
        cmd = self._head_cmd_prev + np.clip(tgt - self._head_cmd_prev, -step, step)
        self._head_cmd_prev = cmd
        self._link.publish_head_cmd(float(cmd[0]), float(cmd[1]))

    def _hands(self):
        h = self._link.hand_positions()
        return (2, h) if h and len(h) == 20 else (1, [0.0, 0.0])

    def _send_body_pose(self, ref4) -> None:
        """Control de signo hacia la referencia (c07): u = ±0.3 si |e| > deadband, con compensación
        de inflight; sin medida de cintura/cabeza, ceros (mantener)."""
        w = getattr(self._link, "wh_state", None)
        q = None
        if w and "waist" in w:
            head = self._link.latest("head")
            if head is not None:
                q = np.array([w["waist"][0], w["waist"][1], w["waist"][2], head[J.HEAD_JOINTS[0]]])
        if q is None:
            self._link.send_body_pose(np.zeros(4))
            return
        u = np.zeros(4)
        off = self.cfg.waist_mode == "off"
        for f in range(4):
            if off and f < 3:     # waist off: u=0 exacto, sin control ni inflight
                continue
            q_est = q[f] + sum(self._bp_inflight[f])
            e = float(np.asarray(ref4).reshape(-1)[f]) - q_est
            if abs(e) <= BP_DEADBAND[f]:
                u[f] = 0.0
            elif f == 3:     # FIX HE: cabeza proporcional y simétrica (LEFT>0, RIGHT<0, FRONT≈0)
                u[f] = float(np.clip(BP_HEAD_KP * e, -BP_U, BP_U))
            else:
                u[f] = math.copysign(BP_U, e)
            self._bp_inflight[f].append(u[f] * BP_SPEED[f] * TICK)
        self._link.send_body_pose(u)

    def _csv_for(self, motion_id: int, area: int):
        if self.store is None:
            return None
        try:
            from x2_ultra.show.hri.motion import CsvPreset
            path = None
            if hasattr(self.store, "csv_for_id"):
                path = self.store.csv_for_id(motion_id, area)
            else:
                path = getattr(self.store, "animations", {}).get((motion_id, area))
            if path is None:
                return None
            path = Path(path)
            return CsvPreset.load(path) if path.is_file() else None
        except Exception:
            return None

    def _point_targets(self) -> dict:
        if self.store is None:
            return {}
        try:
            return self.store.point_targets()
        except Exception:
            return {}

    # ----------------------------------------------------------------------------- arm (real)
    def arm_session(self, timeout_s: float = 15.0) -> list[str]:
        """Secuencia de armado real: preflight → fuente → URS → READY. Devuelve problemas (vacío = ok).
        En dry-run sólo registra la secuencia (pruebas y DEPLOY_X2_REAL.md)."""
        problems = []
        obs = self._link.observed(graph=False)
        if obs.battery is not None and obs.battery < self.cfg.min_battery and not self.cfg.ignore_battery:
            problems.append(f"batería al {obs.battery} %")
        if obs.mode not in (K.MODE_STAND, K.MODE_URS):
            problems.append(f"MC en {obs.mode or '?'}; hace falta STAND_DEFAULT")
        if problems:
            self.log("arm_refused", problems=problems)
            return problems
        self._link.register_source(self.cfg.priority)
        if obs.mode != K.MODE_URS:
            self._link.request_mode(K.MODE_URS)
            if not self._link.wait_mode(K.MODE_URS, timeout_s):
                self.log("arm_failed", reason="URS no confirmado")
                return ["URS no confirmado por el MC"]
        if hasattr(self._link, "hand_init"):
            self._link.hand_init()
        meas = self._arms_now()
        self.limiter.reset(meas if meas is not None else READY_ARMS)
        self.stopped = False
        self.log("armed", mode=K.MODE_URS, priority=self.cfg.priority, dry_run=self.dry_run)
        return []


def main(argv=None) -> int:
    """CLI: dry-run por defecto; ``--arm`` publica de verdad (requiere X2_REAL=robot)."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arm", action="store_true", help="ARMAR: publicar de verdad (peligro; sin ello, dry-run)")
    ap.add_argument("--presets", action="store_true", help="emotes por preset del MC en vez de CSV cinemático")
    ap.add_argument("--ros-domain", type=int, default=None)
    ap.add_argument("--seconds", type=float, default=5.0, help="segundos de stream READY de prueba")
    args = ap.parse_args(argv)
    cfg = AimdkConfig(arm=args.arm, use_presets=args.presets,
                      ros_domain=args.ros_domain)
    if cfg.ros_domain is not None:
        os.environ["ROS_DOMAIN_ID"] = str(cfg.ros_domain)
    be = AimdkBackend(cfg)
    print(f"AimdkBackend dry_run={be.dry_run} presets={cfg.use_presets}")
    problems = be.arm_session()
    if problems:
        print("no armado:", problems)
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        be.stream_upper_body(time.monotonic(), READY_ARMS, np.zeros(3), np.zeros(2))
        time.sleep(TICK)
    be.stop()
    time.sleep(0.3)
    be.close()
    print("comandos registrados:", len(be.commands))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
