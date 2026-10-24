from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from llm.api.app.memory import Store


def _secure(path: Path) -> None:
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.chmod(0o600)
        except FileNotFoundError:
            pass


class ConversationMemory:
    """Uses the existing LLM Local RAG Memory Store; only agent turns are saved."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.path = path
        self.store = Store(str(path))
        _secure(path)

    def history(self, session_id: str) -> list[dict[str, str]]:
        return [
            {"role": "assistant", "content": row["content"]}
            for row in self.store.history(session_id)
            if row["role"] == "assistant" and row["content"].strip()
        ]

    def append_spoken_agent_turn(self, session_id: str, speech: str) -> None:
        self.store.add_message(session_id, "assistant", speech)
        _secure(self.path)

    def close(self) -> None:
        self.store.close()


class ExperimentJournal:
    """Separate HRIEGO run/turn audit DB; never stores facial embeddings or frames."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS runs(
      run_id TEXT PRIMARY KEY, visit_id TEXT NOT NULL, session_id TEXT NOT NULL,
      person_id TEXT NOT NULL, rank_code TEXT, scenario_id TEXT NOT NULL,
      started_at REAL NOT NULL, completed_at REAL, status TEXT NOT NULL,
      error_class TEXT
    );
    CREATE TABLE IF NOT EXISTS events(
      event_seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
      run_id TEXT NOT NULL, visit_id TEXT NOT NULL, session_id TEXT NOT NULL,
      turn_id TEXT, state TEXT NOT NULL, component TEXT NOT NULL, status TEXT NOT NULL,
      at_unix REAL NOT NULL, duration_ms REAL, details_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_hriego_events_run ON events(run_id,event_seq);
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.path = path
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(self.SCHEMA)
        self.db.commit()
        _secure(path)

    def begin_run(self, *, run_id: str, visit_id: str, session_id: str,
                  person_id: str, rank_code: str | None, scenario_id: str) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO runs(run_id,visit_id,session_id,person_id,rank_code,scenario_id,started_at,status) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (run_id, visit_id, session_id, person_id, rank_code, scenario_id, time.time(), "running"),
            )
            self.db.commit()
            _secure(self.path)

    def event(self, *, event_id: str, run_id: str, visit_id: str, session_id: str,
              turn_id: str | None, state: str, component: str, status: str,
              duration_ms: float | None = None, details: dict[str, Any] | None = None) -> None:
        encoded = json.dumps(details or {}, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            self.db.execute(
                "INSERT INTO events(event_id,run_id,visit_id,session_id,turn_id,state,component,status,at_unix,duration_ms,details_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, run_id, visit_id, session_id, turn_id, state, component, status,
                 time.time(), duration_ms, encoded),
            )
            self.db.commit()
            _secure(self.path)

    def finish_run(self, run_id: str, status: str, error_class: str | None = None) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE runs SET completed_at=?,status=?,error_class=? WHERE run_id=?",
                (time.time(), status, error_class, run_id),
            )
            self.db.commit()
            _secure(self.path)

    def close(self) -> None:
        with self.lock:
            self.db.close()
