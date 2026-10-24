"""Identity flag — simulated (default) vs real (T6/T2).

Flag: env ``HRI_IDENTITY_REAL`` (default ``"0"``) o el parámetro ``identity_real``
del app (``ClubHriApp``). Modos:

- **False (simulated)**: comportamiento actual — el dict de persona se pasa tal
  cual a la FSM (confirm_fn simulado); se registra ``identity_mode="simulated"``.
- **True (real)**: reconocimiento facial sobre una foto (reusa
  ``x2_ultra.show.hri.face_id.identify_photo`` — sonda en el venv de Face;
  UniFace SIN cuantizar, igual que face_probe; todo con imports/lazy load, no se
  carga nada pesado al importar este módulo) + decodeo del QR de un PNG vía
  ``confirm.confirm_identity(face_result, qr_image=<ruta png o array>, cedula=...)``.
  Si la confirmación falla (status != CONFIRMED, o cualquier excepción) se
  devuelve una persona **REJECTED** (rank None): la FSM NO saluda como rango
  conocido y NO hay fallback silencioso.

El modo resuelto (``identity_mode``) queda grabado en el turn record del app.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

ENV_FLAG = "HRI_IDENTITY_REAL"
MODE_SIMULATED = "simulated"
MODE_REAL = "real"
STATUS_REJECTED = "REJECTED"

_FALSY = ("", "0", "false", "no", "off")


def identity_real_from_env() -> bool:
    """Lee ``HRI_IDENTITY_REAL`` (default "0")."""
    return os.environ.get(ENV_FLAG, "0").strip().lower() not in _FALSY


def rejected_person(reason: str, detail: Any = None) -> dict:
    """Persona REJECTED: rank None ⇒ la FSM no puede saludar como rango conocido."""
    return {"person_id": None, "name": None, "rank": None, "worker_subtype": None,
            "work_order": None, "status": STATUS_REJECTED, "reason": reason,
            "detail": detail, "identity_mode": MODE_REAL}


def recognize_face(photo: Any, timeout: float = 180.0) -> dict:
    """Reconocimiento facial sobre una foto — lazy (el import y el modelo sólo se
    cargan aquí) y SIN cuantizar UniFace (la sonda de face_probe usa el
    FaceAnalyzer nativo fp32 del venv de Face)."""
    from x2_ultra.show.hri.face_id import identify_photo
    return identify_photo(photo, timeout=timeout)


def resolve_identity(*, identity_real: bool, person: Optional[dict] = None,
                     face_result: Any = None, photo: Any = None,
                     qr_image: Any = None, cedula: Any = None) -> dict:
    """Resuelve la persona del turno según el flag.

    - ``identity_real=False``: passthrough del ``person`` dict simulado
      (+ ``identity_mode="simulated"``).
    - ``identity_real=True``: cara (si llega ``photo`` y no un ``face_result``
      ya calculado) → ``confirm_identity`` (QR PNG / cédula). Fallo o excepción
      ⇒ ``rejected_person`` (nunca un rango conocido por fallback).
    """
    if not identity_real:
        out = dict(person or {})
        out["identity_mode"] = MODE_SIMULATED
        return out
    try:
        if face_result is None and photo is not None:
            face_result = recognize_face(photo)
        from x2_ultra.hri_runtime import confirm as _confirm
        res = _confirm.confirm_identity(face_result, qr_image=qr_image, cedula=cedula)
        if not isinstance(res, dict) or res.get("status") != _confirm.STATUS_CONFIRMED:
            res = res if isinstance(res, dict) else {}
            return rejected_person(res.get("reason") or "NOT_CONFIRMED",
                                   res.get("detail"))
        out = dict(res)
        out["identity_mode"] = MODE_REAL
        return out
    except Exception as exc:                      # sin fallback silencioso
        return rejected_person("IDENTITY_ERROR", f"{type(exc).__name__}: {exc}")
