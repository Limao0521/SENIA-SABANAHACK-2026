"""Expresiones faciales del X2 (pantalla de la cara) sincronizadas con la interacción HRI.

Interfaz AimDK (docs X2 AimDK v0.8, «5.2.2 Screen Control» y ejemplo Python «6.1.26
Emoji Control» / ``py_examples/play_emoji``):

- servicio ``/aimdk_5Fmsgs/srv/PlayEmoji``, tipo ``aimdk_msgs/srv/PlayEmoji``;
- petición ``header`` (``header.header.stamp``), ``emotion_id`` (uint8), ``mode`` (1 = una vez,
  2 = bucle), ``priority`` (int32); respuesta ``success`` + ``message`` (el header no se rellena);
- la petición con prioridad >= la actual la sustituye; los módulos del sistema usan 8
  (sobretemperatura) y 10 (fallo grave). La doc recomienda > 10 para no ser desplazado;
- lo sirve el PC de interacción (PC3): la llamada cruza de máquina ⇒ reintentos cortos.

Solo pantalla: no mueve nada. Aun así va por la lista blanca de ``hriego_v2.robot_io``
(``HRIEGO_NO_ACTUATION=1``), ampliada SOLO con ``PlayEmoji``.

Diseño (lecciones del robot real):

- UN nodo y UN cliente por sesión, creados la primera vez en el hilo de trabajo y reutilizados
  (crear/destruir nodos por llamada mató el executor compartido ⇒ robot sin audio);
- ninguna llamada ROS en el hilo de quien pide la expresión: ``show`` solo deja el objetivo y
  despierta al hilo ``hri-cara``; el hilo coalesce (gana la última), no repite la misma
  expresión y respeta un intervalo mínimo entre llamadas;
- si el tipo o el servicio no existen, la función se desactiva sola con una línea de log.

Activación: ``HRI_EXPRESIONES=1`` (el compose con actuación la pone a 1); ausente o 0 ⇒ objeto
inerte (ningún hilo, ningún nodo, ningún log). En la etapa ``seco`` (y en el PC) va en modo
registro: escribe ``[cara] …`` y no llama a ROS, salvo ``HRI_EXPRESIONES_EN_SECO=1`` (la pantalla
no es actuación: permite probar la cara sin armar).

Mapa estado → expresión: ver ``EXPRESIONES``, ``ESTADO_HABLANDO`` y ``EMOTE_EXPRESION`` (tabla en
docs/reto-2/DEPLOY_SABANAHACK4.md, «Expresiones faciales»).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Optional

SERVICE = "/aimdk_5Fmsgs/srv/PlayEmoji"
NODE_NAME = "hri_club_cara"           # prefijo hri_club_ ⇒ lo revisa deploy/hri_audit_ros.py
MODE_ONCE, MODE_LOOP = 1, 2
DEFAULT_PRIORITY = 60                 # en el X2 real la cara ya se reproduce a prioridad 60 (medido
                                      # 2026-10-24: 11 se rechaza "prioridad menor que la actual"); igual sustituye
MIN_INTERVAL_S = 0.4                  # entre llamadas al servicio (rate limit)

# Tabla de emojis del firmware (docs AimDK X2, «Emoji ID»): solo IDs que existen.
EMOJI_IDS = {1: "parpadeo", 10: "calma (ojos 1)", 11: "calma (ojos 2)", 20: "calma (juego)",
             30: "calma (tierno 1)", 31: "calma (tierno 2)", 32: "calma (tierno 3)",
             33: "calma (tierno 4)", 40: "cerrar ojos", 50: "abrir ojos", 60: "aburrido",
             70: "anómalo", 80: "dormido", 90: "feliz", 100: "muy feliz", 101: "eufórico",
             110: "triste", 120: "compasión", 130: "confundido", 140: "sorprendido",
             150: "tierno", 160: "serio", 170: "pensando", 180: "enfadado", 190: "muy enfadado",
             200: "adoración", 210: "mucha adoración", 220: "cargando"}

# expresión lógica → (emotion_id, modo)
EXPRESIONES: dict[str, tuple[int, int]] = {
    "espera": (10, MODE_LOOP),        # sin visitante: neutra/atenta
    "curioso": (50, MODE_ONCE),       # cara detectada: «abre los ojos»
    "atento": (11, MODE_LOOP),        # pidiendo / esperando el QR
    "escuchando": (1, MODE_LOOP),     # micrófono abierto: parpadeo tranquilo
    "pensando": (170, MODE_LOOP),     # ASR hecho, LLM/TTS en curso
    "hablando": (90, MODE_LOOP),      # voz del robot (conversación, guía, despedida)
    "alegre": (100, MODE_LOOP),       # saludo / despedida con gesto
    "encantado": (200, MODE_LOOP),    # SOCIAL_HEART
    "euforico": (101, MODE_LOOP),     # APPLAUSE
    "disculpa": (120, MODE_LOOP),     # negación / derivación a personal
    "serio": (160, MODE_LOOP),        # negación fuerte
    "triste": (110, MODE_LOOP),       # QR rechazado
    "reposo": (10, MODE_ONCE),        # cierre de sesión: UNA vez ⇒ termina y no deja un bucle
}                                     # a prioridad 60 tapando la cara del sistema
# expresión mientras habla, según el estado de la FSM (si ningún emote/override la fija)
ESTADO_HABLANDO = {"GREETING": "alegre", "CONFIRMACION": "hablando", "DESTINO_DE_ROL": "hablando",
                   "CONVERSATION": "hablando", "GUIDANCE_OFFER": "hablando",
                   "FAREWELL": "alegre", "END": "hablando"}
# tipo de gesto (contract.GESTURES) → expresión
EMOTE_EXPRESION = {"GREETING_VIP": "alegre", "GREETING_REGULAR": "alegre", "GREETING_GUEST": "alegre",
                   "GREETING_STAFF": "alegre", "GREETING_WORKER": "alegre",
                   "FAREWELL_WAVE": "alegre", "SOCIAL_HEART": "encantado", "APPLAUSE": "euforico",
                   "NEGATION_NORMAL": "disculpa", "NEGATION_STRONG": "serio",
                   "CONFIRMATION": "hablando", "THUMBS_UP": "alegre",
                   "POINT_RIGHT": "hablando", "POINT_LEFT": "hablando", "POINT_FRONT": "hablando"}


def emote_type(motion_id: Any) -> Optional[str]:
    """motion_id del preset → tipo de gesto del contrato (GREETING_VIP, APPLAUSE…)."""
    from .contract import GESTURES
    for name, g in GESTURES.items():
        if g.get("motion_id") is not None and g.get("motion_id") == motion_id:
            return name
    return None


class FaceUnavailable(RuntimeError):
    """El tipo PlayEmoji o el servicio no existen: la función se desactiva."""


class RosEmojiClient:
    """Cliente ``PlayEmoji`` sobre el executor compartido de hriego (un nodo para toda la
    sesión, nunca destruido aquí: lo cierra el atexit de robot_io)."""

    def __init__(self, node_factory: Optional[Callable[[str], Any]] = None, wait_s: float = 3.0,
                 timeout_s: float = 0.3, retries: int = 6):
        self.node_factory = node_factory
        self.wait_s, self.timeout_s, self.retries = float(wait_s), float(timeout_s), int(retries)
        self._node = self._client = self._srv = None

    def ensure(self) -> None:
        if self._client is not None:
            return
        try:
            from aimdk_msgs.srv import PlayEmoji
        except Exception as exc:  # noqa: BLE001 - imagen sin el tipo
            raise FaceUnavailable(f"aimdk_msgs.srv.PlayEmoji no importa ({exc})") from exc
        factory = self.node_factory
        if factory is None:
            from .live_robot import _ros_node as factory     # executor compartido + lista blanca
        self._srv = PlayEmoji
        self._node = factory(NODE_NAME)
        self._client = self._node.create_client(PlayEmoji, SERVICE)
        if not self._client.wait_for_service(timeout_sec=self.wait_s):
            raise FaceUnavailable(f"servicio {SERVICE} no disponible en {self.wait_s:.0f} s")

    def play(self, emotion_id: int, mode: int, priority: int) -> tuple[bool, str]:
        self.ensure()
        req = self._srv.Request()
        req.emotion_id = int(emotion_id)
        req.mode = int(mode)
        req.priority = int(priority)
        for _ in range(self.retries):          # el peer remoto (PC3) no siempre contesta a la 1ª
            try:
                req.header.header.stamp = self._node.get_clock().now().to_msg()
            except Exception:  # noqa: BLE001 - header distinto según versión del SDK
                pass
            fut = self._client.call_async(req)
            done = threading.Event()
            fut.add_done_callback(lambda _f: done.set())
            if done.wait(self.timeout_s):
                resp = fut.result()
                if resp is None:
                    return False, "sin respuesta"
                return bool(resp.success), str(getattr(resp, "message", ""))
        return False, f"sin respuesta tras {self.retries} intentos"


class FaceExpressions:
    """Expresiones de la cara para una sesión HRI. Todas las llamadas públicas son baratas y
    no bloquean (seguras desde el hilo de voz o el de la sesión)."""

    def __init__(self, client: Any = None, *, enabled: bool = True, dry_run: bool = False,
                 log: Callable[[str], None] = print, priority: int = DEFAULT_PRIORITY,
                 min_interval_s: float = MIN_INTERVAL_S,
                 state_fn: Optional[Callable[[], Optional[str]]] = None):
        self.enabled = bool(enabled)
        self.dry_run = bool(dry_run)
        self.client = client
        self.log = log
        self.priority = int(priority)
        self.min_interval_s = float(min_interval_s)
        self.state_fn = state_fn
        self.mood: Optional[str] = None       # expresión para la próxima voz (emote / override)
        self.current: Optional[str] = None    # última expresión pedida
        self.sent: list[tuple[str, int, int]] = []    # (nombre, id, modo) enviados (o registrados)
        self.errors = 0
        self._target: Optional[tuple[str, str]] = None
        self._last_id: Optional[tuple[int, int]] = None
        self._last_t = 0.0
        self._cv = threading.Condition()
        self._closed = False
        self._busy = False
        self._thread: Optional[threading.Thread] = None
        if self.enabled:
            if not self.dry_run and self.client is None:
                self.client = RosEmojiClient()
            self._thread = threading.Thread(target=self._run, name="hri-cara", daemon=True)
            self._thread.start()
            self.log(f"[cara] expresiones activas ({'registro, sin ROS' if self.dry_run else SERVICE}"
                     f", prioridad {self.priority})")

    # ------------------------------------------------------------ construcción
    @classmethod
    def from_env(cls, etapa: str = "seco", log: Callable[[str], None] = print,
                 env: Optional[dict] = None, **kw) -> "FaceExpressions":
        env = os.environ if env is None else env
        if env.get("HRI_EXPRESIONES", "0").strip().lower() in ("", "0", "false", "no", "off"):
            return cls(enabled=False, log=log, **kw)
        dry = etapa == "seco" and env.get("HRI_EXPRESIONES_EN_SECO", "0") != "1"
        prio = int(env.get("HRI_EXPRESIONES_PRIORIDAD", DEFAULT_PRIORITY))
        return cls(enabled=True, dry_run=dry, log=log, priority=prio, **kw)

    # ------------------------------------------------------------ API (no bloquea)
    def show(self, name: str, why: str = "") -> None:
        """Pide la expresión ``name`` ya (el hilo la envía; gana la última petición)."""
        if not self.enabled or name not in EXPRESIONES:
            return
        with self._cv:
            self.current = name
            self._target = (name, why)
            self._cv.notify()

    def set_mood(self, name: Optional[str]) -> None:
        """Expresión con la que hablará la próxima voz (None ⇒ la del estado de la FSM)."""
        if self.enabled:
            self.mood = name if name in EXPRESIONES else None

    def thinking(self) -> None:
        """Turno nuevo: LLM/TTS en curso. Olvida el override del turno anterior."""
        self.set_mood(None)
        self.show("pensando", "turno")

    def listening(self) -> None:
        self.set_mood(None)
        self.show("escuchando", "micrófono abierto")

    def emote(self, motion_id: Any = None, typ: Optional[str] = None) -> None:
        """Emote despachado (GestureSync): su expresión manda durante esa frase."""
        if not self.enabled:
            return
        typ = typ or emote_type(motion_id)
        name = EMOTE_EXPRESION.get(typ or "")
        if name:
            self.set_mood(name)
            self.show(name, f"emote {typ}")

    def speech_start(self, meta: Optional[dict] = None) -> None:
        """SpeechPlayer.on_play: arranca una frase."""
        if self.enabled:
            self.show(self.mood or ESTADO_HABLANDO.get(self._state() or "", "hablando"), "voz")

    def speech_end(self) -> None:
        """SpeechPlayer.on_idle: la cola de voz quedó vacía. Si la visita acabó ⇒ espera."""
        if self.enabled and self._state() == "END":
            self.set_mood(None)
            self.show("espera", "fin de la visita")

    def close(self, final: Optional[str] = "reposo") -> None:
        if not self.enabled or self._thread is None:
            return
        if final:
            self.show(final, "fin de sesión")
        with self._cv:
            self._closed = True
            self._cv.notify()
        self._thread.join(timeout=1.0)

    def wait_sent(self, timeout: float = 2.0) -> bool:
        """Tests/diagnóstico: espera a que no quede ninguna petición pendiente."""
        t_end = time.monotonic() + timeout
        while time.monotonic() < t_end:
            with self._cv:
                if self._target is None and not self._busy:
                    return True
            time.sleep(0.01)
        return False

    # ------------------------------------------------------------ internos
    def _state(self) -> Optional[str]:
        try:
            return self.state_fn() if self.state_fn else None
        except Exception:  # noqa: BLE001
            return None

    def _run(self) -> None:
        while True:
            with self._cv:
                while self._target is None and not self._closed:
                    self._cv.wait()
                if self._target is None and self._closed:
                    return
                name, why = self._target
                self._target = None
                self._busy = True
            try:
                self._send(name, why)
            finally:
                self._busy = False
            if not self.enabled:
                return

    def _send(self, name: str, why: str) -> None:
        eid, mode = EXPRESIONES[name]
        if self._last_id == (eid, mode):
            return
        wait = self.min_interval_s - (time.monotonic() - self._last_t)
        if wait > 0:
            time.sleep(wait)
            with self._cv:                         # durante la espera llegó otra: gana la última
                if self._target is not None:
                    return
        self._last_t = time.monotonic()
        tag = f"[cara] {name} → emoji {eid} ({EMOJI_IDS.get(eid, '?')}, " \
              f"{'bucle' if mode == MODE_LOOP else 'una vez'}){(' · ' + why) if why else ''}"
        if self.dry_run:
            self._last_id = (eid, mode)
            self.sent.append((name, eid, mode))
            self.log(tag + " [registro]")
            return
        try:
            ok, msg = self.client.play(eid, mode, self.priority)
        except FaceUnavailable as exc:
            self.enabled = False
            self.log(f"[cara] expresiones DESACTIVADAS: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - nunca tumba la sesión
            ok, msg = False, f"{type(exc).__name__}: {exc}"
        if ok:
            self._last_id = (eid, mode)
            self.sent.append((name, eid, mode))
            self.log(tag)
        else:
            self.errors += 1
            self._last_id = None
            self.log(f"{tag} FALLÓ: {msg}")
            if self.errors >= 5 and not self.sent:
                self.enabled = False
                self.log("[cara] expresiones DESACTIVADAS: 5 fallos seguidos sin ningún éxito")
