"""Supertonic M1 via hriego_v2.SupertonicTTSRunner (worker supertonic_worker.py). DIALOGUE uses GUIDANCE prosody (speed 1.0)."""
from __future__ import annotations

import hashlib
import wave
from pathlib import Path

from hriego_v2.v2_workers import SupertonicTTSRunner

from .paths import rel

WORKER_STATE = {"GREETING": "GREETING", "GUIDANCE": "GUIDANCE", "DIALOGUE": "GUIDANCE", "FAREWELL": "FAREWELL"}


class GestureTTS:
    def __init__(self, settings, audio_root: Path):
        self.settings = settings
        settings.audio_root = Path(audio_root)
        self.runner = SupertonicTTSRunner(settings)

    def synth(self, *, speech, state, turn_id, visit_id):
        out = self.runner.synthesize(speech=speech, state=WORKER_STATE[state], turn_id=turn_id, visit_id=visit_id)
        path = Path(out.value["path"]) if hasattr(out, "value") else Path(out[0]["path"])
        m = out.metrics if hasattr(out, "metrics") else out[1]
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"wav_path": rel(path),  # relative to the HRIEGO_gesture root (see paths.py)
                "wav_sha256": sha, "sample_rate": m["sample_rate"],
                "duration_s": m["duration_s"], "synth_s": m["synthesis_s"]}

    def close(self):
        self.runner.close()
