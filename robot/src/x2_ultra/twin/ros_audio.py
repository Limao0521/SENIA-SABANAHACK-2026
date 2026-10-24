"""Audio y voz del gemelo con la interfaz de AimDK (``interactor/voice``), sobre el audio del PC.

Servicios: PlayTts (espeak-ng), PlayAudioFile, StopAudioPlay, Get/SetVolume, Get/SetMute, Request/AbandonAudioFocus,
Get/SetMicSourceRequest. Topics: ``/aima/hal/audio/playback`` (reproducción en streaming), ``play_state``,
``focus_response``, ``/aima/hal/audio/capture`` (micrófono del PC, 16 kHz, 6 canales entrelazados: 4 de micrófono
+ 2 de referencia a cero, como el micrófono integrado) y ``/agent/process_audio_output`` (VAD por energía; el
robot real exige palabra de activación, aquí no).

El micrófono solo se abre mientras haya suscriptores de captura o VAD. Sin servidor de sonido (PulseAudio/PipeWire)
los servicios responden igual y no suena nada.
"""
from __future__ import annotations

import os
import shutil
from array import array
import subprocess
import tempfile
import threading
import time
import wave
from dataclasses import dataclass

import numpy as np
from aimdk_msgs.msg import (AudioVadStateType, CommonState, FocusResponse, PlayStateChange, PlayStateType,
                            ProcessedAudioOutput)
from aimdk_msgs.msg import AudioCapture, AudioPlayback
from aimdk_msgs.srv import (AbandonAudioFocus, GetMicSourceRequest, GetMute, GetVolume, PlayAudioFile, PlayTts,
                            RequestAudioFocus, SetMicSourceRequest, SetMute, SetVolume, StopAudioPlay)
from builtin_interfaces.msg import Time as TimeMsg
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.node import Node

from x2_ultra.twin import contract as C
from x2_ultra.twin.ros_hal import qos

TTS_VOICE = os.environ.get("X2_TWIN_TTS_VOICE", "es")
ERROR_NONE, ERROR_HIGHER_PRIORITY_BUSY = 0, 1


def _now() -> TimeMsg:
    t = time.time()
    return TimeMsg(sec=int(t), nanosec=int((t % 1.0) * 1e9))


@dataclass
class Holder:
    pkg_name: str
    priority: int
    weight: int

    @property
    def rank(self) -> float:
        return self.priority + self.weight / 100.0


class Player:
    """Reproduce un archivo o un flujo PCM por el altavoz del PC (paplay/pacat). Una reproducción a la vez."""

    def __init__(self, on_state):
        self.on_state = on_state            # on_state(pkg_name, playing: bool)
        self.proc: subprocess.Popen | None = None
        self.owner, self.priority = "", -1.0
        self.volume, self.muted = 70, False
        self.lock = threading.Lock()
        self.available = shutil.which("paplay") is not None and bool(os.environ.get("PULSE_SERVER"))

    def _vol_arg(self) -> list[str]:
        v = 0 if self.muted else int(65536 * self.volume / 100)
        return [f"--volume={v}"]

    def stop(self) -> None:
        with self.lock:
            proc, owner = self.proc, self.owner
            self.proc, self.owner, self.priority = None, "", -1.0
        if proc is not None and proc.poll() is None:
            proc.terminate()
            self.on_state(owner, False)

    def busy_rank(self) -> float:
        with self.lock:
            return self.priority if self.proc is not None and self.proc.poll() is None else -1.0

    def play_file(self, path: str, owner: str, rank: float, raw: tuple[int, int] | None = None) -> None:
        """WAV, o PCM S16LE crudo si ``raw = (sample_rate, channels)``."""
        self.stop()
        if not self.available:
            return
        if raw is None:
            cmd = ["paplay", *self._vol_arg(), path]
        else:
            cmd = ["pacat", "--raw", "--format=s16le", f"--rate={raw[0]}", f"--channels={raw[1]}", *self._vol_arg(), path]
        with self.lock:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.owner, self.priority = owner, rank
            proc = self.proc
        self.on_state(owner, True)

        def wait():
            proc.wait()
            with self.lock:
                if self.proc is proc:
                    self.proc, self.owner, self.priority = None, "", -1.0
                    finished = True
                else:
                    finished = False
            if finished:
                self.on_state(owner, False)
        threading.Thread(target=wait, daemon=True).start()


class StreamSink:
    """``/aima/hal/audio/playback``: un pacat por flujo; cambiar de pkg_name o token_id vacía el búfer."""

    def __init__(self, player: Player, on_state):
        self.player, self.on_state = player, on_state
        self.proc: subprocess.Popen | None = None
        self.key = None
        self.last = 0.0
        self.lock = threading.Lock()

    def write(self, msg: AudioPlayback) -> None:
        if not self.player.available:
            return
        key = (msg.pkg_name, msg.token_id, msg.info.sample_rate or C.AUDIO_RATE_HZ, msg.info.channels or 1)
        with self.lock:
            if key != self.key or self.proc is None or self.proc.poll() is not None:
                self._close()
                self.proc = subprocess.Popen(["pacat", "--raw", "--format=s16le", f"--rate={key[2]}",
                                              f"--channels={key[3]}", *self.player._vol_arg()],
                                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
                self.key = key
                self.on_state(msg.pkg_name, True)
            try:
                self.proc.stdin.write(bytes(msg.data.data))
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError):
                self._close()
            self.last = time.monotonic()

    def _close(self) -> None:
        if self.proc is not None:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            self.proc.terminate()
            self.on_state(self.key[0] if self.key else "", False)
        self.proc, self.key = None, None

    def idle_check(self, timeout: float = 1.0) -> None:
        with self.lock:
            if self.proc is not None and time.monotonic() - self.last > timeout:
                self._close()


class TwinAudio(Node):
    def __init__(self):
        super().__init__("x2_twin_audio")
        group = ReentrantCallbackGroup()
        self.state_pub = self.create_publisher(PlayStateChange, C.AUDIO_PLAY_STATE_TOPIC, qos(C.AUDIO_PLAY_STATE_QOS))
        self.focus_pub = self.create_publisher(FocusResponse, C.AUDIO_FOCUS_TOPIC, qos(C.AUDIO_FOCUS_QOS))
        self.capture_pub = self.create_publisher(AudioCapture, C.AUDIO_CAPTURE_TOPIC, qos(C.AUDIO_CAPTURE_QOS))
        self.vad_pub = self.create_publisher(ProcessedAudioOutput, C.AUDIO_VAD_TOPIC, qos(C.AUDIO_VAD_QOS))
        self.player = Player(self._play_state)
        self.stream = StreamSink(self.player, self._play_state)
        self.focus: Holder | None = None
        self.mic_source = 0
        self.tmpdir = tempfile.mkdtemp(prefix="x2_twin_tts_")
        self._play_state("", None)                      # CLOSED, una vez al arrancar
        srv = lambda t, name, cb: self.create_service(t, C.service(name), cb, callback_group=group)
        srv(PlayTts, "PlayTts", self._tts)
        srv(PlayAudioFile, "PlayAudioFile", self._play_file)
        srv(StopAudioPlay, "StopAudioPlay", self._stop)
        srv(GetVolume, "GetVolume", self._get_volume)
        srv(SetVolume, "SetVolume", self._set_volume)
        srv(GetMute, "GetMute", self._get_mute)
        srv(SetMute, "SetMute", self._set_mute)
        srv(RequestAudioFocus, "RequestAudioFocus", self._request_focus)
        srv(AbandonAudioFocus, "AbandonAudioFocus", self._abandon_focus)
        srv(GetMicSourceRequest, "GetMicSourceRequest", self._get_mic)
        srv(SetMicSourceRequest, "SetMicSourceRequest", self._set_mic)
        self.create_subscription(AudioPlayback, C.AUDIO_PLAYBACK_TOPIC, self.stream.write, qos(C.AUDIO_PLAYBACK_QOS))
        self.create_timer(0.5, self.stream.idle_check)
        self._mic_thread = threading.Thread(target=self._mic_loop, daemon=True)
        self._mic_thread.start()
        if not self.player.available:
            self.get_logger().warn("sin servidor de sonido: el audio responde pero no suena")

    # --- estado y foco ----------------------------------------------------------------------------
    def _play_state(self, pkg_name: str, playing: bool | None) -> None:
        value = (PlayStateType.PLAYER_STATE_CLOSED if playing is None else
                 PlayStateType.PLAYER_STATE_PLAYING if playing else PlayStateType.PLAYER_STATE_STOPED)
        self.state_pub.publish(PlayStateChange(pkg_name=pkg_name, state=PlayStateType(value=value)))

    def _grant_focus(self, h: Holder) -> bool:
        cur = self.focus
        if cur is None or cur.pkg_name == h.pkg_name or h.rank >= cur.rank:
            if cur is not None and cur.pkg_name != h.pkg_name:
                self.focus_pub.publish(FocusResponse(pkg_name=cur.pkg_name, focus_gain=False))
            self.focus = h
            return True
        return False

    @staticmethod
    def _ok(response, field: str = "reponse"):
        r = getattr(response, field)
        r.header.stamp = _now()
        r.status.value = CommonState.SUCCESS
        return response

    # --- servicios ------------------------------------------------------------------------------
    def _tts(self, request, response):
        req = request.tts_req
        rank = float(req.priority_level.value) + req.priority_weight / 100.0
        busy = self.player.busy_rank()
        resp = response.tts_resp
        resp.text, resp.priority_level, resp.priority_weight = req.text, req.priority_level, req.priority_weight
        resp.domain, resp.trace_id = req.domain, req.trace_id
        response.header.header.stamp = _now()
        if busy > rank or (busy == rank and not req.is_interrupted):
            resp.is_success, resp.error_code = False, ERROR_HIGHER_PRIORITY_BUSY
            resp.error_message = "reproducción de mayor prioridad en curso"
            response.header.status.value = CommonState.FAILURE
            return response
        wav = os.path.join(self.tmpdir, f"tts_{time.monotonic_ns()}.wav")
        ok = subprocess.run(["espeak-ng", "-v", TTS_VOICE, "-w", wav, req.text], capture_output=True).returncode == 0
        if ok:
            self.player.play_file(wav, req.domain or "tts", rank)
        resp.is_success, resp.error_code = ok, ERROR_NONE if ok else 3
        resp.error_message = "" if ok else "espeak-ng falló"
        response.header.status.value = CommonState.SUCCESS if ok else CommonState.FAILURE
        return response

    def _play_file(self, request, response):
        f = request.file
        path = f.file_name if os.path.isabs(f.file_name) else os.path.join(f.file_path or "/var/tmp", f.file_name)
        response.reponse.header.stamp = _now()
        ext = os.path.splitext(f.file_name)[1].lower()
        if ext not in (".wav", ".pcm") or not os.path.isfile(path) or os.path.getsize(path) == 0:
            response.reponse.status.value = CommonState.INVALID
            response.reponse.message = f"archivo no válido: {path}"
            return response
        raw = None
        if ext == ".pcm":
            raw = (f.info.sample_rate or C.AUDIO_RATE_HZ, f.info.channels or 1)
        else:
            try:
                with wave.open(path) as w:
                    if w.getsampwidth() != 2:
                        raise ValueError
            except (wave.Error, ValueError, EOFError):
                response.reponse.status.value = CommonState.INVALID
                response.reponse.message = "solo WAV PCM de 16 bits"
                return response
        if self._grant_focus(Holder(f.pkg_name, int(f.priority or 6), int(f.priority_weight))):
            self.player.play_file(path, f.pkg_name, float(f.priority or 6))
        response.reponse.status.value = CommonState.SUCCESS     # siempre SUCCESS, como el robot (ver docs)
        return response

    def _stop(self, request, response):
        self.player.stop()
        return self._ok(response)

    def _get_volume(self, request, response):
        response.audio_volume = self.player.volume
        return self._ok(response)

    def _set_volume(self, request, response):
        self.player.volume = int(np.clip(request.audio_volume, 0, 100))
        self.player.muted = self.player.volume == 0          # SetVolume(0) silencia; >0 quita el silencio
        response.audio_volume = self.player.volume
        return self._ok(response)

    def _get_mute(self, request, response):
        response.is_mute = self.player.muted
        return self._ok(response)

    def _set_mute(self, request, response):
        self.player.muted = bool(request.is_mute)
        response.is_mute = self.player.muted
        return self._ok(response)

    def _request_focus(self, request, response):
        r = request.focus_requester
        granted = self._grant_focus(Holder(r.pkg_name, int(r.priority or 6), int(r.priority_weight)))
        response.focus_response = FocusResponse(pkg_name=r.pkg_name, focus_gain=granted)
        return self._ok(response)

    def _abandon_focus(self, request, response):
        r = request.focus_requester
        cur = self.focus
        released = cur is not None and (cur.pkg_name, cur.priority, cur.weight) == (r.pkg_name, int(r.priority or 6),
                                                                                    int(r.priority_weight))
        if released:
            self.focus = None
        response.focus_response = FocusResponse(pkg_name=r.pkg_name, focus_gain=not released)
        return self._ok(response)

    def _get_mic(self, request, response):
        response.mic_source = self.mic_source
        return self._ok(response, "header")

    def _set_mic(self, request, response):
        self.mic_source = 1 if request.mic_source else 0
        return self._ok(response, "header")

    # --- micrófono -------------------------------------------------------------------------------
    def _mic_loop(self) -> None:
        chunk = int(C.AUDIO_RATE_HZ * C.AUDIO_CAPTURE_PERIOD)     # 480 muestras = 30 ms
        n_mic, n_ref = C.AUDIO_CAPTURE_CHANNELS
        proc = None
        vad = {"active": False, "quiet": 0.0, "history": []}
        while True:
            want = self.capture_pub.get_subscription_count() > 0 or self.vad_pub.get_subscription_count() > 0
            if not want or not self.player.available:
                if proc is not None:
                    proc.terminate()
                    proc = None
                time.sleep(0.2)
                continue
            if proc is None:
                proc = subprocess.Popen(["parec", "--raw", "--format=s16le", f"--rate={C.AUDIO_RATE_HZ}",
                                         "--channels=1", "--latency-msec=30"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
            data = proc.stdout.read(chunk * 2)
            if not data or len(data) < chunk * 2:
                proc = None
                time.sleep(0.2)
                continue
            mono = np.frombuffer(data, dtype="<i2")
            if self.capture_pub.get_subscription_count() > 0:
                frames = np.zeros((chunk, n_mic + n_ref), dtype="<i2")
                frames[:, :n_mic] = mono[:, None]
                m = AudioCapture()
                m.stamps = _now()
                m.mic_channels, m.ref_channels = n_mic, n_ref
                m.info.channels, m.info.sample_rate = n_mic + n_ref, C.AUDIO_RATE_HZ
                m.data.data = array("B", frames.tobytes())    # con bytes, rclpy valida byte a byte
                self.capture_pub.publish(m)
            if self.vad_pub.get_subscription_count() > 0:
                self._vad(vad, data, float(np.sqrt(np.mean(mono.astype(np.float32) ** 2))))

    def _vad(self, st: dict, data: bytes, rms: float, threshold: float = 600.0) -> None:
        def send(state: int, payload: bytes) -> None:
            m = ProcessedAudioOutput()
            m.header.stamp = _now()
            m.stream_id = self.mic_source
            m.audio_vad_state = AudioVadStateType(value=state)
            m.audio_data = array("B", payload)
            self.vad_pub.publish(m)
        st["history"] = (st["history"] + [data])[-16:]           # ~0,5 s antes del inicio
        if not st["active"] and rms > threshold:
            st["active"], st["quiet"] = True, 0.0
            send(AudioVadStateType.AUDIO_VAD_STATE_BEGIN, b"".join(st["history"]))
        elif st["active"]:
            st["quiet"] = st["quiet"] + C.AUDIO_CAPTURE_PERIOD if rms < threshold else 0.0
            if st["quiet"] > 0.8:
                st["active"] = False
                send(AudioVadStateType.AUDIO_VAD_STATE_END, b"")
            else:
                send(AudioVadStateType.AUDIO_VAD_STATE_PROCESSING, data)
