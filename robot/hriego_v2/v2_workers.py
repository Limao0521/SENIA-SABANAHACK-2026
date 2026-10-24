"""Persistent HRIEGO v2 adapters. The LLM itself stays in x2-llm."""
from __future__ import annotations

import json
import logging
import os
import select
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Callable

from hriego.workers import AudioPlayer, ModelOutput
from .resources import snapshot

log = logging.getLogger("hriego_v2.workers")

_LLM_BRIDGE = r'''
import json, sys, time
from app.llm_client import LocalLLM
req = json.load(sys.stdin)
llm = LocalLLM()
t0 = time.perf_counter()
try:
    payload = llm._payload(req["messages"], req["max_tokens"], req["temperature"], False)
    payload["response_format"] = {"type": "json_schema", "json_schema": {
        "name": "hriego_intervention", "schema": req["schema"], "strict": True}}
    response = llm.post_raw("/chat/completions", json.dumps(payload).encode())
    response.raise_for_status()
    data = response.json()
    elapsed = time.perf_counter() - t0
    usage = data.get("usage") or {}
    timing = data.get("timings") or {}
    raw = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    print(json.dumps({"ok": True, "raw": raw, "metrics": {
        "total_s": round(elapsed, 4),
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        "prompt_ms": timing.get("prompt_ms"),
        "predicted_ms": timing.get("predicted_ms"),
        "service": "x2-llm LocalLLM -> llama-server",
    }}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
    sys.exit(1)
finally:
    llm.close()
'''


class LocalQwenRunner:
    def __init__(self, settings, progress: Callable[[dict], None] | None = None) -> None:
        self.settings = settings
        self.progress = progress or (lambda _event: None)

    @staticmethod
    def _schema(state: str, turn_id: str, scene_id: str) -> dict[str, Any]:
        intent = {"type": "object", "additionalProperties": False,
                  "required": ["event_id", "type", "trigger_text", "reference_frame", "target_vector"],
                  "properties": {"event_id": {"const": turn_id},
                                 "type": {"const": "POINT_RIGHT"},
                                 "trigger_text": {"const": "a mi derecha"},
                                 "reference_frame": {"const": "X2_base_local"},
                                 "target_vector": {"type": "array", "items": {"type": "number"},
                                                   "minItems": 3, "maxItems": 3}}}
        acts = {"GREETING": "GREETING", "GUIDANCE": "GUIDE", "FAREWELL": "CLOSING"}
        return {"type": "object", "additionalProperties": False,
                "required": ["schema_version", "scene_id", "utterance_id", "speech",
                             "communicative_acts", "motor_intents"],
                "properties": {"schema_version": {"const": "rcp01c.intervention.v1"},
                               "scene_id": {"const": scene_id},
                               "utterance_id": {"const": turn_id},
                               "speech": {"type": "string", "minLength": 1},
                               "communicative_acts": {"type": "array", "minItems": 1,
                                                      "maxItems": 1, "items": {"const": acts[state]}},
                               "motor_intents": {"type": "array", "items": intent,
                                                 "minItems": 1 if state == "GUIDANCE" else 0,
                                                 "maxItems": 1 if state == "GUIDANCE" else 0}}}

    def generate(self, *, messages: list[dict[str, str]], state: str,
                 turn_id: str, scene_id: str,
                 cancel_event: threading.Event | None = None) -> ModelOutput:
        before = snapshot()
        if before.gpu_free_bytes is None or before.ram_available_bytes is None:
            raise RuntimeError("No se pueden medir RAM y VRAM antes de consultar Qwen")
        self.progress({"component": "Qwen", "status": "generating", "resources": before.as_dict()})
        messages = [dict(item) for item in messages]
        if state == "GUIDANCE":
            messages[-1]["content"] += (
                '\nREGLA LITERAL OBLIGATORIA PARA SPEECH: debe contener exactamente la subcadena '
                '"a mi derecha" (primera persona). Usa trigger_text="a mi derecha". '
                'No uses "a su derecha" ni "a la derecha".'
            )
        request = {"messages": messages, "max_tokens": int(self.settings.runtime["max_new_tokens"]),
                   "temperature": float(self.settings.runtime["qwen_temperature"]),
                   "schema": self._schema(state, turn_id, scene_id)}
        cmd = ["docker", "exec", "-i", self.settings.llm_container, "python", "-c", _LLM_BRIDGE]
        started = time.perf_counter()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        try:
            assert proc.stdin is not None
            proc.stdin.write(json.dumps(request, ensure_ascii=False))
            proc.stdin.close()
            deadline = time.monotonic() + 120
            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    proc.terminate()
                    raise RuntimeError("Generación Qwen cancelada")
                if time.monotonic() > deadline:
                    proc.terminate()
                    raise TimeoutError("LocalLLM excedió 120 segundos")
                current = snapshot()
                self.progress({"component": "Qwen", "status": "generating", "resources": current.as_dict()})
                if current.ram_available_bytes is not None and current.ram_available_bytes < self.settings.runtime["v2_min_ram_available_bytes"]:
                    proc.terminate()
                    raise MemoryError("LocalLLM abortado por presión de RAM")
                if current.gpu_free_bytes is not None and current.gpu_free_bytes < self.settings.runtime["v2_min_gpu_free_bytes"]:
                    proc.terminate()
                    raise MemoryError("LocalLLM abortado por presión de VRAM")
                time.sleep(.25)
            stdout = proc.stdout.read() if proc.stdout else ""
            stderr = proc.stderr.read() if proc.stderr else ""
            try:
                result = json.loads(stdout.strip().splitlines()[-1])
            except (IndexError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"LocalLLM no devolvió JSON: {stderr[-500:]}") from exc
            if proc.returncode or not result.get("ok"):
                raise RuntimeError(f"LocalLLM falló: {result.get('error', stderr[-500:])}")
            result["metrics"]["bridge_wall_s"] = round(time.perf_counter() - started, 4)
            result["metrics"]["state"] = state
            log.info("HRIEGO v2 -> %s state=%s wall_s=%s tokens=%s",
                     self.settings.llm_container, state, result["metrics"]["bridge_wall_s"],
                     result["metrics"].get("completion_tokens"))
            return ModelOutput(result, result["metrics"])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


class Qwen3TTSRunner:
    """One CPU mixed-precision server shared by all FSM states and visits."""

    INSTRUCTIONS = {
        "GREETING": "Speak naturally in Spanish. Warm welcome, conversational rhythm, brief pause after the greeting.",
        "GUIDANCE": "Speak naturally in Spanish. Give the direction clearly at a calm conversational pace.",
        "FAREWELL": "Speak naturally in Spanish. Warm, calm farewell with a gentle falling intonation.",
    }

    def __init__(self, settings, progress: Callable[[dict], None] | None = None) -> None:
        self.settings = settings
        self.progress = progress or (lambda _event: None)
        self.process: subprocess.Popen | None = None
        self.log_path: Path | None = None
        self._log_file = None
        self._guard_stop = threading.Event()
        self._guard_thread: threading.Thread | None = None
        self._guard_error: str | None = None
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.settings.tts_port}"

    def _health(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + "/v1/health", timeout=.5) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def _guard_loop(self) -> None:
        cfg = self.settings.runtime
        while not self._guard_stop.wait(.25):
            proc = self.process
            if proc is None or proc.poll() is not None:
                return
            snap = snapshot()
            self.progress({"component": "Qwen3-TTS", "status": "resident", "resources": snap.as_dict()})
            if snap.ram_available_bytes is not None and snap.ram_available_bytes < cfg["v2_min_ram_available_bytes"]:
                self._guard_error = "RAM disponible inferior a 2 GiB"
            elif snap.gpu_free_bytes is not None and snap.gpu_free_bytes < cfg["v2_min_gpu_free_bytes"]:
                self._guard_error = "VRAM libre inferior a 1 GiB"
            if self._guard_error:
                log.error("Guardián TTS: %s", self._guard_error)
                if proc.poll() is None:
                    proc.terminate()
                return

    def start(self, cancel_event: threading.Event | None = None) -> float:
        with self._lock:
            if self._guard_error:
                raise MemoryError("Qwen3-TTS suspendido por el guardián: " + self._guard_error)
            if self.process is not None and self.process.poll() is None:
                if self._health():
                    return 0.0
                raise RuntimeError("Worker TTS existe pero no está listo")
            before = snapshot()
            cfg = self.settings.runtime
            if before.ram_available_bytes is None or before.ram_available_bytes < cfg["v2_tts_start_ram_available_bytes"]:
                raise MemoryError("No hay 5 GiB de RAM disponible para cargar Qwen3-TTS con seguridad")
            if before.gpu_free_bytes is None or before.gpu_free_bytes < cfg["v2_min_gpu_free_bytes"]:
                raise MemoryError("No se puede verificar 1 GiB de VRAM libre antes del TTS")
            if self._health():
                raise RuntimeError(f"Puerto TTS {self.settings.tts_port} ocupado por otro proceso")
            logs = self.settings.root / "hriego_v2/data/workers"
            logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.log_path = logs / f"qwen3_tts_{uuid.uuid4().hex}.log"
            self._log_file = self.log_path.open("w", encoding="utf-8")
            self.log_path.chmod(0o600)
            cmd = [str(self.settings.tts_binary), "-d", str(self.settings.tts_model_dir),
                   "--backend", "cpu", "--threads", os.getenv("HRIEGO_V2_TTS_THREADS", "4"),
                   "--quant-mixed-int6=q4n10",
                   "--serve", str(self.settings.tts_port)]
            env = os.environ.copy()
            env["QWEN_CP_PREC"] = "int4"
            env["MALLOC_ARENA_MAX"] = "2"
            started = time.perf_counter()
            self._guard_error = None
            self._guard_stop.clear()
            self.process = subprocess.Popen(cmd, stdout=self._log_file, stderr=subprocess.STDOUT, env=env)
            self._guard_thread = threading.Thread(target=self._guard_loop, name="hriego-v2-tts-guard", daemon=True)
            self._guard_thread.start()
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if cancel_event is not None and cancel_event.is_set():
                    self.close()
                    raise RuntimeError("Carga TTS cancelada")
                if self._guard_error:
                    reason = self._guard_error
                    self.close()
                    raise MemoryError("Qwen3-TTS abortado: " + reason)
                if self.process.poll() is not None:
                    self.close()
                    raise RuntimeError(f"Qwen3-TTS salió durante la carga; log: {self.log_path}")
                if self._health():
                    log_text = self.log_path.read_text(errors="replace")
                    if ("[mixed-int6] plan:" not in log_text
                            or "int8 10 / q6 0 / q4 18" not in log_text
                            or "Q4_0 quantization done (5 layers + 15 lm_heads)" not in log_text):
                        self.close()
                        raise RuntimeError("El backend no confirmó Talker mixto INT8/INT4 y Code Predictor INT4")
                    load_s = time.perf_counter() - started
                    log.info("Qwen3-TTS residente: Talker mixto INT8/INT4, CP Q4_0 INT4; load_s=%.3f pid=%s", load_s, self.process.pid)
                    return load_s
                time.sleep(.25)
            self.close()
            raise TimeoutError("Qwen3-TTS no quedó listo en 45 segundos")

    def synthesize(self, *, speech: str, state: str, turn_id: str, visit_id: str,
                   cancel_event: threading.Event | None = None) -> ModelOutput:
        load_s = self.start(cancel_event)
        output_path = self.settings.audio_root / visit_id / f"{turn_id}.wav"
        output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        output_path.parent.chmod(0o700)
        request = {"text": speech, "speaker": "aiden", "language": "Spanish",
                   "instruct": self.INSTRUCTIONS[state], "seed": 2026,
                   "temperature": .5, "top_k": 50, "top_p": 1.0, "rep_penalty": 1.05}
        req = urllib.request.Request(self.url + "/v1/tts/stream",
                                     data=json.dumps(request, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        started = time.perf_counter()
        first_pcm = None
        samples = 0
        player: subprocess.Popen | None = None
        playback_started = None
        playback_finished = None
        player_name = "pw-play" if Path("/usr/bin/pw-play").exists() else "aplay"
        stream_audio = os.getenv("HRIEGO_V2_STREAM_AUDIO", "0") == "1"
        try:
            with urllib.request.urlopen(req, timeout=120) as response, wave.open(str(output_path), "wb") as wav:
                if response.status != 200 or response.headers.get_content_type() != "audio/pcm":
                    raise RuntimeError(f"Respuesta TTS inesperada: HTTP {response.status}")
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(24_000)
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Síntesis TTS cancelada")
                    if self._guard_error:
                        raise MemoryError("Qwen3-TTS abortado: " + self._guard_error)
                    block = response.read(4096)
                    if not block:
                        break
                    if first_pcm is None:
                        first_pcm = time.perf_counter()
                    if len(block) % 2:
                        raise ValueError("PCM TTS de longitud impar")
                    wav.writeframes(block)
                    samples += len(block) // 2
                    if stream_audio and player is None:
                        if player_name == "pw-play":
                            command = ["pw-play", "--rate", "24000", "--channels", "1",
                                       "--format", "s16", "-"]
                        else:
                            command = ["aplay", "-q", "-t", "raw", "-f", "S16_LE",
                                       "-r", "24000", "-c", "1", "-"]
                        player = subprocess.Popen(command, stdin=subprocess.PIPE,
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                                  bufsize=0)
                        playback_started = time.perf_counter()
                        self.progress({"component": "Audio", "status": "playing"})
                    if player is not None:
                        assert player.stdin is not None
                        player.stdin.write(block)
            duration_s = samples / 24_000
            if duration_s <= 0 or duration_s > float(self.settings.runtime["max_audio_duration_seconds"]):
                raise ValueError(f"Duración WAV fuera de rango: {duration_s:.2f}s")
            if stream_audio:
                if player is None or player.stdin is None:
                    raise RuntimeError("Qwen3-TTS no produjo audio reproducible")
                player.stdin.close()
                deadline = time.monotonic() + max(10, duration_s + 5)
                while player.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        raise RuntimeError("Reproducción TTS cancelada")
                    if self._guard_error:
                        raise MemoryError("Qwen3-TTS abortado: " + self._guard_error)
                    if time.monotonic() > deadline:
                        raise TimeoutError("La reproducción PCM excedió su duración")
                    time.sleep(.05)
                playback_finished = time.perf_counter()
                if player.returncode:
                    stderr = player.stderr.read().decode(errors="replace") if player.stderr else ""
                    raise RuntimeError(f"El reproductor PCM falló: {stderr[-300:]}")
                self.progress({"component": "Audio", "status": "finished"})
            output_path.chmod(0o600)
            metrics = {"model_id": self.settings.runtime["tts_model_id"],
                       "quantization": self.settings.runtime["tts_quantization"],
                       "backend": "cpu", "persistent_pid": self.process.pid if self.process else None,
                       "load_s": round(load_s, 4),
                       "first_pcm_ms": round((first_pcm - started) * 1000, 2) if first_pcm else None,
                       "audio_onset_ms": round((playback_started - started) * 1000, 2) if playback_started else None,
                       "synthesis_s": round(time.perf_counter() - started, 4),
                       "duration_s": round(duration_s, 4), "sample_rate": 24_000,
                       "audio_path": str(output_path), "prosody": self.INSTRUCTIONS[state],
                       "stream_played": stream_audio,
                       "playback_player": player_name if stream_audio else None,
                       "playback_s": round(playback_finished - playback_started, 4) if playback_finished else None}
            log.info("Qwen3-TTS state=%s pid=%s synth_s=%s audio_s=%s", state,
                     metrics["persistent_pid"], metrics["synthesis_s"], metrics["duration_s"])
            return ModelOutput({"path": str(output_path)}, metrics)
        except BaseException:
            if player is not None and player.poll() is None:
                player.terminate()
                try:
                    player.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    player.kill()
                    player.wait()
            output_path.unlink(missing_ok=True)
            raise

    def close(self) -> None:
        self._guard_stop.set()
        proc, self.process = self.process, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._guard_thread is not None and self._guard_thread is not threading.current_thread():
            self._guard_thread.join(timeout=2)
        self._guard_thread = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None


class SupertonicTTSRunner:
    """Optional persistent ONNX CPU voice; preserves the existing audio contract."""

    def __init__(self, settings, progress: Callable[[dict], None] | None = None) -> None:
        self.settings = settings
        self.progress = progress or (lambda _event: None)
        self.process: subprocess.Popen | None = None
        self._guard_error: str | None = None
        self._guard_stop = threading.Event()
        self._guard_thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def _reply(self, deadline: float, cancel_event: threading.Event | None) -> dict:
        proc = self.process
        assert proc is not None and proc.stdout is not None
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Síntesis Supertonic cancelada")
            if self._guard_error:
                raise MemoryError("Supertonic detenido: " + self._guard_error)
            if proc.poll() is not None:
                raise RuntimeError(f"Worker Supertonic terminó con código {proc.returncode}")
            if select.select([proc.stdout], [], [], .1)[0]:
                line = proc.stdout.readline()
                if line.startswith("HRIEGO_WORKER_JSON="):
                    result = json.loads(line.removeprefix("HRIEGO_WORKER_JSON="))
                    if not result.get("ok"):
                        raise RuntimeError("Supertonic: " + str(result.get("error")))
                    return result
        raise TimeoutError("Supertonic excedió el tiempo de espera")

    def _guard_loop(self) -> None:
        while not self._guard_stop.wait(.25):
            proc = self.process
            if proc is None or proc.poll() is not None:
                return
            snap = snapshot()
            self.progress({"component": "Supertonic", "status": "resident", "resources": snap.as_dict()})
            if snap.ram_available_bytes is None or snap.ram_available_bytes < self.settings.runtime["v2_min_ram_available_bytes"]:
                self._guard_error = "RAM disponible inferior a 2 GiB o no medible"
            elif snap.gpu_free_bytes is None or snap.gpu_free_bytes < self.settings.runtime["v2_min_gpu_free_bytes"]:
                self._guard_error = "VRAM libre inferior a 1 GiB o no medible"
            if self._guard_error:
                log.error("Guardián Supertonic: %s", self._guard_error)
                proc.terminate()
                return

    def start(self, cancel_event: threading.Event | None = None) -> float:
        with self._lock:
            if self._guard_error:
                raise MemoryError("Supertonic suspendido: " + self._guard_error)
            if self.process is not None:
                if self.process.poll() is None:
                    return 0.0
                raise RuntimeError("Worker Supertonic terminó inesperadamente")
            before = snapshot()
            if before.ram_available_bytes is None or before.ram_available_bytes < self.settings.runtime["v2_tts_start_ram_available_bytes"]:
                raise MemoryError("No hay 5 GiB de RAM disponible para cargar Supertonic")
            if before.gpu_free_bytes is None or before.gpu_free_bytes < self.settings.runtime["v2_min_gpu_free_bytes"]:
                raise MemoryError("No queda 1 GiB de VRAM libre")
            cmd = [str(self.settings.supertonic_python), "-u",
                   str(self.settings.root / "hriego_v2/supertonic_worker.py"),
                   str(self.settings.supertonic_model_dir)]
            self._guard_error = None
            self._guard_stop.clear()
            self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True, bufsize=1)
            self._guard_thread = threading.Thread(target=self._guard_loop, name="hriego-supertonic-guard", daemon=True)
            self._guard_thread.start()
            try:
                result = self._reply(time.monotonic() + 30, cancel_event)
                if not result.get("ready") or result.get("sample_rate") != 44100:
                    raise RuntimeError("Worker Supertonic no confirmó carga y frecuencia")
                log.info("Supertonic M1 residente pid=%s load_s=%s", self.process.pid, result["load_s"])
                return float(result["load_s"])
            except BaseException:
                self.close()
                raise

    def synthesize(self, *, speech: str, state: str, turn_id: str, visit_id: str,
                   cancel_event: threading.Event | None = None) -> ModelOutput:
        load_s = self.start(cancel_event)
        with self._lock:
            output = self.settings.audio_root / visit_id / f"{turn_id}.wav"
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output.parent.chmod(0o700)
            proc = self.process
            assert proc is not None and proc.stdin is not None
            self.progress({"component": "Supertonic", "status": "synthesizing"})
            try:
                proc.stdin.write(json.dumps({"speech": speech, "state": state, "output": str(output)},
                                            ensure_ascii=False) + "\n")
                proc.stdin.flush()
                result = self._reply(time.monotonic() + 60, cancel_event)
                duration_s = float(result["duration_s"])
                if not output.is_file() or not 0 < duration_s <= float(self.settings.runtime["max_audio_duration_seconds"]):
                    raise ValueError("WAV Supertonic ausente o de duración inválida")
                output.chmod(0o600)
                metrics = {"model_id": "Supertone/supertonic-3", "voice": "M1",
                           "backend": "onnxruntime CPU", "persistent_pid": proc.pid,
                           "load_s": round(load_s, 4), "synthesis_s": result["synthesis_s"],
                           "duration_s": duration_s, "sample_rate": result["sample_rate"],
                           "audio_path": str(output), "prosody": f"M1 speed={result['speed']}",
                           "stream_played": False}
                log.info("Supertonic state=%s pid=%s synth_s=%s audio_s=%s",
                         state, proc.pid, result["synthesis_s"], duration_s)
                return ModelOutput({"path": str(output)}, metrics)
            except BaseException:
                output.unlink(missing_ok=True)
                raise

    def close(self) -> None:
        self._guard_stop.set()
        proc, self.process = self.process, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._guard_thread is not None and self._guard_thread is not threading.current_thread():
            self._guard_thread.join(timeout=2)
        self._guard_thread = None
