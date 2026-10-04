from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        status TEXT NOT NULL,
        source_input TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        workspace_root TEXT NOT NULL,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        progress_percent REAL NOT NULL DEFAULT 0,
        current_stage TEXT NOT NULL DEFAULT 'queued',
        current_message TEXT NOT NULL DEFAULT '',
        error_message TEXT NOT NULL DEFAULT '',
        video_id INTEGER,
        created_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS task_progress_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id TEXT NOT NULL,
        status TEXT NOT NULL,
        stage TEXT NOT NULL,
        message TEXT NOT NULL DEFAULT '',
        percent REAL NOT NULL DEFAULT 0,
        indeterminate INTEGER NOT NULL DEFAULT 0,
        detail_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_kind TEXT NOT NULL,
        source_input TEXT NOT NULL,
        source_url TEXT,
        source_bv TEXT,
        title TEXT NOT NULL,
        display_name TEXT NOT NULL,
        language TEXT,
        engine TEXT NOT NULL,
        model TEXT NOT NULL,
        video_path TEXT,
        audio_path TEXT NOT NULL,
        metadata_path TEXT NOT NULL UNIQUE,
        current_transcript_version_id INTEGER,
        category_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS transcript_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        video_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        file_path TEXT NOT NULL,
        text_sha256 TEXT NOT NULL,
        char_count INTEGER NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS video_tags (
        video_id INTEGER NOT NULL,
        tag_id INTEGER NOT NULL,
        PRIMARY KEY (video_id, tag_id)
    )""",
)


def migrate_database(connection: sqlite3.Connection) -> None:
    current = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if current > SCHEMA_VERSION:
        raise RuntimeError(f"newer database schema: {current} > {SCHEMA_VERSION}")
    connection.execute("PRAGMA journal_mode=WAL")
    if current == SCHEMA_VERSION:
        return

    connection.execute("BEGIN IMMEDIATE")
    try:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        if "cancel_requested" not in columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
