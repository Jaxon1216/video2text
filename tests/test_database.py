import json
import sqlite3
from pathlib import Path

import pytest

from v2t.config import Settings
from v2t.database import AppDatabase
from v2t.library import WorkspaceLibrary
from v2t.migrations import SCHEMA_STATEMENTS
from v2t.models import ProgressSnapshot, SourceRef, TranscriptResult


def test_settings_create_database_and_workspace_directories(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()

    assert settings.transcripts_original_dir.exists()
    assert settings.transcripts_edited_dir.exists()
    assert settings.tasks_dir.exists()


def test_new_database_has_versioned_schema(tmp_path: Path) -> None:
    database = AppDatabase(Settings.from_workspace(tmp_path / ".v2t"))
    with sqlite3.connect(database.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
        assert "cancel_requested" in columns


def test_legacy_task_schema_is_upgraded_without_losing_rows(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    with sqlite3.connect(settings.app_db_path) as conn:
        conn.execute(
            """CREATE TABLE tasks (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                source_input TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                workspace_root TEXT NOT NULL, progress_percent REAL NOT NULL DEFAULT 0,
                current_stage TEXT NOT NULL DEFAULT 'queued', current_message TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '', video_id INTEGER, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT
            )"""
        )
        conn.execute(
            """INSERT INTO tasks
                (id, kind, status, source_input, provider, model, workspace_root, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("old", "transcription", "failed", "BV1xx411c7XD", "whisper", "small",
             str(settings.workspace_root), "2026-09-29"),
        )
    database = AppDatabase(settings)
    assert database.get_task("old").id == "old"
    with sqlite3.connect(database.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT cancel_requested FROM tasks WHERE id = 'old'").fetchone()[0] == 0
    AppDatabase(settings)
    assert database.get_task("old").id == "old"


def test_unversioned_database_preserves_video_and_transcript_version(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    database = AppDatabase(settings)
    video_id = database.create_video(
        source_kind="bilibili", source_input="BV1xx411c7XD", source_url=None,
        source_bv="BV1xx411c7XD", title="旧视频", display_name="旧视频",
        language="zh", engine="whisper", model="small", video_path=None,
        audio_path="", metadata_path="metadata/old.json",
    )
    version_id = database.create_transcript_version(
        video_id=video_id, kind="original", file_path="transcripts/original/old.txt",
        text_sha256="abc", char_count=3, is_active=True,
    )
    with sqlite3.connect(database.path) as conn:
        conn.execute("PRAGMA user_version = 0")

    upgraded = AppDatabase(settings)

    assert upgraded.get_video(video_id)["title"] == "旧视频"
    assert upgraded.get_active_transcript_version(video_id).id == version_id
    with sqlite3.connect(upgraded.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_newer_schema_version_is_rejected(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    with sqlite3.connect(settings.app_db_path) as conn:
        conn.execute("PRAGMA user_version = 999")
    with pytest.raises(RuntimeError, match="newer database schema"):
        AppDatabase(settings)


def test_v1_database_migrates_retry_parameters_and_preserves_rows(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path)
    settings.ensure_directories()
    with sqlite3.connect(settings.app_db_path) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO tasks (id, kind, status, source_input, provider, model, workspace_root, created_at) "
            "VALUES ('legacy', 'transcription', 'failed', 'BV1xx411c7XD', 'whisper', 'small', ?, '2026-10-22')",
            (str(tmp_path),),
        )
    database = AppDatabase(settings)
    old = database.get_task("legacy")
    assert old is not None
    assert (old.status, old.source_input, old.provider, old.model) == (
        "failed", "BV1xx411c7XD", "whisper", "small"
    )
    assert (old.prompt, old.force_asr, old.retranscribe_video_id) == ("", False, None)
    new = database.create_task(
        kind="transcription", source_input="demo", provider="whisper", model="medium",
        prompt="术语", force_asr=True, retranscribe_video_id=42,
    )
    reopened = AppDatabase(settings)
    loaded = reopened.get_task(new.id)
    assert (loaded.prompt, loaded.force_asr, loaded.retranscribe_video_id) == ("术语", True, 42)
    with sqlite3.connect(database.path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_database_persists_task_progress(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    database = AppDatabase(settings)

    task = database.create_task(
        kind="transcription",
        source_input="BV1xx411c7XD",
        provider="whisper",
        model="small",
    )
    database.record_progress(
        ProgressSnapshot(
            task_id=task.id,
            status="running",
            stage="downloading",
            message="downloading",
            percent=0.25,
        )
    )

    loaded = database.get_task(task.id)
    assert loaded is not None
    assert loaded.current_stage == "downloading"
    assert loaded.progress_percent == 0.25


def test_workspace_library_indexes_existing_files(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    transcript_path = settings.transcripts_original_dir / "demo-1.txt"
    metadata_path = settings.metadata_dir / "demo-1.json"
    transcript_path.write_text("hello world\n", encoding="utf-8")
    metadata_path.write_text(
        json.dumps(
            {
                "source": {
                    "raw_input": "BV1xx411c7XD",
                    "kind": "bilibili",
                    "url": "https://www.bilibili.com/video/BV1xx411c7XD",
                    "bv": "BV1xx411c7XD",
                },
                "engine": "whisper",
                "model": "small",
                "audio_path": str(settings.audio_dir / "demo.wav"),
                "video_path": str(settings.downloads_dir / "demo.mp4"),
                "download": {"title": "Demo Title"},
                "language": "zh",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    library.index_existing_workspace()

    videos = database.list_videos()
    assert len(videos) == 1
    assert videos[0]["title"] == "Demo Title"
    active = database.get_active_transcript_version(int(videos[0]["id"]))
    assert active is not None
    assert active.file_path == str(transcript_path)


def test_register_transcript_result_indexes_local_file_without_download_metadata(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    settings.ensure_directories()
    transcript_path = settings.transcripts_original_dir / "local-video.txt"
    transcript_path.write_text("hello from local file\n", encoding="utf-8")
    metadata_path = settings.metadata_dir / "local-video.json"
    metadata_path.write_text("{}", encoding="utf-8")

    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    video_id = library.register_transcript_result(
        TranscriptResult(
            source=SourceRef(
                raw_input=str(tmp_path / "local-video.mp4"),
                kind="video",
                display_name="local-video",
                path=tmp_path / "local-video.mp4",
            ),
            engine="whisper",
            model="small",
            text="hello from local file",
            audio_path=settings.audio_dir / "local-video.wav",
            transcript_path=transcript_path,
            metadata_path=metadata_path,
            video_path=tmp_path / "local-video.mp4",
            metadata={"language": "zh", "download": None},
        )
    )

    video = database.get_video(video_id)
    assert video is not None
    assert video["title"] == "local-video"
    assert video["source_kind"] == "video"
