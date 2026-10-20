"""SQLite helpers."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path=None) -> sqlite3.Connection:
    """Open (and create/upgrade) the database. Use ':memory:' for tests."""
    path = path if path is not None else config.DB_PATH
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, None if value is None else str(value)),
    )
