"""Backends del runtime HRI (T5): Protocolo común + Show, Twin y Aimdk.

Importar este paquete NO importa rclpy ni MuJoCo: las implementaciones se resuelven bajo demanda
(PEP 562) — ``show`` tira de mujoco (ya lo usa el show del reto 2) y los enlaces reales de
``aimdk``/``twin`` sólo se construyen con ``arm=True``.
"""
from __future__ import annotations

from x2_ultra.hri_runtime.backends.base import Backend  # noqa: F401

_LAZY = {
    "ShowBackend": "show",
    "AimdkBackend": "aimdk",
    "AimdkConfig": "aimdk",
    "DryLink": "aimdk",
    "TwinBackend": "twin",
    "twin_config": "twin",
    "twin_memory_ok": "twin",
    "twin_running": "twin",
}


def __getattr__(name: str):
    if name in _LAZY:
        import importlib
        mod = importlib.import_module(f"x2_ultra.hri_runtime.backends.{_LAZY[name]}")
        return getattr(mod, name)
    raise AttributeError(f"módulo {__name__!r} no tiene atributo {name!r}")


__all__ = ["Backend", "ShowBackend", "TwinBackend", "AimdkBackend", "AimdkConfig", "DryLink",
           "twin_config", "twin_memory_ok", "twin_running"]
