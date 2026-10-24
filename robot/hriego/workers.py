from __future__ import annotations

import json
import os
import select
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .resources import GIB, MemoryGuard, snapshot


@dataclass
class ModelOutput:
    value: dict[str, Any]
    metrics: dict[str, Any]
    stderr_path: Path | None = None


class ExclusiveModelSlot:
    """Serializes Qwen and IndexTTS; processes exit before the next model loads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: str | None = None

    @property
    def active(self) -> str | None:
        with self._lock:
            return self._active

    def acquire(self, name: str) -> None:
        with self._lock:
            if self._active is not None:
                raise RuntimeError(f"Modelo {self._active} sigue activo; no se carga {name}")
            self._active = name

    def release(self, name: str) -> None:
        with self._lock:
            if self._active != name:
                raise RuntimeError(f"Liberación inconsistente: activo={self._active}, solicitado={name}")
            self._active = None


class QwenRunner:
    def __init__(self, settings, model_slot: ExclusiveModelSlot, progress: Callable[[dict], None] | None = None) -> None:
        self.settings = settings
        self.model_slot = model_slot
        self.progress = progress or (lambda _event: None)

    def generate(self, *, messages: list[dict[str, str]], state: str,
                 cancel_event: threading.Event | None = None) -> ModelOutput:
        self.model_slot.acquire("Qwen3.5-4B")
        process: subprocess.Popen[str] | None = None
        before = None
        stderr_path = None
        try:
            guard = MemoryGuard(self.settings, allowed_gpu_pids={os.getpid()})
            before = guard.preflight("qwen")
            self.progress({"component": "Qwen", "status": "loading", "resources": before.as_dict()})
            self.settings.audio_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            logs = self.settings.root / "hriego/data/workers"
            logs.mkdir(parents=True, exist_ok=True, mode=0o700)
            logs.chmod(0o700)
            stderr_path = logs / f"qwen_{uuid.uuid4().hex}.stderr.log"
            stderr_file = stderr_path.open("w", encoding="utf-8")
            stderr_path.chmod(0o600)
            env = os.environ.copy()
            env.update({
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "PYTHONPATH": str(self.settings.root) + os.pathsep + env.get("PYTHONPATH", ""),
            })
            process = subprocess.Popen(
                [str(self.settings.qwen_python), "-u", "-m", "hriego.qwen_worker"],
                cwd=self.settings.root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=stderr_file, text=True, bufsize=1, start_new_session=True,
            )
            stderr_file.close()
            request = {
                "model_path": str(self.settings.qwen_model_path),
                "model_id": self.settings.runtime["qwen_model_id"],
                "model_revision": self.settings.runtime["qwen_revision"],
                "messages": messages,
                "dtype": self.settings.runtime["qwen_dtype"],
                "device_map": self.settings.runtime["qwen_device_map"],
                "gpu_limit": self.settings.runtime["qwen_gpu_limit"],
                "cpu_limit": self.settings.runtime["qwen_cpu_limit"],
                "input_token_budget": int(self.settings.runtime["input_token_budget"]),
                "max_new_tokens": int(self.settings.runtime["max_new_tokens"]),
            }
            assert process.stdin is not None
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.close()
            deadline = time.monotonic() + 900.0
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    process.terminate()
                    process.wait(timeout=5)
                    raise RuntimeError("Generación Qwen cancelada")
                snap = snapshot()
                self.progress({"component": "Qwen", "status": "generating", "resources": snap.as_dict()})
                if snap.ram_available_bytes is not None and snap.ram_available_bytes < int(self.settings.runtime["abort_ram_available_bytes"]):
                    process.terminate()
                    process.wait(timeout=5)
                    raise MemoryError("Qwen abortado por presión de RAM")
                if snap.gpu_free_bytes is not None and snap.gpu_free_bytes < int(self.settings.runtime["abort_gpu_free_bytes"]):
                    process.terminate()
                    process.wait(timeout=5)
                    raise MemoryError("Qwen abortado por presión de VRAM")
                if guard.swap_pressure(snap):
                    process.terminate()
                    process.wait(timeout=5)
                    raise MemoryError("Qwen abortado por presión de swap")
                if time.monotonic() > deadline:
                    process.terminate()
                    process.wait(timeout=5)
                    raise TimeoutError("Qwen excedió 900 segundos")
                time.sleep(0.5)
            stdout = process.stdout.read() if process.stdout else ""
            if process.returncode != 0 and not stdout.strip():
                raise RuntimeError(f"Qwen worker terminó con código {process.returncode}; log: {stderr_path}")
            try:
                response = json.loads(stdout.strip().splitlines()[-1])
            except (json.JSONDecodeError, IndexError) as exc:
                raise RuntimeError(f"Respuesta JSON inválida del worker Qwen; log: {stderr_path}") from exc
            if not response.get("ok"):
                raise RuntimeError(f"Qwen no pudo generar: {response.get('error', 'error desconocido')}; log: {stderr_path}")
            after = snapshot()
            self._assert_gpu_released(before.gpu_free_bytes, after.gpu_free_bytes, "Qwen")
            return ModelOutput(response, response.get("metrics", {}), stderr_path)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.wait()
            self.model_slot.release("Qwen3.5-4B")

    @staticmethod
    def _assert_gpu_released(before_free: int | None, after_free: int | None, name: str) -> None:
        if before_free is None or after_free is None:
            raise RuntimeError(f"No se pudo verificar la liberación de VRAM tras {name}")
        if after_free < before_free - int(1.0 * GIB):
            raise MemoryError(f"VRAM no regresó cerca del nivel inicial tras {name}; se cancela la siguiente carga")


@dataclass
class WorkerAudio:
    path: Path
    sample_rate: int
    duration_s: float
    model_id: str
    model_revision: str | None
    metadata: dict[str, Any]


class IndexTTSWorker:
    """Process adapter for the existing Laplace IndexTTS backend."""

    def __init__(self, settings, environment: dict[str, str], log_path: Path) -> None:
        self.settings = settings
        self.environment = environment
        self.log_path = log_path
        self.process: subprocess.Popen[str] | None = None
        self._stderr = None
        self.model_id = settings.runtime["indextts_model_id"]
        self.model_revision = settings.runtime["indextts_model_revision"]

    def load(self, config: dict[str, Any]) -> dict[str, Any]:
        if self.process is not None:
            raise RuntimeError("IndexTTS worker already started")
        self.log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.log_path.parent.chmod(0o700)
        self._stderr = self.log_path.open("w", encoding="utf-8")
        self.log_path.chmod(0o600)
        env = os.environ.copy()
        env.update(self.environment)
        env["PYTHONPATH"] = str(self.settings.root) + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(
            [str(self.settings.index_python), "-u", "-m", "hriego.index_tts_worker"],
            cwd=self.settings.voice_root, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=self._stderr, text=True, bufsize=1, start_new_session=True,
        )
        try:
            result = self._request({"op": "load", "config": config}, timeout_s=2400.0)
            self.model_id = result.get("model_id", self.model_id)
            self.model_revision = result.get("model_revision", self.model_revision)
            return result
        except Exception:
            self.unload()
            raise

    def synthesize(self, stimulus, output_path: Path) -> WorkerAudio:
        result = self._request({
            "op": "synthesize",
            "stimulus": {"stimulus_id": stimulus.stimulus_id, "spoken_text": stimulus.spoken_text},
            "output_path": str(output_path), "instruct": None, "seed": None,
        }, timeout_s=2400.0)
        return WorkerAudio(
            path=Path(result["path"]), sample_rate=int(result["sample_rate"]),
            duration_s=float(result["duration_s"]), model_id=result.get("model_id", self.model_id),
            model_revision=result.get("model_revision", self.model_revision),
            metadata=result.get("metadata", {}),
        )

    def _request(self, request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        process = self.process
        if process is None or process.poll() is not None:
            raise RuntimeError(f"IndexTTS worker not running (exit={process.returncode if process else None})")
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("IndexTTS worker pipes unavailable")
        process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], timeout_s)
        if not ready:
            raise TimeoutError(f"IndexTTS worker timed out after {timeout_s}s")
        line = process.stdout.readline()
        if not line:
            raise RuntimeError(f"IndexTTS worker closed stdout (exit={process.poll()}); log: {self.log_path}")
        try:
            response = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid IndexTTS worker response; log: {self.log_path}") from exc
        if not response.get("ok"):
            raise RuntimeError(f"IndexTTS worker failed: {response.get('error')}; log: {self.log_path}")
        return response.get("result", {})

    def unload(self) -> None:
        process, self.process = self.process, None
        if process is None:
            if self._stderr is not None:
                self._stderr.close()
                self._stderr = None
            return
        try:
            if process.poll() is None:
                self._request_on(process, {"op": "close"}, timeout_s=20.0)
        except Exception:
            pass
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=15.0)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdout, process.stderr):
            if stream and stream is not self._stderr:
                stream.close()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None

    def _request_on(self, process: subprocess.Popen[str], request: dict[str, Any], *, timeout_s: float) -> dict[str, Any]:
        if process.poll() is not None or process.stdin is None or process.stdout is None:
            return {}
        process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()
        ready, _, _ = select.select([process.stdout], [], [], timeout_s)
        if not ready:
            raise TimeoutError("IndexTTS worker did not close cleanly")
        response = json.loads(process.stdout.readline())
        if not response.get("ok"):
            raise RuntimeError(response.get("error", "worker close failed"))
        return response.get("result", {})


class IndexTTSRunner:
    def __init__(self, settings, model_slot: ExclusiveModelSlot,
                 progress: Callable[[dict], None] | None = None) -> None:
        self.settings = settings
        self.model_slot = model_slot
        self.progress = progress or (lambda _event: None)

    def synthesize(self, *, speech: str, turn_id: str, visit_id: str,
                   cancel_event: threading.Event | None = None) -> ModelOutput:
        self.model_slot.acquire("IndexTTS-2.5")
        backend = None
        before = None
        watchdog = None
        guard = None
        watchdog_stop = threading.Event()
        watchdog_error: list[str] = []
        started = time.perf_counter()
        output_path = self.settings.audio_root / visit_id / f"{turn_id}.wav"
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            output_path.parent.chmod(0o700)
            guard = MemoryGuard(self.settings, allowed_gpu_pids={os.getpid()})
            before = guard.preflight("indextts")
            self.progress({"component": "IndexTTS", "status": "loading", "resources": before.as_dict()})
            env = {
                "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
                "HF_HUB_DISABLE_XET": "1",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "NUMBA_CACHE_DIR": str(self.settings.index_cache),
            }
            cfg = self.settings.runtime
            config = {
                "model_dir": str(self.settings.index_model_dir),
                "reference_audio": str(self.settings.voice_reference),
                "model_revision": cfg["indextts_model_revision"],
                "cfg_path": str(self.settings.index_model_dir / "config.yaml"),
                "language": cfg["index_language"],
                "use_qwen_emo": cfg["index_use_qwen_emo"],
                "emo_alpha": cfg["index_emo_alpha"],
                "use_emo_text": cfg["index_use_emo_text"],
                "emotion_vector": cfg["index_emotion_vector"],
                "duration_factor": cfg["index_duration_factor"],
                "use_bf16": cfg["index_use_bf16"],
            }
            logs = self.settings.root / "hriego/data/workers"
            backend = IndexTTSWorker(
                self.settings, env, logs / f"indextts_{uuid.uuid4().hex}.stderr.log"
            )
            load_error: list[BaseException] = []

            def watchdog_loop() -> None:
                while not watchdog_stop.wait(0.5):
                    proc = getattr(backend, "process", None)
                    if proc is not None and proc.poll() is None:
                        snap = snapshot()
                        if cancel_event is not None and cancel_event.is_set():
                            reason = "síntesis cancelada"
                        elif snap.ram_available_bytes is not None and snap.ram_available_bytes < int(cfg["abort_ram_available_bytes"]):
                            reason = "IndexTTS abortado por presión de RAM"
                        elif snap.gpu_free_bytes is not None and snap.gpu_free_bytes < int(cfg["abort_gpu_free_bytes"]):
                            reason = "IndexTTS abortado por presión de VRAM"
                        elif guard.swap_pressure(snap):
                            reason = "IndexTTS abortado por presión de swap"
                        else:
                            self.progress({"component": "IndexTTS", "status": "loading_or_synthesizing", "resources": snap.as_dict()})
                            continue
                        watchdog_error.append(reason)
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        return

            watchdog = threading.Thread(target=watchdog_loop, name="hriego-memory-guard", daemon=True)
            watchdog.start()
            load_started = time.perf_counter()
            try:
                load_result = backend.load(config)
            except BaseException as exc:
                load_error.append(exc)
            load_s = time.perf_counter() - load_started
            if watchdog_error:
                raise MemoryError(watchdog_error[-1]) from (load_error[-1] if load_error else None)
            if load_error:
                raise load_error[0]
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Síntesis IndexTTS cancelada")
            self.progress({"component": "IndexTTS", "status": "synthesizing", "turn_id": turn_id})
            synth_started = time.perf_counter()
            audio = backend.synthesize(
                SynthesisInput(stimulus_id=turn_id, spoken_text=speech), output_path,
            )
            synthesis_s = time.perf_counter() - synth_started
            if watchdog_error:
                raise MemoryError(watchdog_error[-1])
            if cancel_event is not None and cancel_event.is_set():
                raise RuntimeError("Síntesis IndexTTS cancelada")
            if not output_path.is_file() or output_path.stat().st_size <= 44:
                raise RuntimeError("IndexTTS no produjo un WAV no vacío")
            output_path.chmod(0o600)
            with wave.open(str(output_path), "rb") as wav:
                sample_rate = wav.getframerate()
                frames = wav.getnframes()
                channels = wav.getnchannels()
                duration_s = frames / sample_rate if sample_rate else 0.0
            if sample_rate != 22050 or duration_s <= 0 or duration_s > float(cfg["max_audio_duration_seconds"]):
                raise ValueError(f"WAV fuera de parámetros: {sample_rate} Hz, {duration_s:.2f} s")
            metrics = {
                "model_id": audio.model_id,
                "model_revision": audio.model_revision,
                "source_revision": cfg["indextts_source_revision"],
                "voice_identity_id": "USER_AUTHORIZED_VOICE_20261014",
                "load_s": round(load_s, 4),
                "synthesis_s": round(synthesis_s, 4),
                "wall_s": round(time.perf_counter() - started, 4),
                "sample_rate": sample_rate,
                "channels": channels,
                "duration_s": round(duration_s, 4),
                "wav_bytes": output_path.stat().st_size,
                "emotion": "fixed calm vector from Laplace config",
                "qwen_emotion_loaded": bool(load_result.get("qwen_emotion_loaded", True)),
                "audio_path": str(output_path),
                "metadata": {"sample_rate": audio.sample_rate, "duration_s": audio.duration_s},
            }
            return ModelOutput({"path": str(output_path)}, metrics)
        finally:
            try:
                watchdog_stop.set()
                if watchdog is not None:
                    watchdog.join(timeout=2.0)
                if backend is not None:
                    try:
                        backend.unload()
                    except Exception as exc:
                        self.progress({"component": "IndexTTS", "status": "unload_error", "error": str(exc)})
                if before is not None:
                    after = snapshot()
                    QwenRunner._assert_gpu_released(before.gpu_free_bytes, after.gpu_free_bytes, "IndexTTS")
            finally:
                self.model_slot.release("IndexTTS-2.5")


class SynthesisInput:
    """Small contract-compatible object for Laplace's existing worker adapter."""

    def __init__(self, stimulus_id: str, spoken_text: str) -> None:
        self.stimulus_id = stimulus_id
        self.spoken_text = spoken_text


class AudioPlayer:
    def __init__(self, progress: Callable[[dict], None] | None = None) -> None:
        self.progress = progress or (lambda _event: None)
        self._active: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def play(self, path: Path, *, cancel_event: threading.Event | None = None) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(path)
        attempts = []
        commands = []
        if shutil.which("pw-play"):
            commands.append(["pw-play", str(path)])
        if shutil.which("aplay"):
            commands.append(["aplay", "-q", str(path)])
        if not commands:
            raise RuntimeError("No hay pw-play ni aplay disponible para reproducir el WAV")
        last_error = None
        for command in commands:
            started = time.perf_counter()
            try:
                process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                           start_new_session=True)
            except OSError as exc:
                last_error = exc
                continue
            with self._lock:
                self._active = process
            self.progress({"component": "Audio", "status": "playing", "player": command[0]})
            try:
                while process.poll() is None:
                    if cancel_event is not None and cancel_event.is_set():
                        process.terminate()
                        process.wait(timeout=3)
                        raise RuntimeError("Reproducción cancelada")
                    time.sleep(0.1)
                stderr = process.stderr.read() if process.stderr else b""
                if process.returncode == 0:
                    return {"player": command[0], "duration_s": round(time.perf_counter() - started, 4)}
                last_error = RuntimeError(stderr.decode(errors="replace")[-500:] or f"{command[0]} exit={process.returncode}")
                attempts.append({"player": command[0], "error": str(last_error)})
            finally:
                with self._lock:
                    self._active = None
                if process.stderr:
                    process.stderr.close()
        raise RuntimeError(f"No se pudo reproducir el WAV: {attempts or last_error}")

    def stop(self) -> None:
        with self._lock:
            process = self._active
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
