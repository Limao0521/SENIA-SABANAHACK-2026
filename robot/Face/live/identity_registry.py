"""Local relational registry for institutional identities and face templates.

Biometric templates and administrative profiles share a stable ``person_id``
but remain in separate tables. This module never derives rank from a face,
embedding, or tracker ID.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "institutional_identity.sqlite3"
REGISTRY_RANKS = {
    "VIP": "VIP members",
    "REGULAR": "regular members",
    "GUEST": "invited guests",
    "STAFF": "Club staff",
    "EXTERNAL": "external workers",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _secure_files(path: Path) -> None:
    """Keep the live registry and SQLite sidecars private to the current user."""
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        try:
            candidate.chmod(0o600)
        except FileNotFoundError:
            pass


def _schema(db: sqlite3.Connection) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS registry_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS persons (
            person_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'inactive')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS face_templates (
            template_id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
            encoder_key TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL CHECK (embedding_dim > 0),
            embedding BLOB NOT NULL,
            source_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (person_id, encoder_key),
            CHECK (length(embedding) = embedding_dim * 4)
        );
        CREATE INDEX IF NOT EXISTS idx_face_templates_encoder
            ON face_templates(encoder_key, person_id);

        CREATE TABLE IF NOT EXISTS ranks (
            rank_code TEXT PRIMARY KEY,
            display_name TEXT NOT NULL UNIQUE,
            active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS person_ranks (
            person_id TEXT PRIMARY KEY REFERENCES persons(person_id) ON DELETE RESTRICT,
            rank_code TEXT NOT NULL REFERENCES ranks(rank_code) ON DELETE RESTRICT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rank_change_log (
            change_id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT NOT NULL REFERENCES persons(person_id) ON DELETE RESTRICT,
            old_rank_code TEXT REFERENCES ranks(rank_code) ON DELETE RESTRICT,
            new_rank_code TEXT NOT NULL REFERENCES ranks(rank_code) ON DELETE RESTRICT,
            changed_at TEXT NOT NULL,
            changed_by TEXT NOT NULL
        );
        """
    )
    db.execute(
        "INSERT INTO registry_meta(key, value) VALUES ('schema_version', '1') "
        "ON CONFLICT(key) DO NOTHING"
    )


class IdentityRegistry:
    """Thread-safe-by-connection SQLite access for the operational registry."""

    def __init__(self, path: Path = REGISTRY_PATH, *, timeout: float = 5.0) -> None:
        self.path = Path(path)
        self.timeout = timeout

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        if readonly:
            uri = f"file:{self.path.resolve()}?mode=ro"
            db = sqlite3.connect(uri, uri=True, timeout=self.timeout)
            db.execute("PRAGMA query_only=ON")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=self.timeout)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")
        return db

    def initialize_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            _schema(db)
        _secure_files(self.path)

    def load_gallery(self, encoder_key: str) -> dict[str, dict]:
        """Load one immutable-in-memory snapshot for a specific encoder."""
        with self.connect(readonly=True) as db:
            rows = db.execute(
                """SELECT p.person_id, p.display_name, t.embedding_dim, t.embedding
                   FROM persons AS p
                   JOIN face_templates AS t ON t.person_id=p.person_id
                   WHERE p.status='active' AND t.encoder_key=?
                   ORDER BY p.person_id""",
                (encoder_key,),
            ).fetchall()
        gallery: dict[str, dict] = {}
        for row in rows:
            vector = np.frombuffer(row["embedding"], dtype=np.float32).copy()
            if (
                vector.shape != (row["embedding_dim"],)
                or vector.shape != (512,)
                or not np.isfinite(vector).all()
            ):
                raise ValueError(f"Invalid {row['embedding_dim']}-D template for {row['person_id']}")
            norm = float(np.linalg.norm(vector))
            if norm <= 0.0 or not np.isclose(norm, 1.0, atol=1e-3):
                raise ValueError(f"Template for {row['person_id']} is not L2-normalized: {norm}")
            gallery[row["person_id"]] = {
                "display_name": row["display_name"],
                "embedding": vector,
            }
        return gallery

    def active_encoder_key(self) -> str:
        with self.connect(readonly=True) as db:
            row = db.execute(
                "SELECT value FROM registry_meta WHERE key='active_encoder_key'"
            ).fetchone()
        if row is None:
            raise ValueError("Registry has no active encoder; run the controlled initialization first")
        return str(row["value"])

    def get_profiles(self, person_ids: set[str] | list[str] | tuple[str, ...]) -> dict[str, dict]:
        ids = sorted(set(person_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.connect(readonly=True) as db:
            rows = db.execute(
                f"""SELECT p.person_id, p.display_name, p.status,
                           pr.rank_code, r.display_name AS rank_name, pr.updated_at
                    FROM persons AS p
                    JOIN person_ranks AS pr ON pr.person_id=p.person_id
                    JOIN ranks AS r ON r.rank_code=pr.rank_code
                    WHERE p.person_id IN ({placeholders})""",
                ids,
            ).fetchall()
        return {
            row["person_id"]: {
                "person_id": row["person_id"],
                "display_name": row["display_name"],
                "person_status": row["status"],
                "rank_code": row["rank_code"],
                "rank_name": row["rank_name"],
                "rank_updated_at": row["updated_at"],
            }
            for row in rows
        }

    def list_people(self) -> list[dict]:
        with self.connect(readonly=True) as db:
            rows = db.execute(
                """SELECT p.person_id, p.display_name, p.status,
                          pr.rank_code, r.display_name AS rank_name, pr.updated_at
                   FROM persons AS p
                   LEFT JOIN person_ranks AS pr ON pr.person_id=p.person_id
                   LEFT JOIN ranks AS r ON r.rank_code=pr.rank_code
                   ORDER BY p.person_id"""
            ).fetchall()
        return [dict(row) for row in rows]

    def get_person(self, person_id: str) -> dict | None:
        return self.get_profiles({person_id}).get(person_id)

    def set_person_rank(self, person_id: str, rank_code: str, *, changed_by: str) -> dict:
        """Atomically change one current rank and append its audit record."""
        if not changed_by.strip():
            raise ValueError("changed_by must not be empty")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            person = db.execute(
                "SELECT person_id FROM persons WHERE person_id=?", (person_id,)
            ).fetchone()
            if person is None:
                raise KeyError(f"Unknown person_id: {person_id}")
            rank = db.execute(
                "SELECT rank_code FROM ranks WHERE rank_code=? AND active=1", (rank_code,)
            ).fetchone()
            if rank is None:
                raise KeyError(f"Unknown or inactive rank_code: {rank_code}")
            previous = db.execute(
                "SELECT rank_code FROM person_ranks WHERE person_id=?", (person_id,)
            ).fetchone()
            old_rank = previous["rank_code"] if previous else None
            if old_rank == rank_code:
                db.rollback()
                return {"person_id": person_id, "old_rank_code": old_rank, "new_rank_code": rank_code, "changed": False}
            changed_at = utc_now()
            db.execute(
                """INSERT INTO person_ranks(person_id, rank_code, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(person_id) DO UPDATE SET
                     rank_code=excluded.rank_code, updated_at=excluded.updated_at""",
                (person_id, rank_code, changed_at),
            )
            db.execute(
                """INSERT INTO rank_change_log
                   (person_id, old_rank_code, new_rank_code, changed_at, changed_by)
                   VALUES (?, ?, ?, ?, ?)""",
                (person_id, old_rank, rank_code, changed_at, changed_by.strip()),
            )
        _secure_files(self.path)
        return {"person_id": person_id, "old_rank_code": old_rank, "new_rank_code": rank_code, "changed": True}

    def next_person_id(self) -> str:
        with self.connect(readonly=True) as db:
            ids = [row[0] for row in db.execute("SELECT person_id FROM persons")]
        numbers = [int(i[1:]) for i in ids if i[:1] == "P" and i[1:].isdigit()]
        return f"P{max(numbers, default=0) + 1:03d}"

    def enroll_person(self, person_id: str, display_name: str, rank_code: str,
                      embedding: np.ndarray, source_sha256: str, *, changed_by: str) -> dict:
        """Add one person with their face template and rank in a single transaction."""
        if not display_name.strip() or not changed_by.strip():
            raise ValueError("display_name and changed_by must not be empty")
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if vector.shape != (512,) or not np.isfinite(vector).all():
            raise ValueError(f"Expected a finite 512-D embedding, got {vector.shape}")
        vector = vector / float(np.linalg.norm(vector))
        encoder_key = self.active_encoder_key()
        created_at = utc_now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM persons WHERE person_id=?", (person_id,)).fetchone():
                raise KeyError(f"person_id already exists: {person_id}")
            if db.execute("SELECT 1 FROM ranks WHERE rank_code=? AND active=1", (rank_code,)).fetchone() is None:
                raise KeyError(f"Unknown or inactive rank_code: {rank_code}")
            db.execute(
                "INSERT INTO persons(person_id, display_name, status, created_at) VALUES (?, ?, 'active', ?)",
                (person_id, display_name.strip(), created_at),
            )
            db.execute(
                """INSERT INTO face_templates
                   (person_id, encoder_key, embedding_dim, embedding, source_sha256, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (person_id, encoder_key, 512, vector.tobytes(), source_sha256, created_at),
            )
            db.execute(
                "INSERT INTO person_ranks(person_id, rank_code, updated_at) VALUES (?, ?, ?)",
                (person_id, rank_code, created_at),
            )
            db.execute(
                """INSERT INTO rank_change_log
                   (person_id, old_rank_code, new_rank_code, changed_at, changed_by)
                   VALUES (?, NULL, ?, ?, ?)""",
                (person_id, rank_code, created_at, changed_by.strip()),
            )
        _secure_files(self.path)
        return {"person_id": person_id, "display_name": display_name.strip(),
                "rank_code": rank_code, "encoder_key": encoder_key, "created_at": created_at}

    def integrity_check(self) -> dict:
        with self.connect(readonly=True) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            foreign_key_errors = [tuple(row) for row in db.execute("PRAGMA foreign_key_check")]
            counts = {
                table: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("persons", "face_templates", "ranks", "person_ranks", "rank_change_log")
            }
        return {"integrity_check": integrity, "foreign_key_errors": foreign_key_errors, "counts": counts}


def vector_sha256(vector_blob: bytes) -> str:
    import hashlib

    return hashlib.sha256(vector_blob).hexdigest()


def secure_registry_permissions(path: Path = REGISTRY_PATH) -> None:
    """Public helper used after write transactions and CLI initialization."""
    _secure_files(Path(path))
