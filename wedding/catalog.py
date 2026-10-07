"""SQLite catalog: one row per source file, plus per-stage status for resumable runs."""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS photos (
    id               TEXT PRIMARY KEY,   -- <sha8>_<stem>
    filename         TEXT UNIQUE NOT NULL,
    stem             TEXT NOT NULL,
    role             TEXT NOT NULL,      -- original | edited | collage
    variant_group    TEXT NOT NULL,      -- stem of the original this file belongs to
    sha256           TEXT NOT NULL,
    bytes            INTEGER NOT NULL,
    width            INTEGER,
    height           INTEGER,
    exif_orientation INTEGER,
    exif_datetime    TEXT,
    exif_json        TEXT,
    filename_time    TEXT,               -- phone capture time parsed from the filename (2019)
    sidecar          TEXT,               -- Google Takeout sidecar filename, if any
    taken_ts         INTEGER,            -- sidecar photoTakenTime (phone capture, not 1982)
    google_url       TEXT,
    fs_mtime         REAL,               -- stored for reference only, never trusted as a date
    capture_order    INTEGER,            -- position of the variant group in capture order
    segment          INTEGER             -- capture sitting, split on time gaps
);
CREATE TABLE IF NOT EXISTS stage_status (
    photo_id    TEXT NOT NULL,
    stage       TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status      TEXT NOT NULL,           -- done | error
    error       TEXT,
    updated_at  REAL NOT NULL,
    PRIMARY KEY (photo_id, stage)
);
CREATE TABLE IF NOT EXISTS metrics (
    photo_id TEXT PRIMARY KEY,
    data     TEXT NOT NULL               -- JSON
);
CREATE TABLE IF NOT EXISTS variants (
    variant_group TEXT PRIMARY KEY,
    data          TEXT NOT NULL          -- JSON comparison of original vs edited
);
"""


def connect() -> sqlite3.Connection:
    paths.ensure_output_dirs()
    con = sqlite3.connect(paths.assert_not_in_source(paths.CATALOG_DB))
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def load_config(name: str) -> dict:
    return json.loads((paths.CONFIG_DIR / name).read_text(encoding="utf-8"))


def fingerprint(*parts) -> str:
    """Stable hash of everything a stage result depends on (source hash, stage version, config)."""
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def is_done(con, photo_id: str, stage: str, fp: str) -> bool:
    row = con.execute(
        "SELECT fingerprint, status FROM stage_status WHERE photo_id=? AND stage=?", (photo_id, stage)
    ).fetchone()
    return bool(row) and row["status"] == "done" and row["fingerprint"] == fp


def mark(con, photo_id: str, stage: str, fp: str, status: str = "done", error: str | None = None) -> None:
    con.execute(
        "INSERT OR REPLACE INTO stage_status VALUES (?,?,?,?,?,?)",
        (photo_id, stage, fp, status, error, time.time()),
    )


def write_text_atomic(path: Path, text: str) -> None:
    path = paths.assert_not_in_source(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
