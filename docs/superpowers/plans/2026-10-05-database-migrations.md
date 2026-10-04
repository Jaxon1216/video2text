# Database Migration Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make SQLite schema upgrades versioned, repeatable, and safe for existing workspaces.

**Architecture:** Move database initialization SQL into `v2t/migrations.py`. `AppDatabase` opens the connection as before and invokes a migration function that checks `PRAGMA user_version`, rejects newer unsupported versions, and upgrades version 0 to version 1 in a transaction. Existing data and public database methods keep their shape.

**Tech Stack:** Python 3.10–3.12, standard-library `sqlite3`, pytest.

## Global Constraints

- Keep SQLite and the existing table/column names; no new database dependency.
- Existing `.v2t/app.db` files may have `PRAGMA user_version=0` with or without `tasks.cancel_requested`.
- Schema migration must not erase existing tasks, videos, or transcript versions.
- Tests use temporary local SQLite files and no network.
- Update `AGENTS.md`, `docs/architecture.md`, and append the architectural choice to `docs/decisions.md`.

---

## File map

- `src/v2t/migrations.py`: sole owner of table creation and upgrade steps.
- `src/v2t/database.py`: replace inline DDL with a call to `migrate_database(connection)`; retain query and CRUD methods.
- `tests/test_database.py`: verify new database version, version-0 upgrade, idempotent reopen, and unsupported newer version.
- `AGENTS.md`, `docs/architecture.md`, `docs/decisions.md`: explain migration ownership and the version policy.

### Task 1: Versioned baseline migration

**Files:**
- Create: `src/v2t/migrations.py`
- Modify: `src/v2t/database.py:30-123`
- Modify: `tests/test_database.py`
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`
- Modify: `docs/decisions.md`

**Interfaces:**
- Consumes: `sqlite3.Connection` supplied by `AppDatabase._connect()`.
- Produces: `SCHEMA_VERSION = 1` and `migrate_database(connection: sqlite3.Connection) -> None`.

- [x] **Step 1: Write failing migration tests**

Add these tests to `tests/test_database.py` (and import `sqlite3` and `pytest`):

```python
def test_new_database_has_versioned_schema(tmp_path: Path) -> None:
    database = AppDatabase(Settings.from_workspace(tmp_path / ".v2t"))
    with sqlite3.connect(database.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "cancel_requested" in columns


def test_legacy_task_schema_is_upgraded_without_losing_rows(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    with sqlite3.connect(settings.app_db_path) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, source_input TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, workspace_root TEXT NOT NULL, progress_percent REAL NOT NULL DEFAULT 0, current_stage TEXT NOT NULL DEFAULT 'queued', current_message TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '', video_id INTEGER, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)")
        conn.execute("INSERT INTO tasks (id, kind, status, source_input, provider, model, workspace_root, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ("old", "transcription", "failed", "BV1xx411c7XD", "whisper", "small", str(settings.workspace_root), "2026-09-29"))
    database = AppDatabase(settings)
    assert database.get_task("old").id == "old"
    with sqlite3.connect(database.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("SELECT cancel_requested FROM tasks WHERE id = 'old'").fetchone()[0] == 0
    AppDatabase(settings)
    assert database.get_task("old").id == "old"


def test_newer_schema_version_is_rejected(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    with sqlite3.connect(settings.app_db_path) as conn:
        conn.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="newer database schema"):
        AppDatabase(settings)
```

- [x] **Step 2: Verify red**

Run: `/Users/eastonjiang/code/ideas/video2text/.venv/bin/python -m pytest tests/test_database.py -q`
Expected: new-version and unsupported-newer-version tests fail against the current inline initializer.

- [x] **Step 3: Implement migration module and delegate initializer**

Create `src/v2t/migrations.py` with the following complete definitions. The schema is copied from the current initializer without changing its columns or constraints:

```python
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 1
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
        source_input TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
        workspace_root TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0,
        progress_percent REAL NOT NULL DEFAULT 0, current_stage TEXT NOT NULL DEFAULT 'queued',
        current_message TEXT NOT NULL DEFAULT '', error_message TEXT NOT NULL DEFAULT '',
        video_id INTEGER, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS task_progress_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
        status TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
        percent REAL NOT NULL DEFAULT 0, indeterminate INTEGER NOT NULL DEFAULT 0,
        detail_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_kind TEXT NOT NULL,
        source_input TEXT NOT NULL, source_url TEXT, source_bv TEXT,
        title TEXT NOT NULL, display_name TEXT NOT NULL, language TEXT,
        engine TEXT NOT NULL, model TEXT NOT NULL, video_path TEXT,
        audio_path TEXT NOT NULL, metadata_path TEXT NOT NULL UNIQUE,
        current_transcript_version_id INTEGER, category_id INTEGER,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS transcript_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, video_id INTEGER NOT NULL,
        kind TEXT NOT NULL, file_path TEXT NOT NULL, text_sha256 TEXT NOT NULL,
        char_count INTEGER NOT NULL, is_active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS video_tags (
        video_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
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
```

In `database.py`, replace `_init_schema` with:

```python
def _init_schema(self) -> None:
    with self._connect() as conn:
        migrate_database(conn)
```

Import `migrate_database` from `v2t.migrations` in `database.py`. Changing table constraints is outside this plan.

- [x] **Step 4: Verify green and full regressions**

Run: `/Users/eastonjiang/code/ideas/video2text/.venv/bin/python -m pytest tests/test_database.py -q`
Expected: all database tests pass.

Run: `/Users/eastonjiang/code/ideas/video2text/.venv/bin/python -m pytest -q`
Expected: full suite passes with the same optional-browser skips as baseline.

- [x] **Step 5: Document and commit**

Add `migrations.py` to the directory map in `AGENTS.md`; describe `PRAGMA user_version` and legacy version-0 upgrade in `docs/architecture.md`; append decision D15 to `docs/decisions.md` explaining that SQLite migrations are versioned without adding a migration framework. Commit with `refactor: version sqlite schema migrations`.

## Self-review

- Coverage: new database, existing version-0 database without cancellation column, idempotent reopen, unsupported future version.
- Consistency: `AppDatabase._init_schema` remains the only startup call site; migration function owns DDL.
- Scope: no query API change, table rename, new database engine, or destructive data migration.
