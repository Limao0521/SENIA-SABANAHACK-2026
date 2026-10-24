from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


class Settings:
    def __init__(self) -> None:
        self.root = ROOT
        self.laplace_root = Path(os.getenv("LAPLACE_ROOT", ROOT.parent / "Laplace")).expanduser().resolve()
        self.voice_root = self.laplace_root / "voice"
        self.face_root = ROOT / "Face"
        self.face_python = Path(os.getenv("HRIEGO_FACE_PYTHON", self.face_root / ".venv-face/bin/python")).expanduser()

        self.identity_db = Path(os.getenv(
            "HRIEGO_IDENTITY_DB", self.face_root / "data/institutional_identity.sqlite3"
        )).expanduser()
        self.conversation_db = Path(os.getenv(
            "HRIEGO_V2_CONVERSATION_DB", ROOT / "llm/data/hriego_v2_conversations.sqlite3"
        )).expanduser()
        self.experiment_db = Path(os.getenv(
            "HRIEGO_V2_EXPERIMENT_DB", ROOT / "hriego_v2/data/hriego_experiment.sqlite3"
        )).expanduser()
        self.audio_root = Path(os.getenv("HRIEGO_V2_AUDIO_DIR", ROOT / "hriego_v2/data/audio")).expanduser()
        self.llm_container = os.getenv("HRIEGO_V2_LLM_CONTAINER", "x2-llm")
        self.tts_binary = ROOT / "experiments/hriego_v2_sprint_20261021/backend-source/qwen_tts"
        self.tts_model_dir = self.voice_root / "models/Qwen3-TTS-12Hz-1.7B-CustomVoice"
        self.tts_port = int(os.getenv("HRIEGO_V2_TTS_PORT", "19373"))
        self.tts_backend = os.getenv("HRIEGO_V2_TTS_BACKEND", "supertonic").lower()
        if self.tts_backend not in {"qwen3", "supertonic"}:
            raise ValueError("HRIEGO_V2_TTS_BACKEND debe ser qwen3 o supertonic")
        self.supertonic_root = ROOT / "experiments/hriego_v2_tts_eval_20261021"
        self.supertonic_python = Path(os.getenv(
            "HRIEGO_SUPERTONIC_PYTHON", self.supertonic_root / ".venv/bin/python")).expanduser()
        self.supertonic_model_dir = self.supertonic_root / "model"
        # A bordo del X2: audio por AimDK (hal_audio) en vez de pw-play/aplay.
        self.audio_backend = os.getenv("HRIEGO_AUDIO_BACKEND", "local").lower()
        if self.audio_backend not in {"local", "aimdk", "none"}:
            raise ValueError("HRIEGO_AUDIO_BACKEND debe ser local, aimdk o none")
        # Estados en los que la visita termina (p. ej. GUIDANCE: no se guía aún).
        self.stop_before_states = {s.strip().upper() for s in
                                   os.getenv("HRIEGO_STOP_BEFORE", "").split(",") if s.strip()}

        self.runtime = _read_json(ROOT / "hriego/config/runtime_v1.json")
        self.runtime.update(qwen_model_id="Qwen3-4B-Q4_K_M.gguf",
                            tts_model_id="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
                            tts_quantization="Talker 10 capas INT8 / 18 Q4_0 INT4; Code Predictor Q4_0 INT4",
                            v2_min_ram_available_bytes=2 * 1024**3,
                            v2_min_gpu_free_bytes=1024**3,
                            v2_tts_start_ram_available_bytes=5 * 1024**3)
        if self.tts_backend == "supertonic":
            self.runtime.update(tts_model_id="Supertone/supertonic-3",
                                tts_quantization="ONNX CPU")
        # Umbrales por plataforma (p. ej. deploy/hriego_jetson_runtime.json en el Orin,
        # donde la memoria es unificada y los servicios del robot ocupan ~7 GiB).
        overrides = os.getenv("HRIEGO_RUNTIME_OVERRIDES")
        if overrides:
            self.runtime.update(_read_json(Path(overrides).expanduser()))
        self.identity_text = (ROOT / "hriego/config/agent_identity_v1.md").read_text(encoding="utf-8")
        self.state_config = _read_json(ROOT / "hriego/config/state_instructions_v1.json")
        self.rank_config = _read_json(ROOT / "hriego/config/rank_policies_v1.json")
        self.scenario_config = _read_json(ROOT / "hriego/config/scenarios_v1.json")

        self.rank_policies = self.rank_config["policies"]
        self.scenarios = self.scenario_config["scenarios"]
        self.default_scenario_id = os.getenv("HRIEGO_SCENARIO", self.runtime["default_scenario_id"])
        self.rag_enabled = os.getenv("HRIEGO_RAG_ENABLED", str(self.runtime["rag_enabled"])).lower() in {"1", "true", "yes"}
        self.camera_index = int(os.getenv("HRIEGO_CAMERA_INDEX", str(self.runtime["camera_index"])))
        # Índice V4L2 local o ros:<topic> (cámaras del X2 publicadas por el HAL).
        self.camera_source = os.getenv("HRIEGO_CAMERA_SOURCE", str(self.camera_index))
        self.camera_open_timeout_s = float(os.getenv("HRIEGO_CAMERA_OPEN_TIMEOUT", "5"))
        self.api_host = os.getenv("HRIEGO_API_HOST", self.runtime["api_host"])
        self.api_port = int(os.getenv("HRIEGO_API_PORT", str(self.runtime["api_port"])))
        self.window_width = int(os.getenv("HRIEGO_WINDOW_WIDTH", str(self.runtime["window_width"])))
        self.window_height = int(os.getenv("HRIEGO_WINDOW_HEIGHT", str(self.runtime["window_height"])))

        self.contract_schema_path = ROOT / "hriego_v2/contract_assets/intervention.schema.json"
        self.contract_validator_path = ROOT / "hriego_v2/contract_assets/schema.py"

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
            "SQLite institucional": self.identity_db,
            "Qwen GGUF": self.root / "llm/models/Qwen3-4B-Q4_K_M.gguf",
            "contrato motor": self.contract_schema_path,
            "validador motor": self.contract_validator_path,
        }
        player = shutil.which("pw-play") or shutil.which("aplay")
        if player is None and self.audio_backend == "local":
            raise FileNotFoundError("HRIEGO necesita pw-play o aplay para reproducir audio")
        if self.tts_backend == "qwen3":
            required_files.update({"Qwen3-TTS binario": self.tts_binary,
                                   "Qwen3-TTS modelo": self.tts_model_dir / "model.safetensors"})
        else:
            required_files.update({"Supertonic Python": self.supertonic_python,
                                   "Supertonic modelo": self.supertonic_model_dir / "onnx/duration_predictor.onnx"})
        missing = [f"{name}: {path}" for name, path in required_files.items() if not path.exists()]
        if missing:
            raise FileNotFoundError("HRIEGO no puede iniciar; faltan recursos locales:\n" + "\n".join(missing))
