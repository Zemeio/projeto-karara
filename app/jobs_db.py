"""Tiny SQLite-backed job queue shared by app.py (producer) and worker.py (consumer)."""
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    original_filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    session_id TEXT,
    entries_count INTEGER,
    error_message TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
"""


class JobsDB:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def pending_and_processing_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE status IN ('pending', 'processing')"
            ).fetchone()
            return row["n"]

    def enqueue(self, original_filename: str, stored_path: str) -> str:
        job_id = uuid.uuid4().hex
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO jobs (id, original_filename, stored_path, status, created_at, updated_at) "
                "VALUES (?, ?, ?, 'pending', ?, ?)",
                (job_id, original_filename, stored_path, now, now),
            )
        return job_id

    def claim_next(self):
        """Atomically pick the oldest pending job and mark it processing. Returns a dict or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'pending' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE jobs SET status = 'processing', updated_at = ? WHERE id = ?",
                (time.time(), row["id"]),
            )
            return dict(row)

    def mark_done(self, job_id: str, session_id: str, entries_count: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'done', session_id = ?, entries_count = ?, updated_at = ? WHERE id = ?",
                (session_id, entries_count, time.time(), job_id),
            )

    def mark_error(self, job_id: str, message: str):
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'error', error_message = ?, updated_at = ? WHERE id = ?",
                (message[:2000], time.time(), job_id),
            )

    def get(self, job_id: str):
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def recent(self, limit: int = 50):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
