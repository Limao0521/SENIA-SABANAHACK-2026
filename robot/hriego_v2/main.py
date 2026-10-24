"""HRIEGO v2 launcher; v1 policy, identity, FSM and motor validation stay intact."""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from hriego.main import validate_registry, verify_scenario_and_audio
from .contract import ContractValidator
from .coordinator import HriCoordinator
from .events import EventBus
from .http_api import HriegoHTTPServer
from .perception import FacePerception
from .resources import preflight_environment, snapshot
from .settings import Settings
from .storage import ConversationMemory, ExperimentJournal
from .ui import HriegoDashboard

log = logging.getLogger("hriego_v2")


def _configure_logging(settings: Settings) -> None:
    path = settings.root / "hriego_v2/data/hriego.log"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    path.chmod(0o600)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[handler, logging.StreamHandler(sys.stderr)])


def validate_llm(settings: Settings) -> dict:
    def docker(*args: str) -> str:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise RuntimeError(f"x2-llm no disponible: {result.stderr[-400:]}")
        return result.stdout.strip()

    details = json.loads(docker("inspect", settings.llm_container))[0]
    if not details["State"]["Running"]:
        raise RuntimeError("El contenedor x2-llm no está ejecutándose")
    # PC: GPU vía DeviceRequests (compose.x86-cuda.yml); Jetson: runtime nvidia.
    gpu = details["HostConfig"].get("DeviceRequests") or (
        [{"Runtime": "nvidia"}] if details["HostConfig"].get("Runtime") == "nvidia" else None)
    if not gpu:
        # Solo mientras x2-llm sea la imagen CPU (sin internet para la de Jetson/CUDA).
        if os.getenv("HRIEGO_LLM_ALLOW_CPU") != "1":
            raise RuntimeError("x2-llm no tiene GPU asignada")
        log.warning("x2-llm sin GPU: HRIEGO_LLM_ALLOW_CPU=1, respuestas más lentas")
        gpu = [{"Runtime": "cpu"}]
    ready = json.loads(docker("exec", settings.llm_container, "curl", "-sf",
                              "http://127.0.0.1:8000/ready"))
    health = json.loads(docker("exec", settings.llm_container, "curl", "-sf",
                               "http://127.0.0.1:8000/health"))
    if ready.get("ready") is not True or health.get("model_path") != "/models/Qwen3-4B-Q4_K_M.gguf":
        raise RuntimeError(f"LocalLLM no está listo con el GGUF requerido: {health}")
    # La ruta del binario cambia según la imagen base (ggml-org: /app; dustynv: otra).
    pattern = os.getenv("HRIEGO_LLAMA_SERVER_PATTERN", "^/app/llama-server")
    pid = docker("exec", settings.llm_container, "pgrep", "-f", pattern).splitlines()[0]
    return {"container": settings.llm_container, "llama_container_pid": int(pid),
            "model_path": health["model_path"], "llm": health["llm"],
            "rag": health["rag"], "device_requests": gpu}


def validate_tts(settings: Settings) -> dict:
    if settings.tts_backend == "supertonic":
        if not os.access(settings.supertonic_python, os.X_OK):
            raise RuntimeError("El entorno Supertonic no es ejecutable")
        if not (settings.supertonic_model_dir / "onnx/duration_predictor.onnx").is_file():
            raise RuntimeError("Falta el modelo ONNX Supertonic")
        return {"model": settings.runtime["tts_model_id"], "backend": "onnxruntime cpu",
                "voice": "M1", "python": str(settings.supertonic_python)}
    if not os.access(settings.tts_binary, os.X_OK):
        raise RuntimeError("El binario Qwen3-TTS mixto no es ejecutable")
    if not settings.tts_model_dir.joinpath("model.safetensors").is_file():
        raise RuntimeError("No está el modelo Qwen3-TTS CustomVoice")
    return {"model": settings.runtime["tts_model_id"], "backend": "cpu",
            "quantization": settings.runtime["tts_quantization"],
            "binary": str(settings.tts_binary)}


def _resource_loop(coordinator: HriCoordinator, stop: threading.Event) -> None:
    while not stop.wait(1):
        coordinator.update_component(resources=snapshot().as_dict())


def run(*, check_only: bool = False, headless: bool = False, max_seconds: float = 0.0) -> int:
    settings = Settings()
    _configure_logging(settings)
    settings.validate_paths()
    hardware = preflight_environment(settings)
    llm = validate_llm(settings)
    tts = validate_tts(settings)
    if settings.audio_backend in {"aimdk", "none"}:
        # Mismo control de escenario que v1, sin exigir pw-play/aplay (audio por AimDK).
        if settings.scenario(settings.default_scenario_id).get("configured_in_user_request") is not True:
            raise RuntimeError("El escenario debe estar expresamente autorizado/configurado")
    else:
        verify_scenario_and_audio(settings)
    registry = validate_registry(settings)
    contract = ContractValidator(settings.contract_schema_path, settings.contract_validator_path)
    snap = snapshot()
    if snap.ram_available_bytes is None or snap.ram_available_bytes < settings.runtime["v2_tts_start_ram_available_bytes"]:
        raise MemoryError("No hay margen de RAM para iniciar el worker TTS persistente")
    if snap.gpu_free_bytes is None or snap.gpu_free_bytes < settings.runtime["v2_min_gpu_free_bytes"]:
        raise MemoryError("No queda la reserva mínima de VRAM")
    report = {"preflight": "ok", "hardware": hardware, "llm": llm, "tts": tts,
              "registry": registry, "contract": "rcp01c.intervention.v1"}
    if check_only:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    settings.conversation_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.experiment_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    memory = ConversationMemory(settings.conversation_db)
    journal = ExperimentJournal(settings.experiment_db)
    event_bus = EventBus()
    coordinator = HriCoordinator(settings, contract, memory, journal, event_bus)
    api = HriegoHTTPServer(coordinator, event_bus, settings.api_host, settings.api_port)
    perception = None
    dashboard = None
    resource_stop = threading.Event()
    resource_thread = None
    started = time.monotonic()
    try:
        perception = FacePerception(settings, coordinator.update_perception, event_bus.publish)
        perception.start()
        tts_load_s = coordinator.tts.start()
        log.info("%s precargado con cámara activa; load_s=%.3f resources=%s",
                 settings.runtime["tts_model_id"],
                 tts_load_s, snapshot().as_dict())
        api.start()
        if not headless:
            dashboard = HriegoDashboard(settings)
            dashboard.create()
        resource_thread = threading.Thread(target=_resource_loop,
                                           args=(coordinator, resource_stop), daemon=True)
        resource_thread.start()
        print(f"HRIEGO v2 listo · LocalLLM GPU {settings.llm_container} · {settings.runtime['tts_model_id']} residente "
              f"· cámara {settings.camera_index} · API http://{settings.api_host}:{settings.api_port}/hriego",
              flush=True)
        while True:
            if max_seconds and time.monotonic() - started >= max_seconds:
                break
            current = perception.snapshot()
            if dashboard:
                key = dashboard.show(dashboard.render(current, coordinator.status()))
                if key in (ord("q"), 27) or not dashboard.visible():
                    break
                if key == ord("c"):
                    coordinator.cancel("keyboard")
            else:
                time.sleep(.03)
    except KeyboardInterrupt:
        pass
    finally:
        api.close()
        coordinator.close()
        if perception is not None:
            perception.stop()
        resource_stop.set()
        if resource_thread:
            resource_thread.join(timeout=2)
        if dashboard:
            dashboard.close()
        memory.close()
        journal.close()
    log.info("HRIEGO v2 detenido; resources=%s", snapshot().as_dict())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="HRIEGO v2: LocalLLM GPU + TTS CPU")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-seconds", type=float, default=0)
    args = parser.parse_args()
    return run(check_only=args.check_only, headless=args.headless, max_seconds=args.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
