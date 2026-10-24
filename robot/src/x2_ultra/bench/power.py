"""Modelo de consumo y puente de arquitectura PC (x86-64) -> Jetson Orin NX (ARM64).

CONSUMO EN ESTE PC
------------------
Windows no expone RAPL sin driver, así que la potencia del CPU no se mide: se
ESTIMA con un modelo lineal documentado (constantes por CLI --p-idle/--p-tdp):

    P(t) = P_idle + (P_tdp - P_idle) * nucleos_ocupados(t) / n_logicos
    E    = P_idle * wall + (P_tdp - P_idle) * min(cpu_busy, wall * n_logicos) / n_logicos

con ``cpu_busy`` = segundos de CPU del proceso (user+system, psutil). Por
defecto P_idle=12 W y P_tdp=45 W (TDP del Ryzen 7 5800H de este portátil).
La GPU sí se mide (nvidia-smi power.draw, integrado en bench.system).

EL ROBOT REAL (referencia, docs/01 y docs/09)
---------------------------------------------
- PC1 RK3588s: MC (locomoción) — el equivalente funcional de nuestra policy a 50 Hz.
- PC2 Jetson Orin NX 16 GB (8x A78AE @ ~2.0 GHz, GPU Ampere 1024 cores, 102.4 GB/s):
  código propio (Face, LLM Qwen3-4B, TTS/gestos HRIEGO). Modos de potencia 10-25 W.
- Batería 421 Wh, ~2 h caminando a 0.5 m/s -> ~210 W medios TOTALES del robot.
  Ojo: casi todo es tracción de los actuadores, no computación; el compute del
  Orin (<=25 W) es ~12% del total. Este benchmark mide el COMPUTE, y los logs de
  docs/metrics/robot_real_20261020 solo dan batería al 1% (cuantización demasiado
  gruesa para sesiones de minutos), así que la referencia válida es el ~210 W de ficha.

PUENTE DE ARQUITECTURA (estimaciones, NO medidas)
-------------------------------------------------
Factores de latencia x86 -> Orin NX por clase de carga, de datos públicos:
- Geekbench 5 single-core: Ryzen 7 5800H ~1450-1500 vs Orin NX (A78AE) ~700-780
  -> razón ~1.9-2.0. Se usa 2.0 para cargas enteras/ML de CPU.
- Overhead de Python/alloc: algo peor en el SoC embebido -> 2.2.
- Cargas limitadas por ancho de banda de memoria: el LPDDR5-6400 del Orin
  (102.4 GB/s) supera al DDR4-3200 del 5800H (~51.2 GB/s) -> factor 0.9 (mejor).
Estos factores producen ESTIMACIONES orientativas para comparar con el presupuesto
de 20 ms del lazo de 50 Hz; no sustituyen a medir en el Orin real.
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------- consumo


@dataclass
class PowerModel:
    p_idle_w: float = 12.0        # portátil completo en reposo (pantalla incluida, aprox.)
    p_tdp_w: float = 45.0         # TDP del paquete CPU (Ryzen 7 5800H: 45 W nominales)
    n_logical: int = 16

    def energy_j(self, wall_s: float, cpu_busy_s: float, gpu_energy_j: float = 0.0) -> dict:
        """Energía de un escenario: CPU estimada + GPU medida (si hay)."""
        busy_cores = min(cpu_busy_s / wall_s, float(self.n_logical)) if wall_s > 0 else 0.0
        avg_cpu_w = self.p_idle_w + (self.p_tdp_w - self.p_idle_w) * busy_cores / self.n_logical
        cpu_j = avg_cpu_w * wall_s
        total_j = cpu_j + gpu_energy_j
        return {
            "wall_s": round(wall_s, 2),
            "cpu_busy_s": round(cpu_busy_s, 2),
            "busy_cores_mean": round(busy_cores, 2),
            "cpu_avg_w_estimated": round(avg_cpu_w, 1),
            "cpu_energy_j_estimated": round(cpu_j, 1),
            "gpu_energy_j_measured": round(gpu_energy_j, 1),
            "total_energy_j": round(total_j, 1),
            "total_avg_w": round(total_j / wall_s, 1) if wall_s > 0 else None,
            "method": f"CPU lineal P_idle={self.p_idle_w}W P_tdp={self.p_tdp_w}W sobre cpu_busy; GPU nvidia-smi",
        }


# ------------------------------------------------------------------- puente Jetson

ORIN_NX_FACTORS = {
    "cpu_bound": 2.0,        # Geekbench5 SC: 5800H ~1480 vs Orin NX ~740
    "onnx_cpu": 2.0,         # inferencia MLP entera/float en CPU: misma razón SC
    "python": 2.2,           # overhead de intérprete/alloc algo peor en A78AE
    "memory_bound": 0.9,     # LPDDR5 102.4 GB/s vs DDR4-3200 ~51.2 GB/s (Orin mejor)
}

ORIN_NX_POWER_W = {"min": 10.0, "typ": 15.0, "maxn": 25.0}

# Ficha del X2 Ultra-N (docs/01): batería y autonomía declarada caminando.
X2_BATTERY_WH = 421.0
X2_WALK_HOURS = 2.0
X2_WALK_AVG_W = X2_BATTERY_WH / X2_WALK_HOURS   # ~210 W


def scale_latency(ms: float, kind: str = "cpu_bound", factors: dict | None = None) -> float:
    """ESTIMACIÓN de lo que tardaría ``ms`` en el Jetson Orin NX (ver módulo docstring)."""
    f = (factors or ORIN_NX_FACTORS).get(kind, 2.0)
    return ms * f


def orin_budget_fraction(cycle_ms: float, budget_ms: float = 20.0, kind: str = "cpu_bound",
                         factors: dict | None = None) -> float:
    """Fracción del presupuesto de 20 ms (50 Hz) que ocuparía el ciclo en el Orin NX."""
    return scale_latency(cycle_ms, kind, factors) / budget_ms


def robot_reference() -> dict:
    """Referencia del robot real para contextualizar el informe."""
    return {
        "x2_battery_wh": X2_BATTERY_WH,
        "x2_walk_autonomy_h": X2_WALK_HOURS,
        "x2_walk_avg_power_w": round(X2_WALK_AVG_W, 0),
        "note_actuators": "El ~210 W del robot andando es casi todo tracción de actuadores; "
                          "el compute (PC1 RK3588s + PC2 Orin NX) es una fracción menor "
                          "(el Orin admite 10-25 W). Este benchmark mide solo el compute emulado.",
        "orin_nx_power_modes_w": ORIN_NX_POWER_W,
        "orin_nx_factors": ORIN_NX_FACTORS,
        "factor_source": "Geekbench 5 single-core publicos: Ryzen 7 5800H ~1450-1500, "
                         "Jetson Orin NX 16GB ~700-780 -> ~1.9-2.0x; ancho de banda LPDDR5 vs DDR4 -> 0.9x",
        "real_logs": "docs/metrics/robot_real_20261020: bateria al 1% en sesiones de 1-5 min, "
                     "demasiado grueso para estimar W; se usa la ficha (421 Wh / ~2 h).",
    }
