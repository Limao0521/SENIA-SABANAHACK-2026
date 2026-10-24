"""Postura del gemelo en memoria compartida para los procesos que solo dibujan (visor, cámaras, LiDAR).

La física escribe ``qpos`` y el tiempo ~100 veces por segundo; los lectores reconstruyen la cinemática en su propio
``MjData``. Así ni el visor ni el render de cámaras frenan el bucle de 1 kHz (con el visor en el mismo proceso la
física bajaba a 0,77× tiempo real). Sincronización por "seqlock": el contador es impar mientras se escribe.
"""
from __future__ import annotations

import numpy as np
from multiprocessing import shared_memory

_HEADER = 3            # [seq, tiempo de simulación, pórtico]
TWIN_SHM_NAME = "x2_twin_state"   # nombre fijo: la app de control (twin.sh gui) se engancha por él
_OWNED_NAMES: set[str] = set()     # evita quitar del tracker al creador si lee en el mismo proceso


class SharedState:
    def __init__(self, nq: int, name: str | None = None, create: bool | None = None):
        """Sin ``name``: memoria nueva con nombre aleatorio. Con ``name`` y ``create=True``: la crea con ese nombre
        (borrando una que hubiera quedado de otra ejecución). Con ``name`` solo: se engancha a una existente."""
        self.nq = nq
        size = (_HEADER + nq) * 8
        create = name is None if create is None else create
        if create:
            if name is not None:
                try:
                    old = shared_memory.SharedMemory(name=name)
                    old.close()
                    old.unlink()
                except FileNotFoundError:
                    pass
            self.shm = shared_memory.SharedMemory(name=name, create=True, size=size)
            self.owner = True
            _OWNED_NAMES.add(self.shm._name)
        else:
            self.shm = shared_memory.SharedMemory(name=name)
            self.owner = False
            # Python < 3.13 registra también al que solo se engancha y, al salir, borra el segmento del otro
            # proceso. Se le quita del registro: la borra solo quien la creó.
            if self.shm._name not in _OWNED_NAMES:
                try:
                    from multiprocessing import resource_tracker
                    resource_tracker.unregister(self.shm._name, "shared_memory")
                except Exception:
                    pass
        self.arr = np.ndarray((_HEADER + nq,), dtype=np.float64, buffer=self.shm.buf)
        if self.owner:
            self.arr[:] = 0.0

    @property
    def name(self) -> str:
        return self.shm.name

    def write(self, qpos: np.ndarray, t: float, gantry: bool) -> None:
        a = self.arr
        a[0] += 1.0                       # impar: escribiendo
        a[1], a[2] = t, float(gantry)
        a[_HEADER:] = qpos
        a[0] += 1.0                       # par: listo

    def read(self) -> tuple[np.ndarray, float, bool, int] | None:
        """(qpos, tiempo, pórtico, secuencia) o None si no se pudo leer una copia coherente."""
        a = self.arr
        for _ in range(100):
            s1 = a[0]
            if int(s1) % 2:
                continue
            q, t, g = a[_HEADER:].copy(), float(a[1]), bool(a[2])
            if a[0] == s1:
                return q, t, g, int(s1)
        return None

    def close(self) -> None:
        self.shm.close()
        if self.owner:
            try:
                self.shm.unlink()
            finally:
                _OWNED_NAMES.discard(self.shm._name)
