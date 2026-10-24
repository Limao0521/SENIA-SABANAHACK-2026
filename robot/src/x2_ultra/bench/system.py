"""Muestreo de recursos del sistema durante un escenario del benchmark.

Un hilo que cada 100 ms anota: CPU y RAM del proceso, CPU y RAM del sistema y,
si hay NVIDIA, utilización/potencia/temperatura de la GPU vía nvidia-smi (cada
1 s, que es lo que la herramienta refresca). La potencia de la GPU es MEDIDA;
la del CPU no es accesible en Windows sin RAPL, así que se estima en bench.power
a partir del tiempo de CPU que este muestreador contabiliza.

Incluye utilidades de plataforma que el lazo de 50 Hz necesita en Windows:
- ``timer_resolution(1)``: sube la resolución del reloj a 1 ms (timeBeginPeriod);
  sin ello Windows cuantiza time.sleep a ~15,6 ms y el jitter medido sería falso.
- ``limit_cores(n)``: fija la afinidad del proceso a n núcleos lógicos, para
  emular los 8 núcleos del Orin NX del robot real.
"""
from __future__ import annotations

import ctypes
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field

import psutil

_PROC = psutil.Process()
_N_LOGICAL = psutil.cpu_count(logical=True) or 1


@contextmanager
def timer_resolution(ms: int = 1):
    """En Windows, pide resolución de reloj de ``ms``; en otras plataformas no hace nada."""
    winmm = None
    if hasattr(ctypes, "windll"):
        try:
            winmm = ctypes.windll.winmm
            winmm.timeBeginPeriod(ms)
        except OSError:
            winmm = None
    try:
        yield
    finally:
        if winmm is not None:
            try:
                winmm.timeEndPeriod(ms)
            except OSError:
                pass


def limit_cores(n: int | None) -> int | None:
    """Fija la afinidad del proceso a ``n`` núcleos lógicos; devuelve cuántos se usaron."""
    if n is None:
        return None
    try:
        avail = _PROC.cpu_affinity()
    except (AttributeError, OSError):
        return None
    chosen = avail[: max(1, min(n, len(avail)))]
    try:
        _PROC.cpu_affinity(chosen)
    except OSError:
        return None
    return len(chosen)


def _nvidia_smi() -> str | None:
    exe = shutil.which("nvidia-smi")
    return exe


@dataclass
class Sample:
    t: float                       # s desde el inicio del muestreo
    proc_cpu: float                # % de un núcleo (0..100*n)
    proc_rss_mb: float
    sys_cpu: float                 # % total (0..100)
    sys_avail_mb: float
    gpu_util: float | None = None  # %
    gpu_power_w: float | None = None
    gpu_temp_c: float | None = None


@dataclass
class SystemSampler:
    """Hilo de muestreo; usar como contexto alrededor de la carga a medir."""

    interval: float = 0.1
    gpu: bool = True
    samples: list[Sample] = field(default_factory=list, repr=False)
    cpu_seconds: float = 0.0       # tiempo de CPU del proceso (user+system) durante la ventana
    wall_seconds: float = 0.0      # duración de la ventana de muestreo
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _cpu0: tuple[float, float] | None = field(default=None, repr=False)
    _smi: str | None = field(default=None, repr=False)

    def __enter__(self) -> "SystemSampler":
        self.samples.clear()
        self._stop.clear()
        u, s = _PROC.cpu_times()[:2]
        self._cpu0 = (u + s, time.perf_counter())
        psutil.cpu_percent(interval=None)          # primer descarte
        self._smi = _nvidia_smi() if self.gpu else None
        self._thread = threading.Thread(target=self._run, name="bench-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        u, s = _PROC.cpu_times()[:2]
        cpu0, t0 = self._cpu0 if self._cpu0 is not None else (u + s, time.perf_counter())
        self.cpu_seconds = (u + s) - cpu0
        self.wall_seconds = time.perf_counter() - t0

    def _gpu_snapshot(self) -> tuple[float | None, float | None, float | None]:
        if self._smi is None:
            return None, None, None
        try:
            out = subprocess.run(
                [self._smi, "--query-gpu=utilization.gpu,power.draw,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3)
            util, power, temp = out.stdout.strip().splitlines()[0].split(",")
            return float(util), float(power), float(temp)
        except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
            self._smi = None                       # nvidia-smi falló: no reintentar
            return None, None, None

    def _run(self) -> None:
        t0 = time.perf_counter()
        next_gpu = 0.0
        gpu_last = (None, None, None)
        while not self._stop.is_set():
            proc_cpu = _PROC.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            now = time.perf_counter() - t0
            if now >= next_gpu:
                gpu_last = self._gpu_snapshot()
                next_gpu = now + 1.0
            self.samples.append(Sample(
                t=now, proc_cpu=proc_cpu,
                proc_rss_mb=_PROC.memory_info().rss / 1e6,
                sys_cpu=psutil.cpu_percent(interval=None),
                sys_avail_mb=mem.available / 1e6,
                gpu_util=gpu_last[0], gpu_power_w=gpu_last[1], gpu_temp_c=gpu_last[2]))
            self._stop.wait(self.interval)

    # ------------------------------------------------------------------ resúmenes
    def summary(self) -> dict:
        if not self.samples:
            return {}
        proc_cpu = [s.proc_cpu for s in self.samples]
        rss = [s.proc_rss_mb for s in self.samples]
        sys_cpu = [s.sys_cpu for s in self.samples]
        gpu_p = [s.gpu_power_w for s in self.samples if s.gpu_power_w is not None]
        gpu_u = [s.gpu_util for s in self.samples if s.gpu_util is not None]
        gpu_t = [s.gpu_temp_c for s in self.samples if s.gpu_temp_c is not None]
        proc_cpu_mean = _mean(proc_cpu) or 0.0
        out = {
            "proc_cpu_pct_mean": proc_cpu_mean,
            "proc_cpu_pct_peak": max(proc_cpu),
            "proc_cpu_cores_mean": round(proc_cpu_mean / 100.0, 2),
            "proc_rss_mb_mean": _mean(rss),
            "proc_rss_mb_peak": max(rss),
            "sys_cpu_pct_mean": _mean(sys_cpu),
            "sys_cpu_pct_peak": max(sys_cpu),
            "cpu_seconds": round(self.cpu_seconds, 3),
            "wall_seconds": round(self.wall_seconds, 3),
        }
        if gpu_p:
            # Energía de GPU integrada (regla trapezoidal sobre las muestras a ~1 Hz): MEDIDA.
            pairs: list[tuple[float, float]] = [(s.t, float(s.gpu_power_w))
                                                for s in self.samples if s.gpu_power_w is not None]
            energy = sum((t1 - t0) * (p0 + p1) / 2 for (t0, p0), (t1, p1) in zip(pairs, pairs[1:]))
            out.update({
                "gpu_power_w_mean": _mean(gpu_p),
                "gpu_power_w_peak": max(gpu_p),
                "gpu_energy_j_measured": round(energy, 1),
                "gpu_util_pct_mean": _mean(gpu_u) if gpu_u else None,
                "gpu_temp_c_peak": max(gpu_t) if gpu_t else None,
            })
        return out


def _mean(xs) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def environment() -> dict:
    """Foto del equipo y del software, para el informe."""
    import platform
    import sys

    import mujoco
    import numpy
    import onnxruntime

    cpu = "desconocida"
    try:
        cpu = platform.processor() or cpu
    except OSError:
        pass
    if not cpu or cpu.startswith(("Intel64 Family", "AMD64 Family")):
        try:  # Windows: nombre real vía WMI/CIM sin depender de wmic (deprecado)
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor).Name"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0 and out.stdout.strip():
                cpu = out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    gpu_name = None
    smi = _nvidia_smi()
    if smi:
        try:
            out = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5)
            gpu_name = out.stdout.strip().splitlines()[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    try:
        import x2_ultra
        version = getattr(x2_ultra, "__version__", "0.1.0")
    except Exception:
        version = "?"
    return {
        "os": f"{platform.system()} {platform.release()} ({platform.version()})",
        "arch": platform.machine(),
        "python": sys.version.split()[0],
        "cpu": cpu,
        "logical_cores": _N_LOGICAL,
        "ram_gb": round(psutil.virtual_memory().total / 1e9, 1),
        "gpu": gpu_name,
        "affinity_cores": len(_PROC.cpu_affinity()) if hasattr(_PROC, "cpu_affinity") else None,
        "mujoco": mujoco.__version__,
        "numpy": numpy.__version__,
        "onnxruntime": onnxruntime.__version__,
        "x2_ultra": version,
        "timer_resolution_note": "Windows: timeBeginPeriod(1) activo durante el lazo en tiempo real",
    }
