"""Identificación del huésped que llega: foto → persona → rango, con el venv de Face en un subproceso.

La foto reemplaza a la cámara (cámara simulada): ``identify_photo`` lanza ``face_probe.py`` con el
Python de ``Face/.venv-face`` (aislado del venv del show) y devuelve el dict de la sonda. Sin cara
conocida (``status != KNOWN``) la recepción sigue con una categoría por defecto (invitado) y el
estado de identidad correspondiente.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# El módulo Face es OPCIONAL (privacidad/licencia: ni fotos ni galería se versionan). Por defecto
# se busca el Face de este repo; el modo en vivo necesita su venv (scripts/setup_hriego_v2.sh) o
# X2_FACE_ROOT/X2_FACE_PYTHON apuntando al checkout de la rama
# `Facial,-LLM-and-Gesture-integration`. Sin él, la demo usa identities.json (precomputado).
REPO_ROOT = Path(__file__).resolve().parents[4]
FACE_ROOT = Path(os.environ.get("X2_FACE_ROOT", str(REPO_ROOT / "Face")))
FACE_PYTHON = Path(os.environ.get("X2_FACE_PYTHON", str(FACE_ROOT / ".venv-face" / "bin" / "python")))
PROBE = Path(__file__).resolve().parent / "face_probe.py"
DEFAULT_CATEGORY_UNKNOWN = "invitado"


def face_available() -> bool:
    return Path(FACE_PYTHON).is_file()


def precomputed_identity(person: str) -> dict:
    """Resultado de identidad precomputado (persona → rango) sin ejecutar Face: mismo formato
    que la sonda para que ``reception_identity`` funcione igual."""
    from x2_ultra.show.hri.data import load_identities
    entry = load_identities().get(person)
    if entry is None:
        return {"photo": None, "status": "ERROR", "error": f"identidad precomputada desconocida: {person}"}
    return {"photo": None, "status": entry.get("status", "KNOWN"), "source": "precomputed",
            "top1_id": entry.get("person_id"), "top1_score": None,
            "profile": {"person_id": entry.get("person_id"),
                        "display_name": entry.get("display_name", person),
                        "rank_code": entry.get("rank_code"),
                        "identity_status": ("CONFIRMED" if entry.get("status") == "KNOWN"
                                            else "UNCONFIRMED")}}


def identify_photo(photo: Path | str, timeout: float = 180.0) -> dict:
    if not face_available():
        return {"photo": str(photo), "status": "ERROR",
                "error": ("módulo Face no disponible (falta el intérprete de Face/.venv-face; "
                          "opcional: use identidades precomputadas sin --photo)")}
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(FACE_ROOT / "thirdparty" / "uniface"), str(FACE_ROOT)])
    env.setdefault("UNIFACE_CACHE_DIR", str(FACE_ROOT / "thirdparty" / "uniface-models"))
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    proc = subprocess.run([str(FACE_PYTHON), "-u", str(PROBE), str(photo)], capture_output=True,
                          text=True, timeout=timeout, env=env)
    line = next((ln for ln in reversed(proc.stdout.splitlines()) if ln.startswith("{")), None)
    if line is None:
        return {"photo": str(photo), "status": "ERROR",
                "error": (proc.stderr or proc.stdout or "sin salida")[-500:]}
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        return {"photo": str(photo), "status": "ERROR", "error": f"JSON inválido: {exc}"}


def reception_identity(result: dict) -> tuple[str, dict, str]:
    """(categoría del club, perfil, estado visible) a partir del resultado de la sonda."""
    from x2_ultra.show.hri.llm_script import RANK_CATEGORY
    profile = result.get("profile") or {}
    score = result.get("top1_score")
    score_txt = (f"cos {score:.2f}" if isinstance(score, (int, float))
                 else ("identidad precomputada" if result.get("source") == "precomputed" else "cos —"))
    if result.get("status") == "KNOWN" and profile.get("rank_code") in RANK_CATEGORY:
        category = RANK_CATEGORY[profile["rank_code"]]
        status = (f"reconocimiento facial (foto): {profile.get('display_name', '?')} · "
                  f"{profile['rank_code']} ({score_txt})")
        return category, profile, status
    status = (f"reconocimiento facial (foto): visitante NO identificado "
              f"({result.get('status', '?')}, {score_txt})")
    return DEFAULT_CATEGORY_UNKNOWN, {"person_id": None, "display_name": "visitante",
                                      "rank_code": "GUEST", "identity_status": "UNCONFIRMED"}, status
