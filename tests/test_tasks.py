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
