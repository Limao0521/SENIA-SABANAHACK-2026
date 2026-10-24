"""Versioned RCP-01c intervention contract and conservative checks."""
from __future__ import annotations

import math
from typing import Any

SCHEMA_VERSION = "rcp01c.intervention.v1"
SCENE_IDS = {"E1", "E2", "E3"}
ACTS = {"GREETING", "INFORMATION_REQUEST", "INFORM", "GUIDE", "ACKNOWLEDGE", "CLARIFICATION_REQUEST", "CLOSING"}
VECTORS = {
    "POINT_RIGHT": [0.0, -1.0, 0.0],
    "POINT_LEFT": [0.0, 1.0, 0.0],
    "POINT_FRONT": [1.0, 0.0, 0.0],
}


def validate_intervention(value: Any, expected_id: str | None = None, expected_scene: str | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["intervention must be a JSON object"]
    required = {"schema_version", "scene_id", "utterance_id", "speech", "communicative_acts", "motor_intents"}
    if set(value) != required:
        errors.append(f"keys must be exactly {sorted(required)}")
    if value.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema_version")
    scene_id = value.get("scene_id")
    if not isinstance(scene_id, str) or scene_id not in SCENE_IDS:
        errors.append("scene_id must be E1, E2, or E3")
    if expected_scene and value.get("scene_id") != expected_scene:
        errors.append("scene_id does not match the frozen task")
    if not isinstance(value.get("utterance_id"), str) or not value.get("utterance_id", "").strip():
        errors.append("utterance_id must be non-empty text")
    elif expected_id and value["utterance_id"] != expected_id:
        errors.append("utterance_id does not match the frozen task")
    speech = value.get("speech")
    if not isinstance(speech, str) or not speech.strip():
        errors.append("speech must be non-empty text")
        speech = ""
    acts = value.get("communicative_acts")
    if not isinstance(acts, list) or not acts:
        errors.append("communicative_acts must be a non-empty array")
    elif any(not isinstance(a, str) or a not in ACTS for a in acts) or (
            all(isinstance(a, str) for a in acts) and len(set(acts)) != len(acts)):
        errors.append("communicative_acts contains an unknown or duplicate class")
    intents = value.get("motor_intents")
    if not isinstance(intents, list):
        errors.append("motor_intents must be an array; [] encodes NONE")
        intents = []
    normalized_speech = speech.casefold()
    for index, intent in enumerate(intents):
        prefix = f"motor_intents[{index}]"
        if not isinstance(intent, dict):
            errors.append(f"{prefix} must be an object")
            continue
        keys = {"event_id", "type", "trigger_text", "reference_frame", "target_vector"}
        if set(intent) != keys:
            errors.append(f"{prefix} keys must be exactly {sorted(keys)}")
        event_id = intent.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            errors.append(f"{prefix}.event_id must be non-empty text")
        kind = intent.get("type")
        if not isinstance(kind, str) or kind not in VECTORS:
            errors.append(f"{prefix}.type is not a supported explicit motor intent")
            continue
        trigger = intent.get("trigger_text")
        if not isinstance(trigger, str) or not trigger.strip() or trigger not in speech:
            errors.append(f"{prefix}.trigger_text must be a literal substring of speech")
            trigger = ""
        if intent.get("reference_frame") != "X2_base_local":
            errors.append(f"{prefix}.reference_frame must be X2_base_local")
        vector = intent.get("target_vector")
        if not isinstance(vector, list) or len(vector) != 3 or any(
                not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(float(x)) for x in vector):
            errors.append(f"{prefix}.target_vector must contain three numbers")
        elif any(abs(float(a) - b) > 1e-8 for a, b in zip(vector, VECTORS[kind])):
            errors.append(f"{prefix}.target_vector contradicts the local X2 convention")
        if value.get("scene_id") != "E3":
            errors.append(f"{prefix}: spatial intents are only allowed in E3 in this corpus")
        trigger_l = trigger.casefold()
        direction_word = {"POINT_RIGHT": "derech", "POINT_LEFT": "izquierd", "POINT_FRONT": "frente"}[kind]
        if direction_word not in trigger_l:
            errors.append(f"{prefix}.trigger_text does not literally name its declared direction")
        # Conservative POV/negation/quotation guard: do not silently reinterpret.
        if any(x in trigger_l for x in ("tu derecha", "su derecha", "tu izquierda", "su izquierda")):
            errors.append(f"{prefix}: visitor-relative or ambiguous perspective is rejected")
        local_cue = {
            "POINT_RIGHT": "mi derecha" in trigger_l,
            "POINT_LEFT": "mi izquierda" in trigger_l,
            "POINT_FRONT": any(x in trigger_l for x in ("frente a mí", "frente a mi", "delante de mí", "delante de mi", "frente al x2", "delante del x2")),
        }[kind]
        if not local_cue:
            errors.append(f"{prefix}: trigger lacks an explicit X2-local first-person reference")
        start = normalized_speech.find(trigger.casefold()) if trigger else -1
        if start >= 0:
            left_context = normalized_speech[max(0, start - 48):start]
            if any(mark in left_context for mark in (" no ", " nunca ", " no queda", " no está")):
                errors.append(f"{prefix}: trigger appears under a nearby negation")
            before = speech[:speech.find(trigger)]
            quote_open = max(before.rfind("«"), before.rfind("“"), before.rfind("‘"))
            quote_close = max(before.rfind("»"), before.rfind("”"), before.rfind("’"))
            if quote_open > quote_close or before.count('"') % 2:
                errors.append(f"{prefix}: a quoted mention cannot activate a motor action")
    return errors


def intents_as_class(value: dict[str, Any] | None) -> str:
    intents = (value or {}).get("motor_intents", [])
    return intents[0].get("type", "INVALID") if len(intents) == 1 else ("NONE" if not intents else "MULTIPLE")
