"""Portable path helpers: manifests and generated JSON store paths relative to the HRIEGO_gesture root.

Root = $HRIEGO_GESTURE_ROOT, default: the directory of this package. Paths outside the root (rare: user-chosen --out
elsewhere) are stored absolute; use `tools/relativize_paths.py` to normalize old artifacts.
Also hosts the environment-variable lookups shared by the entry points (interpreters, external roots).
"""
from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("HRIEGO_GESTURE_ROOT") or PACKAGE_DIR).resolve()


def rel(p) -> str:
    """Path -> POSIX string relative to ROOT when inside it, else the absolute string. None stays None."""
    if p is None:
        return None
    pp = Path(p)
    try:
        return pp.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(p) if pp.is_absolute() else pp.as_posix()


def resolve(p) -> Path:
    """Stored path (relative to ROOT, or absolute) -> absolute Path. Placeholders like $LAPLACE_ROOT are expanded from the env."""
    s = os.path.expandvars(str(p))
    pp = Path(s)
    return pp if pp.is_absolute() else ROOT / pp


# ---- interpreters / external roots (env var -> default) -------------------------------------------------------
def _conda_base() -> Path:
    return Path(os.environ.get("CONDA_BASE") or Path.home() / "anaconda3")


def _env_python(prefix_var: str, name: str) -> str:
    pre = os.environ.get(prefix_var)
    return str(Path(pre) / "bin/python") if pre else str(_conda_base() / "envs" / name / "bin/python")


def py_physicalai() -> str:
    """`physicalai` conda env python (CTC alignment, StreamTalk, SONIC). $HRIEGO_PY_PHYSICALAI, else $CONDA_PREFIX_PHYSICALAI/bin/python."""
    return os.environ.get("HRIEGO_PY_PHYSICALAI") or _env_python("CONDA_PREFIX_PHYSICALAI", "physicalai")


def py_gmr() -> str:
    """`gmr` conda env python (GMR retargeting). $HRIEGO_PY_GMR, else $CONDA_PREFIX_GMR/bin/python."""
    return os.environ.get("HRIEGO_PY_GMR") or _env_python("CONDA_PREFIX_GMR", "gmr")


def supertonic_py() -> str:
    """Supertonic TTS venv python. $SUPERTONIC_PY, else <X2_ULTRA_ROOT>/experiments/hriego_v2_tts_eval_20261021/.venv/bin/python."""
    return os.environ.get("SUPERTONIC_PY") or str(x2_ultra_root() / "experiments/hriego_v2_tts_eval_20261021/.venv/bin/python")


def x2_ultra_root() -> Path:
    """x2_ultra repo root. $X2_ULTRA_ROOT, default: parent of the HRIEGO_gesture directory."""
    return Path(os.environ.get("X2_ULTRA_ROOT") or PACKAGE_DIR.parent)


def laplace_root() -> Path:
    """Laplace repo (RCP-01 c02/c03/c07 scripts, Gesture/StreamTalk assets, voice/ E4 QuartzNet). $LAPLACE_ROOT, default ~/GitHub/Laplace."""
    return Path(os.environ.get("LAPLACE_ROOT") or Path.home() / "GitHub/Laplace")


def semantic_corporea_root() -> Path:
    """SemanticCorporeHRI repo (GMR assets, BEAT2 data). $SEMANTIC_CORPOREA_ROOT, default ~/GitHub/SemanticCorporeaHRI."""
    return Path(os.environ.get("SEMANTIC_CORPOREA_ROOT") or Path.home() / "GitHub/SemanticCorporeaHRI")


def smplx_neutral_npz() -> Path:
    """$SMPLX_NEUTRAL_NPZ, default <SEMANTIC_CORPOREA_ROOT>/third_party/GMR/assets/body_models/smplx/SMPLX_NEUTRAL.npz."""
    return Path(os.environ.get("SMPLX_NEUTRAL_NPZ") or semantic_corporea_root() / "third_party/GMR/assets/body_models/smplx/SMPLX_NEUTRAL.npz")


def beat2_smplx_dir() -> Path:
    """$BEAT2_SMPLX_DIR, default <SEMANTIC_CORPOREA_ROOT>/data/raw/BEAT2/beat_english_v2.0.0/smplxflame_30."""
    return Path(os.environ.get("BEAT2_SMPLX_DIR") or semantic_corporea_root() / "data/raw/BEAT2/beat_english_v2.0.0/smplxflame_30")


def commercial_show_dir() -> Path:
    """Commercial-show audio/motion dir used by workers/bench.py. $COMMERCIAL_SHOW_DIR."""
    return Path(os.environ.get("COMMERCIAL_SHOW_DIR") or laplace_root() / "voice/experiments/x2_commercial_show_indextts_corregido_20261022")


DEFAULT_LATENCY = ROOT / "config/preset_latency_c08.json"


# ---- vendored motion/ scripts: external roots -------------------------------------------------------------------
MOTION_DIR = PACKAGE_DIR / "motion"


def x2_external_root() -> Path:
    """X2 external checkouts (GMR/, agibot_x2_retargeter_bb582d97/, sonic_x2_c9959443/). $X2_EXTERNAL_ROOT, default $LAPLACE_ROOT/mimo/X2/vendor/x2_external_evidence."""
    return Path(os.environ.get("X2_EXTERNAL_ROOT") or laplace_root() / "mimo/X2/vendor/x2_external_evidence")


def x2_ik_dir() -> Path:
    """Audited toe-proxy IK model dir (retarget/model/x2_mocap_toe_proxy.xml + audit JSON). $X2_IK_DIR, default $LAPLACE_ROOT/mimo/X2/results/v2m_02_x2_ultra."""
    return Path(os.environ.get("X2_IK_DIR") or laplace_root() / "mimo/X2/results/v2m_02_x2_ultra")


def streamtalk_root() -> Path:
    """StreamTalk code + checkpoints (external). $STREAMTALK_ROOT, default $LAPLACE_ROOT/Gesture/third_party/StreamTalk."""
    return Path(os.environ.get("STREAMTALK_ROOT") or laplace_root() / "Gesture/third_party/StreamTalk")


def streamtalk_retrieval_cache() -> Path:
    """Retrieval KD-tree cache (external, *.npz). $STREAMTALK_RETRIEVAL_CACHE, default c02 cache in Laplace."""
    return Path(os.environ.get("STREAMTALK_RETRIEVAL_CACHE") or laplace_root() / "experiments/RCP-01/runs/20261019_rcp01c02_fixedcalm/cache/streamtalk_retrieval")


def streamtalk_train_csv() -> Path:
    """Retrieval train split CSV. $STREAMTALK_TRAIN_CSV, default Laplace Gesture/experiments/wayne069_audio_only_20261014/configs/streamtalk_train_excluding_wayne069.csv."""
    return Path(os.environ.get("STREAMTALK_TRAIN_CSV") or laplace_root() / "Gesture/experiments/wayne069_audio_only_20261014/configs/streamtalk_train_excluding_wayne069.csv")


READY_PROMPT = MOTION_DIR / "config/streamtalk_ready_prompt.npz"


# ---- digital-twin (twin/) helpers --------------------------------------------------------------------------------
def conda_exe() -> str:
    """conda binary. $CONDA_EXE, else <CONDA_BASE>/bin/conda."""
    return os.environ.get("CONDA_EXE") or str(_conda_base() / "bin/conda")


def shim_dir() -> Path:
    """Dir holding the docker->podman `docker`/`podman` shim put on PATH by twin scripts. $X2_TWIN_SHIM_DIR, default ~/.local/share/hriego/shim."""
    return Path(os.environ.get("X2_TWIN_SHIM_DIR") or Path.home() / ".local/share/hriego/shim")


def x2_teleop_root() -> Path:
    """x2_ultra-real-robot-teleop checkout (provides x2_ultra.robot.joints etc. via <root>/src). $X2_TELEOP_ROOT, default ~/GitHub/x2_ultra-real-robot-teleop."""
    return Path(os.environ.get("X2_TELEOP_ROOT") or Path.home() / "GitHub/x2_ultra-real-robot-teleop")


def x2_urdf_root() -> Path:
    """AgiBot X2 URDF/meshes root used by the renderers. $X2_URDF_ROOT, default ~/robots/agibot_x2_urdf."""
    return Path(os.environ.get("X2_URDF_ROOT") or Path.home() / "robots/agibot_x2_urdf")


def x2_sim_root() -> Path:
    """x2_ultra official_sim dir (Dockerfile.twin, twin.sh). $X2_SIM_ROOT, default <X2_ULTRA_ROOT>/official_sim."""
    return Path(os.environ.get("X2_SIM_ROOT") or x2_ultra_root() / "official_sim")
