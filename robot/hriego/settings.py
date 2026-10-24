from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class Settings:
    def __init__(self) -> None:
        self.root = ROOT
        self.laplace_root = Path(os.getenv("LAPLACE_ROOT", "/home/luis/GitHub/Laplace")).expanduser().resolve()
        self.voice_root = self.laplace_root / "voice"
        self.face_root = ROOT / "Face"
        self.face_python = Path(os.getenv("HRIEGO_FACE_PYTHON", self.face_root / ".venv-face/bin/python")).expanduser()
        self.qwen_python = Path(os.getenv("HRIEGO_QWEN_PYTHON", "/home/luis/anaconda3/envs/physicalai/bin/python")).expanduser()
        self.index_python = Path(os.getenv("HRIEGO_INDEX_PYTHON", self.voice_root / ".venv-index/bin/python")).expanduser()

        self.identity_db = Path(os.getenv(
            "HRIEGO_IDENTITY_DB", self.face_root / "data/institutional_identity.sqlite3"
        )).expanduser()
        self.conversation_db = Path(os.getenv(
            "HRIEGO_CONVERSATION_DB", ROOT / "llm/data/hriego_conversations.sqlite3"
        )).expanduser()
        self.experiment_db = Path(os.getenv(
            "HRIEGO_EXPERIMENT_DB", ROOT / "hriego/data/hriego_experiment.sqlite3"
        )).expanduser()
        self.audio_root = Path(os.getenv("HRIEGO_AUDIO_DIR", ROOT / "hriego/data/audio")).expanduser()
        self.index_cache = ROOT / "hriego/data/numba"

        self.qwen_model_path = Path(os.getenv(
            "HRIEGO_QWEN_MODEL", self.voice_root / "models/Qwen3.5-4B"
        )).expanduser()
        self.index_model_dir = Path(os.getenv(
            "HRIEGO_INDEX_MODEL", self.voice_root / "models/IndexTTS-2.5"
        )).expanduser()
        default_voice = Path.home() / "Downloads/WhatsApp Ptt 2026-10-14 at 8.08.50 PM.ogg"
        self.voice_reference = Path(os.getenv("VOICE_REFERENCE_AUDIO", default_voice)).expanduser()

        self.runtime = _read_json(ROOT / "hriego/config/runtime_v1.json")
        self.identity_text = (ROOT / "hriego/config/agent_identity_v1.md").read_text(encoding="utf-8")
        self.state_config = _read_json(ROOT / "hriego/config/state_instructions_v1.json")
        self.rank_config = _read_json(ROOT / "hriego/config/rank_policies_v1.json")
        self.scenario_config = _read_json(ROOT / "hriego/config/scenarios_v1.json")

        self.rank_policies = self.rank_config["policies"]
        self.scenarios = self.scenario_config["scenarios"]
        self.default_scenario_id = os.getenv("HRIEGO_SCENARIO", self.runtime["default_scenario_id"])
        self.rag_enabled = os.getenv("HRIEGO_RAG_ENABLED", str(self.runtime["rag_enabled"])).lower() in {"1", "true", "yes"}
        self.camera_index = int(os.getenv("HRIEGO_CAMERA_INDEX", str(self.runtime["camera_index"])))
        self.api_host = os.getenv("HRIEGO_API_HOST", self.runtime["api_host"])
        self.api_port = int(os.getenv("HRIEGO_API_PORT", str(self.runtime["api_port"])))
        self.window_width = int(os.getenv("HRIEGO_WINDOW_WIDTH", str(self.runtime["window_width"])))
        self.window_height = int(os.getenv("HRIEGO_WINDOW_HEIGHT", str(self.runtime["window_height"])))

        # The audited files are bundled with HRIEGO v2 and hash checked by ContractValidator.
        # Keep explicit overrides for installations that use the original Laplace copy.
        self.contract_schema_path = Path(os.getenv(
            "HRIEGO_CONTRACT_SCHEMA", ROOT / "hriego_v2/contract_assets/intervention.schema.json"
        )).expanduser()
        self.contract_validator_path = Path(os.getenv(
            "HRIEGO_CONTRACT_VALIDATOR", ROOT / "hriego_v2/contract_assets/schema.py"
        )).expanduser()

    def scenario(self, scenario_id: str | None = None) -> dict[str, Any]:
        sid = scenario_id or self.default_scenario_id
        scenario = self.scenarios.get(sid)
        if scenario is None:
            raise ValueError(f"Escenario no configurado: {sid}")
        if scenario.get("scene_id") not in {"E1", "E2", "E3"}:
            raise ValueError(f"scene_id inválido en escenario {sid}")
        if scenario.get("direction") != "right" or scenario.get("target_vector") != [0.0, -1.0, 0.0]:
            raise ValueError(f"Escenario {sid} no coincide con el catálogo motor verificado")
        if scenario.get("reference_frame") != "X2_base_local":
            raise ValueError(f"reference_frame inválido en escenario {sid}")
        return {"scenario_id": sid, **scenario}

    def validate_paths(self) -> None:
        required_files = {
            "Face virtualenv": self.face_python,
            "Qwen virtualenv": self.qwen_python,
            "IndexTTS virtualenv": self.index_python,
            "SQLite institucional": self.identity_db,
            "Qwen local model": self.qwen_model_path / "config.json",
            "IndexTTS local model": self.index_model_dir / "config.yaml",
            "voz autorizada": self.voice_reference,
            "contrato motor": self.contract_schema_path,
            "validador motor": self.contract_validator_path,
            "reproductor PipeWire/ALSA": Path("/usr/bin/pw-play") if Path("/usr/bin/pw-play").exists() else Path("/usr/bin/aplay"),
        }
        missing = [f"{name}: {path}" for name, path in required_files.items() if not path.exists()]
        if missing:
            raise FileNotFoundError("HRIEGO no puede iniciar; faltan recursos locales:\n" + "\n".join(missing))
