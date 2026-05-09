from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS photos (
    hash TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    captured_at TIMESTAMP NOT NULL,
    width INT,
    height INT,
    file_size INT,
    camera_make TEXT,
    camera_model TEXT,
    perceptual_hash TEXT,
    detected_at TIMESTAMP,
    landmarks_path TEXT,
    quality_score REAL,
    yaw REAL,
    pitch REAL,
    roll REAL,
    eye_open_ratio REAL,
    mouth_open_ratio REAL,
    skipped BOOLEAN DEFAULT 0,
    skip_reason TEXT,
    user_override BOOLEAN DEFAULT 0,
    warnings_json TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    name TEXT,
    source_folder TEXT,
    created_at TIMESTAMP,
    last_scanned_at TIMESTAMP,
    canonical_landmarks_path TEXT,
    config_json TEXT
);

CREATE TABLE IF NOT EXISTS project_photos (
    project_id INT REFERENCES projects(id) ON DELETE CASCADE,
    photo_hash TEXT REFERENCES photos(hash) ON DELETE CASCADE,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, photo_hash)
);

CREATE TABLE IF NOT EXISTS renders (
    id INTEGER PRIMARY KEY,
    project_id INT REFERENCES projects(id),
    output_path TEXT,
    config_json TEXT,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    status TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_photos_captured ON photos(captured_at);
CREATE INDEX IF NOT EXISTS idx_photos_skipped ON photos(skipped);
CREATE INDEX IF NOT EXISTS idx_photos_perceptual_hash ON photos(perceptual_hash);
CREATE INDEX IF NOT EXISTS idx_project_photos_project ON project_photos(project_id);
CREATE INDEX IF NOT EXISTS idx_renders_project ON renders(project_id);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, detect_types=sqlite3.PARSE_DECLTYPES, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def migrate(self) -> None:
        with sqlite3.connect(self.path) as conn:
            conn.executescript(SCHEMA)
            _ensure_column(conn, "projects", "last_scanned_at", "TIMESTAMP")

    def fetchone(self, query: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(query, params).fetchone()

    def fetchall(self, query: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(query, params).fetchall()

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> int:
        with self.connect() as conn:
            cur = conn.execute(query, params)
            return int(cur.lastrowid)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("config_json", "warnings_json"):
        if key in result and isinstance(result[key], str):
            try:
                result[key.removesuffix("_json")] = json.loads(result[key])
            except json.JSONDecodeError:
                result[key.removesuffix("_json")] = None
    return result


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
