from __future__ import annotations

import io
import json
from pathlib import Path
from threading import Event
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient

from test_web import FakePipeline, build_test_app
from v2t.services import screenshots
from v2t.services.tasks import TaskService

SEGMENTS = [
    {"start": 0, "end": 60, "text": "第一段正文"},
    {"start": 100, "end": 160, "text": "第二段正文"},
    {"start": 160, "end": 199, "text": "第三段正文"},
]


def test_opt_in_api_zip_and_untimed_versions(tmp_path, monkeypatch):
    app, service, _, library = build_test_app(tmp_path)
    probes = []
    original = FakePipeline.transcribe

    def transcribe(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        result.metadata["segments"] = SEGMENTS
        result.transcript_path.write_text("".join(row["text"] for row in SEGMENTS))
        return result

    def resolve(self, source, **kwargs):
        probes.append(source)
        return Path("example.mp4")

    monkeypatch.setattr(FakePipeline, "transcribe", transcribe)
    monkeypatch.setattr(FakePipeline, "resolve_screenshot_video", resolve, raising=False)
    monkeypatch.setattr(screenshots, "_duration", lambda *args: 161.)
    monkeypatch.setattr(screenshots, "_frame", lambda _file, second, *, jpeg, **kw:
                        (b"jpeg" + bytes([second])) if jpeg else bytes([second]) * (308 * 149))
    with TestClient(app) as client:
        for options in ({}, {"include_screenshots": False}):
            res = client.post("/api/tasks/transcribe", json={"source": "BV1xx411c7XD", **options})
            task = service.wait_for_task(res.json()["task_id"])
            assert task.status == "completed" and not task.include_screenshots
            assert client.get(f"/api/videos/{task.video_id}/screenshots").json()["status"] == "disabled"
        assert not probes
        res = client.post("/api/tasks/batch", json={"sources": ["BV1yy411c7XD", "BV1zz411c7XD"], "include_screenshots": True})
        tasks = [service.wait_for_task(item["id"]) for item in res.json()["items"]]
        assert len(tasks) == 2 and len(probes) == 2
        assert all(task.status == "completed" and task.include_screenshots for task in tasks)
        video = tasks[0].video_id
        base = f"/api/videos/{video}"
        orig = library.load_document(video).version_id
        view = client.get(f"{base}/screenshots").json()
        assert view["status"] == "ready" and view["sampled_count"] == 3
        # 80 lies halfway across a gap; 160 belongs to the third paragraph.
        assert [im["block_index"] for im in view["images"]] == [0, 0, 2]
        assert client.get(f"{base}/screenshots/0").status_code == 404
        assert client.get(f"{base}/screenshots/4").status_code == 404
        other_version = library.load_document(tasks[1].video_id).version_id
        assert client.get(f"{base}/illustrated.zip?version_id={other_version}").status_code == 404

        edited = library.save_edited_transcript(video, "只有编辑稿正文")
        library.ai_output_path(video).write_text("只有优化稿正文")
        ai = library.sync_ai_transcript(video)
        for version, body in ((orig, None), (edited, "只有编辑稿正文"), (ai, "只有优化稿正文")):
            params = {"version_id": version}
            images = client.get(f"{base}/screenshots", params=params).json()["images"]
            res = client.get(f"{base}/illustrated.zip", params=params)
            assert res.status_code == 200
            with ZipFile(io.BytesIO(res.content)) as archive:
                md = archive.read(next(n for n in archive.namelist() if n.endswith(".md"))).decode()
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["version_id"] == version
                for entry, image in zip(manifest["images"], images, strict=True):
                    assert entry["block_index"] == image["block_index"] and entry["file"] in md
                    assert archive.read(entry["file"]) == client.get(image["url"]).content
                if body is None:
                    assert all(md.count(seg["text"]) == 1 for seg in SEGMENTS)
                    assert md.index("images/2.jpg") < md.index("第二段正文") < md.index("第三段正文") < md.index("images/3.jpg")
                else:
                    assert all(image["block_index"] is None for image in images)
                    assert "第一段正文" not in md and md.count(body) == 1 and "截图时间线" in md


def test_image_failure_delivers_readable_transcript(tmp_path, monkeypatch):
    app, service, _, _ = build_test_app(tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError("没有可解码的视频轨")

    monkeypatch.setattr(FakePipeline, "resolve_screenshot_video", fail, raising=False)
    with TestClient(app) as client:
        task = service.wait_for_task(client.post("/api/tasks/transcribe", json={
            "source": "BV1xx411c7XD", "include_screenshots": True,
        }).json()["task_id"])
        assert task.status == "completed"
        base = f"/api/videos/{task.video_id}"
        assert client.get(f"{base}/document").json()["text"].strip() == "demo text"
        view = client.get(f"{base}/screenshots").json()
        assert view["status"] == "failed" and "视频轨" in view["reason"]
        assert client.get(f"{base}/illustrated.zip").status_code == 409


@pytest.mark.parametrize("restart", [False, True])
def test_retry_resumes_registered_transcript_after_image_interruption(tmp_path, monkeypatch, restart):
    app, service, db, library = build_test_app(tmp_path)
    entered, release = Event(), Event()
    calls = 0

    def resolve(self, source, *, progress, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(10)
            progress.check_cancelled()
        return Path("video.mp4")

    monkeypatch.setattr(FakePipeline, "resolve_screenshot_video", resolve, raising=False)
    monkeypatch.setattr(screenshots, "_duration", lambda *args: 2.)
    monkeypatch.setattr(screenshots, "_frame", lambda *args, **kwargs: b"image")
    task = service.submit_transcription(source="BV1xx411c7XD", provider="whisper", model="small", include_screenshots=True)
    try:
        assert entered.wait(10)
        video = db.get_task(task.id).video_id
        assert library.load_document(video).text == "demo text\n"
        service.cancel_task(task.id)
    finally:
        release.set()
    assert service.wait_for_task(task.id).status == "cancelled"
    service.close()
    if restart:
        # Simulate a process exit after text registration but before the terminal task update.
        pending = db.create_task(kind="transcription", source_input=task.source_input, provider=task.provider,
                                 model=task.model, video_id=video, include_screenshots=True)
        task = pending
    monkeypatch.setattr(FakePipeline, "transcribe", lambda *a, **kw: pytest.fail("retry ran ASR again"))
    new = TaskService(database=db, library=library, pipeline_factory=service.pipeline_factory)
    try:
        retried = new.retry_task(task.id)
        finished = new.wait_for_task(retried.id)
        assert finished.include_screenshots and finished.status == "completed" and finished.video_id == video
        assert len(db.list_videos()) == len(db.list_transcript_versions(video)) == 1
        key = screenshots.screenshot_key(Path(db.get_video(video)["metadata_path"]))
        assert screenshots.read_screenshots(db.settings, key)["status"] == "ready"
    finally:
        new.close()


def test_rebuild_index_keeps_images_with_the_matching_transcript(tmp_path, monkeypatch):
    from v2t.config import Settings
    from v2t.services.illustrated_export import screenshot_view
    from v2t.services.library import WorkspaceLibrary
    from v2t.storage.database import AppDatabase

    settings = Settings.from_workspace(tmp_path)
    db = AppDatabase(settings)
    library = WorkspaceLibrary(settings, db)
    monkeypatch.setattr(screenshots, "_duration", lambda *args: 1.)
    monkeypatch.setattr(screenshots, "_frame", lambda *args, **kwargs: b"jpeg")
    for name in ("z", "a"):
        metadata = settings.metadata_dir / f"{name}.json"
        metadata.write_text(json.dumps({"title": name}))
        (settings.transcripts_original_dir / f"{name}.txt").write_text(f"{name} 课程")
        library.index_existing_workspace()
        if name == "z":
            screenshots.capture_screenshots(settings, screenshots.screenshot_key(metadata), Path("video.mp4"))
    assert screenshot_view(db, library.load_document(1))["status"] == "ready"

    settings.app_db_path.unlink()
    rebuilt = AppDatabase(settings)
    lib = WorkspaceLibrary(settings, rebuilt)
    lib.index_existing_workspace()
    assert lib.load_document(1).title == "a"
    assert screenshot_view(rebuilt, lib.load_document(1))["status"] == "disabled"
    assert lib.load_document(2).title == "z"
    assert screenshot_view(rebuilt, lib.load_document(2))["images"][0]["url"] == "/api/videos/2/screenshots/1"
