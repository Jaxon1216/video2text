from pathlib import Path
from threading import Event, Thread
import os
import subprocess
import sys

import pytest

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
    service.close()


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
    service.close()


def test_terminal_progress_already_has_result_or_error(tmp_path):
    for failure in (False, True):
        settings = Settings.from_workspace(tmp_path / str(failure))
        database = AppDatabase(settings)
        class Pipeline(FakePipeline):
            def transcribe(self, *args, **kwargs):
                if failure:
                    raise RuntimeError('recognition failed')
                return super().transcribe(*args, **kwargs)
        service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                              pipeline_factory=lambda provider, model: Pipeline(settings, provider, model))
        observed = []
        def listener(snapshot):
            if snapshot.status in ('completed', 'failed'):
                observed.append(database.get_task(snapshot.task_id))
        task = service.submit_transcription(source='BV1xx411c7XD', provider='whisper', model='small', listener=listener)
        try:
            service.wait_for_task(task.id)
        except RuntimeError:
            assert failure
        finally:
            service.close()
        assert len(observed) == 1
        assert observed[0].finished_at is not None
        if failure:
            assert observed[0].error_message == 'recognition failed'
        else:
            assert observed[0].video_id is not None


def test_cancel_queued_task_never_runs_or_creates_video(tmp_path, monkeypatch):
    monkeypatch.setenv("V2T_TASK_WORKERS", "1")
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    entered, release = Event(), Event()

    class BlockingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().transcribe(source, **kwargs)

    service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                          pipeline_factory=lambda provider, model: BlockingPipeline(settings, provider, model))
    first = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
    assert entered.wait(5)
    second = service.submit_transcription(source="BV1yy411c7XD", provider="whisper", model="small")
    cancelled = service.cancel_task(second.id)
    assert cancelled.status == "cancelled" and cancelled.finished_at
    release.set()
    service.wait_for_task(first.id)
    assert service.wait_for_task(second.id).status == "cancelled"
    assert len(database.list_videos()) == 1
    service.close()


def test_cancel_running_task_discards_result(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    entered, release = Event(), Event()

    class BlockingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().transcribe(source, **kwargs)

    service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                          pipeline_factory=lambda provider, model: BlockingPipeline(settings, provider, model))
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
    assert entered.wait(5)
    pending = service.cancel_task(task.id)
    assert pending.status == "running" and pending.cancel_requested
    release.set()
    finished = service.wait_for_task(task.id)
    assert finished.status == "cancelled" and finished.video_id is None and finished.finished_at
    assert database.list_videos() == []
    service.close()


def test_service_recovers_tasks_left_active_by_restart(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    task = database.create_task(kind="transcription", source_input="demo", provider="whisper", model="small")
    TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None).close()
    recovered = database.get_task(task.id)
    assert recovered.status == "failed"
    assert "重启" in recovered.error_message


def test_error_after_cancel_request_stays_cancelled(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    entered, release = Event(), Event()

    class FailingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            entered.set()
            assert release.wait(5)
            raise RuntimeError("download stopped")

    service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                          pipeline_factory=lambda provider, model: FailingPipeline(settings, provider, model))
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
    assert entered.wait(5)
    service.cancel_task(task.id)
    release.set()
    service.wait_for_task(task.id)
    assert database.get_task(task.id).status == "cancelled"
    service.close()


def test_retranscription_passes_force_asr_to_pipeline(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    observed = []

    class RecordingPipeline(FakePipeline):
        def transcribe(self, source, *, force_asr=False, **kwargs):
            observed.append(force_asr)
            return super().transcribe(source, **kwargs)

    service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                          pipeline_factory=lambda provider, model: RecordingPipeline(settings, provider, model))
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="medium", force_asr=True)
    assert service.wait_for_task(task.id).status == "completed"
    assert observed == [True]
    service.close()


def test_submit_retranscription_uses_existing_video_and_forces_asr(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    observed = []

    class RecordingPipeline(FakePipeline):
        def transcribe(self, source, *, force_asr=False, **kwargs):
            observed.append((source, force_asr))
            result = super().transcribe(source, **kwargs)
            result.transcript_path = settings.transcripts_original_dir / f"demo-task-{self.model}.txt"
            result.metadata_path = settings.metadata_dir / f"demo-task-{self.model}.json"
            result.transcript_path.write_text(result.text + "\n", encoding="utf-8")
            result.metadata_path.write_text("{}", encoding="utf-8")
            return result

    service = TaskService(database=database, library=library,
                          pipeline_factory=lambda provider, model: RecordingPipeline(settings, provider, model))
    first = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
    original = service.wait_for_task(first.id)
    second = service.submit_retranscription(video_id=original.video_id, provider="whisper", model="medium")
    result = service.wait_for_task(second.id)
    assert result.status == "completed"
    assert result.video_id != original.video_id
    assert observed[-1] == ("BV1xx411c7XD", True)
    service.close()


def test_submit_retranscription_rejects_missing_video_without_creating_task(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    with pytest.raises(KeyError, match="999"):
        service.submit_retranscription(video_id=999, provider="whisper", model="small")
    assert database.list_tasks() == []
    service.close()


def run_workspace_process(workspace: Path, code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    script = """
import os
import sys
from pathlib import Path
from v2t.config import Settings
from v2t.database import AppDatabase
from v2t.library import WorkspaceLibrary
from v2t.models import ProgressSnapshot
from v2t.tasks import TaskService
settings = Settings.from_workspace(Path(sys.argv[1]))
database = AppDatabase(settings)
library = WorkspaceLibrary(settings, database)
""" + code
    return subprocess.run(
        [sys.executable, "-c", script, str(workspace)], env=env, text=True,
        capture_output=True, timeout=15,
    )


def test_second_process_and_instance_cannot_recover_active_tasks(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    entered, release = Event(), Event()

    class BlockingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            entered.set()
            assert release.wait(10)
            return super().transcribe(source, **kwargs)

    service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                          pipeline_factory=lambda provider, model: BlockingPipeline(settings, provider, model))
    try:
        task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
        assert entered.wait(5)
        before = database.get_task(task.id)
        with pytest.raises(RuntimeError, match="工作区已有转写服务"):
            TaskService(database=database, library=service.library, pipeline_factory=None)
        second = run_workspace_process(tmp_path, """
try:
    service = TaskService(database=database, library=library, pipeline_factory=None)
except RuntimeError as error:
    print(error)
else:
    service.close()
    raise AssertionError('second process acquired active workspace')
""")
        assert second.returncode == 0, second.stderr
        assert "工作区已有转写服务" in second.stdout
        assert database.get_task(task.id) == before
    finally:
        release.set()
        service.close()
    assert database.get_task(task.id).status == "completed"


def test_crashed_process_releases_lock_and_real_restart_recovers_tasks(tmp_path):
    crashed = run_workspace_process(tmp_path, """
service = TaskService(database=database, library=library, pipeline_factory=None)
task = database.create_task(kind='transcription', source_input='BV1xx411c7XD',
                            provider='whisper', model='small', prompt='术语', force_asr=True)
database.record_progress(ProgressSnapshot(task_id=task.id, status='running', stage='transcribing'))
print(task.id, flush=True)
os._exit(17)
""")
    assert crashed.returncode == 17, crashed.stderr
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    task_id = crashed.stdout.strip()
    assert database.get_task(task_id).status == "running"
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    try:
        task = database.get_task(task_id)
        assert task.status == "failed" and "重启" in task.error_message
        assert task.prompt == "术语" and task.force_asr
    finally:
        service.close()


def test_close_keeps_lock_until_accepted_work_finishes_and_rejects_submission(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    entered, release, closing = Event(), Event(), Event()

    class BlockingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            entered.set()
            assert release.wait(10)
            return super().transcribe(source, **kwargs)

    service = TaskService(database=database, library=WorkspaceLibrary(settings, database),
                          pipeline_factory=lambda provider, model: BlockingPipeline(settings, provider, model))
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small")
    assert entered.wait(5)
    shutdown = service.executor.shutdown
    def notify_shutdown(**kwargs):
        closing.set()
        return shutdown(**kwargs)
    monkeypatch.setattr(service.executor, "shutdown", notify_shutdown)
    thread = Thread(target=service.close)
    thread.start()
    try:
        assert closing.wait(5)
        with pytest.raises(RuntimeError, match="服务已关闭"):
            service.submit_transcription(source="BV1yy411c7XD", provider="whisper", model="small")
        assert len(database.list_tasks()) == 1
        with pytest.raises(RuntimeError, match="工作区已有转写服务"):
            TaskService(database=database, library=service.library, pipeline_factory=None)
    finally:
        release.set()
        thread.join(10)
        service.close()
    assert not thread.is_alive()
    assert database.get_task(task.id).status == "completed"
    replacement = TaskService(database=database, library=service.library, pipeline_factory=None)
    replacement.close()


def test_constructor_failure_releases_workspace_lock(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    def fail_recovery():
        raise RuntimeError("recovery failed")
    with monkeypatch.context() as patch:
        patch.setattr(database, "recover_interrupted_tasks", fail_recovery)
        with pytest.raises(RuntimeError, match="recovery failed"):
            TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    service.close()


@pytest.mark.parametrize("explicit_force_asr,prefer_subtitles", [(True, True), (False, False), (False, True)])
def test_retry_after_restart_preserves_original_parameters(tmp_path, explicit_force_asr, prefer_subtitles):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    calls = []

    class RecordingPipeline(FakePipeline):
        def transcribe(self, source, *, force_asr=False, prompt=None, **kwargs):
            calls.append((source, self.provider, self.model, prompt, force_asr, self.prefer_subtitles))
            if len(calls) == 1:
                raise RuntimeError("temporary download failure")
            return super().transcribe(source, prompt=prompt, **kwargs)

    factory = lambda provider, model: RecordingPipeline(settings, provider, model)
    service = TaskService(database=database, library=library, pipeline_factory=factory,
                          prefer_subtitles=prefer_subtitles)
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="medium",
                                        prompt="Kubernetes，幂等", force_asr=explicit_force_asr)
    with pytest.raises(RuntimeError, match="temporary download failure"):
        service.wait_for_task(task.id)
    service.close()
    service = TaskService(database=AppDatabase(settings), library=library, pipeline_factory=factory,
                          prefer_subtitles=not prefer_subtitles)
    try:
        retried = service.retry_task(task.id)
        assert retried.id != task.id
        assert retried.status == "queued"
        assert service.wait_for_task(retried.id).status == "completed"
        assert calls[0] == calls[1]
        assert calls[1][-2:] == (explicit_force_asr or not prefer_subtitles,
                                  not (explicit_force_asr or not prefer_subtitles))
        assert database.get_task(task.id).status == "failed"
    finally:
        service.close()


def test_retry_retranscription_prepares_original_video_again(tmp_path, monkeypatch):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    prepared = []
    def prepare(video_id):
        prepared.append(video_id)
        return "BV1xx411c7XD"
    monkeypatch.setattr(library, "prepare_retranscription_source", prepare)
    class FailingPipeline(FakePipeline):
        def transcribe(self, *args, **kwargs):
            raise RuntimeError("download failed")
    service = TaskService(database=database, library=library,
                          pipeline_factory=lambda provider, model: FailingPipeline(settings, provider, model))
    try:
        task = service.submit_retranscription(video_id=42, provider="whisper", model="small", prompt="术语")
        with pytest.raises(RuntimeError, match="download failed"):
            service.wait_for_task(task.id)
        retried = service.retry_task(task.id)
        with pytest.raises(RuntimeError, match="download failed"):
            service.wait_for_task(retried.id)
        assert prepared == [42, 42]
        assert retried.retranscribe_video_id == 42 and retried.force_asr and retried.prompt == "术语"
    finally:
        service.close()


@pytest.mark.parametrize("status", ["queued", "running", "completed"])
def test_retry_rejects_other_states_without_creating_task(tmp_path, status):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    try:
        task = database.create_task(kind="transcription", source_input="demo", provider="whisper", model="small")
        from v2t.models import ProgressSnapshot
        database.record_progress(ProgressSnapshot(task_id=task.id, status=status, stage=status))
        with pytest.raises(ValueError, match="失败或已中断"):
            service.retry_task(task.id)
        with pytest.raises(KeyError):
            service.retry_task("missing")
        assert len(database.list_tasks()) == 1
    finally:
        service.close()


def test_retry_local_source_uses_original_absolute_path_after_cwd_changes(tmp_path, monkeypatch):
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "clip.wav").write_text("original audio")
    (second_dir / "clip.wav").write_text("different audio")
    settings = Settings.from_workspace(tmp_path / "workspace")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    observed = []
    class RecordingPipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            observed.append(Path(source).read_text())
            raise RuntimeError("temporary failure")
    factory = lambda provider, model: RecordingPipeline(settings, provider, model)
    monkeypatch.chdir(first_dir)
    service = TaskService(database=database, library=library, pipeline_factory=factory)
    task = service.submit_transcription(source="clip.wav", provider="whisper", model="small")
    with pytest.raises(RuntimeError, match="temporary failure"):
        service.wait_for_task(task.id)
    service.close()
    assert database.get_task(task.id).source_input == str(first_dir / "clip.wav")
    monkeypatch.chdir(second_dir)
    service = TaskService(database=AppDatabase(settings), library=library, pipeline_factory=factory)
    try:
        retried = service.retry_task(task.id)
        with pytest.raises(RuntimeError, match="temporary failure"):
            service.wait_for_task(retried.id)
        assert observed == ["original audio", "original audio"]
    finally:
        service.close()


@pytest.mark.parametrize("file_exists", [True, False])
def test_retry_legacy_relative_local_source_rejects_ambiguous_file(tmp_path, monkeypatch, file_exists):
    monkeypatch.chdir(tmp_path)
    if file_exists:
        (tmp_path / "clip.wav").write_text("wrong same-name audio")
    settings = Settings.from_workspace(tmp_path / "workspace")
    database = AppDatabase(settings)
    task = database.create_task(kind="transcription", source_input="clip.wav", provider="whisper", model="small")
    database.fail_task(task.id, error_message="previous failure")
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    try:
        with pytest.raises(ValueError, match="绝对路径重新提交"):
            service.retry_task(task.id)
        assert len(database.list_tasks()) == 1
    finally:
        service.close()
