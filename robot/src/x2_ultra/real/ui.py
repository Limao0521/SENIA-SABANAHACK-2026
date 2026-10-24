"""Piezas comunes de las ventanas del robot real: teclas mantenidas, registro JSONL y parada por señal."""
from __future__ import annotations

import json
import signal
import threading
import time
from pathlib import Path

from x2_ultra.paths import REPO_ROOT

BG, FG, ARMED_BG = "#141a24", "#e8eef8", "#4a1418"
FONT = ("DejaVu Sans Mono", 11)


class HeldKeys:
    """Teclas pulsadas ahora mismo en una ventana Tk.

    X11 repite las teclas mantenidas con pares falsos soltar/pulsar: una suelta sólo cuenta si en 40 ms no llega
    otra pulsación. Al perder el foco se sueltan todas (la ventana deja de ver las teclas).
    """

    def __init__(self, root, names: set[str], on_change):
        self.root, self.names, self.on_change = root, names, on_change
        self.down: set[str] = set()
        self.shift = False
        self._pending: dict[str, float] = {}
        root.bind("<FocusOut>", lambda _e: self.clear())

    @staticmethod
    def name(event) -> str:
        return event.keysym.lower()

    def press(self, event) -> bool:
        """True si la tecla es de las mantenidas (ya procesada)."""
        k = self.name(event)
        if k in ("shift_l", "shift_r"):
            self.shift = True
        elif k in self.names:
            self._pending.pop(k, None)
            self.down.add(k)
        else:
            return False
        self.on_change()
        return True

    def release(self, event) -> None:
        k = self.name(event)
        if k in ("shift_l", "shift_r"):
            self.shift = False
            self.on_change()
        elif k in self.names:
            self._pending[k] = time.monotonic()
            self.root.after(40, lambda kk=k: self._confirm(kk))

    def _confirm(self, k: str) -> None:
        if self._pending.pop(k, None) is not None:
            self.down.discard(k)
            self.on_change()

    def clear(self) -> None:
        self.down.clear()
        self.shift = False
        self._pending.clear()
        self.on_change()


class SessionLog:
    """Una línea JSON por evento o muestra, en ``logs/real/<programa>-<fecha>.jsonl``."""

    def __init__(self, program: str):
        folder = REPO_ROOT / "logs" / "real"
        folder.mkdir(parents=True, exist_ok=True)
        self.path: Path = folder / f"{program}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
        self._file = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def write(self, kind: str, **data) -> None:
        line = json.dumps({"t": round(time.time(), 3), "tipo": kind, **data}, ensure_ascii=False)
        with self._lock:
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


def stop_on_signals() -> threading.Event:
    """Ctrl+C / SIGTERM / SIGHUP piden parar; el programa cierra la ventana y manda sus órdenes finales."""
    event = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: event.set())
    return event


def fmt(value: float | None, width: int = 6, digits: int = 3) -> str:
    return "—".rjust(width) if value is None else f"{value:+{width}.{digits}f}"
