"""Pausa el asistente de voz de fábrica del X2 mientras corre nuestro sistema HRI.

El asistente de AgiBot (``interaction_slave``, en el PC3) también escucha y habla: compite con
nuestra conversación por el micrófono y los altavoces. AimDK expone
``/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest`` con ``AGENT_PROPERTY_RUN_MODE``:

- ``normal``: asistente + reconocimiento de voz (modo de fábrica);
- ``only_voice``: asistente DESACTIVADO, el robot sigue entregando el audio del micrófono
  (lo que usamos: ``/aima/hal/audio/capture``) — verificado en el X2 el 2026-10-24: el
  micrófono siguió a ~34 mensajes/s tras el cambio;
- ``no_voice``: desactiva todo, también el audio (nos dejaría sin micrófono): nunca se usa.

``pause()`` al arrancar la sesión (antes de armar) y ``resume()`` al terminar (siempre, en el
``finally``). Un solo cliente ROS por sesión sobre el executor compartido de hriego (crear y
destruir nodos por llamada mató ese executor en el robot). El servicio responde ``code=0`` con
el estado sin rellenar: se registra lo que devuelve, sin darlo por fallo.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Callable, Optional

SERVICE = "/aimdk_5Fmsgs/srv/SetAgentPropertiesRequest"
NODE_NAME = "hri_club_asistente"
MODO_PAUSA = "only_voice"
MODO_FABRICA = "normal"


class Asistente:
    def __init__(self, *, enabled: bool = True, dry_run: bool = False,
                 log: Callable[[str], None] = print, node_factory: Optional[Callable[[str], Any]] = None,
                 caller: Optional[Callable[[str], tuple[bool, str]]] = None):
        self.enabled = bool(enabled)
        self.dry_run = bool(dry_run)
        self.log = log
        self.node_factory = node_factory
        self._caller = caller                 # tests: sustituye la llamada ROS
        self._node = self._client = None
        self.paused = False
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls, *, dry_run: bool = False, log: Callable[[str], None] = print) -> "Asistente":
        return cls(enabled=os.environ.get("HRI_PAUSAR_ASISTENTE", "1") != "0", dry_run=dry_run, log=log)

    def pause(self) -> bool:
        ok = self._set(MODO_PAUSA)
        self.paused = self.paused or ok
        return ok

    def resume(self) -> bool:
        if not self.paused:
            return True
        ok = self._set(MODO_FABRICA)
        if ok:
            self.paused = False
        return ok

    def _set(self, modo: str) -> bool:
        if not self.enabled:
            return False
        if self.dry_run:
            self.log(f"[asistente] (seco) modo {modo} no enviado")
            return True
        with self._lock:
            try:
                ok, msg = (self._caller or self._call)(modo)
            except Exception as exc:  # noqa: BLE001 - nunca tumba la sesión
                ok, msg = False, f"{type(exc).__name__}: {exc}"
        verbo = "pausado" if modo == MODO_PAUSA else "reactivado"
        self.log(f"[asistente] de fábrica {verbo} (RUN_MODE={modo})" if ok
                 else f"[asistente] AVISO: no se pudo poner RUN_MODE={modo}: {msg}")
        return ok

    def _call(self, modo: str) -> tuple[bool, str]:
        from aimdk_msgs.msg import AgentPropertiesValue, AgentPropertyIdType, CommonRequest
        from aimdk_msgs.srv import SetAgentPropertiesRequest
        if self._client is None:
            factory = self.node_factory
            if factory is None:
                from .live_robot import _ros_node as factory   # executor compartido + lista blanca
            self._node = factory(NODE_NAME)
            self._client = self._node.create_client(SetAgentPropertiesRequest, SERVICE)
        if not self._client.wait_for_service(timeout_sec=3.0):
            return False, f"servicio {SERVICE} no disponible"
        req = SetAgentPropertiesRequest.Request()
        req.header = CommonRequest()
        v = AgentPropertiesValue()
        v.key = AgentPropertyIdType(value=AgentPropertyIdType.AGENT_PROPERTY_RUN_MODE)
        v.value = modo
        req.contents.properties = [v]
        for _ in range(8):                    # el peer remoto (PC3) no siempre contesta a la 1ª
            req.header.header.stamp = self._node.get_clock().now().to_msg()
            fut = self._client.call_async(req)
            done = threading.Event()
            fut.add_done_callback(lambda _f: done.set())
            if done.wait(0.5):
                res = fut.result()
                if res is None:
                    return False, "sin respuesta"
                code = getattr(res.header.header, "code", 0)
                return code == 0, f"code={code} status={res.header.status.value} {res.header.message}"
        return False, "sin respuesta tras 8 intentos"
