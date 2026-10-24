"""Runner EN VIVO y SIN ACTUACIÓN del runtime HRI conversacional (Club N) en el X2 Ultra.

    python -m x2_ultra.hri_runtime.live_robot --visit VIP            # micrófono → voz (robot)
    python -m x2_ultra.hri_runtime.live_robot --visit GUEST --sin-voz
    python -m x2_ultra.hri_runtime.live_robot --entrada stdin --sin-voz --permitir-sin-llm  # PC, sin ROS
    python -m x2_ultra.hri_runtime.live_robot --check-only           # comprobación previa

Cadena por turno (semidúplex, sin barge-in)::

    /aima/hal/audio/capture (AudioCapture, S16LE entrelazado) → canal de micrófono → 16 kHz mono
    → VAD por energía (numpy) → utterance float32 → AsrClient (UNIX socket, contenedor x2-asr)
    → ClubHriApp.user_turn(text=...) (FSM + x2-llm) → TTS por frase (Supertonic)
    → cola de reproducción → AimdkAudioPlayer (foco de audio + /aima/hal/audio/playback)

EL ROBOT NUNCA SE MUEVE. Capas de seguridad, de fuera a dentro:

1. ``safety_preflight`` (antes de importar nada de ROS): aborta si existe ``X2_REAL`` o se pasa
   ``--arm``; fuerza ``HRI_NO_ACTUATION=1`` y ``HRIEGO_NO_ACTUATION=1`` (aborta si alguien los
   puso a otro valor).
2. ``install_import_guard``: un finder en ``sys.meta_path`` impide importar los módulos que
   actúan (``x2_ultra.real``, backends aimdk/twin/show, gemelo, motor del show, motion_online,
   mujoco). Si alguno ya estaba importado, aborta.
3. ``ClubHriApp`` con ``NullBackend`` y ``motion=None`` (sin servidor de movimiento, sin MuJoCo):
   las intenciones motoras (EMOTE/POINT/WALK_WITH/DISPATCH_D1) solo se registran como
   NO EJECUTADAS.
4. Nodos ROS solo vía ``hriego_v2.robot_io._ros_node`` (lista blanca ``_guard_node``: el único
   publicador posible es ``/aima/hal/audio/playback`` y los únicos servicios, el foco de audio);
   el micrófono es una suscripción (no actúa). ``assert_node_guarded`` lo comprueba.
5. ``deploy/hri_club_sin_actuacion.sh`` audita los nodos ``hriego_*``/``hri_club_*`` en vivo.

Formato del micrófono: según el gemelo (src/x2_ultra/twin/ros_audio.py) 16 kHz, 6 canales
entrelazados (4 mic + 2 referencia), S16LE. NO verificado en el robot real ⇒ se lee de
``msg.info`` / ``mic_channels`` en cada mensaje y se elige el canal ``--mic-canal`` (0 por defecto).

Este módulo NO importa mujoco/torch/faster_whisper (la imagen x2-hri no los tiene).
"""
from __future__ import annotations

import argparse
import importlib.abc
import json
import os
import queue
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

REPO = Path(__file__).resolve().parents[3]
SR = 16_000
RANKS = ("VIP", "REGULAR", "GUEST", "STAFF", "WORKER")
MIC_TOPIC = "/aima/hal/audio/capture"
# Cámara frontal central (mira al frente). La RGB-D de la cabeza mira al suelo en el X2 real
# (verificado a bordo el 2026-10-24); la cruda de la central no siempre publica: comprimida.
CAMERA_TOPIC = os.environ.get("HRI_CAMARA_TOPIC", "/aima/hal/sensor/rgb_head_front_center/rgb_image/compressed")
DEFAULT_ASR_SOCK = "/tmp/hri_asr.sock"
MOTOR_ACTS = ("EMOTE", "POINT", "WALK_WITH", "DISPATCH_D1")

# Mismas personas simuladas que wavtest (no se importa wavtest: arrastra app/vídeo del show).
PERSON_BY_RANK = {
    "VIP": {"person_id": "P005", "name": "Laura", "rank": "VIP"},
    "REGULAR": {"person_id": "P003", "name": "Sebastian", "rank": "REGULAR"},
    "GUEST": {"person_id": "P004", "name": "Mario", "rank": "GUEST"},
    "STAFF": {"person_id": "P001", "name": "Luis", "rank": "STAFF",
              "work_order": {"area": "oficinas_administrativas", "task": "turno administrativo"}},
    "WORKER": {"person_id": "P002", "name": "Josaed", "rank": "WORKER",
               "worker_subtype": "logistica",
               "work_order": {"area": "almacenes_logistica", "task": "descarga de insumos"}},
}


# =========================================================================== seguridad
class SafetyAbort(SystemExit):
    """Aborto de seguridad (código 3): el runner se niega a arrancar."""

    def __init__(self, msg: str):
        print(f"[SEGURIDAD] ABORTO: {msg}", file=sys.stderr, flush=True)
        super().__init__(3)
        self.msg = msg


def safety_preflight(argv: Optional[list[str]] = None,
                     environ: Optional[dict] = None) -> None:
    """Primera línea de defensa; se llama ANTES de importar rclpy/aimdk_msgs/el app."""
    argv = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    if any(a == "--arm" or a.startswith("--arm=") for a in argv):
        raise SafetyAbort("--arm no existe en el runner sin actuación (el robot nunca se mueve)")
    if "X2_REAL" in env:
        raise SafetyAbort(f"X2_REAL está definido ({env.get('X2_REAL')!r}): esta sesión es sin "
                          "actuación; quítalo del entorno")
    for key in ("HRI_NO_ACTUATION", "HRIEGO_NO_ACTUATION"):
        val = env.get(key)
        if val is None:
            env[key] = "1"
        elif str(val).strip() != "1":
            raise SafetyAbort(f"{key}={val!r}: el runner en vivo exige {key}=1")


# Módulos que pueden mover el robot (o cargar física/MuJoCo): nunca en este proceso.
BLOCKED_MODULES = (
    "x2_ultra.real",
    "x2_ultra.twin",
    "x2_ultra.control",
    "x2_ultra.sim",
    "x2_ultra.hri_runtime.backends.aimdk",
    "x2_ultra.hri_runtime.backends.twin",
    "x2_ultra.hri_runtime.backends.show",
    "x2_ultra.hri_runtime.motion_online",
    "x2_ultra.show.engine",
    "x2_ultra.show.stage",
    "x2_ultra.show.club",
    "x2_ultra.show.guide",
    "x2_ultra.show.app",
    "x2_ultra.show.venue",
    "x2_ultra.show.dance",
    "x2_ultra.show.commercial",
    "x2_ultra.show.hri.player",
    "x2_ultra.show.hri.motion",
    "x2_ultra.show.hri.pointing",
    "x2_ultra.show.hri.tour",
    "x2_ultra.show.hri.render",
    "mujoco",
)


class ActuationImportBlocked(ImportError):
    pass


def _is_blocked(fullname: str, blocked=BLOCKED_MODULES) -> bool:
    return any(fullname == b or fullname.startswith(b + ".") for b in blocked)


class ActuationImportGuard(importlib.abc.MetaPathFinder):
    """Finder que corta en seco cualquier import de un módulo de actuación."""

    def __init__(self, blocked=BLOCKED_MODULES):
        self.blocked = tuple(blocked)
        self.attempts: list[str] = []

    def find_spec(self, fullname, path=None, target=None):
        if _is_blocked(fullname, self.blocked):
            self.attempts.append(fullname)
            print(f"[SEGURIDAD] import bloqueado: {fullname}", file=sys.stderr, flush=True)
            raise ActuationImportBlocked(
                f"HRI_NO_ACTUATION: el import de {fullname!r} está prohibido en el runner en vivo")
        return None


_GUARD: Optional[ActuationImportGuard] = None


def install_import_guard(blocked=BLOCKED_MODULES) -> ActuationImportGuard:
    """Instala el guard (idempotente). Aborta si ya hay un módulo prohibido cargado (los
    módulos presentes en sys.modules no pasan por los finders)."""
    global _GUARD
    loaded = sorted(m for m in list(sys.modules) if _is_blocked(m, blocked))
    if loaded:
        raise SafetyAbort(f"módulos de actuación ya importados en este proceso: {loaded}")
    if _GUARD is None or _GUARD not in sys.meta_path:
        _GUARD = ActuationImportGuard(blocked)
        sys.meta_path.insert(0, _GUARD)
    return _GUARD


def uninstall_import_guard() -> None:
    """Solo para tests."""
    global _GUARD
    if _GUARD is not None and _GUARD in sys.meta_path:
        sys.meta_path.remove(_GUARD)
    _GUARD = None


def assert_node_guarded(node) -> None:
    """Comprueba que el nodo pasó por robot_io._guard_node (lista blanca activa)."""
    pub = getattr(getattr(node, "create_publisher", None), "__name__", "")
    cli = getattr(getattr(node, "create_client", None), "__name__", "")
    if pub != "guarded_publisher" or cli != "guarded_client":
        raise SafetyAbort("el nodo ROS no tiene la lista blanca de robot_io (HRIEGO_NO_ACTUATION)")


def _ros_node(name: str):
    """Único punto de creación de nodos: el executor compartido de hriego_v2 + lista blanca."""
    from hriego_v2.robot_io import _ros_node as _rn
    node = _rn(name)
    assert_node_guarded(node)
    return node


# =========================================================================== audio (puro numpy)
_FMT_DTYPE = {"s16le": "<i2", "s16": "<i2", "pcm_s16le": "<i2", "int16": "<i2",
              "s32le": "<i4", "s32": "<i4", "int32": "<i4",
              "f32le": "<f4", "f32": "<f4", "float32": "<f4", "flt": "<f4"}
_FMT_SCALE = {"<i2": 32768.0, "<i4": 2147483648.0, "<f4": 1.0}


def extract_mono(raw, channels: int, sample_format: str = "s16le",
                 channel: Any = 0, mic_channels: int = 0) -> np.ndarray:
    """Bytes PCM entrelazados → float32 mono [-1, 1].

    ``channel``: índice del canal (int) o ``"mean"`` (media de los ``mic_channels``
    primeros canales; si no se conoce, de todos)."""
    dt = _FMT_DTYPE.get(str(sample_format or "s16le").strip().lower())
    if dt is None:
        raise ValueError(f"formato de micrófono no soportado: {sample_format!r}")
    width = np.dtype(dt).itemsize
    if isinstance(raw, (list, tuple)):
        raw = bytes(raw)
    buf = raw if isinstance(raw, (bytes, bytearray)) else memoryview(raw).cast("B")
    n = len(buf) // width
    x = np.frombuffer(bytes(buf[: n * width]), dtype=dt)
    ch = max(1, int(channels or 1))
    frames = len(x) // ch
    x = x[: frames * ch].reshape(frames, ch)
    if channel == "mean":
        k = int(mic_channels) if mic_channels and 0 < int(mic_channels) <= ch else ch
        mono = x[:, :k].astype(np.float32).mean(axis=1)
    else:
        idx = int(channel)
        if not 0 <= idx < ch:
            raise ValueError(f"canal {idx} fuera de rango (el mensaje trae {ch} canales)")
        mono = x[:, idx].astype(np.float32)
    return (mono / _FMT_SCALE[dt]).astype(np.float32)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Remuestreo sin scipy (la imagen del robot puede no traerla): diezmado por medias
    de bloque si la razón es entera; si no, interpolación lineal con un filtro de caja
    previo al bajar de tasa (suficiente para voz → ASR/altavoz)."""
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    sr_in, sr_out = int(sr_in), int(sr_out)
    if sr_in == sr_out or len(x) == 0:
        return x.copy()
    if sr_in > sr_out and sr_in % sr_out == 0:
        k = sr_in // sr_out
        n = len(x) // k
        return x[: n * k].reshape(n, k).mean(axis=1).astype(np.float32)
    if sr_in > sr_out:
        k = max(1, int(round(sr_in / sr_out)))
        if k > 1:
            x = np.convolve(x, np.ones(k, dtype=np.float32) / k, mode="same").astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    t_out = np.arange(n_out, dtype=np.float64) * (sr_in / sr_out)
    return np.interp(t_out, np.arange(len(x), dtype=np.float64), x).astype(np.float32)


def write_wav_s16(path: Path, pcm: np.ndarray, sr: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sr))
        w.writeframes((np.clip(pcm, -1.0, 1.0) * 32767.0).astype("<i2").tobytes())
    return path


# =========================================================================== VAD / endpointing
class EnergyVad:
    """Endpointing por energía (dBFS por trama de 30 ms) con suelo de ruido adaptativo.

    Voz si ``dB > max(ruido + margen, mínimo absoluto)``; arranca tras ``start_ms`` de voz
    seguida; cierra tras ``end_silence_ms`` de silencio (histéresis de 3 dB); incluye
    ``pre_roll_ms`` antes del arranque; descarta < ``min_speech_ms``; corta a ``max_utt_s``.
    ``feed`` devuelve la lista de utterances cerradas ({"pcm", "dur_s", "t_end", "peak_db"})."""

    def __init__(self, sr: int = SR, frame_ms: int = 30, margin_db: float = 12.0,
                 min_dbfs: float = -45.0, start_ms: int = 120, end_silence_ms: int = 700,
                 pre_roll_ms: int = 300, min_speech_ms: int = 350, max_utt_s: float = 15.0,
                 noise_init_db: float = -60.0, noise_alpha: float = 0.05,
                 calib_ms: int = 500):
        self.sr = int(sr)
        self.frame = int(self.sr * frame_ms / 1000)
        self.frame_s = self.frame / self.sr
        self.margin_db, self.min_dbfs = float(margin_db), float(min_dbfs)
        self.start_frames = max(1, int(round(start_ms / frame_ms)))
        self.end_frames = max(1, int(round(end_silence_ms / frame_ms)))
        self.pre_frames = max(0, int(round(pre_roll_ms / frame_ms)))
        self.min_speech_s = min_speech_ms / 1000.0
        self.max_frames = int(max_utt_s / self.frame_s)
        self.noise_init_db, self.noise_alpha = float(noise_init_db), float(noise_alpha)
        self.noise_db = self.noise_init_db
        self.calib_frames = max(0, int(round(calib_ms / frame_ms)))
        self._calib: list[float] = []
        self.reset()

    def reset(self, keep_noise: bool = True) -> None:
        self._rest = np.zeros(0, np.float32)
        self._pre: list[np.ndarray] = []
        self._utt: list[np.ndarray] = []
        self._in_speech = False
        self._run = 0           # tramas de voz seguidas (antes de arrancar)
        self._sil = 0           # tramas de silencio seguidas (dentro de utterance)
        self._voiced = 0        # tramas con voz dentro de la utterance
        self._peak = -120.0
        if not keep_noise:
            self.noise_db = self.noise_init_db
            self._calib = []

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    @staticmethod
    def frame_db(frame: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64)))) if len(frame) else 0.0
        return 20.0 * np.log10(max(rms, 1e-6))

    def feed(self, pcm: np.ndarray) -> list[dict]:
        x = np.concatenate([self._rest, np.asarray(pcm, np.float32).reshape(-1)])
        n = len(x) // self.frame
        self._rest = x[n * self.frame:]
        out: list[dict] = []
        for i in range(n):
            fr = x[i * self.frame:(i + 1) * self.frame]
            db = self.frame_db(fr)
            if len(self._calib) < self.calib_frames:      # calibración inicial del ruido
                self._calib.append(db)
                self.noise_db = float(np.median(self._calib))
                self._pre = (self._pre + [fr])[-(self.pre_frames + self.start_frames):]
                continue
            thr = max(self.noise_db + self.margin_db, self.min_dbfs)
            if not self._in_speech:
                if db > thr:
                    self._run += 1
                    self._pre.append(fr)
                    if self._run >= self.start_frames:
                        self._in_speech = True
                        self._utt = list(self._pre[-(self.pre_frames + self._run):])
                        self._voiced, self._sil, self._peak = self._run, 0, db
                        self._pre = []
                else:
                    self._run = 0
                    self.noise_db += self.noise_alpha * (db - self.noise_db)
                    self._pre.append(fr)
                if len(self._pre) > self.pre_frames + self.start_frames:
                    self._pre = self._pre[-(self.pre_frames + self.start_frames):]
                continue
            self._utt.append(fr)
            self._peak = max(self._peak, db)
            if db > thr - 3.0:
                self._voiced += 1
                self._sil = 0
            else:
                self._sil += 1
            if self._sil >= self.end_frames or len(self._utt) >= self.max_frames:
                if self._sil < self.end_frames:
                    # corte por duración máxima: probablemente ruido continuo ⇒ sube el suelo
                    self.noise_db = max(self.noise_db, db - 3.0)
                res = self._close(trim=self._sil if self._sil >= self.end_frames else 0)
                if res is not None:
                    out.append(res)
        return out

    def _close(self, trim: int = 0) -> Optional[dict]:
        frames = self._utt[: len(self._utt) - max(0, trim - 3)] if trim else self._utt
        voiced_s = self._voiced * self.frame_s
        peak = self._peak
        self._utt, self._in_speech, self._run, self._sil, self._voiced = [], False, 0, 0, 0
        self._pre = []
        if voiced_s < self.min_speech_s or not frames:
            return None
        pcm = np.concatenate(frames).astype(np.float32)
        return {"pcm": pcm, "dur_s": len(pcm) / self.sr, "t_end": time.perf_counter(),
                "peak_db": round(peak, 1), "voiced_s": round(voiced_s, 2)}

    def flush(self) -> Optional[dict]:
        if not self._in_speech:
            self.reset()
            return None
        res = self._close()
        self.reset()
        return res


# =========================================================================== micrófono
class MicPipeline:
    """AudioCapture → canal elegido → 16 kHz → VAD → cola de utterances.

    Sin dependencias de ROS (testeable): ``on_msg`` acepta cualquier objeto con la forma
    de aimdk_msgs/AudioCapture. ``enabled`` (Event) implementa el semidúplex: mientras el
    robot piensa/habla el audio se descarta (no se oye a sí mismo)."""

    def __init__(self, vad: EnergyVad, out: "queue.Queue[dict]", channel: Any = 0,
                 log: Callable[[str], None] = print, default_sr: int = SR,
                 default_channels: int = 6):
        self.vad, self.out, self.channel, self.log = vad, out, channel, log
        self.default_sr, self.default_channels = int(default_sr), int(default_channels)
        self.enabled = threading.Event()
        self.format_seen: Optional[dict] = None
        self.msgs = 0
        self.dropped = 0
        self.errors = 0
        self._lock = threading.Lock()
        self.level_db = -120.0

    def describe(self, msg) -> dict:
        info = getattr(msg, "info", None)
        mic = int(getattr(msg, "mic_channels", 0) or 0)
        ref = int(getattr(msg, "ref_channels", 0) or 0)
        ch = int(getattr(info, "channels", 0) or 0) or (mic + ref) or self.default_channels
        sr = int(getattr(info, "sample_rate", 0) or 0) or self.default_sr
        fmt = str(getattr(info, "sample_format", "") or "s16le")
        coding = str(getattr(info, "coding_format", "") or "pcm")
        return {"channels": ch, "sample_rate": sr, "sample_format": fmt, "coding_format": coding,
                "mic_channels": mic, "ref_channels": ref}

    def on_msg(self, msg) -> None:
        try:
            fmt = self.describe(msg)
            if self.format_seen is None:
                self.format_seen = fmt
                self.log(f"[mic] formato detectado: {json.dumps(fmt)} · canal usado: {self.channel}")
            elif fmt != self.format_seen:
                self.log(f"[mic] AVISO: el formato cambió → {json.dumps(fmt)}")
                self.format_seen = fmt
            if fmt["coding_format"].lower() not in ("pcm", "raw", ""):
                raise ValueError(f"coding_format no soportado: {fmt['coding_format']}")
            data = getattr(getattr(msg, "data", None), "data", b"")
            mono = extract_mono(data, fmt["channels"], fmt["sample_format"], self.channel,
                                fmt["mic_channels"])
            if fmt["sample_rate"] != SR:
                mono = resample(mono, fmt["sample_rate"], SR)
            self.msgs += 1
            self.level_db = EnergyVad.frame_db(mono)
            if not self.enabled.is_set():
                self.dropped += 1
                return
            with self._lock:
                for utt in self.vad.feed(mono):
                    self.out.put(utt)
        except Exception as exc:
            self.errors += 1
            if self.errors <= 5 or self.errors % 100 == 0:
                self.log(f"[mic] error #{self.errors}: {type(exc).__name__}: {exc}")

    def resume(self) -> None:
        with self._lock:
            self.vad.reset(keep_noise=True)
        self.enabled.set()

    def pause(self) -> None:
        self.enabled.clear()
        with self._lock:
            self.vad.reset(keep_noise=True)


class RosMicSubscriber:
    """Suscripción (no actúa) a /aima/hal/audio/capture en un nodo con lista blanca."""

    def __init__(self, pipeline: MicPipeline, topic: str = MIC_TOPIC, qos: str = "best_effort"):
        from aimdk_msgs.msg import AudioCapture
        from rclpy.qos import QoSProfile, QoSReliabilityPolicy

        rel = (QoSReliabilityPolicy.RELIABLE if qos == "reliable"
               else QoSReliabilityPolicy.BEST_EFFORT)   # best-effort casa con ambos publicadores
        self.node = _ros_node("hri_club_mic")
        self.sub = self.node.create_subscription(AudioCapture, topic, pipeline.on_msg,
                                                 QoSProfile(depth=50, reliability=rel))
        self.topic = topic

    def close(self) -> None:
        try:
            from hriego_v2.robot_io import _ros_drop
            self.node.destroy_subscription(self.sub)
            _ros_drop(self.node)
        except Exception:
            pass


# =========================================================================== reproducción
class SpeechPlayer:
    """Cola de reproducción en su propio hilo: el app sintetiza la frase N+1 mientras suena
    la N. Cada frase → resample a ``playback_sr`` → WAV S16LE temporal → ``player.play``
    (bloquea el tiempo REAL de reproducción)."""

    def __init__(self, player: Any, playback_sr: int = SR, tmp_dir: Optional[Path] = None,
                 log: Callable[[str], None] = print):
        self.player = player
        self.playback_sr = int(playback_sr)
        self.tmp_dir = Path(tmp_dir or Path(tempfile.gettempdir()) / "hri_club")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.log = log
        self._q: "queue.Queue[Optional[tuple]]" = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self._pending = 0
        self._lock = threading.Lock()
        self._n = 0
        self.t_first_start: Optional[float] = None
        self.t_last_end: float = 0.0
        self.played_s = 0.0
        self.errors: list[str] = []
        # Ganchos opcionales (expresiones de la cara): deben ser baratos y no bloquear.
        self.on_play: Optional[Callable[[dict], None]] = None     # justo antes de sonar una frase
        self.on_idle: Optional[Callable[[], None]] = None         # la cola de voz quedó vacía
        self._thread = threading.Thread(target=self._run, name="hri-club-voz", daemon=True)
        self._thread.start()

    def mark_turn(self) -> None:
        self.t_first_start = None
        self.played_s = 0.0

    def enqueue(self, pcm: np.ndarray, sr: int, meta: Optional[dict] = None) -> None:
        with self._lock:
            self._pending += 1
            self._idle.clear()
        self._q.put((np.asarray(pcm, np.float32), int(sr), dict(meta or {})))

    __call__ = enqueue            # se pasa como ClubHriApp(audio_sink=...)

    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    def wait_idle(self, timeout: Optional[float] = None) -> bool:
        return self._idle.wait(timeout)

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            pcm, sr, meta = item
            path = None
            try:
                out_sr = self.playback_sr or sr
                pcm = resample(pcm, sr, out_sr) if out_sr != sr else pcm
                gap = float(meta.get("gap_s", 0.0) or 0.0)
                if gap > 0:
                    pcm = np.concatenate([pcm, np.zeros(int(gap * out_sr), np.float32)])
                self._n += 1
                path = write_wav_s16(self.tmp_dir / f"frase_{os.getpid()}_{self._n:04d}.wav",
                                     pcm, out_sr)
                # not_before (reloj monotónico): el runner con actuación retrasa la frase para
                # que el gesto anclado tenga su preparación antes del audio.
                wait = float(meta.get("not_before", 0.0) or 0.0) - time.monotonic()
                if wait > 0:
                    time.sleep(min(wait, 5.0))
                t0 = time.perf_counter()
                if self.t_first_start is None:
                    self.t_first_start = t0
                self._hook(self.on_play, meta)
                self.player.play(path)
                self.played_s += time.perf_counter() - t0
            except Exception as exc:
                msg = f"{type(exc).__name__}: {exc}"
                self.errors.append(msg)
                self.log(f"[voz] error de reproducción: {msg}")
            finally:
                self.t_last_end = time.monotonic()
                if path is not None:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                with self._lock:
                    self._pending -= 1
                    idle = self._pending <= 0
                    if idle:
                        self._pending = 0
                        self._idle.set()
                if idle:
                    release = getattr(self.player, "release_focus", None)
                    if release is not None:
                        self._hook(release)       # foco de audio retenido durante el turno
                    self._hook(self.on_idle)

    def _hook(self, fn: Optional[Callable], *a) -> None:
        if fn is None:
            return
        try:
            fn(*a)
        except Exception as exc:  # noqa: BLE001 - un gancho nunca corta la voz
            self.log(f"[voz] gancho {getattr(fn, '__name__', fn)}: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        try:
            self.player.stop()
        except Exception:
            pass
        self._q.put(None)


# =========================================================================== ASR
def connect_asr(sock_path: str, wait_s: float = 60.0, log: Callable[[str], None] = print):
    """AsrClient sobre el socket del contenedor x2-asr (sin autostart: aquí no hay venv)."""
    from .asr.client import AsrClient

    t0 = time.monotonic()
    last = None
    while time.monotonic() - t0 <= wait_s:
        try:
            cli = AsrClient(sock_path=sock_path, autostart=False, ready_timeout=60.0)
            pong = cli._rpc({"cmd": "ping"})
            if not pong.get("ok"):
                raise ConnectionError(f"ping ASR: {pong}")
            log(f"[asr] conectado a {sock_path}")
            from .nube import envolver_asr          # HRI_NUBE_URL vacío ⇒ devuelve cli tal cual
            return envolver_asr(cli, log)
        except Exception as exc:          # socket aún no creado / servidor cargando
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"ASR no disponible en {sock_path} tras {wait_s:.0f} s: {last}")


# =========================================================================== sesión
def motor_intents(acts: list[dict]) -> list[dict]:
    return [dict(a, ejecutado=False) for a in acts if a.get("act") in MOTOR_ACTS]


def _intent(acts: list[dict]) -> tuple[Optional[str], Optional[str]]:
    for a in acts:
        if a.get("act") == "INTENT":
            return a.get("intent"), a.get("area")
    return None, None


class TurnLogger:
    """Consola legible + JSONL (out_reto2/live/ por defecto)."""

    motor_key = "motor_intents_not_executed"     # live_robot_actuado: "motor_intents"

    def __init__(self, log_dir: Optional[Path], quiet: bool = False):
        self.quiet = quiet
        self.path = None
        if log_dir is not None:
            try:
                Path(log_dir).mkdir(parents=True, exist_ok=True)
                self.path = Path(log_dir) / time.strftime("live_%Y%m%d_%H%M%S.jsonl")
            except OSError:
                self.path = None

    def info(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def record(self, rec: dict) -> None:
        if self.path is not None:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def motor_status(self, act: dict) -> tuple[str, bool]:
        return "NO EJECUTADO (sin actuación)", False

    def turn(self, kind: str, rec: dict, extra: dict) -> None:
        acts = rec.get("acts") or []
        intent, area = _intent(acts)
        motors = motor_intents(acts)
        for m in motors:
            m["estado"], m["ejecutado"] = self.motor_status(m)
        lines = [f"──── turno {kind} · estado FSM: {rec.get('state')}"]
        if extra.get("transcript") is not None:
            lines.append(f"  usuario : {extra['transcript']!r}")
        for s in rec.get("spoken") or []:
            lines.append(f"  robot   : {s}")
        if intent:
            lines.append(f"  intent  : {intent} · área: {area}")
        for m in motors:
            desc = {k: v for k, v in m.items() if k not in ("t", "ejecutado")}
            desc.pop("estado", None)
            lines.append(f"  motor   : {json.dumps(desc, ensure_ascii=False, default=str)} → {m['estado']}")
        lat = {k: v for k, v in (extra.get("latencias") or {}).items() if v is not None}
        lines.append(f"  latencias (s): {json.dumps(lat)}")
        self.info("\n".join(lines))
        self.record({"t": time.time(), "kind": kind, "state": rec.get("state"),
                     "intent": intent, "area": area, "spoken": rec.get("spoken"),
                     self.motor_key: motors, "acts": acts, **extra})


def make_person(rank: str) -> dict:
    rank = rank.upper()
    if rank not in PERSON_BY_RANK:
        raise ValueError(f"--visit debe ser uno de {RANKS}")
    return json.loads(json.dumps(PERSON_BY_RANK[rank]))


def build_app(*, llm: Any, tts: Any, sink: Any, conversation_timeout_s: float,
              identity_real: bool, person: Optional[dict]):
    """ClubHriApp con NullBackend, sin motion, sin engine, reloj real y salida de audio."""
    from .app import ClubHriApp, NullBackend

    confirm_fn = None
    if not identity_real and person is not None:
        # Admisión simulada: passthrough de la persona (no toca confirm/QR/show).
        confirm_fn = lambda *_a, **_k: dict(person, status="ok_simulado")  # noqa: E731
    app = ClubHriApp(NullBackend(), llm=llm, asr=None, motion=None, tts=tts, engine=None,
                     confirm_fn=confirm_fn, conversation_timeout_s=conversation_timeout_s,
                     owns_io=False, identity_real=identity_real, audio_sink=sink,
                     realtime=True)
    assert isinstance(app.backend_raw, NullBackend) and app.motion is None and app.engine is None
    return app


def grab_photo(topic: str, out_dir: Path, timeout_s: float = 5.0) -> tuple[Optional[Path], Any]:
    """Fase 2: un fotograma de la cámara de la cabeza (RosImageCapture, solo suscripción)."""
    from hriego_v2.robot_io import RosImageCapture
    import cv2

    cap = RosImageCapture(topic, timeout_s=timeout_s)
    try:
        assert_node_guarded(cap._node)
        ok, frame = cap.read(timeout_s=timeout_s)
        if not ok or frame is None:
            return None, None
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / time.strftime("visita_%Y%m%d_%H%M%S.jpg")
        cv2.imwrite(str(path), frame)
        return path, frame
    finally:
        cap.release()


class LiveSession:
    """Bucle semidúplex: escuchar → (ASR) → FSM/LLM → hablar (audio real) → escuchar."""

    def __init__(self, args, *, llm: Any, tts: Any, player: Any, asr: Any = None,
                 text_source: Optional[Callable[[], Optional[str]]] = None,
                 logger: Optional[TurnLogger] = None):
        self.args = args
        self.llm, self.tts, self.asr = llm, tts, asr
        self.logger = logger or TurnLogger(None)
        self.speech = SpeechPlayer(player, playback_sr=args.playback_sr, log=self.logger.info)
        self.utts: "queue.Queue[dict]" = queue.Queue()
        self.vad = EnergyVad(margin_db=args.vad_margin_db, min_dbfs=args.vad_min_dbfs,
                             end_silence_ms=args.vad_silencio_ms, min_speech_ms=args.vad_min_voz_ms,
                             max_utt_s=args.vad_max_s)
        self.mic = MicPipeline(self.vad, self.utts, channel=args.mic_canal, log=self.logger.info)
        self.text_source = text_source
        self.app = None
        self.turns = 0
        self._stop = threading.Event()

    # ------------------------------------------------------------ admisión
    def admit(self, photo: Optional[Path] = None, frame: Any = None,
              face_result: Optional[dict] = None) -> dict:
        identity_real = bool(self.args.identity_real)
        person = None if identity_real else make_person(self.args.visit)
        self.app = self._build_app(identity_real=identity_real, person=person)
        self.speech.mark_turn()
        t0 = time.perf_counter()
        if identity_real:
            p = self.app.admit(photo=str(photo) if photo else None, qr_image=frame,
                               face_result=face_result)
        else:
            p = self.app.admit(qr_image=person)
        t_admit = time.perf_counter() - t0
        self.speech.wait_idle()
        rec = self.app.turns[-1]
        lat = {"admision_s": round(t_admit, 3),
               "primer_audio_s": (round(self.speech.t_first_start - t0, 3)
                                  if self.speech.t_first_start else None),
               "audio_real_s": round(self.speech.played_s, 2), **(rec.get("metrics") or {})}
        self.logger.turn("admision", rec, {"person": p, "latencias": lat,
                                           "identity_mode": p.get("identity_mode")})
        return p

    def _build_app(self, *, identity_real: bool, person: Optional[dict]):
        # Gancho: live_robot_actuado lo sustituye por un ClubHriApp con el backend real.
        return build_app(llm=self.llm, tts=self.tts, sink=self.speech,
                         conversation_timeout_s=self.args.timeout_conversacion,
                         identity_real=identity_real, person=person)

    # ------------------------------------------------------------ turno
    def handle_text(self, text: str, utt: Optional[dict] = None, t_asr: Optional[float] = None,
                    asr_s: Optional[float] = None) -> dict:
        self.speech.mark_turn()
        t0 = time.perf_counter()
        rec = self.app.user_turn(text=text)
        t_fsm = time.perf_counter()
        self.speech.wait_idle()
        t_end = time.perf_counter()
        base = utt["t_end"] if utt else t0
        lat = {"asr_s": round(asr_s, 3) if asr_s is not None else None,
               "fsm_llm_tts_s": round(t_fsm - t0, 3),
               "fin_habla_a_primer_audio_s": (round(self.speech.t_first_start - base, 3)
                                              if self.speech.t_first_start else None),
               "audio_real_s": round(self.speech.played_s, 2),
               "turno_total_s": round(t_end - base, 3),
               "endpoint_silencio_s": self.args.vad_silencio_ms / 1000.0 if utt else None,
               **{f"app_{k}": v for k, v in (rec.get("metrics") or {}).items()}}
        extra = {"transcript": text, "latencias": lat}
        if utt:
            extra["utterance"] = {"dur_s": round(utt["dur_s"], 2), "peak_db": utt.get("peak_db"),
                                  "voiced_s": utt.get("voiced_s")}
        self.turns += 1
        self.logger.turn(f"usuario #{self.turns}", rec, extra)
        return rec

    def transcribe(self, utt: dict) -> tuple[str, float]:
        t0 = time.perf_counter()
        try:
            res = self.asr.transcribe(utt["pcm"], SR)
        except (OSError, ConnectionError) as exc:     # reconexión única
            self.logger.info(f"[asr] reconectando tras {type(exc).__name__}: {exc}")
            self.asr = connect_asr(self.args.asr_sock, wait_s=10.0, log=self.logger.info)
            res = self.asr.transcribe(utt["pcm"], SR)
        if not res.get("ok", True) and res.get("error"):
            self.logger.info(f"[asr] error: {res['error']}")
        return (res.get("text") or "").strip(), time.perf_counter() - t0

    def _ended(self) -> bool:
        return self.app.fsm.state.value in ("END",)

    def _listen(self) -> None:
        # cola de reproducción vacía + cola del eco → micrófono abierto
        self.speech.wait_idle()
        tail = self.args.post_tts_mute_s - (time.monotonic() - self.speech.t_last_end)
        if tail > 0:
            time.sleep(tail)
        while not self.utts.empty():
            self.utts.get_nowait()
        self.mic.resume()

    def run(self, photo: Optional[Path] = None, frame: Any = None) -> int:
        visits = 0
        while not self._stop.is_set():
            self.admit(photo, frame)
            visits += 1
            while not self._stop.is_set() and not self._ended():
                if self.args.max_turnos and self.turns >= self.args.max_turnos:
                    self._stop.set()
                    break
                if self.text_source is not None:          # modo stdin / tests
                    text = self.text_source()
                    if text is None:
                        self._stop.set()
                        break
                    if text.strip():
                        self.handle_text(text.strip())
                    continue
                if not self.mic.enabled.is_set():
                    self._listen()
                try:
                    utt = self.utts.get(timeout=0.5)
                except queue.Empty:
                    mark = len(self.app.fsm.log)
                    if self.app.fsm.tick():                 # 30 s sin interacción ⇒ despedida
                        self.mic.pause()
                        self.speech.wait_idle()
                        acts = self.app.fsm.log[mark:]
                        rec = self.app._end_turn(kind="timeout", acts=acts)
                        rec["spoken"] = [a.get("text") for a in acts if a.get("act") == "SAY"]
                        self.logger.turn("timeout", rec, {})
                    continue
                self.mic.pause()                            # semidúplex: nada entra hasta hablar
                text, asr_s = self.transcribe(utt)
                if not text:
                    self.logger.info(f"[asr] utterance de {utt['dur_s']:.2f}s sin texto; sigo escuchando")
                    continue
                self.handle_text(text, utt=utt, asr_s=asr_s)
            self.mic.pause()
            self.speech.wait_idle()
            self.logger.info(f"[sesión] visita {visits} terminada (estado {self.app.fsm.state.value})")
            if not self.args.repetir or self._stop.is_set():
                break
            time.sleep(self.args.pausa_entre_visitas)
            self.turns = 0
        self.speech.stop()
        return 0

    def stop(self) -> None:
        self._stop.set()


# =========================================================================== CLI
def _env_bool(key: str, default: str = "0") -> bool:
    return os.environ.get(key, default).strip().lower() not in ("", "0", "false", "no", "off")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m x2_ultra.hri_runtime.live_robot",
                                 description="Runner en vivo SIN ACTUACIÓN del HRI conversacional.")
    ap.add_argument("--visit", default=os.environ.get("HRI_CLUB_VISIT", "REGULAR"),
                    type=str.upper, choices=RANKS, help="persona simulada (HRI_IDENTITY_REAL=0)")
    ap.add_argument("--sin-voz", action="store_true",
                    default=os.environ.get("HRI_CLUB_AUDIO", "aimdk").lower() == "none",
                    help="no suena nada (NullAudioPlayer; mismos tiempos)")
    ap.add_argument("--entrada", choices=("mic", "stdin"),
                    default=os.environ.get("HRI_CLUB_ENTRADA", "mic"),
                    help="mic = /aima/hal/audio/capture + ASR; stdin = texto por teclado (PC)")
    ap.add_argument("--camara", nargs="?", const=CAMERA_TOPIC, default=None,
                    help="fase 2: foto de la cámara (topic ROS) para identidad real")
    ap.add_argument("--tts", default=os.environ.get("HRI_CLUB_TTS", "auto"),
                    choices=("auto", "supertonic", "espeak", "null"))
    ap.add_argument("--playback-sr", type=int, default=int(os.environ.get("HRI_PLAYBACK_SR", SR)),
                    help="Hz del WAV enviado al altavoz (0 = la del TTS)")
    ap.add_argument("--mic-topic", default=os.environ.get("HRI_MIC_TOPIC", MIC_TOPIC))
    ap.add_argument("--mic-canal", default=os.environ.get("HRI_MIC_CANAL", "0"),
                    help="índice de canal o 'mean' (media de los canales de micrófono)")
    ap.add_argument("--mic-qos", choices=("best_effort", "reliable"),
                    default=os.environ.get("HRI_MIC_QOS", "best_effort"))
    ap.add_argument("--vad-margin-db", type=float, default=float(os.environ.get("HRI_VAD_MARGIN_DB", 12)))
    ap.add_argument("--vad-min-dbfs", type=float, default=float(os.environ.get("HRI_VAD_MIN_DBFS", -45)))
    ap.add_argument("--vad-silencio-ms", type=int, default=int(os.environ.get("HRI_VAD_SILENCIO_MS", 700)))
    ap.add_argument("--vad-min-voz-ms", type=int, default=int(os.environ.get("HRI_VAD_MIN_VOZ_MS", 350)))
    ap.add_argument("--vad-max-s", type=float, default=float(os.environ.get("HRI_VAD_MAX_S", 15)))
    ap.add_argument("--post-tts-mute-s", type=float, default=float(os.environ.get("HRI_POST_TTS_MUTE_S", 0.5)),
                    help="micrófono cerrado tras terminar de hablar (cola del eco)")
    ap.add_argument("--asr-sock", default=os.environ.get("HRI_ASR_SOCK", DEFAULT_ASR_SOCK))
    ap.add_argument("--asr-espera", type=float, default=float(os.environ.get("HRI_ASR_WAIT_S", 120)))
    ap.add_argument("--llm-espera", type=float, default=float(os.environ.get("HRI_LLM_WAIT_S", 120)))
    ap.add_argument("--permitir-sin-llm", action="store_true",
                    default=_env_bool("HRI_ALLOW_NO_LLM"),
                    help="si x2-llm no responde, seguir con las frases de respaldo de la FSM")
    ap.add_argument("--timeout-conversacion", type=float,
                    default=float(os.environ.get("HRI_CONVERSATION_TIMEOUT_S", 30)))
    ap.add_argument("--max-turnos", type=int, default=int(os.environ.get("HRI_CLUB_MAX_TURNOS", 0)))
    ap.add_argument("--repetir", action="store_true", default=_env_bool("HRI_CLUB_REPETIR"),
                    help="al despedirse, vuelve a admitir la misma persona simulada")
    ap.add_argument("--pausa-entre-visitas", type=float, default=5.0)
    ap.add_argument("--log-dir", default=os.environ.get("HRI_CLUB_LOG_DIR",
                                                        str(REPO / "out_reto2" / "live")))
    ap.add_argument("--check-only", action="store_true",
                    help="comprueba seguridad, LLM, ASR, TTS y micrófono; no conversa")
    return ap


def _parse_channel(v: str):
    return "mean" if str(v).strip().lower() == "mean" else int(v)


def make_llm(wait_s: float, allow_missing: bool, log: Callable[[str], None]):
    from .llm_client import HriLLM, llm_url
    from .nube import make_llm_client

    client = make_llm_client(log)              # local (defecto) o nube con respaldo local
    t0 = time.monotonic()
    while not client.health():
        if time.monotonic() - t0 > wait_s:
            if allow_missing:
                log(f"[llm] AVISO: {llm_url()} no responde; FSM con frases de respaldo")
                return None
            raise RuntimeError(f"x2-llm no responde en {llm_url()} (usa --permitir-sin-llm)")
        time.sleep(2.0)
    log(f"[llm] x2-llm listo en {llm_url()}")
    return HriLLM(client=client)


def make_player(sin_voz: bool):
    from hriego_v2.robot_io import AimdkAudioPlayer, NullAudioPlayer
    if sin_voz:
        return NullAudioPlayer()
    player = AimdkAudioPlayer()
    # foco de audio por turno, no por frase (HRI_FOCO_POR_TURNO=0 vuelve a lo de antes)
    player.hold_focus = os.environ.get("HRI_FOCO_POR_TURNO", "1") != "0"
    return player


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    safety_preflight(argv)                     # 1) antes de cualquier import de ROS / app
    guard = install_import_guard()             # 2) módulos de actuación vetados
    args = build_parser().parse_args(argv)
    args.mic_canal = _parse_channel(args.mic_canal)
    from . import identity
    args.identity_real = identity.identity_real_from_env()
    logger = TurnLogger(Path(args.log_dir) if args.log_dir else None)
    log = logger.info
    log(f"[seguridad] HRI_NO_ACTUATION={os.environ['HRI_NO_ACTUATION']} "
        f"HRIEGO_NO_ACTUATION={os.environ['HRIEGO_NO_ACTUATION']} · backend=NullBackend · "
        f"motion=off · import guard: {len(guard.blocked)} módulos vetados")
    if args.identity_real and not args.camara:
        raise SystemExit("HRI_IDENTITY_REAL=1 requiere --camara (fase 2)")
    log(f"[config] visita={'(identidad real)' if args.identity_real else args.visit} "
        f"entrada={args.entrada} voz={'no' if args.sin_voz else 'aimdk'} tts={args.tts} "
        f"playback_sr={args.playback_sr} mic={args.mic_topic}[{args.mic_canal}] "
        f"asr={args.asr_sock} log={logger.path}")

    from .nube import envolver_tts
    from .tts import make_tts
    tts = envolver_tts(make_tts(args.tts), log)
    log(f"[tts] motor: {tts.name}" + ("  ⚠ sin motor de voz real (silencio estimado)"
                                        if tts.estimated else ""))
    llm = make_llm(args.llm_espera, args.permitir_sin_llm, log)
    asr = connect_asr(args.asr_sock, args.asr_espera, log) if args.entrada == "mic" else None
    player = make_player(args.sin_voz)

    text_source = None
    if args.entrada == "stdin":
        def text_source() -> Optional[str]:
            try:
                return input("usuario> ")
            except EOFError:
                return None
    session = LiveSession(args, llm=llm, tts=tts, player=player, asr=asr,
                          text_source=text_source, logger=logger)
    mic_sub = None
    try:
        if args.entrada == "mic":
            mic_sub = RosMicSubscriber(session.mic, args.mic_topic, args.mic_qos)
            log(f"[mic] suscrito a {args.mic_topic} (QoS {args.mic_qos})")
        photo = frame = None
        if args.camara:
            photo, frame = grab_photo(args.camara, Path(args.log_dir) / "fotos")
            log(f"[camara] foto: {photo}")
        if args.check_only:
            return _check_only(session, args, log)
        return session.run(photo, frame)
    except KeyboardInterrupt:
        log("[sesión] interrumpido (Ctrl-C)")
        return 130
    finally:
        session.stop()
        session.speech.stop()
        if mic_sub is not None:
            mic_sub.close()
        if asr is not None:
            try:
                asr.stop()
            except Exception:
                pass
        if guard.attempts:
            log(f"[seguridad] imports de actuación bloqueados durante la sesión: {guard.attempts}")


def _check_only(session: LiveSession, args, log) -> int:
    ok = True
    if args.entrada == "mic":
        session.mic.enabled.clear()
        t0 = time.monotonic()
        while session.mic.msgs == 0 and time.monotonic() - t0 < 10.0:
            time.sleep(0.2)
        if session.mic.msgs == 0:
            log(f"[check] FALLO: sin mensajes en {args.mic_topic} en 10 s")
            ok = False
        else:
            time.sleep(2.0)
            log(f"[check] micrófono OK: {session.mic.msgs} mensajes, formato "
                f"{session.mic.format_seen}, nivel {session.mic.level_db:.1f} dBFS, "
                f"errores {session.mic.errors}")
        try:
            log(f"[check] ASR ping: {session.asr._rpc({'cmd': 'ping'})}")
        except Exception as exc:
            log(f"[check] FALLO ASR: {exc}")
            ok = False
    loaded = sorted(m for m in sys.modules if _is_blocked(m))
    log(f"[check] módulos de actuación cargados: {loaded or 'ninguno'}")
    ok = ok and not loaded
    log("[check] OK" if ok else "[check] FALLÓ")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
