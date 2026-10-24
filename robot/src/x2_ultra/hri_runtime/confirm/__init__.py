"""T2 — CONFIRMACION: QR vinculado a la cara + cédula simulada (WORKER).

Contrato (spec HRI_RUNTIME_SPEC.md)::

    confirm_identity(face_result, qr_image=None, cedula=None) -> dict
    # {"person_id","name","rank","worker_subtype","work_order":{"area","task"},"status"}

Flujo: cara (face_id.py + identities.json como respaldo) → QR (zxing-cpp o
OpenCV; formatos QR01/QC1/IDV1/IDV2; la credencial debe estar vinculada a la
persona CONFIRMADA por cara, si no QR_OTHER_PERSON) → sin QR: cédula simulada
(consenso temporal ≥3 lecturas) ⇒ WORKER + orden de trabajo simulada. Fallo ⇒
status "PASE_RECEPCION_HUMANA". Llaves fuera del repo (rutas por env).
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from .cedula_sim import (CEDULA_WORKERS, CedulaConfig, CedulaOcrSim, SUBTYPE_AREAS,
                         WORK_ORDERS, consensus_over, work_order_for)
from .qr_decode import decode_qr_texts
from .qr_verify import (ACCEPTED_STATUS, DEFAULT_QR_ROOT, QRVerifier, b64url_encode,
                        qc1_pack)

# Categoría de la credencial → rango del club (spec: EXTERNAL ⇒ WORKER).
RANK_FROM_QR = {"VIP": "VIP", "REGULAR": "REGULAR", "INVITED_GUEST": "GUEST",
                "STAFF": "STAFF", "EXTERNAL": "WORKER"}
# rank_code de identities.json → rango del club.
RANK_FROM_FACE = {"VIP": "VIP", "REGULAR": "REGULAR", "GUEST": "GUEST",
                  "STAFF": "STAFF", "EXTERNAL": "WORKER"}
STATUS_CONFIRMED = "CONFIRMED"
STATUS_HUMAN = "PASE_RECEPCION_HUMANA"


@dataclass
class ConfirmConfig:
    public_key: str | None = None
    manifest: str | None = None
    bindings: str | None = None
    cedula: CedulaConfig = field(default_factory=CedulaConfig)
    _verifier: QRVerifier | None = None

    @classmethod
    def from_env(cls) -> "ConfirmConfig":
        root = Path(os.environ.get("QR01_ROOT", str(DEFAULT_QR_ROOT)))
        bindings = os.environ.get(
            "HRI_QR_BINDINGS", str(Path(__file__).parent / "qr_bindings.default.json"))
        return cls(
            public_key=os.environ.get("HRI_QR_PUBLIC_KEY", str(root / "config" / "qr_public_key.pem")),
            manifest=os.environ.get("HRI_QR_MANIFEST", str(root / "config" / "demo_credentials.json")),
            bindings=bindings,
            cedula=CedulaConfig(
                noise=float(os.environ.get("HRI_CEDULA_NOISE", 0.15)),
                confidence=float(os.environ.get("HRI_CEDULA_CONFIDENCE", 0.90)),
                min_confidence=float(os.environ.get("HRI_CEDULA_MIN_CONF", 0.60)),
                consensus_n=int(os.environ.get("HRI_CEDULA_CONSENSUS_N", 3)),
                max_reads=int(os.environ.get("HRI_CEDULA_MAX_READS", 12))))

    def verifier(self) -> QRVerifier:
        if self._verifier is None:
            self._verifier = QRVerifier.from_paths(self.public_key, self.manifest, self.bindings)
        return self._verifier


def _result(person_id, name, rank, status, *, source=None, reason=None, detail=None,
            worker_subtype=None, work_order=None):
    return {"person_id": person_id, "name": name, "rank": rank,
            "worker_subtype": worker_subtype, "work_order": work_order,
            "status": status, "source": source, "reason": reason, "detail": detail}


def _identities():
    try:
        from x2_ultra.show.hri.data import load_identities
        return load_identities() or {}
    except Exception:
        return {}


def _person_by_id(person_id):
    for key, e in _identities().items():
        if isinstance(e, dict) and e.get("person_id") == person_id:
            return e
    return None


def resolve_face(face_result):
    """face_result (dict de la sonda/precomputado, o nombre) -> persona o None.

    Reusa ``x2_ultra.show.hri.face_id``: si llega un nombre (o un dict con
    ``person``), usa las identidades precomputadas de ``assets/hri/identities.json``.
    """
    if face_result is None:
        return None
    if isinstance(face_result, str):
        from x2_ultra.show.hri.face_id import precomputed_identity
        face_result = precomputed_identity(face_result)
    elif isinstance(face_result, dict) and face_result.get("person") and \
            face_result.get("status") not in ("KNOWN",):
        from x2_ultra.show.hri.face_id import precomputed_identity
        face_result = precomputed_identity(str(face_result["person"]))
    if not isinstance(face_result, dict):
        return None
    profile = face_result.get("profile") or face_result
    known = (face_result.get("status") == "KNOWN"
             or profile.get("identity_status") == "CONFIRMED")
    rank_code = profile.get("rank_code")
    if not known or rank_code not in RANK_FROM_FACE or not profile.get("person_id"):
        return None
    return {"person_id": profile.get("person_id"),
            "name": profile.get("display_name") or profile.get("name"),
            "rank_code": rank_code}


def _worker_result(person_id, name, subtype, seed_key, status=STATUS_CONFIRMED, source=None):
    return _result(person_id, name, "WORKER", status, source=source,
                   worker_subtype=subtype, work_order=work_order_for(subtype, seed_key))


def _process_qr(qr_image, person, cfg: ConfirmConfig):
    """-> (resultado final | None, dict de diagnóstico)."""
    err, texts = decode_qr_texts(qr_image)
    if err == "decoder_unavailable":
        return None, {"reason": "QR_DECODER_UNAVAILABLE"}
    if err or not texts:
        return None, {"reason": "QR_UNREADABLE", "detail": err}
    v = cfg.verifier()
    last = None
    for text in texts:
        val = v.validate(text)
        last = {"reason": f"QR_{val['verification_status']}",
                "detail": {"format": val["format"], "credential_id": val["credential_id"],
                           "verification_status": val["verification_status"]}}
        if val["verification_status"] not in ACCEPTED_STATUS:
            continue
        binding = v.binding_for(val, text)
        if binding is None:
            last = {"reason": "QR_UNBOUND", "detail": last["detail"]}
            continue
        holders = binding.get("holders") or [binding.get("person_id")]
        if person is None or person["person_id"] not in holders:
            # credencial de otra persona (o cara sin confirmar): nunca aceptar
            last = {"reason": "QR_OTHER_PERSON",
                    "detail": dict(last["detail"], bound_person=binding.get("person_id"))}
            continue
        cat = val["claimed_cat"] or binding.get("category")
        rank = RANK_FROM_QR.get(cat)
        if rank is None:
            last = {"reason": f"QR_{val['verification_status']}", "detail": last["detail"]}
            continue
        if rank == "WORKER":
            res = _worker_result(person["person_id"], person["name"], "logistica",
                                 person["person_id"], source="qr")
            res["detail"] = last["detail"]
            return res, {}
        return _result(person["person_id"], person["name"], rank, STATUS_CONFIRMED,
                       source="qr", detail=last["detail"]), {}
    return None, last or {"reason": "QR_UNREADABLE"}


def _process_cedula(cedula, cfg: ConfirmConfig):
    if isinstance(cedula, (list, tuple)):
        sim = consensus_over(list(cedula), cfg.cedula)
    elif isinstance(cedula, dict) and isinstance(cedula.get("readings"), list):
        sim = consensus_over(cedula["readings"], cfg.cedula)
    else:
        sim = CedulaOcrSim.for_input(cedula, cfg.cedula).run()
    if not sim["ok"]:
        return None, {"reason": sim["reason"],
                      "detail": {"reads_used": sim["reads_used"]}}
    subtype = sim.get("subtype")
    if subtype not in WORK_ORDERS:
        h = int.from_bytes(hashlib.sha256(sim["nuip"].encode()).digest()[:4], "big")
        subtype = "logistica" if h % 2 == 0 else "mantenimiento"
    res = _worker_result(None, sim["name"], subtype, sim["nuip"], source="cedula_sim")
    res["person_id"] = f"SIM-{sim['nuip'][-4:]}"
    res["detail"] = {"nuip_masked": "*" * (len(sim["nuip"]) - 2) + sim["nuip"][-2:],
                     "confidence": sim["confidence"], "reads_used": sim["reads_used"]}
    return res, {}


def confirm_identity(face_result: dict, qr_image=None, cedula=None,
                     config: ConfirmConfig | None = None) -> dict:
    """CONFIRMACION (T2). Ver docstring del módulo; contrato en la spec."""
    cfg = config or ConfirmConfig.from_env()
    person = resolve_face(face_result)

    if qr_image is not None:
        res, diag = _process_qr(qr_image, person, cfg)
        if res is not None:
            return res
        return _result(person["person_id"] if person else None,
                       person["name"] if person else None, None, STATUS_HUMAN,
                       reason=diag.get("reason", "QR_REJECTED"), detail=diag.get("detail"))

    if cedula is not None:
        res, diag = _process_cedula(cedula, cfg)
        if res is not None:
            return res
        return _result(None, None, None, STATUS_HUMAN,
                       reason=diag.get("reason", "CEDULA_REJECTED"), detail=diag.get("detail"))

    if person is not None:
        # Respaldo: cara conocida sin credencial (identities.json vía face_id).
        rank = RANK_FROM_FACE[person["rank_code"]]
        if rank == "WORKER":  # EXTERNAL ⇒ WORKER + orden de trabajo simulada
            return _worker_result(person["person_id"], person["name"], "logistica",
                                  person["person_id"], source="face")
        return _result(person["person_id"], person["name"], rank, STATUS_CONFIRMED,
                       source="face")

    return _result(None, None, None, STATUS_HUMAN,
                   reason="FACE_UNKNOWN" if face_result is not None else "NO_CREDENTIAL")


__all__ = ["confirm_identity", "ConfirmConfig", "CedulaConfig", "CedulaOcrSim",
           "CEDULA_WORKERS", "SUBTYPE_AREAS", "WORK_ORDERS", "consensus_over",
           "decode_qr_texts", "QRVerifier", "qc1_pack", "b64url_encode",
           "RANK_FROM_QR", "STATUS_CONFIRMED", "STATUS_HUMAN", "work_order_for"]
