#!/usr/bin/env python3
"""Decision + aligned anchor + WAV identity -> x2_gesture_event.v1 (no schedule yet)."""
from .align_anchor import METHOD

ALIGNED = "aligned_ctc_not_human_validated"


def build_event(utterance_id, decision, span, wav_sha256, sample_rate_hz, sample_count, model_revision):
    """Returns (event|None, reason). Fails closed on any missing piece."""
    if not decision.get("emit_event") or not decision.get("effective"):
        return None, f"no_event:{decision.get('status')}"
    eff = decision["effective"]
    if eff["binding"] is None or eff["anchor"] is None:
        return None, "no_event:missing_binding_or_anchor"
    if span.get("status") != ALIGNED or span.get("start_sample") is None:
        return None, f"alignment:{span.get('status')}"
    if span["sha256"] != wav_sha256 or span.get("sample_rate_hz") != sample_rate_hz:
        return None, "alignment_belongs_to_other_wav"
    b = eff["binding"]
    return {
        "schema_version": "x2_gesture_event.v1",
        "utterance_id": utterance_id,
        "audio": {"sha256": wav_sha256, "sample_rate_hz": sample_rate_hz, "sample_count": sample_count,
                  "playback_clock": "audio_monotonic_start"},
        "decision": {"communicative_act": eff["communicative_act"], "motor_intent": eff["motor_intent"],
                     "decision_source": eff["decision_source"]},
        "binding": {"motion_id": b["motion_id"], "area": b["area"], "asset_sha256": b["asset_sha256"],
                    "animation_player_sha256": b["animation_player_sha256"]},
        "anchor": {"text": span["text"], "source_char_start": span["char_start"], "source_char_end": span["char_end"],
                   "occurrence": span["occurrence"], "start_sample": span["start_sample"], "end_sample": span["end_sample"]},
        "alignment": {"method": METHOD, "model_revision": model_revision, "status": ALIGNED},
        "schedule": {"desired_visible_onset_sample": span["start_sample"], "dispatch_sample": None,
                     "repeat_count": 1, "status": "unscheduled"},
    }, "ok"
