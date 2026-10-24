from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .contract import ContractValidator
from .coordinator import HriCoordinator
from .events import EventBus
from .http_api import HriegoHTTPServer
from .perception import FacePerception
from .resources import MemoryGuard, gpu_apps, snapshot
from .settings import Settings
from .storage import ConversationMemory, ExperimentJournal
from .ui import HriegoDashboard

log = logging.getLogger("hriego")


def _configure_logging(settings: Settings) -> None:
    path = settings.root / "hriego/data/hriego.log"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    path.chmod(0o600)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[handler, logging.StreamHandler(sys.stderr)])


def _run_probe(python: Path, label: str, code: str) -> dict:
    completed = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True,
        timeout=90.0, env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
    )
    if completed.returncode:
        raise RuntimeError(f"No se pudo validar el entorno {label}: {completed.stderr[-1000:]}")
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise RuntimeError(f"El entorno {label} devolvió una sonda no válida") from exc


def validate_model_environments(settings: Settings) -> dict:
    qwen = _run_probe(
        settings.qwen_python, "Qwen",
        "import json,sys,torch,transformers; "
        "print(json.dumps({'python':sys.version.split()[0],'torch':torch.__version__,'transformers':transformers.__version__,"
        "'cuda':torch.cuda.is_available(),'cuda_version':torch.version.cuda,"
        "'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"
        "'free_total':torch.cuda.mem_get_info() if torch.cuda.is_available() else None}))",
    )
    indextts = _run_probe(
        settings.index_python, "IndexTTS",
        "import importlib.metadata as m,json,sys,torch,transformers; "
        "print(json.dumps({'python':sys.version.split()[0],'package':m.version('indextts'),"
        "'torch':torch.__version__,'transformers':transformers.__version__,"
        "'cuda':torch.cuda.is_available(),'cuda_version':torch.version.cuda,"
        "'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,"
        "'free_total':torch.cuda.mem_get_info() if torch.cuda.is_available() else None}))",
    )
    for label, report in (("Qwen", qwen), ("IndexTTS", indextts)):
        if not report.get("cuda"):
            raise RuntimeError(f"{label} no tiene CUDA visible; no se cargarán modelos en CPU")
        if report.get("device") != "NVIDIA GeForce RTX 5070 Ti Laptop GPU":
            raise RuntimeError(f"GPU distinta de la medida en los smoke tests: {report.get('device')}")
    if not qwen.get("transformers", "").startswith("5."):
        raise RuntimeError(f"Qwen3.5 requiere el entorno Transformers 5.x; instalado {qwen.get('transformers')}")
    if indextts.get("python") != "3.11.16" or indextts.get("torch") != "2.8.0+cu128" or indextts.get("transformers") != "4.52.1":
        raise RuntimeError("El entorno IndexTTS no coincide con las versiones verificadas del piloto")
    if indextts.get("package") != "2.0.0":
        raise RuntimeError(f"Paquete IndexTTS no coincide con la versión instalada verificada: {indextts.get('package')}")
    versions_lock = (settings.voice_root / "configs/model_versions.yaml").read_text(encoding="utf-8")
    worker_config = (settings.voice_root / "configs/backend_workers.yaml").read_text(encoding="utf-8")
    if settings.runtime["qwen_revision"] not in versions_lock:
        raise RuntimeError("El revision local de Qwen3.5 no coincide con model_versions.yaml")
    if settings.runtime["indextts_model_revision"] not in versions_lock:
        raise RuntimeError("El revision local de IndexTTS no coincide con model_versions.yaml")
    if settings.runtime["indextts_model_revision"] not in worker_config or "USER_AUTHORIZED_VOICE_20261014" not in worker_config:
        raise RuntimeError("La referencia/identidad vocal no coincide con la configuración de Laplace")
    source_repo = settings.voice_root / "third_party/index-tts"
    source_commit = subprocess.run(["git", "-C", str(source_repo), "rev-parse", "HEAD"],
                                   capture_output=True, text=True, timeout=5.0, check=True).stdout.strip()
    if source_commit != settings.runtime["indextts_source_revision"]:
        raise RuntimeError(f"El código fuente IndexTTS cambió: {source_commit}")
    return {"qwen": qwen, "indextts": indextts}


def validate_registry(settings: Settings) -> dict:
    from live.identity_registry import IdentityRegistry, REGISTRY_RANKS

    registry = IdentityRegistry(settings.identity_db)
    integrity = registry.integrity_check()
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_errors"]:
        raise RuntimeError("La SQLite institucional no supera integrity_check; se deja intacta")
    encoder = registry.active_encoder_key()
    gallery = registry.load_gallery(encoder)
    expected = {"P001", "P002", "P003", "P004", "P005"}
    missing = sorted(expected - set(gallery))
    if missing:
        raise RuntimeError(f"La galería operativa no contiene todas las cinco identidades previstas: faltan {missing}")
    profiles = registry.get_profiles(expected)
    inactive = sorted(person_id for person_id, profile in profiles.items()
                      if profile.get("person_status") != "active")
    if inactive:
        raise RuntimeError(f"Identidades previstas inactivas en la SQLite institucional: {inactive}")
    with registry.connect(readonly=True) as db:
        rank_codes = {row[0] for row in db.execute("SELECT rank_code FROM ranks WHERE active=1")}
    if not set(REGISTRY_RANKS).issubset(rank_codes):
        raise RuntimeError(f"Catálogo institucional incompleto: faltan rangos {sorted(set(REGISTRY_RANKS)-rank_codes)}")
    return {
        "integrity_check": integrity["integrity_check"],
        "foreign_key_errors": 0,
        "active_gallery_id_count": len(gallery),
        "planned_identity_ids_present": sorted(expected),
        "active_rank_codes": sorted(rank_codes),
        "encoder_key": encoder,
        "database_mode": oct(settings.identity_db.stat().st_mode & 0o777),
        "readonly": True,
    }


def reject_duplicate_model_processes(startup_apps: list[dict], own_pid: int) -> None:
    external = [app for app in startup_apps if app["pid"] != own_pid and app["used_mib"] > 512]
    if external:
        raise RuntimeError(f"GPU ya está ocupada por procesos de inferencia no gestionados; no se duplican modelos: {external}")
    pgrep = shutil.which("pgrep")
    if pgrep:
        result = subprocess.run([pgrep, "-x", "llama-server"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            raise RuntimeError("Ya hay un llama-server activo; HRIEGO no iniciará otra instancia Qwen")


def verify_scenario_and_audio(settings: Settings) -> None:
    cfg = settings.scenario(settings.default_scenario_id)
    if cfg.get("configured_in_user_request") is not True:
        raise RuntimeError("El escenario debe estar expresamente autorizado/configurado")
    if not (shutil.which("pw-play") or shutil.which("aplay")):
        raise RuntimeError("No hay un reproductor WAV (pw-play o aplay)")


def run(*, check_only: bool = False, headless: bool = False, max_seconds: float = 0.0) -> int:
    settings = Settings()
    _configure_logging(settings)
    settings.validate_paths()
    # No loads happen before the camera, RAM, swap and VRAM preflight.
    hardware = __import__("hriego.resources", fromlist=["preflight_environment"]).preflight_environment(settings)
    reject_duplicate_model_processes(hardware["resources"].get("gpu_apps", []), os.getpid())
    budget_guard = MemoryGuard(settings, allowed_gpu_pids={os.getpid()})
    budget_preflight = {
        "qwen_sequential": budget_guard.preflight("qwen").as_dict(),
        "indextts_sequential": budget_guard.preflight("indextts").as_dict(),
    }
    joint_peak = int(settings.runtime["qwen_peak_gpu_bytes"]) + int(settings.runtime["indextts_peak_gpu_bytes"])
    joint_reserve = int(settings.runtime["gpu_reserve_bytes"])
    simultaneous_gpu_viable = bool(
        hardware["resources"].get("gpu_free_bytes", 0) >= joint_peak + joint_reserve
    )
    if simultaneous_gpu_viable:
        log.info("Ambos modelos podrían caber según los picos registrados; HRIEGO conserva la carga secuencial.")
    else:
        log.info("Carga conjunta rechazada por presupuesto: Qwen+IndexTTS+p reserva excede la VRAM libre; se usarán workers secuenciales.")
    verify_scenario_and_audio(settings)
    registry_report = validate_registry(settings)
    contract = ContractValidator(settings.contract_schema_path, settings.contract_validator_path)
    model_envs = validate_model_environments(settings)
    log.info("HRIEGO preflight OK: hardware=%s registry=%s models=%s", hardware, registry_report, model_envs)
    if check_only:
        print(json.dumps({"preflight": "ok", "hardware": hardware, "model_budgets": budget_preflight,
                          "simultaneous_gpu_viable": simultaneous_gpu_viable,
                          "registry": registry_report, "models": model_envs,
                          "contract": "rcp01c.intervention.v1"}, ensure_ascii=False, indent=2))
        return 0

    settings.conversation_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings.experiment_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    memory = ConversationMemory(settings.conversation_db)
    journal = ExperimentJournal(settings.experiment_db)
    rag = None
    if settings.rag_enabled:
        # Reuse the original RagIndex/Embedder without auto-ingest or downloads.
        try:
            from llm.api.app.rag import Embedder, RagIndex
            embedder = Embedder("minishlab/potion-multilingual-128M", str(settings.root / "llm/data/fastembed"))
            rag = RagIndex(memory.store, embedder, enabled=True, top_k=int(settings.runtime["rag_top_k"]))
            log.info("RAG habilitado como componente opcional; estado=%s", embedder.state())
        except Exception as exc:
            log.warning("RAG solicitado pero no disponible sin instalación/descarga: %s", type(exc).__name__)
    event_bus = EventBus()
    coordinator = HriCoordinator(settings, contract, memory, journal, event_bus, rag=rag)
    api = HriegoHTTPServer(coordinator, event_bus, settings.api_host, settings.api_port)
    perception = None
    dashboard = None
    resource_stop = __import__("threading").Event()
    resource_thread = None
    started = time.monotonic()
    normal_exit = False
    try:
        perception = FacePerception(settings, coordinator.update_perception, event_bus.publish)
        perception.start()
        api.start()
        log.info("POST http://%s:%d/hriego · GET /state · GET /events", settings.api_host, settings.api_port)
        if not headless:
            dashboard = HriegoDashboard(settings)
            dashboard.create()
        resource_thread = __import__("threading").Thread(
            target=_resource_loop, args=(coordinator, resource_stop), name="hriego-resources", daemon=True
        )
        resource_thread.start()
        print(f"HRIEGO listo · Qwen {settings.runtime['qwen_model_id']} · IndexTTS-2.5 · "
              f"camara {settings.camera_index} · API http://{settings.api_host}:{settings.api_port}/hriego", flush=True)
        while True:
            if max_seconds and time.monotonic() - started >= max_seconds:
                normal_exit = True
                break
            current = perception.snapshot()
            if not current.camera_connected and current.camera_error:
                # Keep the UI visible while the receiver attempts to recover.
                pass
            if dashboard:
                status = coordinator.status()
                key = dashboard.show(dashboard.render(current, status))
                if key in (ord("q"), 27):
                    normal_exit = True
                    break
                if key == ord("c"):
                    coordinator.cancel("keyboard")
                if not dashboard.visible():
                    normal_exit = True
                    break
            else:
                time.sleep(0.03)
    except KeyboardInterrupt:
        normal_exit = True
    finally:
        api.close()
        coordinator.close()
        if perception is not None:
            perception.stop()
        resource_stop.set()
        if resource_thread and resource_thread.is_alive():
            resource_thread.join(timeout=2.0)
        if dashboard:
            dashboard.close()
        memory.close()
        journal.close()
    final_snapshot = snapshot().as_dict()
    log.info("HRIEGO detenido; normal_exit=%s resources=%s", normal_exit, final_snapshot)
    return 0


def _resource_loop(coordinator: HriCoordinator, stop_event) -> None:
    while not stop_event.wait(1.0):
        try:
            coordinator.update_component(resources=snapshot().as_dict())
        except Exception:
            log.exception("No se pudo tomar la métrica de recursos")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recepcionista institucional HRIEGO")
    parser.add_argument("--check-only", action="store_true", help="Valida recursos y contratos sin cargar UniFace/Qwen/IndexTTS")
    parser.add_argument("--headless", action="store_true", help="Ejecuta sin ventana OpenCV")
    parser.add_argument("--max-seconds", type=float, default=0.0, help="Límite de duración para diagnósticos")
    args = parser.parse_args()
    return run(check_only=args.check_only, headless=args.headless, max_seconds=args.max_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
