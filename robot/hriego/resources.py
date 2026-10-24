from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


MIB = 1024 * 1024
GIB = 1024 * MIB


@dataclass
class ResourceSnapshot:
    ram_total_bytes: int | None
    ram_available_bytes: int | None
    swap_total_bytes: int | None
    swap_free_bytes: int | None
    gpu_name: str | None
    gpu_total_bytes: int | None
    gpu_used_bytes: int | None
    gpu_free_bytes: int | None
    gpu_utilization_percent: int | None
    gpu_apps: list[dict[str, Any]]
    sampled_at_unix: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            fields = value.strip().split()
            if fields and fields[0].isdigit():
                result[key] = int(fields[0]) * 1024
    except OSError:
        pass
    return result


def _nvidia_rows(query: str) -> list[list[str]]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [[item.strip() for item in row.split(",")] for row in completed.stdout.splitlines() if row.strip()]


def gpu_apps() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
            check=True, capture_output=True, text=True, timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for line in completed.stdout.splitlines():
        values = [part.strip() for part in line.split(",", 2)]
        if len(values) == 3:
            try:
                rows.append({"pid": int(values[0]), "process_name": values[1], "used_mib": int(values[2])})
            except ValueError:
                continue
    return rows


def snapshot() -> ResourceSnapshot:
    memory = _meminfo()
    rows = _nvidia_rows("name,memory.total,memory.used,memory.free,utilization.gpu")
    if rows and len(rows[0]) >= 5:
        name, total, used, free, util = rows[0][:5]
        try:
            gpu = (name, int(total) * MIB, int(used) * MIB, int(free) * MIB, int(util))
        except ValueError:
            gpu = (None, None, None, None, None)
    else:
        gpu = (None, None, None, None, None)
    return ResourceSnapshot(
        ram_total_bytes=memory.get("MemTotal"),
        ram_available_bytes=memory.get("MemAvailable"),
        swap_total_bytes=memory.get("SwapTotal"),
        swap_free_bytes=memory.get("SwapFree"),
        gpu_name=gpu[0], gpu_total_bytes=gpu[1], gpu_used_bytes=gpu[2],
        gpu_free_bytes=gpu[3], gpu_utilization_percent=gpu[4],
        gpu_apps=gpu_apps(), sampled_at_unix=time.time(),
    )


class MemoryGuard:
    """Model-specific preflight and a live emergency monitor for child workers."""

    def __init__(self, settings, *, allowed_gpu_pids: set[int] | None = None) -> None:
        self.settings = settings
        self.allowed_gpu_pids = set(allowed_gpu_pids or ())
        self.swap_baseline_bytes: int | None = None

    def preflight(self, model: str) -> ResourceSnapshot:
        snap = snapshot()
        cfg = self.settings.runtime
        expected_gpu = int(cfg[f"{model}_peak_gpu_bytes"])
        expected_ram = int(cfg[f"{model}_estimated_host_bytes"])
        gpu_reserve = int(cfg["gpu_reserve_bytes"])
        ram_reserve = int(cfg["ram_reserve_bytes"])
        if snap.gpu_total_bytes is None or snap.gpu_free_bytes is None:
            raise RuntimeError("No se puede comprobar la VRAM con nvidia-smi; no cargo modelos")
        if snap.ram_available_bytes is None:
            raise RuntimeError("No se puede comprobar MemAvailable; no cargo modelos")
        if snap.gpu_free_bytes < expected_gpu + gpu_reserve:
            raise MemoryError(
                f"Rechazo preventivo de {model}: VRAM libre={snap.gpu_free_bytes / GIB:.2f} GiB, "
                f"pico medido={expected_gpu / GIB:.2f} GiB, reserva={gpu_reserve / GIB:.2f} GiB"
            )
        if snap.ram_available_bytes < expected_ram + ram_reserve:
            raise MemoryError(
                f"Rechazo preventivo de {model}: RAM disponible={snap.ram_available_bytes / GIB:.2f} GiB, "
                f"estimación de carga={expected_ram / GIB:.2f} GiB, reserva={ram_reserve / GIB:.2f} GiB"
            )
        self.swap_baseline_bytes = snap.swap_free_bytes
        unknown_apps = [
            app for app in snap.gpu_apps
            if app["pid"] not in self.allowed_gpu_pids and app["used_mib"] > 512
        ]
        if unknown_apps:
            raise RuntimeError(f"GPU ocupada por procesos no gestionados; rechazo duplicar modelos: {unknown_apps}")
        return snap

    def swap_abort_floor(self) -> int:
        cfg = self.settings.runtime
        floor = int(cfg.get("abort_swap_free_bytes", 512 * MIB))
        if self.swap_baseline_bytes is not None:
            max_new_use = int(cfg.get("max_new_swap_use_bytes", 1024 * MIB))
            floor = max(floor, self.swap_baseline_bytes - max_new_use)
        return floor

    def swap_pressure(self, snap: ResourceSnapshot) -> bool:
        """Treat swap depletion as pressure only when physical RAM is also low."""
        if snap.swap_free_bytes is None or snap.swap_free_bytes >= self.swap_abort_floor():
            return False
        ram_floor = int(self.settings.runtime.get("swap_abort_ram_available_bytes", 6 * GIB))
        return snap.ram_available_bytes is None or snap.ram_available_bytes < ram_floor

    def monitor(self, process, stop_event, cancel_event=None) -> None:
        cfg = self.settings.runtime
        ram_floor = int(cfg["abort_ram_available_bytes"])
        gpu_floor = int(cfg["abort_gpu_free_bytes"])
        while not stop_event.wait(0.5):
            if process is None or process.poll() is not None:
                return
            snap = snapshot()
            reason = None
            if snap.ram_available_bytes is not None and snap.ram_available_bytes < ram_floor:
                reason = "MemAvailable descendió por debajo del límite de aborto"
            elif snap.gpu_free_bytes is not None and snap.gpu_free_bytes < gpu_floor:
                reason = "VRAM libre descendió por debajo del límite de aborto"
            elif self.swap_pressure(snap):
                reason = "Swap libre bajo junto con presión de RAM"
            elif cancel_event is not None and cancel_event.is_set():
                reason = "ejecución cancelada"
            if reason:
                try:
                    process.terminate()
                    process.wait(timeout=3.0)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass
                setattr(process, "_hriego_abort_reason", reason)
                return


def preflight_environment(settings) -> dict[str, Any]:
    settings.validate_paths()
    if not os.access(settings.identity_db, os.R_OK):
        raise PermissionError(f"No se puede leer la SQLite institucional: {settings.identity_db}")
    # A one-frame probe verifies the selected device without saving it.
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV no está disponible en el entorno Face") from exc
    camera = cv2.VideoCapture(settings.camera_index, cv2.CAP_V4L2)
    try:
        if not camera.isOpened():
            raise RuntimeError(f"No se pudo abrir /dev/video para la cámara {settings.camera_index}")
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        ok, frame = camera.read()
        if not ok or frame is None or not frame.size:
            raise RuntimeError("La cámara abrió pero no entregó un cuadro de prueba")
        camera_info = {"index": settings.camera_index, "width": int(frame.shape[1]), "height": int(frame.shape[0])}
    finally:
        camera.release()
    snap = snapshot()
    if snap.gpu_name is None or snap.gpu_free_bytes is None:
        raise RuntimeError("No se encontró GPU NVIDIA accesible; se cancela antes de cargar los modelos")
    if snap.ram_available_bytes is None or snap.swap_free_bytes is None:
        raise RuntimeError("No se pudo verificar RAM/swap; se cancela antes de cargar los modelos")
    return {"camera": camera_info, "resources": snap.as_dict()}
