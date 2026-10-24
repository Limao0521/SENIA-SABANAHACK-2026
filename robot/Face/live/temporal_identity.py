"""Time-bounded identity confirmation and suspension policy for tracks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TrackIdentity:
    state: str = "UNCONFIRMED"
    person_id: str | None = None
    candidate_id: str | None = None
    candidate_hits: int = 0
    candidate_since: float = 0.0
    last_accepted_at: float = 0.0
    missing_since: float | None = None
    revalidation: bool = False
    first_observed_at: float = 0.0
    last_seen_at: float = 0.0
    first_confirmed_at: float | None = None
    identity_persistence_seconds: float = 0.0
    time_to_confirm_seconds: float | None = None


class TemporalIdentityManager:
    """Confirm after two matching accepted observations; expire by timestamps."""

    def __init__(
        self,
        *,
        required_hits: int = 2,
        confirmation_window_seconds: float = 2.0,
        persistence_seconds: float = 1.5,
        lost_timeout_seconds: float = 1.2,
    ) -> None:
        self.required_hits = required_hits
        self.confirmation_window_seconds = confirmation_window_seconds
        self.persistence_seconds = persistence_seconds
        self.lost_timeout_seconds = lost_timeout_seconds
        self.tracks: dict[int, TrackIdentity] = {}

    def reset(self) -> None:
        self.tracks.clear()

    def update(
        self,
        *,
        track_id: int,
        decision: dict | None,
        timestamp: float,
        ambiguous: bool = False,
    ) -> TrackIdentity:
        record = self.tracks.setdefault(track_id, TrackIdentity())
        if record.first_observed_at == 0.0:
            record.first_observed_at = timestamp
        record.last_seen_at = timestamp
        record.missing_since = None
        if ambiguous:
            record.state = "SUSPENDED"
            record.revalidation = True
            record.candidate_id = None
            record.candidate_hits = 0
            return record

        accepted_id = (
            decision.get("top1_id")
            if decision and decision.get("status") == "KNOWN"
            else None
        )
        if accepted_id is None:
            if record.state == "CONFIRMED":
                if timestamp - record.last_accepted_at > self.persistence_seconds:
                    record.state = "SUSPENDED"
                    record.revalidation = True
            elif record.state == "TENTATIVE" and timestamp - record.candidate_since > self.confirmation_window_seconds:
                record.state = "UNCONFIRMED"
                record.candidate_id = None
                record.candidate_hits = 0
            return record

        if record.state == "CONFIRMED" and accepted_id == record.person_id:
            if timestamp - record.last_accepted_at <= self.lost_timeout_seconds:
                record.identity_persistence_seconds = max(
                    record.identity_persistence_seconds, timestamp - (record.first_confirmed_at or timestamp)
                )
                record.last_accepted_at = timestamp
                record.candidate_id = None
                record.candidate_hits = 0
                record.revalidation = False
                return record
            record.state = "SUSPENDED"
            record.revalidation = True

        if record.candidate_id != accepted_id or timestamp - record.candidate_since > self.confirmation_window_seconds:
            record.candidate_id = accepted_id
            record.candidate_hits = 1
            record.candidate_since = timestamp
        else:
            record.candidate_hits += 1

        if record.state == "CONFIRMED" and accepted_id != record.person_id:
            record.state = "SUSPENDED"
            record.revalidation = True
        elif record.person_id is not None and record.person_id != accepted_id:
            record.state = "SUSPENDED"
            record.revalidation = True
        elif record.person_id is not None and record.revalidation:
            record.state = "SUSPENDED"
        else:
            record.state = "TENTATIVE"

        if record.candidate_hits >= self.required_hits:
            record.person_id = accepted_id
            record.state = "CONFIRMED"
            if record.first_confirmed_at is None:
                record.first_confirmed_at = timestamp
                record.time_to_confirm_seconds = max(0.0, timestamp - record.first_observed_at)
            record.last_accepted_at = timestamp
            record.revalidation = False
            record.candidate_id = None
            record.candidate_hits = 0
        return record

    def update_unseen(
        self,
        *,
        seen_track_ids: set[int],
        tracker_track_ids: set[int],
        timestamp: float,
    ) -> None:
        """Suspend unmatched tracks and release identity after a short loss timeout."""
        known_ids = set(self.tracks)
        for track_id in known_ids:
            record = self.tracks[track_id]
            if track_id in seen_track_ids:
                continue
            if record.missing_since is None:
                record.missing_since = timestamp
            record.state = "SUSPENDED"
            record.revalidation = True
            record.candidate_id = None
            record.candidate_hits = 0
            if timestamp - record.missing_since >= self.lost_timeout_seconds or track_id not in tracker_track_ids:
                record.state = "LOST"
                record.person_id = None
                record.candidate_id = None
                record.candidate_hits = 0

    def active_count(self) -> int:
        return sum(record.state != "LOST" for record in self.tracks.values())

    def confirmed_count(self) -> int:
        return sum(record.state == "CONFIRMED" for record in self.tracks.values())
