"""Low-rate, background SQLite lookups for confirmed live tracks."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .identity_registry import IdentityRegistry, REGISTRY_PATH


class ProfileService:
    """Fetch many confirmed profiles in one query, away from the GUI thread."""

    def __init__(
        self,
        registry_path: Path = REGISTRY_PATH,
        *,
        poll_interval: float = 0.5,
        lost_display_seconds: float = 2.0,
    ) -> None:
        self.registry = IdentityRegistry(registry_path)
        self.poll_interval = max(0.1, float(poll_interval))
        self.lost_display_seconds = max(0.0, float(lost_display_seconds))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._tracks: list[dict] = []
        self._sequence = 0
        self._snapshot: dict = {"cards": [], "query_latency_ms": 0.0, "status": "CONNECTING"}
        self._thread = threading.Thread(target=self._run, name="sqlite-profile-reader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def submit(self, tracks: list[dict]) -> None:
        safe_tracks = []
        for track in tracks:
            identity = track.get("identity") or {}
            safe_tracks.append({
                "track_id": int(track["track_id"]),
                "state": str(identity.get("state", "UNCONFIRMED")),
                "person_id": identity.get("person_id") if identity.get("state") == "CONFIRMED" else None,
                "last_seen_at": float(identity.get("last_seen_at") or 0.0),
                "seen_now": bool(track.get("seen_now", False)),
            })
        with self._lock:
            self._tracks = safe_tracks
            self._sequence += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "cards": [dict(card) for card in self._snapshot["cards"]],
                "query_latency_ms": self._snapshot["query_latency_ms"],
                "status": self._snapshot["status"],
                "updated_at": self._snapshot.get("updated_at"),
            }

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                tracks = [dict(track) for track in self._tracks]
            confirmed_ids = {
                track["person_id"]
                for track in tracks
                if track["state"] == "CONFIRMED" and track["person_id"]
            }
            started = time.perf_counter()
            try:
                profiles = self.registry.get_profiles(confirmed_ids)
                status = "OK"
            except Exception as exc:
                profiles = {}
                status = f"ERROR: {type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - started) * 1000.0
            cards = self._build_cards(tracks, profiles, time.monotonic())
            with self._lock:
                self._snapshot = {
                    "cards": cards,
                    "query_latency_ms": latency_ms,
                    "status": status,
                    "updated_at": time.monotonic(),
                }
            self._stop.wait(self.poll_interval)

    def _build_cards(self, tracks: list[dict], profiles: dict, now: float) -> list[dict]:
        cards = []
        for track in tracks:
            last_seen_at = track["last_seen_at"]
            if last_seen_at <= 0.0:
                continue
            age = max(0.0, now - last_seen_at)
            if age > self.lost_display_seconds:
                continue
            state = track["state"]
            person_id = track["person_id"] if state == "CONFIRMED" else None
            profile = profiles.get(person_id) if person_id else None
            if profile and profile["person_status"] == "active":
                name = profile["display_name"]
                rank_code = profile["rank_code"]
                rank_name = profile["rank_name"]
                shown_person_id = person_id
                card_state = "CONFIRMED"
            elif state == "CONFIRMED":
                # A database/profile failure must never leave a stale or guessed rank on-screen.
                name = "PROFILE UNAVAILABLE"
                rank_code = rank_name = shown_person_id = None
                card_state = "PROFILE UNAVAILABLE"
            elif state in {"SUSPENDED", "LOST"}:
                name = "VERIFY" if state == "SUSPENDED" else "LOST"
                rank_code = rank_name = shown_person_id = None
                card_state = state
            else:
                name = "UNKNOWN"
                rank_code = rank_name = shown_person_id = None
                card_state = state if state in {"TENTATIVE", "UNCONFIRMED"} else "UNCONFIRMED"
            cards.append({
                "track_id": track["track_id"],
                "person_id": shown_person_id,
                "display_name": name,
                "rank_code": rank_code,
                "rank_name": rank_name,
                "identity_status": card_state,
                "last_seen_seconds": age,
                "seen_now": track["seen_now"],
            })
        cards.sort(key=lambda card: (
            0 if card["identity_status"] == "CONFIRMED" else 1,
            card["display_name"],
            card["track_id"],
        ))
        return cards
