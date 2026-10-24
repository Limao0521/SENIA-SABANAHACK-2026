"""Dónde están los assets externos y los del repo.

Los URDF/mallas de AgiBot no se versionan aquí (pesan ~300 MB). Se buscan, en orden:
  1. variable de entorno ``X2_URDF_ROOT``
  2. ``third_party/agibot_x2_urdf`` dentro del repo (enlace o clon creado por scripts/setup.sh)
  3. ``~/robots/agibot_x2_urdf`` (donde están en el PC de desarrollo)
"""
from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = REPO_ROOT / "models"
ASSETS_DIR = REPO_ROOT / "assets"
POLICIES_DIR = ASSETS_DIR / "policies"

# Commit del repo público github.com/AgibotTech/agibot_x2_urdf con el que se generó el modelo.
URDF_REPO_URL = "https://github.com/AgibotTech/agibot_x2_urdf.git"
URDF_REPO_COMMIT = "575cc6b"
URDF_VERSION_DIR = "X2_URDF-v1.4.0"


def urdf_root() -> Path:
    candidates = []
    env = os.environ.get("X2_URDF_ROOT")
    if env:
        candidates.append(Path(env).expanduser())
    candidates += [REPO_ROOT / "third_party" / "agibot_x2_urdf", Path.home() / "robots" / "agibot_x2_urdf"]
    for c in candidates:
        if (c / URDF_VERSION_DIR / "X2-Ultra.xml").exists():
            return c
    raise FileNotFoundError(
        "No encuentro el URDF de AgiBot X2 (X2_URDF-v1.4.0). Ejecuta scripts/setup.sh "
        f"o define X2_URDF_ROOT. Buscado en: {', '.join(str(c) for c in candidates)}")


def urdf_dir() -> Path:
    return urdf_root() / URDF_VERSION_DIR


def official_mjcf() -> Path:
    """MJCF oficial del X2 Ultra(-N) sin manos (cuerpo, colisiones y sensores afinados por AgiBot)."""
    return urdf_dir() / "X2-Ultra.xml"


def edu_mjcf() -> Path:
    """MJCF oficial del X2 EDU (29 GDL: sin cuello; muñeca sin mano)."""
    return urdf_dir() / "X2-EDU.xml"


def omnihand_urdf() -> Path:
    """URDF oficial del X2 Ultra(-N) con OmniHand (única fuente de la geometría de las manos)."""
    return urdf_dir() / "X2-Ultra_omnihand.urdf"


def meshes_dir() -> Path:
    return urdf_dir() / "meshes"


def d1_root() -> Path:
    """URDF y mallas del perro robot AgiBot D1 Edu (los baja ``scripts/fetch_d1.sh``)."""
    root = Path(os.environ.get("X2_D1_ROOT", REPO_ROOT / "third_party" / "agibot_d1")).expanduser()
    if not (root / "urdf" / "edu_mujoco.urdf").exists():
        raise FileNotFoundError(f"No encuentro el modelo del AgiBot D1 en {root}. Ejecuta scripts/fetch_d1.sh")
    return root


def default_policy() -> Path:
    return POLICIES_DIR / "x2_arms_free_walk.onnx"


def agibot_policy() -> Path:
    """Policy oficial de AgiBot (mc-rl, Apache-2.0): cuerpo entero, no admite brazos libres."""
    return POLICIES_DIR / "agibot_mcrl_policy.onnx"
