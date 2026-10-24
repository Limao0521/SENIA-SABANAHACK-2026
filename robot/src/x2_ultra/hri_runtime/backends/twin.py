"""TwinBackend: el mismo AimdkBackend contra el gemelo digital oficial (contenedor ``x2-twin``).

Acceso según ``HRIEGO_gesture/twin/SETUP.md``: el gemelo corre sin red (``network=none``); los
programas se lanzan DENTRO del contenedor (``podman exec``, ``X2_REAL=gemelo``) con los overlays
``/tmp/aimdk_overlay`` (McBodyPose) y ``/tmp/aimdk_mc_overlay`` (presets). Diferencias con el
robot real (MC v1.0.0 del gemelo frente a v1.1.4 del robot):

- presets: el MC v1.0.0 decodifica ``SetMcPresetMotion`` SIN ``input_source`` (el tipo público lo
  inserta tras la cabecera y el MC lee ``area=len(name)+1, motion=0`` → reproduce otra cosa). Se
  usa el helper ``mc_preset_helper.py`` en el dominio privado 77 (``preset_layout="mc_v1.0.0"``);
- ``McBodyPose`` por el dominio 77 del MC (como c12: ``body_pose_domain=77``);
- el gemelo no publica batería: ``ignore_battery=True``;
- reglas de memoria del spec: NO arrancar el gemelo si ``free -m`` no muestra ≥ 8 GB disponibles
  (``twin_memory_ok``); al terminar, dejarlo en STAND_DEFAULT/STABLE y pararlo (``twin.sh stand``).
"""
from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading

from x2_ultra.hri_runtime.backends.aimdk import AimdkBackend, AimdkConfig

TWIN_CONTAINER = "x2-twin"
MIN_FREE_GB = 8.0
HELPER_ENV = ("source /opt/ros/humble/setup.bash; source /workspace/aimdk/install/local_setup.bash; "
              "source /tmp/aimdk_mc_overlay/install/local_setup.bash; "
              "export RMW_IMPLEMENTATION=rmw_fastrtps_cpp; exec python3 -u /tmp/mc_preset_helper.py")


def twin_memory_ok(min_free_gb: float = MIN_FREE_GB) -> bool:
    """True si ``MemAvailable`` ≥ ``min_free_gb`` (regla del spec: no arrancar el gemelo con menos)."""
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1e6 >= min_free_gb
    except OSError:
        pass
    return False


def twin_running() -> bool:
    podman = shutil.which("podman") or shutil.which("docker")
    if podman is None:
        return False
    try:
        out = subprocess.run([podman, "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
        return TWIN_CONTAINER in out.stdout.split()
    except (subprocess.SubprocessError, OSError):
        return False


class _HelperPreset:
    """Presets vía ``mc_preset_helper.py`` (subproceso, dominio 77, layout sin ``input_source``).

    Protocolo stdin: ``set MOTION AREA INTERRUPT`` / ``get TASK`` / ``quit``; JSON por stdout.
    """

    def __init__(self, log=None):
        self.p = subprocess.Popen(["bash", "-c", HELPER_ENV], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        self.replies: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        first = self.replies.get(timeout=30)
        if not first.get("ready"):
            raise RuntimeError("helper: servicios no encontrados en el dominio 77")
        self.log = log or (lambda **kw: None)

    def _read(self) -> None:
        try:
            for line in self.p.stdout:
                self.replies.put(json.loads(line))
        except (ValueError, OSError):
            pass

    def preset_set(self, motion: int, area: int, layout: str) -> None:
        self.p.stdin.write(f"set {int(motion)} {int(area)} 0\n")
        self.p.stdin.flush()

    def close(self) -> None:
        try:
            self.p.stdin.write("quit\n")
            self.p.stdin.flush()
            self.p.wait(5)
        except Exception:
            self.p.kill()


def twin_config(**kw) -> AimdkConfig:
    """Configuración por defecto del gemelo (MC v1.0.0, dominio 77, sin batería)."""
    kw.setdefault("preset_layout", "mc_v1.0.0")
    kw.setdefault("body_pose_domain", 77)
    kw.setdefault("ignore_battery", True)
    return AimdkConfig(**kw)


class TwinBackend(AimdkBackend):
    """AimdkBackend con los defaults del gemelo; en dry-run no toca el contenedor.

    Armado real (``arm=True``): sólo DENTRO del contenedor (``podman exec x2-twin ...`` con
    ``X2_REAL=gemelo`` — ajustar la comprobación de entorno del padre); los presets usan el helper
    (dos ``aimdk_msgs`` no pueden cargarse a la vez en un proceso).
    """

    def __init__(self, config: AimdkConfig | None = None, **kw):
        super().__init__(config or twin_config(), **kw)
        self._helper = None

    def _preset_request(self, track) -> None:   # override: helper del gemelo (layout mc_v1.0.0)
        if self.dry_run:
            self._link.preset_set(track.motion_id, track.area, "mc_v1.0.0")
            self.log("preset_helper_cmd", op="set", motion=track.motion_id, area=track.area, domain=77)
            return
        if self._helper is None:
            self._helper = _HelperPreset(log=self.log)
        self._helper.preset_set(track.motion_id, track.area, "mc_v1.0.0")
        self.log("preset_helper_cmd", op="set", motion=track.motion_id, area=track.area, domain=77)

    def close(self) -> None:
        super().close()
        if self._helper is not None:
            self._helper.close()
            self._helper = None
