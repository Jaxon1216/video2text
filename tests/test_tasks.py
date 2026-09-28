from pathlib import Path

from v2t.config import Settings
from v2t.database import AppDatabase
from v2t.library import WorkspaceLibrary
from v2t.models import SourceRef, TranscriptResult
from v2t.tasks import TaskService


class FakePipeline:
    def __init__(self, settings: Settings, provider: str, model: str) -> None:
        self.settings = settings
        self.provider = provider
        self.model = model

    def transcribe(self, source: str, *, prompt: str | None = None, output: Path | None = None, progress=None) -> TranscriptResult:
        if progress is not None:
            progress.running("transcribing", message="transcribing", stage_progress=0.5)
        transcript_path = self.settings.transcripts_original_dir / "demo-task.txt"
        transcript_path.write_text("task text\n", encoding="utf-8")
        metadata_path = self.settings.metadata_dir / "demo-task.json"
        metadata_path.write_text("{}", encoding="utf-8")
        return TranscriptResult(
            source=SourceRef(raw_input=source, kind="bilibili", display_name="demo", url=source, bv="BV1xx411c7XD"),
            engine=self.provider,
            model=self.model,
            text="task text",
            audio_path=self.settings.audio_dir / "demo-task.wav",
            transcript_path=transcript_path,
            metadata_path=metadata_path,
            video_path=self.settings.downloads_dir / "demo-task.mp4",
            metadata={"language": "zh", "download": {"title": "Demo Task"}},
        )


def test_task_service_runs_background_transcription_and_indexes_result(tmp_path: Path) -> None:
    settings = Settings.from_workspace(tmp_path / ".v2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    service = TaskService(
        database=database,
        library=library,
        pipeline_factory=lambda provider, model: FakePipeline(settings, provider, model),
    )

    task = service.submit_transcription(
        source="https://www.bilibili.com/video/BV1xx411c7XD",
        provider="whisper",
        model="small",
    )
    completed = service.wait_for_task(task.id)

    assert completed.status == "completed"
    assert completed.video_id is not None
    videos = database.list_videos()
    assert len(videos) == 1
    assert videos[0]["title"] == "Demo Task"


def test_task_stage_logs_do_not_repeat_for_percentage_updates(tmp_path, caplog):
    import logging
    from v2t.progress import ProgressReporter

    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    task = database.create_task(kind="transcription", source_input="demo", provider="whisper", model="small")
    reporter = ProgressReporter(task.id, callback=service._handle_progress)
    with caplog.at_level(logging.INFO, logger="v2t.tasks"):
        reporter.running("transcribing", stage_progress=0.1)
        reporter.running("transcribing", stage_progress=0.9)
        reporter.failed("recognition failed")
    assert len(caplog.records) == 2
    assert "transcribing" in caplog.records[0].message
    assert "recognition failed" in caplog.records[1].message
    assert caplog.records[1].levelno == logging.ERROR
