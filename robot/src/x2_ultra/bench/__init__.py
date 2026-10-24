"""Benchmark de consumo y rendimiento del pipeline del X2 Ultra emulado en PC.

Emula en este PC (x86-64, Windows/Linux) el procesamiento que el robot reparte entre
sus ordenadores (PC1 RK3588s: control de movimiento a 50 Hz; PC2 Jetson Orin NX: HRI)
y mide, por escenario de los dos retos del hackathon:

- latencia del ciclo de control (media y percentiles p50/p95/p99/max),
- factor de tiempo real (RTF) de la física MuJoCo,
- inferencia de las policies ONNX,
- CPU y RAM del proceso y del sistema, GPU (nvidia-smi si existe),
- energía estimada con un modelo lineal de potencia (ver bench.power),
- estimación de lo que tardaría el mismo trabajo en un Jetson Orin NX (ver bench.power).

Uso:  python -m x2_ultra.bench --list  /  x2-bench --quick
Metodología completa: docs/12-benchmark-consumo.md
"""
from x2_ultra.bench.stats import LatencyStats

__all__ = ["LatencyStats"]
