"""Validación determinista de credenciales QR (puerto de solo-lectura de QR-01).

Formatos aceptados (idénticos a los ya existentes en
``/home/luis/GitHub/Laplace/IDV-01B-IDCardYOLO/qr/``):

  QR01.<b64url JSON>.<b64url Ed25519 sig sobre b"QR01.<b64url JSON>">  (firmado)
  QC1:<base45(body||Ed25519 sig)>                                      (compacto firmado)
  IDV1.<b64url JSON>.<b64url sig 64B>                                  (legacy demo)
  IDV2.<b64url 16B>                                                    (legacy compacto)

Claves fuera del repo: la llave pública Ed25519 (PEM) y el manifiesto demo se
referencian por variables de entorno (``HRI_QR_PUBLIC_KEY``, ``HRI_QR_MANIFEST``,
``HRI_QR_BINDINGS``); por defecto apuntan al checkout de Laplace/IDV-01B.
Verificación Ed25519 con ``cryptography`` si existe, si no ``pynacl``; sin
ninguna, los formatos firmados quedan INVALID_SIGNATURE (nunca se confía en
payloads sin verificar).
"""
from __future__ import annotations

import base64
import json
import struct
import time
from pathlib import Path

# ---------------------------------------------------------------- base45 (QC1)
_B45 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"
_B45_IDX = {c: i for i, c in enumerate(_B45)}


def b45encode(data: bytes) -> str:
    out = []
    for i in range(0, len(data) - 1, 2):
        n = data[i] * 256 + data[i + 1]
        c, n = divmod(n, 45)
        e, d = divmod(c, 45)
        out += [_B45[n], _B45[d], _B45[e]]
    if len(data) % 2:
        d, c = divmod(data[-1], 45)
        out += [_B45[c], _B45[d]]
    return "".join(out)


def b45decode(s: str) -> bytes:
    if len(s) % 3 == 1:
        raise ValueError("bad base45 length")
    try:
        v = [_B45_IDX[c] for c in s]
    except KeyError as e:
        raise ValueError(f"bad base45 char {e}") from None
    out = bytearray()
    for i in range(0, len(v), 3):
        chunk = v[i:i + 3]
        if len(chunk) == 3:
            n = chunk[0] + chunk[1] * 45 + chunk[2] * 2025
            if n > 0xFFFF:
                raise ValueError("base45 overflow")
            out += n.to_bytes(2, "big")
        else:
            n = chunk[0] + chunk[1] * 45
            if n > 0xFF:
                raise ValueError("base45 overflow")
            out.append(n)
    return bytes(out)


# ------------------------------------------------------------- formato QC1
QC1_PREFIX = "QC1:"
QC1_SIGNED_PREFIX = b"QC1"
_QC1_BODY = struct.Struct(">BBIH4s")
_QC1_SIG_LEN = 64
QC1_CAT_CODES = {ord("V"): "VIP", ord("R"): "REGULAR", ord("G"): "INVITED_GUEST",
                 ord("S"): "STAFF", ord("E"): "EXTERNAL"}
_QC1_CODE_OF = {v: k for k, v in QC1_CAT_CODES.items()}
_QC1_CID_TAG = {"VIP": "VIP", "REGULAR": "REG", "INVITED_GUEST": "GST",
                "STAFF": "STF", "EXTERNAL": "EXT"}


def qc1_cid_of(cat_code: int, serial: int) -> str:
    return f"QC1-{_QC1_CID_TAG.get(QC1_CAT_CODES.get(cat_code), 'UNK')}-{serial:06d}"


def qc1_pack(cat, serial: int, exp_day: int, nonce: bytes, sign_fn) -> str:
    """cat: categoría (o None); sign_fn(bytes)->64B. Solo para generar fixtures."""
    body = _QC1_BODY.pack(1, _QC1_CODE_OF.get(cat, 0), serial, exp_day, nonce[:4])
    return QC1_PREFIX + b45encode(body + sign_fn(QC1_SIGNED_PREFIX + body))


def qc1_unpack(text: str) -> dict:
    raw = b45decode(text[len(QC1_PREFIX):])
    if len(raw) != _QC1_BODY.size + _QC1_SIG_LEN:
        raise ValueError("bad QC1 length")
    body, sig = raw[:_QC1_BODY.size], raw[_QC1_BODY.size:]
    ver, cat_code, serial, exp_day, nonce = _QC1_BODY.unpack(body)
    if ver != 1:
        raise ValueError("unsupported QC1 version")
    return {"ver": ver, "cat_code": cat_code, "serial": serial, "exp_day": exp_day,
            "nonce": nonce.hex(), "body": body, "sig": sig}


# ------------------------------------------------------------------ utilidades
CATEGORIES = ("VIP", "REGULAR", "INVITED_GUEST", "STAFF", "EXTERNAL")
_CAT_SET = set(CATEGORIES)
ACCEPTED_STATUS = {"VALID_SIGNATURE", "DEMO_UNVERIFIED"}

DEFAULT_QR_ROOT = Path("/home/luis/GitHub/Laplace/IDV-01B-IDCardYOLO")


def b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


# --------------------------------------------------------------- Ed25519 (env)
class Ed25519Verifier:
    """Verificador Ed25519 portátil: cryptography → pynacl → None."""

    def __init__(self, pem_path: str | Path | None):
        self.backend = None
        self.key = None
        if not pem_path or not Path(pem_path).is_file():
            return
        pem = Path(pem_path).read_bytes()
        try:
            from cryptography.hazmat.primitives import serialization
            self.key = serialization.load_pem_public_key(pem)
            self.backend = "cryptography"
            return
        except ImportError:
            pass
        except Exception:
            return
        try:
            import nacl.signing
            der = base64.b64decode(b"".join(pem.splitlines()[1:-1]))
            self.key = nacl.signing.VerifyKey(der[-32:])  # SPKI Ed25519: últimos 32 B
            self.backend = "nacl"
        except Exception:
            self.backend = self.key = None

    def verify(self, sig: bytes, msg: bytes) -> bool:
        if self.key is None:
            return False
        try:
            if self.backend == "cryptography":
                self.key.verify(sig, msg)
            else:
                self.key.verify(msg, sig)
            return True
        except Exception:
            return False


def load_manifest(path: str | Path | None):
    """-> (by_cid, by_text); vacío si el manifiesto no existe (fuera del repo)."""
    by_cid, by_text = {}, {}
    if path and Path(path).is_file():
        try:
            data = json.loads(Path(path).read_text())
        except Exception:
            return by_cid, by_text
        for e in data.get("credentials", []):
            cat = e.get("category", "UNKNOWN")
            if e.get("cid"):
                by_cid[e["cid"]] = cat
            if e.get("text"):
                by_text[e["text"]] = cat
    return by_cid, by_text


def load_bindings(path: str | Path | None):
    """credential (cid o texto completo) -> {person_id, category?, holders}.

    Una credencial puede repetirse con varias personas (p. ej. el QR VIP compartido):
    ``holders`` reúne todos los person_id vinculados a ella."""
    out = {}
    if path and Path(path).is_file():
        try:
            data = json.loads(Path(path).read_text())
        except Exception:
            return out
        for e in data.get("bindings", []):
            cred = e.get("credential")
            if not cred:
                continue
            prev = out.get(cred)
            holders = (prev["holders"] if prev else []) + [e.get("person_id")]
            out[cred] = dict(prev or e, holders=holders)
    return out


# ------------------------------------------------------------------ validador
class QRVerifier:
    def __init__(self, verifier: Ed25519Verifier | None = None,
                 manifest_by_cid=None, manifest_by_text=None, bindings=None):
        self.verifier = verifier
        self.manifest_by_cid = manifest_by_cid or {}
        self.manifest_by_text = manifest_by_text or {}
        self.bindings = bindings or {}

    @classmethod
    def from_paths(cls, public_key=None, manifest=None, bindings=None) -> "QRVerifier":
        by_cid, by_text = load_manifest(manifest)
        return cls(Ed25519Verifier(public_key), by_cid, by_text, load_bindings(bindings))

    def _r(self, fmt, status, cid=None, cat=None, sig=None, nonce=None):
        return {"format": fmt, "verification_status": status, "credential_id": cid,
                "claimed_cat": cat if cat in _CAT_SET else None,
                "signature_valid": sig, "nonce": nonce}

    def validate(self, text):
        if not isinstance(text, str) or not text:
            return self._r(None, "INVALID_FORMAT")
        parts = text.split(".")
        if parts[0] == "QR01" and len(parts) == 3:
            return self._validate_qr01(parts)
        if parts[0] == "IDV1" and len(parts) == 3:
            return self._validate_idv1(parts)
        if parts[0] == "IDV2" and len(parts) == 2:
            return self._validate_idv2(text, parts)
        if text.startswith(QC1_PREFIX):
            return self._validate_qc1(text)
        return self._r(None, "INVALID_FORMAT")

    # ---- QC1 (compacto firmado, base45) ----
    def _validate_qc1(self, text):
        try:
            p = qc1_unpack(text)
        except Exception:
            return self._r("QC1", "INVALID_FORMAT")
        cid = qc1_cid_of(p["cat_code"], p["serial"])
        ok = (self.verifier is not None
              and self.verifier.verify(p["sig"], QC1_SIGNED_PREFIX + p["body"]))
        if not ok:
            return self._r("QC1", "INVALID_SIGNATURE", cid, None, False, p["nonce"])
        if p["exp_day"] * 86400 < time.time():
            return self._r("QC1", "EXPIRED", cid, None, True, p["nonce"])
        cat = QC1_CAT_CODES.get(p["cat_code"])
        if cat:
            return self._r("QC1", "VALID_SIGNATURE", cid, cat, True, p["nonce"])
        if p["cat_code"] == 0 and cid in self.manifest_by_cid:
            return self._r("QC1", "VALID_SIGNATURE", cid, self.manifest_by_cid[cid], True, p["nonce"])
        return self._r("QC1", "UNKNOWN_CREDENTIAL", cid, None, True, p["nonce"])

    # ---- QR01 (firmado) ----
    def _validate_qr01(self, parts):
        try:
            payload = json.loads(b64url_decode(parts[1]))
            sig = b64url_decode(parts[2])
            assert isinstance(payload, dict)
        except Exception:
            return self._r("QR01", "INVALID_FORMAT")
        cid = payload.get("cid") if isinstance(payload.get("cid"), str) else None
        nonce = payload.get("nonce")
        ok = (self.verifier is not None and len(sig) == 64
              and self.verifier.verify(sig, ("QR01." + parts[1]).encode("ascii")))
        if not ok:
            return self._r("QR01", "INVALID_SIGNATURE", cid, None, False, nonce)
        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and exp < time.time():
            return self._r("QR01", "EXPIRED", cid, None, True, nonce)
        cat = payload.get("cat")
        if cat in _CAT_SET:
            return self._r("QR01", "VALID_SIGNATURE", cid, cat, True, nonce)
        if cat is None and cid in self.manifest_by_cid:
            return self._r("QR01", "VALID_SIGNATURE", cid, self.manifest_by_cid[cid], True, nonce)
        return self._r("QR01", "UNKNOWN_CREDENTIAL", cid, None, True, nonce)

    # ---- IDV1 (legacy demo: sin llave pública, categoría solo por manifiesto) ----
    def _validate_idv1(self, parts):
        try:
            payload = json.loads(b64url_decode(parts[1]))
            sig = b64url_decode(parts[2])
            assert isinstance(payload, dict) and len(sig) == 64
        except Exception:
            return self._r("IDV1", "INVALID_FORMAT")
        cid = payload.get("cid") if isinstance(payload.get("cid"), str) else None
        exp = payload.get("exp")
        if isinstance(exp, (int, float)) and exp < time.time():
            return self._r("IDV1", "EXPIRED", cid)
        if cid in self.manifest_by_cid:
            return self._r("IDV1", "DEMO_UNVERIFIED", cid, self.manifest_by_cid[cid], None)
        return self._r("IDV1", "UNKNOWN_CREDENTIAL", cid)

    # ---- IDV2 (legacy compacto: 16 B crudos, sin categoría) ----
    def _validate_idv2(self, text, parts):
        try:
            raw = b64url_decode(parts[1])
            assert len(raw) == 16
        except Exception:
            return self._r("IDV2", "INVALID_FORMAT")
        if text in self.manifest_by_text:
            return self._r("IDV2", "DEMO_UNVERIFIED", None, self.manifest_by_text[text], None)
        # Sin manifiesto: un binding operativo también acredita la categoría (demo).
        b = self.bindings.get(text)
        if b and b.get("category") in _CAT_SET:
            return self._r("IDV2", "DEMO_UNVERIFIED", None, b["category"], None)
        return self._r("IDV2", "UNKNOWN_CREDENTIAL")

    # ---- binding credencial → persona (puerto de qr_rank_binder) ----
    def binding_for(self, val: dict, text: str):
        cid = val.get("credential_id")
        if cid and cid in self.bindings:
            return self.bindings[cid]
        return self.bindings.get(text)
