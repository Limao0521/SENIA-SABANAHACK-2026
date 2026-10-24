"""Memoria persistente en SQLite: sesiones, mensajes, resumen rodante y hechos.

Diseño pensado para la KV cache de llama-server: el historial es append-only y
de orden estable; cuando la sesión crece demasiado, los mensajes más antiguos
se comprimen en un resumen anclado tras el system prompt (``summarized_upto``
marca el corte). El prefijo system+resumen+historial solo cambia en el rollup,
así el servidor reutiliza la cache entre turnos y el TTFT se mantiene mínimo.

En la misma BD viven las tablas del RAG (chunks) y los hechos de largo plazo
(con su embedding), para que TODO el estado quepa en un volumen (./data).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id TEXT PRIMARY KEY,
  created REAL NOT NULL,
  updated REAL NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  summarized_upto INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);
CREATE TABLE IF NOT EXISTS facts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  content TEXT NOT NULL UNIQUE,
  embedding BLOB,
  ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE,
  embedding BLOB,
  ts REAL NOT NULL
);
"""


class Store:
    """Acceso a SQLite seguro entre hilos (requests + worker de fondo)."""

    def __init__(self, path: str = ":memory:") -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # Versión de datos: el RAG la usa para invalidar su matriz en caché
        self.data_version = 0
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    def _bump(self) -> None:
        self.data_version += 1

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # --- sesiones ----------------------------------------------------------------
    def touch_session(self, sid: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        with self.lock:
            self.conn.execute(
                "INSERT INTO sessions(id, created, updated) VALUES(?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated = excluded.updated",
                (sid, now, now),
            )
            self.conn.commit()

    def get_session(self, sid: str) -> dict | None:
        with self.lock:
            row = self.conn.execute("SELECT * FROM sessions WHERE id = ?", (sid,)).fetchone()
        if row is None:
            return None
        s = dict(row)
        s["active_messages"] = self.count_active(sid)
        return s

    def list_sessions(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT s.*, (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id "
                "AND m.id > s.summarized_upto) AS active_messages "
                "FROM sessions s ORDER BY s.updated DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_session(self, sid: str) -> bool:
        with self.lock:
            cur = self.conn.execute("DELETE FROM sessions WHERE id = ?", (sid,))
            self.conn.execute("DELETE FROM messages WHERE session_id = ?", (sid,))
            self.conn.commit()
            return cur.rowcount > 0

    # --- mensajes ------------------------------------------------------------------
    def add_message(self, sid: str, role: str, content: str) -> None:
        now = time.time()
        with self.lock:
            self.touch_session(sid, now)
            self.conn.execute(
                "INSERT INTO messages(session_id, role, content, ts) VALUES(?, ?, ?, ?)",
                (sid, role, content, now),
            )
            self.conn.commit()

    def history(self, sid: str) -> list[dict]:
        """Mensajes activos (posteriores al corte del último resumen), en orden."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT m.id, m.role, m.content FROM messages m "
                "JOIN sessions s ON s.id = m.session_id "
                "WHERE m.session_id = ? AND m.id > s.summarized_upto ORDER BY m.id",
                (sid,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_active(self, sid: str) -> int:
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM messages m JOIN sessions s ON s.id = m.session_id "
                "WHERE m.session_id = ? AND m.id > s.summarized_upto",
                (sid,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def get_summary(self, sid: str) -> str:
        with self.lock:
            row = self.conn.execute("SELECT summary FROM sessions WHERE id = ?", (sid,)).fetchone()
        return row["summary"] if row else ""

    def set_summary(self, sid: str, summary: str, upto_id: int) -> None:
        with self.lock:
            self.touch_session(sid)
            self.conn.execute(
                "UPDATE sessions SET summary = ?, summarized_upto = MAX(summarized_upto, ?), "
                "updated = ? WHERE id = ?",
                (summary, upto_id, time.time(), sid),
            )
            self.conn.commit()

    # --- hechos de largo plazo -------------------------------------------------------
    def add_fact(self, content: str, embedding: bytes | None) -> bool:
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO facts(content, embedding, ts) VALUES(?, ?, ?)",
                (content.strip(), embedding, time.time()),
            )
            self.conn.commit()
            if cur.rowcount > 0:
                self._bump()
                return True
            return False

    def all_facts(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT id, content, embedding FROM facts ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def delete_facts(self) -> int:
        with self.lock:
            cur = self.conn.execute("DELETE FROM facts")
            self.conn.commit()
            self._bump()
            return cur.rowcount

    # --- chunks del RAG ----------------------------------------------------------------
    def insert_chunk(self, source: str, idx: int, text: str, hash_: str) -> int | None:
        """Devuelve el id si el chunk es nuevo; None si ya existía (idempotente)."""
        with self.lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO chunks(source, idx, text, hash, ts) VALUES(?, ?, ?, ?, ?)",
                (source, idx, text, hash_, time.time()),
            )
            self.conn.commit()
            if cur.rowcount > 0 and cur.lastrowid is not None:
                self._bump()
                return int(cur.lastrowid)
            return None

    def set_chunk_embedding(self, chunk_id: int, blob: bytes) -> None:
        with self.lock:
            self.conn.execute("UPDATE chunks SET embedding = ? WHERE id = ?", (blob, chunk_id))
            self.conn.commit()
            self._bump()

    def all_chunks(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT id, source, text, embedding FROM chunks ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def chunks_missing_embedding(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, text FROM chunks WHERE embedding IS NULL ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def chunk_count(self) -> int:
        with self.lock:
            row = self.conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return int(row["n"]) if row else 0

    def chunk_sources(self) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT source, COUNT(*) AS chunks FROM chunks GROUP BY source ORDER BY source"
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_chunks(self) -> int:
        with self.lock:
            cur = self.conn.execute("DELETE FROM chunks")
            self.conn.commit()
            self._bump()
            return cur.rowcount
