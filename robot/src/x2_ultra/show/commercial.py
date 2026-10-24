"""Replay the validated 19-line commercial pack on the Sabana stage.

The pack was recorded for X2 Ultra and X2 Original. The Sabana stage has
Ultra-N and EDU, so Original's compatible 14 arm joints are shown on EDU.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from x2_ultra.robot.joints import ARM_JOINTS, DEFAULT_POSE

PACK = Path(__file__).resolve().parents[3] / "HRIEGO_gesture/twin/replay/commercial"


@dataclass(frozen=True)
class CommercialLine:
    number: int
    robot: str
    start: float
    end: float
    text: str
    scene: str
    audio: Path
    arms: np.ndarray


class CommercialShow:
    """Wall-clock timeline; the UI owns audio and applies targets to the engine."""

    def __init__(self, pack: Path = PACK):
        timeline = json.loads((pack / "timeline_ultra.json").read_text(encoding="utf-8"))
        self.duration = float(timeline["C_END"])
        self.lines: list[CommercialLine] = []
        for entry in timeline["lines"]:
            clip = entry["clip"]
            motion = pack / "motion" / clip / f"{clip}_arms.npz"
            audio = pack / "audio" / f"{clip}.wav"
            with np.load(motion, allow_pickle=False) as data:
                names = tuple(str(name) for name in data["names"])
                if names != ARM_JOINTS:
                    raise ValueError(f"Orden de articulaciones inesperado: {motion}")
                arms = np.asarray(data["cmd"][100:], dtype=float).copy()
            if not audio.is_file() or arms.ndim != 2 or arms.shape[1] != 14 or not len(arms):
                raise ValueError(f"Pista comercial incompleta: {clip}")
            self.lines.append(CommercialLine(
                int(entry["n"]), "ultra" if entry["robot"] == "ultra" else "edu",
                float(entry["start"]), float(entry["end"]), entry["text"], entry["scene"], audio, arms))
        if len(self.lines) != 19 or [line.number for line in self.lines] != list(range(1, 20)):
            raise ValueError("El show comercial debe tener 19 pistas ordenadas")
        self._rest = np.array([DEFAULT_POSE[name] for name in ARM_JOINTS], dtype=float)
        self._next = 0

    def finished(self, elapsed: float) -> bool:
        return elapsed >= self.lines[-1].end

    def due(self, elapsed: float) -> list[CommercialLine]:
        events = []
        while self._next < len(self.lines) and elapsed >= self.lines[self._next].start:
            events.append(self.lines[self._next])
            self._next += 1
        return events

    def targets(self, elapsed: float) -> dict[str, np.ndarray]:
        # A robot only gestures during its own spoken line. The other robot
        # returns to rest, including the gaps between consecutive lines.
        targets = {"ultra": self._rest, "edu": self._rest}
        for line in self.lines:
            if elapsed < line.start:
                break
            if elapsed < line.end:
                sample = min(int((elapsed - line.start) * 50), len(line.arms) - 1)
                targets[line.robot] = line.arms[sample]
                break
        return targets
