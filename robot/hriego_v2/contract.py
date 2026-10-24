from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


SCHEMA_SHA256 = "ea668dff5ba1e8503ab61c3200fefd8b37fe1117c4c7af36b673e76f3c629b00"
VALIDATOR_SHA256 = "34d4648974d157f353bf39e98a1aefa10f531308cabf3e4d880b475e4a441750"


class ContractValidator:
    def __init__(self, schema_path: Path, validator_path: Path) -> None:
        self.schema_path = schema_path
        self.validator_path = validator_path
        self._verify_hash(schema_path, SCHEMA_SHA256, "esquema RCP-01c")
        self._verify_hash(validator_path, VALIDATOR_SHA256, "validador RCP-01c")
        spec = importlib.util.spec_from_file_location("hriego_rcp01c_schema", validator_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"No se pudo cargar el validador original: {validator_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._validate = module.validate_intervention
        self.schema = json.loads(schema_path.read_text(encoding="utf-8"))

    @staticmethod
    def _verify_hash(path: Path, expected: str, label: str) -> None:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"{label} cambió respecto a la versión auditada ({actual})")

    def validate(
        self,
        value: Any,
        *,
        expected_id: str,
        expected_scene: str,
        state: str,
        max_chars: int = 1600,
    ) -> list[str]:
        errors = self._validate(value, expected_id=expected_id, expected_scene=expected_scene)
        if errors or not isinstance(value, dict):
            return errors
        speech = value["speech"]
        if len(speech) > max_chars:
            errors.append(f"speech excede {max_chars} caracteres")
        expected_act = {"GREETING": "GREETING", "GUIDANCE": "GUIDE", "FAREWELL": "CLOSING"}[state]
        if expected_act not in value["communicative_acts"]:
            errors.append(f"{state} requiere el acto {expected_act}")
        allowed_acts = {
            "GREETING": {"GREETING"},
            "GUIDANCE": {"GUIDE", "INFORM"},
            "FAREWELL": {"CLOSING"},
        }[state]
        unexpected_acts = set(value["communicative_acts"]) - allowed_acts
        if unexpected_acts:
            errors.append(f"{state} contiene actos fuera de su política: {sorted(unexpected_acts)}")
        intents = value["motor_intents"]
        if state in {"GREETING", "FAREWELL"} and intents:
            errors.append(f"{state} debe usar motor_intents=[] (NONE)")
        if state == "GUIDANCE":
            if len(intents) != 1:
                errors.append("GUIDANCE requiere exactamente una intención motora POINT_RIGHT")
            elif intents[0].get("type") != "POINT_RIGHT":
                errors.append("GUIDANCE solo permite POINT_RIGHT para el escenario configurado")
            elif intents[0].get("event_id") != expected_id:
                errors.append("GUIDANCE event_id debe coincidir con utterance_id del turno")
        return errors
