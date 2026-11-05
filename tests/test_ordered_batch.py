from __future__ import annotations

import sqlite3
from threading import Event, Thread

import pytest
from fastapi.testclient import TestClient

from test_web import FakePipeline, build_test_app

BASE = "https://www.bilibili.com/video/BV19r4y1w7Nx/?p="


def test_ordered_batch_waits_for_illustrations_and_continues_after_cancel_or_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("V2T_TASK_WORKERS", "3")
    app, service, db, _ = build_test_app(tmp_path)
    parts = [f"{BASE}{i}" for i in (3, 4, 5, 6)]
    seen = []
    illustrating, release = Event(), Event()

    class Pipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            seen.append(source)
            if source == parts[2] and seen.count(source) == 1:
                raise RuntimeError("这一讲下载失败")
            return super().transcribe(source, **kwargs)

        def illustrate(self, source, key, *, publish_guard, **kwargs):
            if source == parts[0]:
                illustrating.set()
                assert release.wait(10)
            with publish_guard():
                pass

    service.pipeline_factory = lambda provider, model: Pipeline(db.settings, provider, model)
    with TestClient(app) as client:
        response = client.post("/api/tasks/batch", json={
            "ordered": True, "sources": parts, "include_screenshots": True, "prompt": "GMP",
        })
        try:
            assert response.status_code == 200
            items = response.json()["items"]
            assert [item["source_input"] for item in items] == parts
            assert all(item["include_screenshots"] and item["prompt"] == "GMP" for item in items)
            ids = [item["id"] for item in items]
            assert illustrating.wait(5)
            assert db.get_task(ids[0]).video_id is not None
            assert all(db.get_task(task_id).status == "queued" for task_id in ids[1:])
            # Other submissions can use spare workers while one lesson is busy.
            other = client.post("/api/tasks/transcribe", json={"source": BASE + "1"}).json()["task_id"]
            assert service.wait_for_task(other).status == "completed"
            later = client.post("/api/tasks/batch", json={"ordered": True, "sources": [BASE + "9", BASE + "10"]})
            later_ids = [item["id"] for item in later.json()["items"]]
            listed = client.get("/api/tasks").json()["items"]
            assert [item["id"] for item in listed] == [*later_ids, other, *ids]
            assert client.post(f"/api/tasks/{ids[1]}/cancel").json()["status"] == "cancelled"
        finally:
            release.set()

        assert service.wait_for_task(ids[3]).status == "completed"
        assert [db.get_task(i).status for i in ids] == ["completed", "cancelled", "failed", "completed"]
        assert [source for source in seen if source in parts] == [parts[0], parts[2], parts[3]]
        assert db.get_task(ids[0]).finished_at <= db.get_task(ids[2]).started_at
        assert db.get_task(ids[2]).finished_at <= db.get_task(ids[3]).started_at
        # The failed lesson can be retried on its own with the original options.
        retried = client.post(f"/api/tasks/{ids[2]}/retry").json()["task_id"]
        retried_task = service.wait_for_task(retried)
        assert (retried_task.status, retried_task.source_input, retried_task.include_screenshots, retried_task.prompt) == (
            "completed", parts[2], True, "GMP",
        )


def test_shutdown_finishes_ordered_selection_after_running_lesson_is_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("V2T_TASK_WORKERS", "2")
    _, service, db, _ = build_test_app(tmp_path)
    entered, release, closing = Event(), Event(), Event()
    parts = [BASE + "1", BASE + "2"]

    class Pipeline(FakePipeline):
        def transcribe(self, source, **kwargs):
            if source == parts[0]:
                entered.set()
                assert release.wait(10)
            return super().transcribe(source, **kwargs)

    service.pipeline_factory = lambda provider, model: Pipeline(db.settings, provider, model)
    shutdown = service.executor.shutdown

    def notify_shutdown(**kwargs):
        closing.set()
        shutdown(**kwargs)

    monkeypatch.setattr(service.executor, "shutdown", notify_shutdown)
    tasks = service.submit_ordered_transcriptions(sources=parts, provider="whisper", model="small")
    closer = Thread(target=service.close)
    closer.start()
    try:
        assert closing.wait(5) and entered.wait(5)
        assert service.cancel_task(tasks[0].id).cancel_requested
        assert db.get_task(tasks[1].id).status == "queued"
        with pytest.raises(RuntimeError, match="服务已关闭"):
            service.submit_ordered_transcriptions(sources=parts, provider="whisper", model="small")
    finally:
        release.set()
        closer.join(10)
    assert not closer.is_alive()
    assert [db.get_task(t.id).status for t in tasks] == ["cancelled", "completed"]
    assert db.get_task(tasks[0].id).finished_at <= db.get_task(tasks[1].id).started_at


def test_ordered_selection_insert_failure_does_not_schedule_a_partial_batch(tmp_path):
    _, service, db, _ = build_test_app(tmp_path)
    with sqlite3.connect(db.path) as conn:
        conn.execute("""CREATE TRIGGER fail_second BEFORE INSERT ON tasks WHEN NEW.source_input LIKE '%?p=2'
                        BEGIN SELECT RAISE(ABORT, 'failed to store selection'); END""")
    try:
        with pytest.raises(sqlite3.IntegrityError, match="failed to store selection"):
            service.submit_ordered_transcriptions(sources=[BASE + "1", BASE + "2"], provider="whisper", model="small")
        assert not service.list_tasks()
    finally:
        service.close()
