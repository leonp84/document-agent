"""Job state management for the DocAssist async API pipeline."""
import sqlite3
import time
from pathlib import Path

_DEFAULT_DB = Path(__file__).parent.parent / "data" / "docassist.db"


def _get_conn(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init_jobs_db(db_path: Path = _DEFAULT_DB) -> None:
    """Create the jobs table if it doesn't exist."""
    conn = _get_conn(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                request_id   TEXT PRIMARY KEY,
                status       TEXT NOT NULL,
                quote_json   TEXT,
                pdf_bytes    BLOB,
                error        TEXT,
                created_at   REAL NOT NULL,
                updated_at   REAL NOT NULL
            )
        """)
    conn.close()


def create_job(request_id: str, db_path: Path = _DEFAULT_DB) -> None:
    """Insert a new job with status='pending'."""
    now = time.time()
    conn = _get_conn(db_path)
    with conn:
        conn.execute(
            "INSERT INTO jobs (request_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (request_id, "pending", now, now),
        )
    conn.close()


def get_job(request_id: str, db_path: Path = _DEFAULT_DB) -> dict | None:
    """Return the job row as a dict, or None if not found."""
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE request_id = ?", (request_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_job(
    request_id: str,
    *,
    status: str | None = None,
    quote_json: str | None = None,
    pdf_bytes: bytes | None = None,
    error: str | None = None,
    db_path: Path = _DEFAULT_DB,
) -> None:
    """Update job fields. Only non-None fields are written."""
    fields: list[str] = ["updated_at = ?"]
    values: list = [time.time()]
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if quote_json is not None:
        fields.append("quote_json = ?")
        values.append(quote_json)
    if pdf_bytes is not None:
        fields.append("pdf_bytes = ?")
        values.append(pdf_bytes)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    values.append(request_id)
    conn = _get_conn(db_path)
    with conn:
        conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE request_id = ?",
            values,
        )
    conn.close()
