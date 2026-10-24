"""Runner EN VIVO y CON ACTUACIÓN del runtime HRI conversacional (Club N) en el X2 Ultra.

Misma conversación que ``live_robot`` (micrófono → VAD → ASR → FSM + x2-llm → Supertonic →
altavoces), pero las intenciones EMOTE/POINT se ejecutan en el robot real con
``AimdkBackend`` (brazos por URS a 50 Hz, emotes cinemáticos desde los CSV de AgiBot,
señalamiento; cintura bloqueada; cabeza por ``McBodyPose``)::

    # en el contenedor x2-hri-actuado (deploy/hri_club_con_actuacion.sh lo lanza así)
    python -m x2_ultra.hri_runtime.live_robot_actuado --etapa seco            # dry-run: no publica
    python -m x2_ultra.hri_runtime.live_robot_actuado --etapa lenta --arm     # 5/10 rad/s
    python -m x2_ultra.hri_runtime.live_robot_actuado --etapa lenta --arm --solo-prueba
    python -m x2_ultra.hri_runtime.live_robot_actuado --etapa completa --arm  # límites del spec

Etapas (DEPLOY_X2_REAL.md §1, sin grúa: no saltar etapas):

- ``seco``: ``AimdkBackend`` en dry-run (``DryLink``): toda la cadena corre y las órdenes que se
  habrían publicado quedan en el log; el robot no recibe nada.
- ``lenta``: armado con ``joint_vmax=5`` y ``emote_vmax=10`` rad/s, cintura bloqueada a la fuerza.
- ``completa``: límites del spec (10/20 rad/s, 40 rad/s²); cintura según ``HRI_WAIST_MODE``.

Qué NO hace (a propósito, primera sesión real):

- no camina: ``walk_to``/``set_velocity`` se rechazan aquí además de en el backend (sin rutas);
- no hay co-speech SONIC: el servidor de movimiento no corre en el Orin (REPORT_DEPLOY_X2 §8a);
  entre gestos el backend mantiene READY a 50 Hz;
- el D1 sigue siendo un stub (solo registro).

Sincronía gesto ↔ voz: el backend usa reloj monotónico y el app en tiempo real usa su propio
reloj, así que ``GestureSync`` retiene los gestos de cada frase y los despacha cuando la
primera frase ya está sintetizada, con un adelanto ``GESTURE_LEAD_S`` (≥ preparación del
señalamiento, 0.8 s) y retrasando el audio con ``not_before`` para que el stroke caiga en el
ancla de la frase.

Capas de seguridad:

1. ``safety_preflight``: armar exige ``--arm`` + ``X2_REAL=robot`` + ``HRI_ACTUATION=1``; ``seco``
   rechaza ``--arm``. Nunca con ``HRI_NO_ACTUATION=1``.
2. ``preflight_robot`` (antes de ``arm_session``): feedback de brazos fresco, pose de odometría
   presente (interlock de caída), MC en STAND/URS, ningún otro publicador de
   ``/mc/upper_body_command`` ni de velocidad, batería.
3. ``AimdkBackend``: limitador vel/acc, interlock de caída siempre activo, bloqueo gesto↔marcha.
4. ``Watchdog`` (5 Hz): interlock disparado, e-stop o feedback de brazos perdido > 1 s ⇒
   ``estop()`` y fin de la sesión.
5. Salida (Ctrl-C, ``docker stop``/SIGTERM, error): ``stop()`` ⇒ velocidad cero, DELETE de la
   fuente URS y STAND_DEFAULT. El mando (prioridad 80) siempre gana a nuestra fuente (65).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import numpy as np

from . import live_robot as LR
from .asistente import Asistente
from .face import FaceExpressions

AUDIO_LEAD_S = 0.25          # = app.AUDIO_LEAD_S (no se importa app aquí: arrastra numpy/tts)
GESTURE_LEAD_S = 0.9         # adelanto mínimo del despacho: prep del señalamiento 0.8 s (+ margen)
FEEDBACK_LOST_S = 1.0        # watchdog: brazos sin feedback ⇒ e-stop
ETAPAS = ("seco", "lenta", "completa")
LIMITES = {"lenta": {"joint_vmax": 5.0, "emote_vmax": 10.0}, "completa": {}}
PRUEBA_EMOTE = (3001, 11)    # DEPLOY_X2_REAL §4.2
DRACO_TAIL = " Si lo prefiere, Draco, nuestro perro guía, puede acompañarle hasta la recepción."
REJECTED_PHRASE = ("Bienvenido al Club N. No he podido confirmar su identidad; "
                   "un miembro del personal le atenderá enseguida." + DRACO_TAIL)
WORKER_PHRASE = ("Hola, {name}. Como personal externo, un miembro del personal le atenderá y le "
                 "indicará su orden de trabajo." + DRACO_TAIL)
QR_ASK = "Hola, {name}. Para confirmar su membresía, por favor muéstreme su credencial QR frente a mi cámara."
QR_REMIND = "Acerque un poco más el código QR a mi cámara, por favor."
QR_MISSING_PHRASE = ("No he podido leer su credencial. Un miembro del personal le atenderá enseguida."
                     + DRACO_TAIL)
QR_REJECTED_PHRASE = ("Esa credencial no corresponde a su membresía. Un miembro del personal le "
                      "atenderá enseguida." + DRACO_TAIL)
QR_DEBUG_EVERY_S = 2.0       # fotogramas del escaneo guardados para diagnóstico (fotos/qr_*)
QR_TIMEOUT_S = float(os.environ.get("HRI_QR_TIMEOUT_S", 20))
# Visitante = cara grande (cerca, de frente) en la cámara gran angular: una cara de paso a 5 m
# (≈40 px) se tomaba como visita y se rechazaba (robot real, 2026-10-24).
MIN_FACE_PX = float(os.environ.get("HRI_MIN_CARA_PX", 70))
UNKNOWN_CONFIRM_FRAMES = 2   # cara no reconocida: debe seguir delante en fotos consecutivas
# Acuse inmediato mientras el LLM piensa (HRI_ACUSE=0 lo apaga): frases cortas por rango con su
# tratamiento (STAFF tutea; VIP sin muletillas coloquiales). Se sintetizan antes de armar.
ACUSES = {
    "VIP": ["Permítame un momento.", "Con mucho gusto, un instante."],
    "REGULAR": ["Claro, déjeme ver.", "Un momento, por favor."],
    "GUEST": ["Claro, un momento.", "Déjeme ver, por favor."],
    "STAFF": ["Va, dame un segundo.", "Claro, ya te digo."],
    "WORKER": ["Un momento, por favor.", "Claro, déjeme ver."],
}
ACUSE_MIN_PALABRAS = 3
POSE_STALE_S = 0.5           # watchdog: pose (IMU/odometría) sin actualizar ⇒ e-stop
MAX_TILT_ARM_DEG = 8.0       # no se arma con el robot inclinado
# Nodos del sistema de AgiBot que tienen SIEMPRE publicador en velocidad/body_pose (medido en el
# robot el 2026-10-24): app, mando, interacción y planificador. Solo mandan cuando se usan.
SYSTEM_PUBLISHERS = re.compile(r"^(app_proxy|soc\d+_rc\d*|soc\d+_interaction_slave\d*|aimrt_planning_planning_node\d*)$")
COMMAND_TOPICS = ("/mc/upper_body_command", "/aima/mc/locomotion/velocity")


# =========================================================================== seguridad
def safety_preflight(argv: list[str], env: Optional[Mapping[str, str]] = None) -> str:
    """Devuelve la etapa validada o aborta (``LR.SafetyAbort``, código 3)."""
    env = os.environ if env is None else env
    etapa = None
    for i, a in enumerate(argv):
        if a == "--etapa" and i + 1 < len(argv):
            etapa = argv[i + 1]
        elif a.startswith("--etapa="):
            etapa = a.split("=", 1)[1]
    if etapa not in ETAPAS:
        raise LR.SafetyAbort(f"--etapa obligatoria: {'|'.join(ETAPAS)}")
    arm = "--arm" in argv
    if env.get("HRI_NO_ACTUATION", "0") == "1":
        raise LR.SafetyAbort("HRI_NO_ACTUATION=1: usa live_robot (sin actuación), no este runner")
    if etapa == "seco":
        if arm:
            raise LR.SafetyAbort("--etapa seco no admite --arm (dry-run: no se publica nada)")
        return etapa
    if not arm:
        raise LR.SafetyAbort(f"--etapa {etapa} publica al robot: hace falta --arm explícito")
    if env.get("X2_REAL") != "robot":
        raise LR.SafetyAbort("armar exige X2_REAL=robot")
    if env.get("HRI_ACTUATION") != "1":
        raise LR.SafetyAbort("armar exige HRI_ACTUATION=1 (confirmación explícita del operador)")
    return etapa


def make_config(etapa: str, env: Optional[Mapping[str, str]] = None):
    from .backends.aimdk import AimdkConfig

    env = os.environ if env is None else env
    cfg = AimdkConfig(arm=etapa != "seco", routes={}, use_presets=False,
                      ignore_battery=env.get("HRI_IGNORE_BATTERY") == "1",
                      **LIMITES.get(etapa, {}))
    if etapa in ("seco", "lenta"):
        cfg.waist_mode = "off"          # primera sesión: cintura bloqueada pase lo que pase
    if cfg.ros_domain is None and env.get("ROS_DOMAIN_ID"):
        cfg.ros_domain = int(env["ROS_DOMAIN_ID"])
    return cfg


def foreign_publishers(backend: Any, topic: str) -> Optional[list[str]]:
    """Nodos que publican en ``topic`` salvo el nuestro y los del sistema de AgiBot; None en dry-run."""
    node = getattr(getattr(backend.link, "link", None), "node", None)
    if node is None:
        return None
    names = {i.node_name for i in node.get_publishers_info_by_topic(topic)}
    return sorted(n for n in names if n != node.get_name() and not SYSTEM_PUBLISHERS.match(n))


def preflight_robot(backend: Any, *, wait_s: float = 10.0, log: Callable[[str], None] = print) -> list[str]:
    """Comprobaciones antes de ``arm_session`` (en dry-run pasan con el estado simulado)."""
    from x2_ultra.real import control as K

    t0 = time.monotonic()
    obs, arms, pose = None, None, {}
    while time.monotonic() - t0 < wait_s:
        obs = backend.link.observed(graph=True)
        arms = backend.link.latest("arms")
        pose = getattr(backend.link, "pose", {}) or {}
        if arms and pose.get("z") is not None and obs.mode and obs.battery is not None:
            break
        time.sleep(0.2)
    problems = []
    if not arms:
        problems.append("sin feedback de brazos (/aima/hal/joint/arm/state)")
    if pose.get("z") is None or pose.get("tilt") is None:
        problems.append("sin pose (ni /aima/hal/odom/state ni IMU del torso + leg_odometry): "
                        "el interlock de caída no vería nada")
    elif pose["z"] < 0.66:
        problems.append(f"pelvis a {pose['z']:.2f} m: el robot no está de pie")
    elif pose["tilt"] > MAX_TILT_ARM_DEG:
        problems.append(f"inclinación {pose['tilt']:.1f}° > {MAX_TILT_ARM_DEG:.0f}°: el robot no está derecho")
    if obs is None or obs.mode not in (K.MODE_STAND, K.MODE_URS):
        problems.append(f"MC en {getattr(obs, 'mode', '') or '?'}: hace falta STAND_DEFAULT (mando/app)")
    if obs is not None and not backend.cfg.ignore_battery:
        if obs.battery is None:
            problems.append("batería desconocida (/aima/hal/pmu/state no llegó): arm_session no la comprobaría")
        elif obs.battery < backend.cfg.min_battery:
            problems.append(f"batería al {obs.battery} % (< {backend.cfg.min_battery} %): cargar antes de armar")
    if obs is not None and obs.body_status is not None and obs.body_status != K.BODY_STAND:
        problems.append(f"body_status={obs.body_status}: el MC no dice que esté de pie")
    for topic in COMMAND_TOPICS:
        names = foreign_publishers(backend, topic)
        if names is None:          # dry-run: contadores del estado simulado
            n = (obs.upper_body_publishers if "upper" in topic else obs.velocity_publishers) if obs else 0
            names = [f"{n} desconocido(s)"] if n else []
        if names:
            problems.append(f"publicador(es) ajeno(s) en {topic}: {', '.join(names)}")
    animations = len(getattr(backend.store, "animations", {}) or {})
    targets = list(backend._point_targets())
    bp = getattr(backend.link, "bp_pub", "dry") is not None
    log(f"[preflight] modo={getattr(obs, 'mode', '?')} batería={getattr(obs, 'battery', None)} "
        f"pelvis={pose.get('z')} tilt={pose.get('tilt')} pose={pose.get('src')} "
        f"fuente_MC={getattr(obs, 'input_source', '') or '-'} brazos={'sí' if arms else 'no'} "
        f"CSV de emotes={animations} objetivos de señalamiento={targets} McBodyPose={'sí' if bp else 'no'}")
    if animations == 0:
        from .backends.emotes import LIBRARY
        log(f"[preflight] sin CSV de emotes de AgiBot: se usan {len(LIBRARY)} emotes cinemáticos propios "
            "(saludo, despedida, aplauso, corazón, pulgar, negación, confirmación)")
    return problems


# =========================================================================== sincronía
class GestureSync:
    """Envoltorio del backend real para ``ClubHriApp``.

    - ``play_emote``/``point``: el app los llama con su reloj (``t = clock + AUDIO_LEAD_S +
      ancla``); se guardan como desfase respecto al inicio del audio de la frase.
    - ``sink`` (``audio_sink`` del app): con la primera frase sintetizada fija
      ``inicio = max(ahora + GESTURE_LEAD_S, fin estimado de la cola de voz)``, despacha los
      gestos en ``inicio + ancla`` (reloj monotónico del backend) y encola el audio con
      ``not_before = inicio``.
    - locomoción deshabilitada (defensa en profundidad además de ``routes={}``).
    """

    def __init__(self, backend: Any, speech: Any, log: Callable[[str], None] = print,
                 lead_s: float = GESTURE_LEAD_S):
        self.backend = backend
        self.speech = speech
        self.log_fn = log
        self.lead_s = float(lead_s)
        self.clock: Callable[[], float] = time.monotonic   # reloj del app (attach)
        self.pending: list[dict] = []
        self.dispatched: list[dict] = []
        self.free_at = 0.0
        self.cospeech: Optional["CoSpeech"] = None
        self.face: Optional[FaceExpressions] = None    # expresión de la cara por emote
        self._lock = threading.Lock()

    def attach(self, app: Any) -> None:
        self.clock = app._clock

    def __getattr__(self, name):          # state/stop/estop/events/close/dispatch_d1/...
        return getattr(self.backend, name)

    # --- gestos (desde ClubHriApp._say, antes de sintetizar) ----------------------------
    def play_emote(self, motion_id: int, area: int, t_dispatch: float) -> None:
        self._hold({"kind": "emote", "motion_id": int(motion_id), "area": int(area)}, t_dispatch)

    def point(self, direction: str, t_stroke: float, hold_s: float = 1.5) -> None:
        self._hold({"kind": "point", "direction": str(direction), "hold_s": float(hold_s)}, t_stroke)

    def _hold(self, g: dict, t_app: float) -> None:
        g["rel_s"] = max(0.0, float(t_app) - self.clock() - AUDIO_LEAD_S)
        with self._lock:
            self.pending.append(g)

    # --- locomoción: deshabilitada en esta fase -----------------------------------------
    def walk_to(self, area: str) -> None:
        self.backend.log("walk_refused", area=str(area), reason="locomoción deshabilitada (live_robot_actuado)")
        self.log_fn(f"[motor] WALK_WITH {area}: NO EJECUTADO (locomoción deshabilitada)")

    def set_velocity(self, vx: float, wz: float) -> None:
        self.backend.log("velocity_refused", reason="locomoción deshabilitada (live_robot_actuado)")

    # --- audio ----------------------------------------------------------------------------
    def sink(self, pcm: np.ndarray, sr: int, meta: Optional[dict] = None) -> None:
        meta = dict(meta or {})
        now = time.monotonic()
        dur = float(len(pcm)) / float(sr or 1) + float(meta.get("gap_s", 0.0) or 0.0)
        with self._lock:
            gestures, self.pending = self.pending, []
        start = max(now, self.free_at)
        if gestures:
            start = max(now + self.lead_s, self.free_at)
            meta["not_before"] = start
            self._dispatch(gestures, start)
        self.free_at = start + dur
        if self.cospeech is not None:
            self.cospeech.schedule(start, float(len(pcm)) / float(sr or 1))
        self.speech.enqueue(pcm, sr, meta)

    __call__ = sink

    def flush(self) -> None:
        """Gestos sin audio (TTS vacío): se ejecutan igual con el adelanto mínimo."""
        with self._lock:
            gestures, self.pending = self.pending, []
        if gestures:
            self._dispatch(gestures, max(time.monotonic() + self.lead_s, self.free_at))

    def _dispatch(self, gestures: list[dict], start: float) -> None:
        for g in gestures:
            t = start + g["rel_s"]
            if g["kind"] == "emote":
                self.backend.play_emote(g["motion_id"], g["area"], t)
                what = f"EMOTE {g['motion_id']}/{g['area']}"
                if self.face is not None:
                    self.face.emote(motion_id=g["motion_id"])
            else:
                self.backend.point(g["direction"], t, g.get("hold_s", 1.5))
                what = f"POINT {g['direction']}"
                if self.face is not None:
                    self.face.emote(typ=f"POINT_{str(g['direction']).upper()}")
            self.dispatched.append(dict(g, t_dispatch=t, t_audio=start))
            self.log_fn(f"[motor] {what} en +{t - time.monotonic():.2f} s (ancla {g['rel_s']:.2f} s)")


# =========================================================================== gesto co-verbal
COSPEECH_AMP = float(os.environ.get("HRI_COSPEECH_AMP", 0.7))   # = show SONIC_ARM_AMP_K
COSPEECH_AUDIO_T0 = 2.0      # en los npz de HRIEGO el cuadro 100 (2,0 s) es el inicio del audio
COSPEECH_LEAD_S = 0.3        # el gesto arranca un poco antes de la voz


def cospeech_clips(root: Optional[Path] = None) -> dict[str, list[Path]]:
    """Clips SONIC precomputados de HRIEGO (brazos a 50 Hz, validados en el gemelo)."""
    root = root or (LR.REPO / "assets" / "hri" / "HRIEGO_gesture")
    paths = sorted(Path(root).glob("**/sonic/**/*_arms.npz"))
    greet = [q for q in paths if "greeting" in q.name]
    return {"greeting": greet, "dialogue": [q for q in paths if q not in greet]}


class CoSpeech:
    """Sustituto del co-speech SONIC en línea (que no corre en el Orin): mientras suena cada
    frase, reproduce a 50 Hz un clip SONIC precomputado por ``stream_upper_body``. El backend
    hace la rampa de entrada/salida, el limitador y da prioridad a emotes/señalamiento."""

    def __init__(self, backend: Any, clips: Optional[dict] = None, amp: float = COSPEECH_AMP,
                 log: Callable[[str], None] = print, seed: int = 0):
        from x2_ultra.show.hri.motion import REST_ARMS, SonicClip
        self.backend = backend
        self.amp = float(amp)
        self.rest = REST_ARMS
        self.log = log
        clips = cospeech_clips() if clips is None else clips
        self._load = SonicClip.load
        self.clips = {k: list(v) for k, v in clips.items() if v}
        self._rng = np.random.default_rng(seed)
        self._windows: list[dict] = []
        self._lock = threading.Lock()
        self._first = True
        self.frames = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hri-cospeech", daemon=True)

    @property
    def available(self) -> bool:
        return bool(self.clips)

    def start(self) -> "CoSpeech":
        if self.available:
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def new_visit(self) -> None:
        self._first = True

    def schedule(self, t_start: float, dur: float) -> None:
        if not self.available or dur <= 0.3:
            return
        kind = "greeting" if self._first and "greeting" in self.clips else "dialogue"
        self._first = False
        pool = self.clips.get(kind) or next(iter(self.clips.values()))
        clip = self._load(pool[int(self._rng.integers(len(pool)))])
        with self._lock:
            self._windows.append({"t0": t_start - COSPEECH_LEAD_S, "t1": t_start + dur, "clip": clip,
                                  "start": t_start})

    def frame(self, now: float):
        """(arms14, waist3, head2) del clip activo en ``now`` o None."""
        with self._lock:
            self._windows = [w for w in self._windows if w["t1"] > now]
            w = next((w for w in self._windows if w["t0"] <= now), None)
        if w is None:
            return None
        clip = w["clip"]
        tc = COSPEECH_AUDIO_T0 + (now - w["start"])
        span = clip.duration - COSPEECH_AUDIO_T0
        if tc > clip.duration and span > 1.0:          # frase más larga que el clip: bucle
            tc = COSPEECH_AUDIO_T0 + (tc - COSPEECH_AUDIO_T0) % span
        arms = self.rest + self.amp * (clip.arms(tc) - self.rest)
        return arms, np.zeros(3), clip.head(tc)

    def _run(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            f = self.frame(now)
            if f is not None and not self.backend.walking():
                self.backend.stream_upper_body(now, *f)
                self.frames += 1
            nxt += 0.02
            time.sleep(max(0.0, nxt - time.monotonic()))
            if nxt < time.monotonic() - 0.25:
                nxt = time.monotonic()


# =========================================================================== watchdog
class Watchdog:
    """5 Hz: interlock de caída, e-stop o feedback de brazos perdido ⇒ e-stop + fin de sesión."""

    def __init__(self, backend: Any, on_trip: Callable[[str], None], period_s: float = 0.2,
                 feedback_lost_s: float = FEEDBACK_LOST_S):
        self.backend = backend
        self.on_trip = on_trip
        self.period_s = period_s
        self.feedback_lost_s = feedback_lost_s
        self.reason: Optional[str] = None
        self._stop = threading.Event()
        self._last_arms = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="hri-watchdog", daemon=True)

    def start(self) -> "Watchdog":
        self._thread.start()
        return self

    def check(self, now: Optional[float] = None) -> Optional[str]:
        now = time.monotonic() if now is None else now
        b = self.backend
        if b.unsafe or b.interlock.tripped:
            return "interlock de caída disparado"
        if b.estopped:
            return "e-stop"
        t_pose = (getattr(b.link, "pose", None) or {}).get("t")
        if t_pose is not None and now - t_pose > POSE_STALE_S:
            return f"pose sin actualizar > {POSE_STALE_S:.1f} s (interlock de caída ciego)"
        if b.link.latest("arms"):
            self._last_arms = now
        elif now - self._last_arms > self.feedback_lost_s:
            return f"sin feedback de brazos > {self.feedback_lost_s:.1f} s"
        return None

    def _run(self) -> None:
        while not self._stop.wait(self.period_s):
            why = self.check()
            if why:
                self.reason = why
                if not self.backend.estopped:
                    self.backend.estop()
                self.on_trip(why)
                return

    def stop(self) -> None:
        self._stop.set()


# =========================================================================== sesión
class ActuatedTurnLogger(LR.TurnLogger):
    """Igual que el de la fase 1, pero dice qué pasó de verdad con cada intención motora."""

    motor_key = "motor_intents"
    STATUS = {"EMOTE": ("ENVIADO AL ROBOT (emote)", True),
              "POINT": ("ENVIADO AL ROBOT (señalamiento)", True),
              "WALK_WITH": ("RECHAZADO (marcha deshabilitada)", False),
              "DISPATCH_D1": ("STUB (D1 solo registro)", False)}

    def motor_status(self, act: dict) -> tuple[str, bool]:
        return self.STATUS.get(act.get("act", ""), ("desconocido", False))


class ActuatedSession(LR.LiveSession):
    """``LiveSession`` con el backend real envuelto en ``GestureSync``."""

    def __init__(self, args, *, backend: Any, face: Optional[FaceExpressions] = None, **kw):
        super().__init__(args, **kw)
        self.backend = backend
        self.sync = GestureSync(backend, self.speech, log=self.logger.info)
        # Cara (HRI_EXPRESIONES=1): inerte si está apagada ⇒ ningún gancho, mismo comportamiento.
        self._identity_phase = False
        self.face = face if face is not None else FaceExpressions.from_env(
            getattr(args, "etapa", "seco"), log=self.logger.info)
        if self.face.enabled:
            self.face.state_fn = self._fsm_state
            self.speech.on_play = self.face.speech_start
            self.speech.on_idle = self.face.speech_end
            self.sync.face = self.face
        self._acuses: dict[str, list] = {}
        self._acuse_n = 0
        if os.environ.get("HRI_ACUSE", "1") != "0" and not getattr(self.tts, "estimated", False):
            self._precalcular_acuses()
        self.cospeech = None
        if os.environ.get("HRI_COSPEECH", "1") != "0":
            self.cospeech = CoSpeech(backend, log=self.logger.info).start()
            self.sync.cospeech = self.cospeech if self.cospeech.available else None
            self.logger.info(f"[gesto] co-verbal: {sum(len(v) for v in self.cospeech.clips.values())} "
                             f"clips SONIC precomputados, amplitud {self.cospeech.amp:.0%}")
        if not (getattr(backend.store, "animations", None) or backend.cfg.use_presets):
            # sin CSV de AgiBot el backend usa los emotes cinemáticos propios (backends/emotes.py)
            self.logger.STATUS = dict(getattr(self.logger, "STATUS", {}),
                                      EMOTE=("ENVIADO AL ROBOT (emote propio, sin CSV de AgiBot)", True))

    def _build_app(self, *, identity_real: bool, person: Optional[dict]):
        from .app import ClubHriApp

        confirm_fn = None
        if not identity_real and person is not None:
            confirm_fn = lambda *_a, **_k: dict(person, status="ok_simulado")  # noqa: E731
        app = ClubHriApp(self.sync, llm=self.llm, asr=None, motion=None, tts=self.tts, engine=None,
                         confirm_fn=confirm_fn, conversation_timeout_s=self.args.timeout_conversacion,
                         owns_io=False, identity_real=identity_real, audio_sink=self.sync,
                         realtime=True)
        self.sync.attach(app)
        return app

    def _fsm_state(self) -> Optional[str]:
        """Estado de la FSM para la cara (None durante la identificación: el app es el de la
        visita anterior, ya en END)."""
        if self._identity_phase or self.app is None:
            return None
        return self.app.fsm.state.value

    def stop(self) -> None:
        super().stop()
        self.face.close()
        if self.cospeech is not None:
            self.cospeech.stop()
        if getattr(self, "_camera", None) is not None:
            try:
                self._camera.release()
            except Exception:
                pass
            self._camera = None

    def camera(self):
        """UNA suscripción a la cámara para toda la sesión: crear y destruir un nodo por foto
        mató el executor ROS compartido de hriego (InvalidHandle) y con él el foco de audio
        (robot mudo) y la propia cámara (prueba en el X2, 2026-10-24)."""
        if getattr(self, "_camera", None) is None:
            from hriego_v2.robot_io import RosImageCapture
            self._camera = RosImageCapture(self.args.camara, timeout_s=5.0)
        return self._camera

    def grab(self, prefix: str = "visita"):
        import cv2
        ok, frame = self.camera().read(timeout_s=3.0)
        if not ok or frame is None:
            return None, None
        d = Path(self.args.log_dir or "/tmp") / "fotos"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000) % 1000:03d}.jpg"
        cv2.imwrite(str(path), frame)
        return path, frame

    def admit(self, photo=None, frame=None, face_result=None) -> dict:
        if self.cospeech is not None:
            self.cospeech.new_visit()
        if self.args.identity_real and self.args.camara:
            return self.admit_identity()
        self.face.thinking()                      # saludo: LLM + TTS
        p = super().admit(photo, frame, face_result=face_result)
        self.sync.flush()
        return p

    # ------------------------------------------------------------ identidad real
    def admit_identity(self) -> dict:
        """Cara => ¿miembro? · miembro => pedir el QR · el QR (vinculado a esa persona) fija el
        tipo de miembro. La cara sola nunca confirma; sin QR válido => personal humano."""
        self._identity_phase = True
        self.face.set_mood(None)
        self.face.show("espera", "esperando visitante")
        got = self.wait_for_visitor()
        if got is None:                           # parada pedida mientras esperaba
            raise KeyboardInterrupt
        photo, _frame, face = got
        self.face.show("curioso", "cara delante")
        name = (face.get("profile") or {}).get("display_name")
        if face.get("status") != "KNOWN" or not name:
            return self._handoff("NO_MIEMBRO", face, REJECTED_PHRASE)
        if (face.get("profile") or {}).get("rank_code") == "EXTERNAL":     # WORKER: sin QR
            return self._handoff("WORKER_A_PERSONAL", face, WORKER_PHRASE.format(name=name))
        self.face.set_mood("hablando")
        self.say_direct(QR_ASK.format(name=name))
        self.face.show("atento", "esperando el QR")
        qr_frame, why = self.scan_qr()
        if qr_frame is None:
            return self._handoff(why, face, QR_MISSING_PHRASE)
        self._identity_phase = False
        self.face.thinking()                                    # verificación + saludo (LLM)
        p = super().admit(photo, qr_frame, face_result=face)   # confirm_identity con el QR
        self._log_identity(p)
        if p.get("status") == "REJECTED":                       # QR inválido / de otra persona
            from .fsm import State
            self.face.set_mood("triste")
            self.app._say(QR_REJECTED_PHRASE)
            self.speech.wait_idle()
            self.app.fsm.state = State.END
            self.logger.info("[identidad] QR no válido para esta persona: personal humano")
        self.sync.flush()
        return p

    def _log_identity(self, p: dict) -> None:
        self.logger.info(f"[identidad] {p.get('status')} · {p.get('name') or '-'} · rango "
                         f"{p.get('rank') or '-'} · fuente {p.get('source') or '-'}"
                         f"{(' · motivo ' + str(p.get('reason'))) if p.get('reason') else ''}")

    def _handoff(self, reason: str, face: dict, phrase: str) -> dict:
        """Visita derivada a personal humano: frase y fin (la FSM no saluda sin rango)."""
        from . import identity
        from .fsm import State
        self.app = self._build_app(identity_real=True, person=None)
        self._identity_phase = False
        self.face.set_mood("disculpa")
        self.speech.mark_turn()
        self.app._say(phrase)
        self.speech.wait_idle()
        self.app.fsm.state = State.END
        p = identity.rejected_person(reason, {"face_status": face.get("status"),
                                              "top1_score": face.get("top1_score")})
        self.logger.record({"t": time.time(), "kind": "admision", "state": "END", "person": p,
                            "spoken": [phrase], "identity_mode": "real"})
        self._log_identity(p)
        return p

    def say_direct(self, text: str) -> None:
        """Frase fuera de la FSM (petición del QR): TTS -> altavoz, esperando a que termine."""
        from .tts import voice_for
        pcm, sr, meta = self.tts.synthesize(text, voice_for("GUEST"))
        if len(pcm):
            self.speech.enqueue(pcm, sr, dict(meta, text=text))
        self.logger.info(f"  robot   : {text}")
        self.speech.wait_idle()

    def scan_qr(self, timeout_s: Optional[float] = None, capture: Any = None,
                decode_qr: Optional[Callable] = None) -> tuple[Any, str]:
        """Lee la cámara de forma continua hasta ver un QR (o agotar el tiempo, con un
        recordatorio a mitad). Devuelve (fotograma con QR | None, motivo)."""
        from x2_ultra.hri_runtime.confirm.qr_decode import decode_qr_texts
        decode_qr = decode_qr or decode_qr_texts
        timeout_s = QR_TIMEOUT_S if timeout_s is None else timeout_s
        live = capture is None
        capture = self.camera() if live else capture
        t0 = time.monotonic()
        reminded = False
        t_dbg, tries = 0.0, 0
        while not self._stop.is_set() and time.monotonic() - t0 < timeout_s:
            if not reminded and time.monotonic() - t0 > timeout_s / 2:
                reminded = True
                self.say_direct(QR_REMIND)
                self.face.show("atento", "esperando el QR")
            ok, frame = capture.read(timeout_s=1.0)
            if not ok or frame is None:
                continue
            tries += 1
            _err, texts = decode_qr(frame)
            if texts:
                self.logger.info(f"[identidad] QR leído en {time.monotonic() - t0:.1f} s ({tries} fotogramas)")
                return frame, "QR_OK"
            if live and time.monotonic() - t_dbg >= QR_DEBUG_EVERY_S:
                t_dbg = time.monotonic()
                try:
                    import cv2
                    d = Path(self.args.log_dir or "/tmp") / "fotos"
                    d.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(d / f"qr_{time.strftime('%H%M%S')}.jpg"), frame)
                except Exception:
                    pass
        self.logger.info(f"[identidad] sin QR legible tras {tries} fotogramas")
        return None, "QR_TIMEOUT"

    def wait_for_visitor(self, grab: Optional[Callable] = None, identify: Optional[Callable] = None):
        """Foto de la cámara de la cabeza hasta que haya una cara CERCANA (>= MIN_FACE_PX);
        una cara no reconocida debe verse en fotos consecutivas antes de tomarla como visita."""
        from x2_ultra.show.hri.face_id import identify_photo
        grab = grab or self.grab
        identify = identify or identify_photo
        self.logger.info("[identidad] esperando a un visitante delante de la cámara…")
        errors = 0
        unknown_streak = 0
        while not self._stop.is_set():
            photo, frame = grab()
            if photo is None:
                errors += 1
                self.logger.info(f"[identidad] sin imagen de {self.args.camara} ({errors})")
                if errors >= 5:
                    raise RuntimeError(f"la cámara {self.args.camara} no entrega imágenes")
                continue
            res = identify(photo)
            status = res.get("status")
            if status == "NO_FACE":
                unknown_streak = 0
                continue
            if status == "ERROR":
                errors += 1
                self.logger.info(f"[identidad] error de la sonda de cara: {res.get('error')}")
                if errors >= 5:
                    raise RuntimeError(f"sonda de cara: {res.get('error')}")
                continue
            bbox = res.get("bbox")
            face_px = abs(bbox[3] - bbox[1]) if bbox else MIN_FACE_PX
            if face_px < MIN_FACE_PX:
                unknown_streak = 0
                self.logger.info(f"[identidad] cara lejana ({face_px:.0f} px < {MIN_FACE_PX:.0f}); sigo esperando")
                continue
            if status != "KNOWN":
                unknown_streak += 1
                if unknown_streak < UNKNOWN_CONFIRM_FRAMES:
                    self.logger.info(f"[identidad] cara {status} ({res.get('top1_score')}, {face_px:.0f} px); "
                                     "confirmo con otra foto")
                    continue
            prof = res.get("profile") or {}
            self.logger.info(f"[identidad] cara {status}: {prof.get('display_name') or '-'} "
                             f"({res.get('top1_score')}) · foto {photo}")
            return photo, frame, res
        return None

    def _listen(self) -> None:
        super()._listen()
        self.face.listening()                     # micrófono abierto

    def transcribe(self, utt: dict) -> tuple[str, float]:
        self.face.thinking()                      # fin del habla del usuario: ASR + LLM + TTS
        return super().transcribe(utt)

    def _precalcular_acuses(self) -> None:
        """Sintetiza los acuses de todos los rangos (antes de armar: la sesión se construye antes)."""
        from .tts import voice_for
        t0 = time.monotonic()
        for rank, frases in ACUSES.items():
            for f in frases:
                try:
                    pcm, sr, _meta = self.tts.synthesize(f, voice_for(rank))
                except Exception:  # noqa: BLE001 - sin acuse no pasa nada
                    continue
                if len(pcm):
                    self._acuses.setdefault(rank, []).append((f, pcm, sr))
        self.logger.info(f"[voz] acuses listos: {sum(len(v) for v in self._acuses.values())} "
                         f"en {time.monotonic() - t0:.1f} s")

    def acuse(self, text: str) -> Optional[str]:
        """Acuse inmediato («Permítame un momento.») si el turno va a pasar por el LLM: no en
        despedidas (respuesta propia rápida), ni respondiendo a una oferta (sí/no), ni con frases
        muy cortas. Devuelve la frase encolada o None."""
        from . import intents
        if not self._acuses or self.app is None or self.app.fsm.person is None:
            return None
        fsm = self.app.fsm
        if fsm.state.value != "CONVERSATION" or len(re.findall(r"\w+", text)) < ACUSE_MIN_PALABRAS:
            return None
        if intents.fallback_intent(text)[0] == "DESPEDIDA":
            return None
        frases = self._acuses.get(fsm.person.get("rank") or "", [])
        if not frases:
            return None
        f, pcm, sr = frases[self._acuse_n % len(frases)]
        self._acuse_n += 1
        self.speech.mark_turn()
        self.sync.sink(pcm, sr, {"text": f, "acuse": True})
        self.logger.info(f"  robot   : {f}  (acuse)")
        return f

    def handle_text(self, text, utt=None, t_asr=None, asr_s=None) -> dict:
        self.face.thinking()
        self.acuse(text)
        rec = super().handle_text(text, utt=utt, t_asr=t_asr, asr_s=asr_s)
        self.sync.flush()
        return rec


def arm_and_start(backend: Any, log: Callable[[str], None]) -> list[str]:
    """``arm_session`` (fuente URS + limitador sembrado con los brazos MEDIDOS) y solo entonces
    la bomba de 50 Hz: construido con ``pump=True`` el backend publicaría READY antes de armar,
    con el limitador sembrado en READY en vez de en la postura real."""
    recal = getattr(backend.link, "recalibrate_height", None)
    if recal is not None:          # robot de pie (preflight): referencia de la altura relativa
        log(f"[armado] altura de referencia (leg_odometry): {'ok' if recal() else 'sin datos'}")
    problems = backend.arm_session()
    if problems:
        log(f"[armado] FALLO: {problems}")
        return problems
    backend.start()
    log("[armado] fuente URS registrada; READY a 50 Hz desde la postura medida")
    return []


def ready_test(backend: Any, seconds: float, log: Callable[[str], None]) -> dict:
    """READY N s (§4.1): error de seguimiento medido vs comandado y recortes del limitador."""
    from .backends.aimdk import READY_ARMS

    n0 = backend.limiter.nlim
    errs = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        meas = backend._arms_now()
        if meas is not None and time.monotonic() - t0 > 1.0:
            errs.append(np.abs(meas - READY_ARMS))
        time.sleep(0.1)
    res = {"segundos": seconds, "limiter_hits": int(backend.limiter.nlim - n0),
           "err_max_rad": round(float(np.max(errs)), 3) if errs else None,
           "err_medio_rad": round(float(np.mean(errs)), 3) if errs else None}
    log(f"[prueba] READY {seconds:.0f} s: {res}")
    return res


def gesture_test(backend: Any, log: Callable[[str], None]) -> dict:
    """Un emote (§4.2) y un señalamiento a la izquierda, con el robot quieto y callado."""
    out = {}
    t = time.monotonic() + GESTURE_LEAD_S
    backend.play_emote(*PRUEBA_EMOTE, t)
    log(f"[prueba] emote {PRUEBA_EMOTE} con stroke en +{GESTURE_LEAD_S:.1f} s")
    time.sleep(0.5)
    deadline = time.monotonic() + 12.0
    while backend.gesture_active() and time.monotonic() < deadline:
        time.sleep(0.1)
    out["emote"] = [e for e in backend.events if e["event"] in ("emote_dispatch", "emote_unavailable")][-1:]
    time.sleep(1.0)
    backend.point("LEFT", time.monotonic() + GESTURE_LEAD_S, 1.5)
    log("[prueba] señalamiento LEFT")
    deadline = time.monotonic() + 6.0
    while backend.gesture_active() and time.monotonic() < deadline:
        time.sleep(0.1)
    out["limiter_hits"] = int(backend.limiter.nlim)
    log(f"[prueba] gestos: {out}")
    return out


def dump_events(backend: Any, log_dir: Optional[str], etapa: str) -> Optional[Path]:
    if not log_dir:
        return None
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / time.strftime(f"actuado_{etapa}_%Y%m%d_%H%M%S_eventos.jsonl")
    with path.open("w", encoding="utf-8") as f:
        for e in list(backend.events):
            f.write(json.dumps(e, ensure_ascii=False, default=str) + "\n")
        if backend.dry_run:
            for c in list(backend.commands)[-2000:]:
                f.write(json.dumps({"event": "dry_command", **c}, ensure_ascii=False, default=str) + "\n")
    return path


# =========================================================================== CLI
def build_parser() -> argparse.ArgumentParser:
    ap = LR.build_parser()
    ap.prog = "python -m x2_ultra.hri_runtime.live_robot_actuado"
    ap.description = "Runner en vivo CON ACTUACIÓN (emotes + señalamiento, sin marcha)."
    ap.add_argument("--etapa", choices=ETAPAS, required=True)
    ap.add_argument("--arm", action="store_true", help="publicar de verdad (etapas lenta/completa)")
    ap.add_argument("--prueba-ready-s", type=float, default=float(os.environ.get("HRI_READY_TEST_S", 5)),
                    help="segundos de READY tras armar, antes de conversar (0 = omitir)")
    ap.add_argument("--solo-prueba", action="store_true",
                    help="armar → READY → 1 emote → 1 señalamiento → parar (sin conversación)")
    ap.add_argument("--preflight-espera", type=float, default=10.0)
    ap.add_argument("--solo-preflight", action="store_true",
                    help="solo comprobaciones (lee el robot, no arma ni publica) y salir")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    etapa = safety_preflight(argv)
    os.environ.setdefault("HRIEGO_NO_ACTUATION", "1")   # nodos hriego_*: solo audio
    args = build_parser().parse_args(argv)
    args.mic_canal = LR._parse_channel(args.mic_canal)
    from . import identity
    args.identity_real = identity.identity_real_from_env()
    logger = ActuatedTurnLogger(Path(args.log_dir) if args.log_dir else None)
    log = logger.info
    if args.identity_real and not args.camara:
        raise SystemExit("HRI_IDENTITY_REAL=1 requiere --camara")

    # SIGTERM (docker stop) → mismo camino que Ctrl-C: el finally para el robot con orden.
    def _sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _sigterm)

    # 1) backend PRIMERO: RobotLink hace rclpy.init(); los nodos de audio de hriego lo reutilizan.
    from .backends.aimdk import AimdkBackend
    cfg = make_config(etapa)
    if cfg.ros_domain is not None:
        os.environ["ROS_DOMAIN_ID"] = str(cfg.ros_domain)
    backend = AimdkBackend(cfg, name="hri_club_act", pump=False)   # sin bomba hasta armar
    log(f"[seguridad] etapa={etapa} dry_run={backend.dry_run} joint_vmax={cfg.joint_vmax} "
        f"emote_vmax={cfg.emote_vmax} amax={cfg.amax} cintura={cfg.waist_mode} cabeza={cfg.head_channel} "
        f"marcha=deshabilitada SONIC=clips-precomputados prioridad={cfg.priority}")
    session = watchdog = mic_sub = asr = asistente = None
    code = 1
    touched = False          # True desde que se intenta armar: solo entonces hay que parar
    try:
        photo = frame = None
        if not (args.solo_preflight or args.solo_prueba):
            # TODO lo pesado ANTES de armar: cargar Supertonic/onnxruntime con el robot armado
            # acaparó el GIL > 0,5 s y dejó sin servicio el bucle de 50 Hz y la IMU (robot real,
            # 2026-10-24: el watchdog hizo e-stop 2,9 s después del READY).
            from .tts import make_tts, voice_for
            tts = make_tts(args.tts)
            t_w = time.monotonic()
            tts.synthesize("Hola.", voice_for(args.visit))          # calentamiento del modelo
            log(f"[tts] motor: {tts.name} (calentado en {time.monotonic() - t_w:.1f} s)")
            from .nube import envolver_tts, clientes_llm
            tts = envolver_tts(tts, log)   # sin HRI_NUBE_URL: el mismo objeto (local ya caliente)
            llm = LR.make_llm(args.llm_espera, args.permitir_sin_llm, log)
            if llm is not None:            # caché de prompt de llama.cpp caliente antes de armar
                for etiqueta, cliente in clientes_llm(llm.client):   # nube y respaldo local
                    t_w = time.monotonic()
                    previo, llm.client = llm.client, cliente
                    try:
                        llm.respond("GREETING", "REGULAR", {"name": "Sebastian", "rank": "REGULAR"}, retry=False)
                        log(f"[llm] {etiqueta}calentado en {time.monotonic() - t_w:.1f} s")
                    except Exception as exc:   # noqa: BLE001 - el calentamiento nunca bloquea
                        log(f"[llm] {etiqueta}calentamiento fallido: {exc}")
                    finally:
                        llm.client = previo
            asr = LR.connect_asr(args.asr_sock, args.asr_espera, log) if args.entrada == "mic" else None
            player = LR.make_player(args.sin_voz)
            text_source: Optional[Callable[[], Optional[str]]] = None
            if args.entrada == "stdin":
                def _stdin() -> Optional[str]:
                    try:
                        return input("usuario> ")
                    except EOFError:
                        return None
                text_source = _stdin
            session = ActuatedSession(args, backend=backend, llm=llm, tts=tts, player=player, asr=asr,
                                      text_source=text_source, logger=logger)
            if args.entrada == "mic":
                mic_sub = LR.RosMicSubscriber(session.mic, args.mic_topic, args.mic_qos)
                log(f"[mic] suscrito a {args.mic_topic} (QoS {args.mic_qos})")
                # el asistente de voz de fábrica no compite por el micrófono ni el altavoz
                asistente = Asistente.from_env(log=log)
                asistente.pause()
            # identidad real: la foto se toma en cada visita (ActuatedSession.wait_for_visitor)
        problems = preflight_robot(backend, wait_s=args.preflight_espera, log=log)
        if problems:
            for p in problems:
                log(f"[preflight] FALLO: {p}")
            return 4
        if args.solo_preflight:
            log("[preflight] OK (no se armó nada)")
            return 0
        touched = True
        if arm_and_start(backend, log):
            return 4
        stop_evt = threading.Event()

        def on_trip(why: str) -> None:
            log(f"[SEGURIDAD] {why}: e-stop y fin de la sesión")
            stop_evt.set()
            if session is not None:
                session.stop()
        watchdog = Watchdog(backend, on_trip).start()      # vigila desde el primer tick armado
        if args.prueba_ready_s > 0:
            ready_test(backend, args.prueba_ready_s, log)
            if stop_evt.is_set():
                return 5
        if args.solo_prueba:
            gesture_test(backend, log)
            code = 0 if watchdog.reason is None else 5
            return code

        if stop_evt.is_set():
            return 5
        code = session.run(photo, frame)
        return 5 if watchdog.reason else code
    except KeyboardInterrupt:
        if watchdog is not None and watchdog.reason:
            return 5
        log("[sesión] interrumpido: paro el robot")
        return 130
    finally:
        if watchdog is not None:
            watchdog.stop()
        if session is not None:
            session.stop()
            session.speech.stop()
        try:
            if touched:                         # vel 0, gestos abortados, DELETE + STAND (worker)
                backend.stop()
                t_end = time.monotonic() + 6.0
                while time.monotonic() < t_end and not (backend.svc.get("stop_final")
                                                         or backend.svc.get("estop_final")):
                    time.sleep(0.1)
        finally:
            path = dump_events(backend, args.log_dir, etapa)
            backend.close()
            log(f"[sesión] backend cerrado; eventos: {path}")
        if asistente is not None:
            asistente.resume()                  # el asistente de fábrica vuelve a su modo normal
        if mic_sub is not None:
            mic_sub.close()
        if asr is not None:
            try:
                asr.stop()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
