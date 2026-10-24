"""Exercise HRIEGO's existing camera/UniFace stack without starting its FSM."""
from __future__ import annotations

import json
import time

from hriego_v2.perception import FacePerception
from hriego_v2.resources import snapshot
from hriego_v2.settings import Settings


def main() -> None:
    settings = Settings()
    first = snapshot()
    if first.ram_available_bytes is None or first.ram_available_bytes < 5 * 1024**3:
        raise MemoryError("Se requieren 5 GiB disponibles para iniciar la prueba")
    if first.gpu_free_bytes is None or first.gpu_free_bytes < 1024**3:
        raise MemoryError("Se requiere 1 GiB libre de VRAM")
    perception = FacePerception(settings, lambda _obs: None, lambda *_args, **_kw: None)
    minimum_ram = first.ram_available_bytes
    maximum_gpu_used = first.gpu_used_bytes
    try:
        perception.start()
        print("CAMERA_STARTED", flush=True)
        end = time.monotonic() + 35
        while time.monotonic() < end:
            sample = snapshot()
            if sample.ram_available_bytes is not None:
                minimum_ram = min(minimum_ram, sample.ram_available_bytes)
                if sample.ram_available_bytes < 2 * 1024**3:
                    raise MemoryError("Guardia: menos de 2 GiB de RAM disponibles")
            if sample.gpu_free_bytes is not None and sample.gpu_free_bytes < 1024**3:
                raise MemoryError("Guardia: menos de 1 GiB de VRAM libre")
            if sample.gpu_used_bytes is not None:
                maximum_gpu_used = max(maximum_gpu_used or 0, sample.gpu_used_bytes)
            time.sleep(.25)
        state = perception.snapshot()
        report = {"camera_connected": state.camera_connected,
                  "camera_status": state.camera_status, "camera_error": state.camera_error,
                  "received_frames": state.received_frames,
                  "inferred_frames": state.inferred_frames,
                  "min_ram_available_gib": round(minimum_ram / 1024**3, 3),
                  "max_gpu_used_mib": round((maximum_gpu_used or 0) / 1024**2)}
        print("FACE_RESULT", json.dumps(report, ensure_ascii=False), flush=True)
        if not state.camera_connected or state.inferred_frames == 0:
            raise RuntimeError("La cámara o la inferencia no funcionó durante la prueba")
    finally:
        perception.stop()


if __name__ == "__main__":
    main()
