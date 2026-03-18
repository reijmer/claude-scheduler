import sqlite3
from datetime import datetime

from .config import DB_PATH, ensure_dirs
from .models import Job, Run

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL,
    prompt      TEXT NOT NULL,
    directory   TEXT NOT NULL,
    schedule    TEXT NOT NULL,
    model       TEXT,
    skip_perms  INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    exit_code   INTEGER,
    cost_usd    REAL,
    duration_ms INTEGER,
    output_file TEXT NOT NULL,
    session_id  TEXT,
    error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_job_id ON runs(job_id);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);
"""


def get_connection() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# -- Job CRUD --


def add_job(
    name: str,
    prompt: str,
    directory: str,
    schedule: str,
    model: str | None = None,
    skip_perms: bool = False,
) -> Job:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO jobs (name, prompt, directory, schedule, model, skip_perms)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (name, prompt, directory, schedule, model, int(skip_perms)),
        )
        conn.commit()
        return get_job_by_name(name, conn=conn)  # type: ignore[return-value]
    finally:
        conn.close()


def get_job_by_name(name: str, conn: sqlite3.Connection | None = None) -> Job | None:
    close = False
    if conn is None:
        conn = get_connection()
        close = True
    try:
        row = conn.execute("SELECT * FROM jobs WHERE name = ?", (name,)).fetchone()
        return Job.from_row(dict(row)) if row else None
    finally:
        if close:
            conn.close()


def get_job_by_id(job_id: int) -> Job | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return Job.from_row(dict(row)) if row else None
    finally:
        conn.close()


def list_jobs() -> list[Job]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM jobs ORDER BY name").fetchall()
        return [Job.from_row(dict(r)) for r in rows]
    finally:
        conn.close()


def update_job(name: str, **kwargs: object) -> Job | None:
    allowed = {"prompt", "directory", "schedule", "model", "skip_perms", "enabled"}
    fields = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not fields:
        return get_job_by_name(name)
    if "skip_perms" in fields:
        fields["skip_perms"] = int(fields["skip_perms"])  # type: ignore[arg-type]
    if "enabled" in fields:
        fields["enabled"] = int(fields["enabled"])  # type: ignore[arg-type]
    fields["updated_at"] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [name]
    conn = get_connection()
    try:
        conn.execute(f"UPDATE jobs SET {set_clause} WHERE name = ?", values)
        conn.commit()
        return get_job_by_name(name, conn=conn)
    finally:
        conn.close()


def delete_job(name: str) -> bool:
    conn = get_connection()
    try:
        cursor = conn.execute("DELETE FROM jobs WHERE name = ?", (name,))
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# -- Run CRUD --


def insert_run(
    job_id: int,
    started_at: str,
    output_file: str,
    finished_at: str | None = None,
    exit_code: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
    error: str | None = None,
) -> int:
    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO runs (job_id, started_at, finished_at, exit_code, cost_usd,
                                duration_ms, output_file, session_id, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, started_at, finished_at, exit_code, cost_usd, duration_ms, output_file, session_id, error),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]
    finally:
        conn.close()


def update_run(
    run_id: int,
    finished_at: str | None = None,
    exit_code: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
    error: str | None = None,
) -> None:
    fields: dict[str, object] = {}
    if finished_at is not None:
        fields["finished_at"] = finished_at
    if exit_code is not None:
        fields["exit_code"] = exit_code
    if cost_usd is not None:
        fields["cost_usd"] = cost_usd
    if duration_ms is not None:
        fields["duration_ms"] = duration_ms
    if session_id is not None:
        fields["session_id"] = session_id
    if error is not None:
        fields["error"] = error
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [run_id]
    conn = get_connection()
    try:
        conn.execute(f"UPDATE runs SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def get_runs_for_job(job_id: int, limit: int = 20) -> list[Run]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM runs WHERE job_id = ? ORDER BY started_at DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
        return [Run.from_row(dict(r)) for r in rows]
    finally:
        conn.close()


def get_run_by_id(run_id: int) -> Run | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return Run.from_row(dict(row)) if row else None
    finally:
        conn.close()


def get_latest_run(job_id: int) -> Run | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE job_id = ? ORDER BY started_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return Run.from_row(dict(row)) if row else None
    finally:
        conn.close()
