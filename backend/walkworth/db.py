"""SQLite storage. Tables mirror schema.py; list and dict fields are JSON text.

Every write is keyed by the row's id, so re-running a pipeline step replaces
rows instead of duplicating them. New columns added to the DDL below are
added to existing databases on connect (see _migrate).
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from . import config

DDL = """
CREATE TABLE IF NOT EXISTS trek (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_mr TEXT,
    fort_pack_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    tz TEXT NOT NULL,
    screen_seconds INTEGER,
    model_versions TEXT NOT NULL DEFAULT '{}',
    is_sample INTEGER NOT NULL DEFAULT 0,
    time_offset_s INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS track_point (
    trek_id TEXT NOT NULL REFERENCES trek(id) ON DELETE CASCADE,
    t TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    ele_m REAL,
    PRIMARY KEY (trek_id, t)
);
CREATE TABLE IF NOT EXISTS voice_note (
    id TEXT PRIMARY KEY,
    trek_id TEXT NOT NULL REFERENCES trek(id) ON DELETE CASCADE,
    t TEXT NOT NULL,
    lat REAL,
    lon REAL,
    location_source TEXT NOT NULL DEFAULT 'fix',
    ele_m REAL,
    landmark_id TEXT,
    audio_path TEXT,
    duration_s REAL NOT NULL,
    transcript TEXT,
    words TEXT NOT NULL DEFAULT '[]',
    lang TEXT,
    translation TEXT,
    confidence REAL,
    skipped TEXT,
    mood TEXT,
    quote TEXT,
    user_edited INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS photo (
    id TEXT PRIMARY KEY,
    trek_id TEXT NOT NULL REFERENCES trek(id) ON DELETE CASCADE,
    t TEXT NOT NULL,
    lat REAL,
    lon REAL,
    location_source TEXT NOT NULL DEFAULT 'fix',
    ele_m REAL,
    landmark_id TEXT,
    path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    caption TEXT,
    caption_source_ids TEXT NOT NULL DEFAULT '[]',
    credit TEXT,
    pinned_note_id TEXT,
    user_edited INTEGER NOT NULL DEFAULT 0,
    UNIQUE (trek_id, sha256)
);
CREATE TABLE IF NOT EXISTS observation (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES voice_note(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    landmark_id TEXT
);
CREATE TABLE IF NOT EXISTS entry (
    id TEXT PRIMARY KEY,
    trek_id TEXT NOT NULL REFERENCES trek(id) ON DELETE CASCADE,
    chapter TEXT NOT NULL,
    t TEXT NOT NULL,
    ele_m REAL,
    note_id TEXT,
    photo_ids TEXT NOT NULL DEFAULT '[]',
    landmark_id TEXT,
    quote TEXT
);
CREATE TABLE IF NOT EXISTS trek_owner (
    trek_id TEXT PRIMARY KEY REFERENCES trek(id) ON DELETE CASCADE,
    owner TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generated_line (
    id TEXT PRIMARY KEY,
    trek_id TEXT NOT NULL REFERENCES trek(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    chapter TEXT,
    text TEXT NOT NULL,
    source_ids TEXT NOT NULL DEFAULT '[]',
    user_edited INTEGER NOT NULL DEFAULT 0
);
"""

TABLES: dict[str, str] = {
    "Trek": "trek",
    "TrackPoint": "track_point",
    "VoiceNote": "voice_note",
    "Photo": "photo",
    "Observation": "observation",
    "Entry": "entry",
    "GeneratedLine": "generated_line",
}

JSON_COLUMNS = {"model_versions", "caption_source_ids", "photo_ids", "source_ids", "words"}
BOOL_COLUMNS = {"is_sample", "user_edited"}

M = TypeVar("M", bound=BaseModel)


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns that exist in DDL but not yet in the database."""
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", DDL, re.S):
        table, body = match.groups()
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for line in body.strip().splitlines():
            line = line.strip().rstrip(",")
            col = line.split()[0]
            if col in {"PRIMARY", "UNIQUE"} or col in have:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {line}")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or config.load().database
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(DDL)
    _migrate(conn)
    return conn


def _encode(value: object) -> object:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return int(value)
    return value


def upsert(conn: sqlite3.Connection, model: BaseModel) -> None:
    """Insert, or update in place. Never INSERT OR REPLACE: that deletes the old
    row first, and ON DELETE CASCADE would take its children with it."""
    table = TABLES[type(model).__name__]
    row = {k: _encode(v) for k, v in model.model_dump(mode="json").items()}
    cols = ", ".join(row)
    marks = ", ".join(f":{k}" for k in row)
    updates = ", ".join(f"{k} = excluded.{k}" for k in row)
    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks}) ON CONFLICT DO UPDATE SET {updates}", row)


def upsert_many(conn: sqlite3.Connection, models: list[BaseModel]) -> None:
    for m in models:
        upsert(conn, m)


def decode_row(row: sqlite3.Row) -> dict:
    out = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS and value is not None:
            value = json.loads(value)
        elif key in BOOL_COLUMNS:
            value = bool(value)
        out[key] = value
    return out


def fetch(conn: sqlite3.Connection, model: type[M], where: str = "1", params: tuple = (), order: str = "") -> list[M]:
    table = TABLES[model.__name__]
    sql = f"SELECT * FROM {table} WHERE {where}" + (f" ORDER BY {order}" if order else "")
    return [model(**decode_row(r)) for r in conn.execute(sql, params)]


def get(conn: sqlite3.Connection, model: type[M], id_: str) -> M | None:
    rows = fetch(conn, model, "id = ?", (id_,))
    return rows[0] if rows else None
