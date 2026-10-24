"""Estadísticas de latencia con percentiles para el benchmark.

Guarda las muestras en un array de numpy y calcula media, desviación y los
percentiles p50/p95/p99/max, que es lo que importa en un lazo de control en
tiempo real: el p99 dice cuántas veces se pasa del presupuesto de 20 ms.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class LatencyStats:
    """Acumula duraciones (en segundos) y las resume con percentiles."""

    name: str = ""
    budget: float | None = None          # presupuesto del ciclo [s]; si se pasa, es un overrun
    _samples: list[float] = field(default_factory=list, repr=False)

    def add(self, seconds: float) -> None:
        self._samples.append(seconds)

    def extend(self, seconds) -> None:
        self._samples.extend(seconds)

    def __len__(self) -> int:
        return len(self._samples)

    @property
    def samples(self) -> np.ndarray:
        return np.asarray(self._samples, dtype=float)

    def percentile(self, p: float) -> float:
        if not self._samples:
            return float("nan")
        return float(np.percentile(self.samples, p))

    def summary_ms(self) -> dict[str, float]:
        """Resumen en milisegundos, listo para el informe."""
        if not self._samples:
            return {}
        s = self.samples * 1e3
        out = {
            "n": float(len(s)),
            "mean": float(s.mean()),
            "std": float(s.std()),
            "min": float(s.min()),
            "p50": float(np.percentile(s, 50)),
            "p95": float(np.percentile(s, 95)),
            "p99": float(np.percentile(s, 99)),
            "max": float(s.max()),
        }
        if self.budget is not None:
            budget_ms = self.budget * 1e3
            over = int((s > budget_ms).sum())
            out["budget_ms"] = budget_ms
            out["overruns"] = float(over)
            out["overrun_pct"] = 100.0 * over / len(s)
        return out
