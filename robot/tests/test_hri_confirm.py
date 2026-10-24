"""Tests T2 — hri_runtime.confirm.confirm_identity (QR + cédula simulada).

Corren en el venv principal (PYTHONPATH=src); los casos que necesitan
zxing-cpp/OpenCV (decodeo de PNG) o Ed25519 (QR01/QC1 firmados) se saltan si
las librerías no están instaladas. Fixtures QR: IDV2 sintéticos de IDV-01B
(sin datos personales).
"""
from __future__ import annotations

import base64
import inspect
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from x2_ultra.hri_runtime.confirm import (  # noqa: E402
    SUBTYPE_AREAS, WORK_ORDERS, ConfirmConfig, CedulaConfig, RANK_FROM_QR,
    STATUS_CONFIRMED, STATUS_HUMAN, CedulaOcrSim, b64url_encode, confirm_identity,
    consensus_over, decode_qr_texts, qc1_pack, work_order_for)
from x2_ultra.show.hri.face_id import precomputed_identity  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
BINDINGS_DEFAULT = str(Path(sys.modules["x2_ultra.hri_runtime.confirm"].__file__).parent
                       / "qr_bindings.default.json")
CFG = ConfirmConfig(public_key=None, manifest=None, bindings=BINDINGS_DEFAULT)
SPKI_ED25519 = bytes.fromhex("302a300506032b6570032100")


def _have_decoder() -> bool:
    for m in ("zxingcpp", "cv2"):
        try:
            __import__(m)
            return True
        except ImportError:
            continue
    return False


def _make_ed25519():
    """(sign_fn, pem_text) con pynacl o cryptography; (None, None) si no hay."""
    try:
        import nacl.signing
        sk = nacl.signing.SigningKey.generate()
        der = SPKI_ED25519 + bytes(sk.verify_key)
        pem = ("-----BEGIN PUBLIC KEY-----\n"
               + base64.encodebytes(der).decode() + "-----END PUBLIC KEY-----\n")
        return (lambda m: bytes(sk.sign(m).signature)), pem
    except ImportError:
        pass
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        sk = Ed25519PrivateKey.generate()
        pem = sk.public_key().public_bytes(serialization.Encoding.PEM,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
        return (lambda m: sk.sign(m)), pem.decode()
    except ImportError:
        return None, None


requires_decoder = pytest.mark.skipif(not _have_decoder(),
                                      reason="sin zxing-cpp ni OpenCV en este venv")
SIGN, PEM = _make_ed25519()
requires_ed25519 = pytest.mark.skipif(SIGN is None,
                                      reason="sin cryptography ni pynacl en este venv")


@pytest.fixture()
def signed_cfg(tmp_path):
    key = tmp_path / "qr_public_key.pem"
    key.write_text(PEM)
    return ConfirmConfig(public_key=str(key), manifest=None, bindings=BINDINGS_DEFAULT)


def _worker_assertions(res):
    assert res["status"] == STATUS_CONFIRMED
    assert res["rank"] == "WORKER"
    assert res["worker_subtype"] in ("logistica", "mantenimiento")
    wo = res["work_order"]
    assert wo and set(wo) == {"area", "task"} and wo["task"]
    assert wo["area"] in SUBTYPE_AREAS[res["worker_subtype"]], \
        "el área de la orden debe ser accesible para el subtipo (tabla de la spec)"


# ------------------------------------------------------------------ contrato
def test_signature_matches_spec():
    params = list(inspect.signature(confirm_identity).parameters)
    assert params[:3] == ["face_result", "qr_image", "cedula"]
    res = confirm_identity(None)
    for k in ("person_id", "name", "rank", "worker_subtype", "work_order", "status"):
        assert k in res


def test_rank_from_qr_maps_external_to_worker():
    assert RANK_FROM_QR["EXTERNAL"] == "WORKER"
    assert RANK_FROM_QR["INVITED_GUEST"] == "GUEST"


# ---------------------------------------------------------------------- cara
def test_face_only_known_person_uses_identities_fallback():
    res = confirm_identity("Laura", config=CFG)
    assert res["status"] == STATUS_CONFIRMED and res["source"] == "face"
    assert res["person_id"] == "P005" and res["rank"] == "VIP"


def test_face_dict_from_precomputed_identity():
    res = confirm_identity(precomputed_identity("Sebastian"), config=CFG)
    assert res["status"] == STATUS_CONFIRMED and res["rank"] == "REGULAR"
    assert res["name"] == "Sebastian"


def test_face_external_becomes_worker_with_work_order():
    res = confirm_identity("Josaed", config=CFG)  # EXTERNAL ⇒ WORKER
    _worker_assertions(res)
    assert res["source"] == "face"


def test_face_unknown_and_no_credential_goes_to_human_reception():
    res = confirm_identity(precomputed_identity("Desconocido"), config=CFG)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "FACE_UNKNOWN"
    res2 = confirm_identity(None, config=CFG)
    assert res2["status"] == STATUS_HUMAN and res2["reason"] == "NO_CREDENTIAL"


# ------------------------------------------------------------------ QR texto
def test_qr_text_idv2_bound_to_confirmed_face():
    res = confirm_identity("Laura", qr_image="IDV2.t6nuic0_EcxSbm81a42yyg", config=CFG)
    assert res["status"] == STATUS_CONFIRMED and res["source"] == "qr"
    assert res["rank"] == "VIP" and res["person_id"] == "P005"
    assert res["detail"]["verification_status"] == "DEMO_UNVERIFIED"


def test_qr_of_other_person_is_rejected():
    res = confirm_identity("Sebastian", qr_image="IDV2.t6nuic0_EcxSbm81a42yyg", config=CFG)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "QR_OTHER_PERSON"


def test_qr_without_confirmed_face_is_rejected():
    res = confirm_identity(precomputed_identity("Desconocido"),
                           qr_image="IDV2.t6nuic0_EcxSbm81a42yyg", config=CFG)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "QR_OTHER_PERSON"


def test_qr_unknown_credential_is_rejected():
    import os
    text = "IDV2." + b64url_encode(os.urandom(16))  # no está en manifiesto ni bindings
    res = confirm_identity("Laura", qr_image=text, config=CFG)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "QR_UNKNOWN_CREDENTIAL"


@requires_ed25519
def test_qr_valid_signature_but_unbound_is_rejected(signed_cfg):
    text = qc1_pack("VIP", 99, int(time.time() // 86400) + 1, b"qqqq", SIGN)
    res = confirm_identity("Laura", qr_image=text, config=signed_cfg)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "QR_UNBOUND"


def test_qr_invalid_format_is_rejected():
    res = confirm_identity("Laura", qr_image="QC1:%%%%", config=CFG)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "QR_INVALID_FORMAT"
    res2 = confirm_identity("Laura", qr_image="texto-cualquiera", config=CFG)
    assert res2["status"] == STATUS_HUMAN and res2["reason"] == "QR_UNREADABLE"


@requires_ed25519
def test_qc1_compact_signed_ok_tampered_and_expired(signed_cfg):
    exp_day = int(time.time() // 86400) + 1
    good = qc1_pack("VIP", 1, exp_day, b"abcd", SIGN)
    res = confirm_identity("Laura", qr_image=good, config=signed_cfg)
    assert res["status"] == STATUS_CONFIRMED and res["rank"] == "VIP"
    assert res["detail"]["verification_status"] == "VALID_SIGNATURE"
    assert res["detail"]["credential_id"] == "QC1-VIP-000001"

    other_sign, _ = _make_ed25519()
    bad = qc1_pack("VIP", 1, exp_day, b"abcd", other_sign)
    res2 = confirm_identity("Laura", qr_image=bad, config=signed_cfg)
    assert res2["status"] == STATUS_HUMAN and res2["reason"] == "QR_INVALID_SIGNATURE"

    expired = qc1_pack("VIP", 1, int(time.time() // 86400) - 2, b"abcd", SIGN)
    res3 = confirm_identity("Laura", qr_image=expired, config=signed_cfg)
    assert res3["status"] == STATUS_HUMAN and res3["reason"] == "QR_EXPIRED"


@requires_ed25519
def test_qr01_signed_and_external_worker(tmp_path):
    payload = {"cid": "QR01-VIP-0001", "cat": "VIP", "exp": time.time() + 3600, "nonce": "n1"}
    p1 = b64url_encode(json.dumps(payload).encode())
    sig = SIGN(("QR01." + p1).encode("ascii"))
    text = f"QR01.{p1}.{b64url_encode(sig)}"
    key = tmp_path / "pub.pem"
    key.write_text(PEM)
    cfg = ConfirmConfig(public_key=str(key), manifest=None, bindings=BINDINGS_DEFAULT)
    res = confirm_identity("Laura", qr_image=text, config=cfg)
    assert res["status"] == STATUS_CONFIRMED and res["rank"] == "VIP"

    # EXTERNAL firmado ⇒ WORKER + orden de trabajo simulada (binding propio)
    ext_cid = "QC1-EXT-000007"
    bindings = tmp_path / "bindings.json"
    bindings.write_text(json.dumps({"bindings": [
        {"credential": ext_cid, "person_id": "P002", "category": "EXTERNAL"}]}))
    cfg2 = ConfirmConfig(public_key=str(key), manifest=None, bindings=str(bindings))
    ext = qc1_pack("EXTERNAL", 7, int(time.time() // 86400) + 1, b"wxyz", SIGN)
    res2 = confirm_identity("Josaed", qr_image=ext, config=cfg2)
    _worker_assertions(res2)
    assert res2["source"] == "qr" and res2["person_id"] == "P002"


def test_idv1_legacy_demo_with_manifest():
    manifest = Path("/home/luis/GitHub/Laplace/IDV-01B-IDCardYOLO/config/demo_credentials.json")
    if not manifest.is_file():
        pytest.skip("manifiesto demo de IDV-01B fuera del repo no disponible")
    cfg = ConfirmConfig(public_key=None, manifest=str(manifest), bindings=BINDINGS_DEFAULT)
    payload = {"cid": "DEMO-VIP-001", "exp": time.time() + 3600}
    text = f"IDV1.{b64url_encode(json.dumps(payload).encode())}.{b64url_encode(b'0' * 64)}"
    res = confirm_identity("Laura", qr_image=text, config=cfg)
    assert res["status"] == STATUS_CONFIRMED and res["rank"] == "VIP"
    assert res["detail"]["verification_status"] == "DEMO_UNVERIFIED"


# ----------------------------------------------------------------- QR imagen
QR_PNG_CASES = [("qr_vip_compacto.png", "Laura", "VIP"),
                ("qr_regular_compacto.png", "Sebastian", "REGULAR"),
                ("qr_invitado_compacto.png", "Mario", "GUEST"),
                ("qr_club_staff_compacto.png", "Luis", "STAFF")]


@requires_decoder
@pytest.mark.parametrize("png,person,rank", QR_PNG_CASES)
def test_qr_png_fixtures_confirm_bound_person(png, person, rank):
    path = FIXTURES / png
    assert path.is_file(), f"falta fixture {path} (copiar de ~/Downloads)"
    err, texts = decode_qr_texts(str(path))
    assert err is None and len(texts) == 1 and texts[0].startswith("IDV2.")
    res = confirm_identity(person, qr_image=str(path), config=CFG)
    assert res["status"] == STATUS_CONFIRMED and res["source"] == "qr"
    assert res["rank"] == rank


@requires_decoder
def test_qr_png_bytes_input_and_other_person():
    data = (FIXTURES / "qr_vip_compacto.png").read_bytes()
    res = confirm_identity("Sebastian", qr_image=data, config=CFG)  # VIP ≠ Sebastian
    assert res["status"] == STATUS_HUMAN and res["reason"] == "QR_OTHER_PERSON"


# ------------------------------------------------------------ cédula simulada
def test_cedula_sim_deterministic_worker_with_valid_work_order():
    r1 = confirm_identity(None, cedula=b"scan-cedula-0001", config=CFG)
    r2 = confirm_identity(None, cedula=b"scan-cedula-0001", config=CFG)
    assert r1 == r2, "la simulación debe ser determinista"
    _worker_assertions(r1)
    assert r1["source"] == "cedula_sim"
    assert r1["detail"]["reads_used"] >= 3, "consenso temporal ≥3 lecturas"
    assert r1["detail"]["nuip_masked"].startswith("*")


def test_cedula_nuip_string_and_dict_inputs():
    res = confirm_identity(None, cedula="1099002233", config=CFG)
    _worker_assertions(res)
    res2 = confirm_identity(None, cedula={"nuip": "1099556677", "name": "Ana Prueba",
                                          "subtype": "mantenimiento"}, config=CFG)
    _worker_assertions(res2)
    assert res2["worker_subtype"] == "mantenimiento" and res2["name"] == "Ana Prueba"


def test_cedula_consensus_needs_three_equal_readings():
    good = {"nuip": "1099002233", "name": "Camila Rojas", "confidence": 0.9}
    other = {"nuip": "1099002234", "name": "Camila Rojas", "confidence": 0.9}
    ok = consensus_over([other, good, good, good])
    assert ok["ok"] and ok["reads_used"] == 4
    fail = consensus_over([good, good, other, other])
    assert not fail["ok"] and fail["reason"] == "CEDULA_CONSENSUS_FAIL"
    res = confirm_identity(None, cedula=[good, good, other], config=CFG)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "CEDULA_CONSENSUS_FAIL"
    res2 = confirm_identity(None, cedula=[good, good, good], config=CFG)
    _worker_assertions(res2)


def test_cedula_noise_and_confidence_configurable_fail_to_human():
    noisy = ConfirmConfig(cedula=CedulaConfig(noise=1.0))
    res = confirm_identity(None, cedula=b"scan-ruidoso", config=noisy)
    assert res["status"] == STATUS_HUMAN and res["reason"] == "CEDULA_CONSENSUS_FAIL"
    lowconf = ConfirmConfig(cedula=CedulaConfig(noise=0.0, confidence=0.2))
    res2 = confirm_identity(None, cedula=b"scan-borroso", config=lowconf)
    assert res2["status"] == STATUS_HUMAN and res2["reason"] == "CEDULA_LOW_CONFIDENCE"


def test_cedula_sim_readings_shape():
    sim = CedulaOcrSim.for_input(b"x", CedulaConfig(noise=0.0))
    r = sim.read(0)
    assert set(r) >= {"t", "nuip", "name", "confidence"}
    assert r["nuip"] == sim.truth["nuip"] and r["name"] == sim.truth["name"]


def test_work_order_table_areas_are_subset_of_access_table():
    for subtype, orders in WORK_ORDERS.items():
        for area, task in orders:
            assert area in SUBTYPE_AREAS[subtype] and task
    assert work_order_for("logistica", "k1") == work_order_for("logistica", "k1")


def test_shared_vip_qr_confirms_every_bound_holder():
    for name, pid in (("Laura", "P005"), ("Jorge Castellanos", "P006")):
        res = confirm_identity(name, qr_image="IDV2.t6nuic0_EcxSbm81a42yyg", config=CFG)
        assert res["status"] == STATUS_CONFIRMED and res["rank"] == "VIP" and res["person_id"] == pid


# ----------------------------------------------------------------- trato por persona (VIP)
def test_trato_vip_por_persona_no_por_laura():
    from x2_ultra.hri_runtime import contract, personas
    kb = contract.load_kb()
    jorge = {"person_id": "P006", "name": "Jorge Castellanos", "rank": "VIP"}
    laura = {"person_id": "P005", "name": "Laura", "rank": "VIP"}
    assert personas.titulo_para(jorge) == "señor" and personas.titulo_para(laura) == "señorita"
    sys_j = "\n".join(m["content"] for m in contract.build_messages("GREETING", "VIP", jorge, kb))
    assert "«señor Jorge Castellanos»" in sys_j
    assert "Laura" not in sys_j and "señorita" not in sys_j.lower().replace("«señorita …»", "")
    sys_l = "\n".join(m["content"] for m in contract.build_messages("GREETING", "VIP", laura, kb))
    assert "«señorita Laura»" in sys_l
    for ejemplos in personas.CARDS["VIP"]["ejemplos"].values():   # ejemplos sin nombre ni género fijos
        assert not any("Laura" in e for e in ejemplos)
