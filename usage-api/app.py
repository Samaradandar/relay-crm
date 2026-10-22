# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Small read-only service the voice platform exposes (or lets us deploy) so the CRM can pull
usage. Authenticated with a shared secret header (`X-Usage-Secret`); put it behind mTLS/VPN in
production as the PRD prefers. Built in: Phase 4.

GET /usage?since=<cursor>&limit=1000 -> {"records": [...], "next_cursor": "<cursor>|null"}
each record: {source_record_id, voice_customer_id, occurred_at, minutes, revision}

The cursor is an opaque string; this reference implementation uses a zero-padded integer offset,
ordered by an always-increasing internal id, which is what the CRM side relies on. The backing
store here is a simple in-memory/SQLite table (`records`) that the voice platform team replaces
with their own usage data, keeping this same contract.
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

DB_PATH = Path(os.environ.get("USAGE_API_DB", "usage_api.db"))
SHARED_SECRET = os.environ.get("USAGE_API_SECRET", "dev-secret-change-me")

app = FastAPI(title="usage-api")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, source_record_id TEXT UNIQUE NOT NULL, "
            "voice_customer_id TEXT NOT NULL, occurred_at TEXT NOT NULL, "
            "minutes TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1)"
        )
        yield conn
        conn.commit()
    finally:
        conn.close()


def _check_secret(secret: str | None) -> None:
    if secret != SHARED_SECRET:
        raise HTTPException(status_code=401, detail="bad or missing X-Usage-Secret")


@app.get("/usage")
def get_usage(
    since: str | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    x_usage_secret: str | None = Header(default=None),
) -> dict:
    _check_secret(x_usage_secret)
    cursor_id = int(since) if since else 0
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, source_record_id, voice_customer_id, occurred_at, minutes, revision "
            "FROM records WHERE id > ? ORDER BY id LIMIT ?",
            (cursor_id, limit),
        ).fetchall()
    records = [
        {
            "source_record_id": r[1],
            "voice_customer_id": r[2],
            "occurred_at": r[3],
            "minutes": r[4],
            "revision": r[5],
        }
        for r in rows
    ]
    next_cursor = str(rows[-1][0]) if rows else since
    return {"records": records, "next_cursor": next_cursor}


@app.post("/usage", status_code=201)
def post_usage(record: dict, x_usage_secret: str | None = Header(default=None)) -> dict:
    """Not part of the PRD contract; a convenience for demos and tests to seed data."""
    _check_secret(x_usage_secret)
    with _conn() as conn:
        conn.execute(
            "INSERT INTO records (source_record_id, voice_customer_id, occurred_at, minutes, "
            "revision) VALUES (?,?,?,?,?) "
            "ON CONFLICT(source_record_id) DO UPDATE SET minutes=excluded.minutes, "
            "revision=excluded.revision",
            (
                record["source_record_id"],
                record["voice_customer_id"],
                record["occurred_at"],
                str(record["minutes"]),
                record.get("revision", 1),
            ),
        )
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
