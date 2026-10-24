from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class State(str, Enum):
    IDLE = "IDLE"
    GREETING = "GREETING"
    GUIDANCE = "GUIDANCE"
    FAREWELL = "FAREWELL"


NEXT = {
    State.IDLE: State.GREETING,
    State.GREETING: State.GUIDANCE,
    State.GUIDANCE: State.FAREWELL,
    State.FAREWELL: State.IDLE,
}


@dataclass
class ReceptionFSM:
    state: State = State.IDLE
    transitions: int = 0

    def start(self) -> State:
        if self.state is not State.IDLE:
            raise RuntimeError(f"FSM cannot start while {self.state.value}")
        self.state = State.GREETING
        self.transitions += 1
        return self.state

    def complete_stage(self, *, output_valid: bool, audio_finished: bool) -> State:
        if self.state is State.IDLE:
            raise RuntimeError("IDLE has no active stage")
        if not output_valid or not audio_finished:
            raise RuntimeError("FSM transition requires validated output and completed playback")
        self.state = NEXT[self.state]
        self.transitions += 1
        return self.state

    def abort(self) -> State:
        if self.state is not State.IDLE:
            self.state = State.IDLE
            self.transitions += 1
        return self.state


class VisitGate:
    """Allows one visit per continuous presence; re-arms only after absence."""

    def __init__(self, absence_seconds: float = 3.0) -> None:
        self.absence_seconds = absence_seconds
        self.absent_since: float | None = None
        self.armed = True
        self.last_person_id: str | None = None

    def observe(self, *, face_count: int, now: float, camera_connected: bool = True) -> bool:
        if not camera_connected:
            # A broken camera is not evidence that the visitor exited.
            return False
        if face_count > 0:
            self.absent_since = None
            return False
        if self.absent_since is None:
            self.absent_since = now
            return False
        if not self.armed and now - self.absent_since >= self.absence_seconds:
            self.armed = True
            self.last_person_id = None
            return True
        return False

    def consume(self, person_id: str) -> None:
        if not self.armed:
            raise RuntimeError("visit gate is not armed; wait for a confirmed exit")
        self.last_person_id = person_id
        self.armed = False
