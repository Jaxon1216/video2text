from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from v2t.config import Settings
from v2t.database import AppDatabase
from v2t.downloaders.youtube import YouTubeDownloader
from v2t.library import WorkspaceLibrary
from v2t.pipeline import V2TPipeline
from v2t.progress import ProgressReporter
from v2t.tasks import TaskService
from v2t.transcribers.base import Transcriber
from v2t.web import create_app
from test_audio_cache import write_wav
from test_youtube_downloader import URL, subtitles, track, ydl


class RecordingTranscriber(Transcriber):
    name = "fake-asr"

    def __init__(self):
        self.calls = []

    def transcribe(self, audio_path, *, prompt=None, progress=None):
        self.calls.append(audio_path)
        assert audio_path.is_file()
        return {"text": "ASR result", "model": "test", "language": "en",
                "segments": [{"start": 0, "end": 3, "text": "ASR result"}]}


class AudioPipeline(V2TPipeline):
    def _extract_audio(self, media: Path, stem: str, progress=None) -> Path:
        path = self.settings.audio_dir / f"{stem}.wav"
        write_wav(path)
        return path


def pipeline(tmp_path):
    settings = Settings.from_workspace(tmp_path / ".v2t")
    asr = RecordingTranscriber()
    return AudioPipeline(settings=settings, downloaders={"youtube": YouTubeDownloader()}, transcriber=asr), asr


def test_subtitle_pipeline_skips_download_and_asr(tmp_path, subtitles, monkeypatch):
    subtitles[0].append(track("en", generated=True))
    from v2t.downloaders import youtube
    monkeypatch.setattr(youtube, "_import_youtube_dl", lambda: pytest.fail("should not download"))
    engine, asr = pipeline(tmp_path)
    result = engine.transcribe(URL)
    assert result.engine == "subtitle" and result.metadata["transcript_source"] == "subtitle"
    assert result.metadata["download"]["is_generated"] is True
    assert result.metadata["segments"] == [{"start": 1.25, "end": 3.75, "text": "hello"}]
    assert result.audio_path is None and result.video_path is None
    assert not asr.calls


@pytest.mark.parametrize("mode", ["no-subtitles", "api-error", "force-asr", "preference-off"])
def test_subtitle_fallback_and_forced_asr_reuse_audio(tmp_path, subtitles, ydl, monkeypatch, mode):
    from v2t.downloaders import youtube
    if mode == "api-error":
        def unavailable():
            raise RuntimeError("IP blocked")
        monkeypatch.setattr(youtube, "_import_transcript_api", unavailable)
    if mode in {"force-asr", "preference-off"}:
        subtitles[0].append(track())
    engine, asr = pipeline(tmp_path)
    engine.prefer_subtitles = mode != "preference-off"
    progress = ProgressReporter("demo")
    first = engine.transcribe(URL, force_asr=mode == "force-asr", progress=progress)
    second = engine.transcribe("https://youtube.com/shorts/dQw4w9WgXcQ", force_asr=True)
    assert first.audio_path == second.audio_path
    assert len(ydl[2]) == 2  # one metadata lookup + one download, second task uses cache
    assert len(asr.calls) == 2
    assert first.transcript_path != second.transcript_path
    assert first.metadata["source"]["kind"] == "youtube"
    assert first.metadata["transcript_source"] == "asr"
    if mode in {"force-asr", "preference-off"}:
        assert not subtitles[1]


def test_api_youtube_subtitle_export_and_retranscription(tmp_path, subtitles, ydl):
    subtitles[0].append(track())
    engine, asr = pipeline(tmp_path)
    database = AppDatabase(engine.settings)
    library = WorkspaceLibrary(engine.settings, database)
    service = TaskService(database=database, library=library, pipeline_factory=lambda provider, model: engine)
    app = create_app(task_service=service, library=library, database=database,
                     default_provider="whisper", default_model="small", enabled_providers=["whisper"])
    try:
        with TestClient(app) as client:
            created = client.post("/api/tasks/transcribe", json={"source": URL})
            assert created.status_code == 200
            task = service.wait_for_task(created.json()["task_id"])
            assert task.status == "completed"
            video_id = task.video_id
            doc = client.get(f"/api/videos/{video_id}/document").json()
            assert doc["platform"] == "youtube" and doc["title"] == "A real title"
            assert doc["url"] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
            exported = client.get(f"/api/videos/{video_id}/export?format=srt")
            assert exported.status_code == 200 and "00:00:01,250 --> 00:00:03,750" in exported.text
            created = client.post(f"/api/videos/{video_id}/retranscribe", json={"provider": "whisper", "model": "small"})
            assert created.status_code == 200
            second = service.wait_for_task(created.json()["task_id"])
            assert second.status == "completed" and second.video_id != video_id
            assert len(asr.calls) == 1 and len(subtitles[1]) == 1
            third = client.post(f"/api/videos/{second.video_id}/retranscribe", json={})
            assert third.status_code == 200
            assert service.wait_for_task(third.json()["task_id"]).status == "completed"
            assert len(ydl[2]) == 2 and len(asr.calls) == 2
            assert client.get(f"/api/videos/{video_id}/document").json()["engine"] == "subtitle"
            bad = client.post("/api/tasks/transcribe", json={"source": "https://youtube.com/playlist?list=demo"})
            assert bad.status_code == 400
    finally:
        service.executor.shutdown(wait=True)
