"""El micrófono de las Quest como micrófono del PC (PipeWire).

Las gafas mandan su micrófono por el WebSocket (PCM s16le, 16 kHz, mono, tramas de 20 ms). ``QuestMic`` crea con
``pw-loopback`` una fuente virtual ``x2_quest_mic`` («Micrófono Quest 3») y le escribe ese audio con libpulse, así
que cualquier programa que grabe del micrófono (``pw-record``, Whisper, el navegador...) oye a quien lleva las gafas.
Con ``default=True`` además la pone como micrófono predeterminado mientras dura la sesión y al salir deja el que había.

El flujo hacia PipeWire es continuo y lo marca PipeWire (``_feed``): un pequeño colchón absorbe las ráfagas del
Wi-Fi, si no llega audio (gafas desconectadas, cortes de red) sale silencio, y si se acumula retraso se recorta.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import json
import queue
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path

RATE = 16000
FRAME_BYTES = RATE // 50 * 2          # 20 ms en s16 mono
NODE = "x2_quest_mic"
DESCRIPTION = "Micrófono Quest 3 (club VR)"
PREBUFFER = 5                         # tramas (100 ms) acumuladas antes de empezar a sonar
MAX_BACKLOG = 25                      # tramas (500 ms) en cola: por encima se recorta hasta PREBUFFER


class _PulseWriter:
    """Flujo de reproducción con libpulse-simple (PipeWire lo atiende con pipewire-pulse). ``write`` se bloquea mientras
    el búfer del servidor (``TARGET_MS``) está lleno, así que el ritmo lo marca PipeWire. Se usa en vez de
    ``pw-cat -`` porque pw-cat leyendo de una tubería metía cortes y el audio salía entrecortado."""
    TARGET_MS = 80

    class _Spec(ctypes.Structure):
        _fields_ = [("format", ctypes.c_int), ("rate", ctypes.c_uint32), ("channels", ctypes.c_uint8)]

    class _Attr(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint32) for n in ("maxlength", "tlength", "prebuf", "minreq", "fragsize")]

    def __init__(self, device: str, name: str):
        lib = ctypes.CDLL(ctypes.util.find_library("pulse-simple") or "libpulse-simple.so.0")
        lib.pa_simple_new.restype = ctypes.c_void_p
        lib.pa_simple_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p,
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
        lib.pa_simple_write.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_int)]
        lib.pa_simple_free.argtypes = [ctypes.c_void_p]
        self._lib = lib
        spec = self._Spec(3, RATE, 1)                                    # PA_SAMPLE_S16LE, 16 kHz, mono
        none = 0xFFFFFFFF
        attr = self._Attr(none, RATE * 2 * self.TARGET_MS // 1000, none, none, none)
        err = ctypes.c_int(0)
        self._s = lib.pa_simple_new(None, name.encode(), 1, device.encode(), b"microfono", ctypes.byref(spec), None,
                                    ctypes.byref(attr), ctypes.byref(err))    # 1 = PA_STREAM_PLAYBACK
        if not self._s:
            raise OSError(f"pa_simple_new falló (error {err.value})")

    def write(self, data: bytes) -> None:
        err = ctypes.c_int(0)
        if self._lib.pa_simple_write(self._s, data, len(data), ctypes.byref(err)) < 0:
            raise OSError(f"pa_simple_write falló (error {err.value})")

    def close(self) -> None:
        if self._s:
            self._lib.pa_simple_free(self._s)
            self._s = None


def _die_with_parent() -> None:
    """Que pw-loopback termine aunque la app muera de golpe (si no, el micrófono virtual se queda)."""
    try:
        import ctypes
        import signal
        ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGTERM)      # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def _node_id(name: str) -> int | None:
    try:
        dump = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5).stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None
    for obj in dump:
        props = (obj.get("info") or {}).get("props") or {}
        if obj.get("type") == "PipeWire:Interface:Node" and props.get("node.name") == name:
            return int(obj["id"])
    return None


def _default_source_id() -> int | None:
    try:
        out = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"], capture_output=True, text=True,
                             timeout=5).stdout
        return int(out.split(",")[0].split()[1])
    except (OSError, subprocess.SubprocessError, IndexError, ValueError):
        return None


class QuestMic:
    def __init__(self, default: bool = True, record_dir: Path | None = None):
        self.default = default
        self.record_dir = record_dir
        self.available = shutil.which("pw-loopback") is not None and ctypes.util.find_library("pulse-simple") is not None
        self._frames: queue.Queue[bytes] = queue.Queue()
        self._loopback: subprocess.Popen | None = None
        self._writer: _PulseWriter | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._previous_default: int | None = None
        self._wav: wave.Wave_write | None = None
        self.level = 0.0                     # pico de la última trama (0-1), para los indicadores

    def start(self) -> str:
        """Arranca la fuente virtual; devuelve un texto para el registro."""
        if not self.available:
            return "sin pw-loopback/libpulse-simple: el audio de las Quest solo llega por la API local"
        self._loopback = subprocess.Popen(
            ["pw-loopback", "-n", NODE,
             "--capture-props", f"media.class=Audio/Sink node.name={NODE}_in node.description=\"{DESCRIPTION} "
                                "(entrada)\" audio.position=[MONO] priority.session=0 priority.driver=0",
             "--playback-props", f"media.class=Audio/Source node.name={NODE} node.description=\"{DESCRIPTION}\" "
                                 "audio.position=[MONO]"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            preexec_fn=_die_with_parent)
        for _ in range(30):                  # que aparezca el nodo antes de conectarle el escritor
            if _node_id(f"{NODE}_in") is not None:
                break
            time.sleep(0.1)
        self._writer = _PulseWriter(f"{NODE}_in", "x2-club-vr")
        if self.record_dir is not None:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            path = self.record_dir / time.strftime("quest_mic_%Y%m%d_%H%M%S.wav")
            self._wav = wave.open(str(path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(RATE)
        self._running = True
        self._thread = threading.Thread(target=self._feed, name="quest-mic", daemon=True)
        self._thread.start()
        note = f"micrófono virtual «{DESCRIPTION}» (nodo {NODE})"
        if self.default:
            self._previous_default = _default_source_id()
            node = _node_id(NODE)
            if node is not None and subprocess.run(["wpctl", "set-default", str(node)]).returncode == 0:
                note += ", predeterminado mientras dure la sesión"
        return note

    def write(self, pcm: bytes) -> None:
        """Una trama del micrófono de las gafas (s16le 16 kHz mono)."""
        if self._running:
            self._frames.put(pcm)

    def _feed(self) -> None:
        """Escribe sin parar en el flujo de libpulse, que se bloquea con su búfer lleno: el ritmo lo marca PipeWire.
        El Wi-Fi entrega el audio a ráfagas: antes de sonar se acumulan PREBUFFER tramas; si la cola se vacía sale
        silencio hasta volver a acumular, y si pasa de MAX_BACKLOG se recorta el retraso."""
        silence = bytes(FRAME_BYTES)
        frames = self._frames
        primed = False
        while self._running:
            if not primed and frames.qsize() >= PREBUFFER:
                primed = True
            if frames.qsize() > MAX_BACKLOG:
                while frames.qsize() > PREBUFFER:
                    frames.get_nowait()
            pcm = silence
            if primed:
                try:
                    pcm = frames.get_nowait()
                except queue.Empty:
                    primed = False
            try:
                self._writer.write(pcm)
            except OSError:
                return
            if pcm is not silence:
                samples = memoryview(pcm).cast("h")
                self.level = max(abs(min(samples)), max(samples)) / 32768 if len(samples) else 0.0
                if self._wav is not None:
                    self._wav.writeframes(pcm)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self.default and self._previous_default is not None:
            subprocess.run(["wpctl", "set-default", str(self._previous_default)], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        if self._writer is not None:
            self._writer.close()
        for proc in (self._loopback,):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if self._wav is not None:
            self._wav.close()
