import hashlib
from pathlib import Path

from fastapi.testclient import TestClient

from v2t.config import Settings
from v2t.database import AppDatabase
from v2t.library import WorkspaceLibrary
from v2t.models import SourceRef, TranscriptResult
from v2t.tasks import TaskService
from v2t.web import create_app


class FakePipeline:
    def __init__(self, settings: Settings, provider: str, model: str) -> None:
        self.settings = settings
        self.provider = provider
        self.model = model

    def transcribe(self, source: str, *, prompt: str | None = None, output: Path | None = None, progress=None) -> TranscriptResult:
        source_id = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
        transcript_path = self.settings.transcripts_original_dir / f"demo-{source_id}.txt"
        metadata_path = self.settings.metadata_dir / f"demo-{source_id}.json"
        transcript_path.write_text("demo text\n", encoding="utf-8")
        metadata_path.write_text("{}", encoding="utf-8")
        if progress is not None:
            progress.running("transcribing", message="transcribing", stage_progress=1.0)
        return TranscriptResult(
            source=SourceRef(raw_input=source, kind="bilibili", display_name="demo", url=source, bv="BV1xx411c7XD"),
            engine=self.provider,
            model=self.model,
            text="demo text",
            audio_path=self.settings.audio_dir / "demo.wav",
            transcript_path=transcript_path,
            metadata_path=metadata_path,
            video_path=self.settings.downloads_dir / "demo.mp4",
            metadata={"language": "zh", "download": {"title": "Demo Title"}},
        )


def build_test_app(tmp_path: Path, web_dist: Path | None = None):
    settings = Settings.from_workspace(tmp_path / ".v2t")
    database = AppDatabase(settings)
    library = WorkspaceLibrary(settings, database)
    service = TaskService(
        database=database,
        library=library,
        pipeline_factory=lambda provider, model: FakePipeline(settings, provider, model),
    )
    app = create_app(
        task_service=service,
        library=library,
        database=database,
        default_provider="sensevoice",
        default_model="base",
        enabled_providers=["sensevoice", "volcengine"],
        web_dist=web_dist,
    )
    return app, service, database, library


def test_frontend_is_served_with_history_fallback(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    app, _, _, _ = build_test_app(tmp_path, web_dist=dist)
    client = TestClient(app)

    assert client.get("/").text == "<div id=root></div>"
    assert client.get("/videos/3").text == "<div id=root></div>"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/../../etc/passwd").text == "<div id=root></div>"
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/health").json() == {"status": "ok"}


def test_missing_frontend_shows_build_hint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("V2T_WEB_DIST", str(tmp_path / "nowhere"))
    app, _, _, _ = build_test_app(tmp_path)
    response = TestClient(app).get("/")
    assert response.status_code == 503
    assert "npm run build" in response.text


def test_api_config_exposes_provider_defaults(tmp_path: Path) -> None:
    app, _, _, _ = build_test_app(tmp_path)
    config = TestClient(app).get("/api/config").json()
    assert config["default_provider"] == "sensevoice"
    assert config["default_model"] == "base"
    assert config["enabled_providers"] == ["sensevoice", "volcengine"]
    assert "faster-whisper" in config["providers"]


def test_api_rejects_unrecognized_source(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.post("/api/tasks/transcribe", json={"source": "https://example.com/watch?v=1"})
    assert response.status_code == 400
    assert "无法识别的输入" in response.json()["detail"]
    batch = client.post("/api/tasks/batch", json={"source_text": "BV1xx411c7XD\nnot a link"})
    assert batch.status_code == 400
    assert service.list_tasks() == []


def test_api_transcribe_returns_task_and_video_can_be_edited(tmp_path: Path) -> None:
    app, service, database, library = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "sensevoice",
            "model": "tiny",
            "prompt": "",
        },
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]

    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    video_response = client.get("/api/videos")
    assert video_response.status_code == 200
    assert len(video_response.json()["items"]) == 1

    transcript_response = client.get(f"/api/videos/{task.video_id}/transcript")
    assert transcript_response.status_code == 200
    assert transcript_response.json()["text"] == "demo text\n"

    update_response = client.put(
        f"/api/videos/{task.video_id}/transcript",
        json={"text": "edited text"},
    )
    assert update_response.status_code == 200

    transcript_after = client.get(f"/api/videos/{task.video_id}/transcript")
    assert transcript_after.json()["text"] == "edited text\n"


def test_api_cancel_distinguishes_missing_and_terminal_tasks(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)
    assert client.post("/api/tasks/missing/cancel").status_code == 404
    created = client.post("/api/tasks/transcribe", json={"source": "BV1xx411c7XD"}).json()
    task = service.wait_for_task(created["task_id"])
    assert task.status == "completed"
    response = client.post(f"/api/tasks/{task.id}/cancel")
    assert response.status_code == 409


def test_api_batch_transcribe_returns_multiple_tasks(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    response = client.post(
        "/api/tasks/batch",
        json={
            "source_text": "\n".join(
                [
                    "https://www.bilibili.com/video/BV1xx411c7XD",
                    "BV1yy411c7XD",
                ]
            ),
            "provider": "sensevoice",
            "model": "tiny",
            "prompt": "",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["items"]) == 2

    for item in payload["items"]:
        task = service.wait_for_task(item["id"])
        assert task.status == "completed"


def test_api_supports_categories_tags_and_versions(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    category = client.post("/api/categories", json={"name": "Research"}).json()
    assert category["name"] == "Research"
    tag = client.post("/api/tags", json={"name": "important"}).json()
    assert tag["name"] == "important"

    assign_category = client.post(
        f"/api/videos/{task.video_id}/category",
        json={"category_id": category["id"]},
    )
    assert assign_category.status_code == 200

    assign_tag = client.post(
        f"/api/videos/{task.video_id}/tags",
        json={"tag_id": tag["id"]},
    )
    assert assign_tag.status_code == 200

    versions = client.get(f"/api/videos/{task.video_id}/versions").json()["items"]
    assert len(versions) == 1

    client.put(f"/api/videos/{task.video_id}/transcript", json={"text": "second version"})
    versions = client.get(f"/api/videos/{task.video_id}/versions").json()["items"]
    assert len(versions) == 2

    activate = client.post(f"/api/videos/{task.video_id}/versions/{versions[-1]['id']}/activate")
    assert activate.status_code == 200


def test_api_exposes_task_events_and_filtered_video_queries(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    events = client.get(f"/api/tasks/{task_id}/events")
    assert events.status_code == 200
    assert len(events.json()["items"]) >= 2
    assert events.json()["items"][0]["task_id"] == task_id

    category = client.post("/api/categories", json={"name": "Research"}).json()
    tag = client.post("/api/tags", json={"name": "important"}).json()
    client.post(f"/api/videos/{task.video_id}/category", json={"category_id": category["id"]})
    client.post(f"/api/videos/{task.video_id}/tags", json={"tag_id": tag["id"]})

    filtered = client.get(f"/api/videos?query=demo&category_id={category['id']}&tag_id={tag['id']}")
    assert filtered.status_code == 200
    assert len(filtered.json()["items"]) == 1
    assert filtered.json()["items"][0]["id"] == task.video_id


def test_api_supports_category_tag_crud_and_version_detail(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "whisper",
            "model": "small",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    category = client.post("/api/categories", json={"name": "Research"}).json()
    updated_category = client.put(f"/api/categories/{category['id']}", json={"name": "Archive"}).json()
    assert updated_category["name"] == "Archive"

    tag = client.post("/api/tags", json={"name": "important"}).json()
    updated_tag = client.put(f"/api/tags/{tag['id']}", json={"name": "featured"}).json()
    assert updated_tag["name"] == "featured"

    client.put(f"/api/videos/{task.video_id}/transcript", json={"text": "edited once"})
    versions = client.get(f"/api/videos/{task.video_id}/versions").json()["items"]
    version_detail = client.get(f"/api/videos/{task.video_id}/versions/{versions[0]['id']}")
    assert version_detail.status_code == 200
    assert "text" in version_detail.json()

    delete_tag = client.delete(f"/api/tags/{updated_tag['id']}")
    assert delete_tag.status_code == 200
    delete_category = client.delete(f"/api/categories/{updated_category['id']}")
    assert delete_category.status_code == 200


def test_api_exposes_task_filters_and_video_metadata(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={
            "source": "https://www.bilibili.com/video/BV1xx411c7XD",
            "provider": "sensevoice",
            "model": "tiny",
            "prompt": "",
        },
    ).json()["task_id"]
    task = service.wait_for_task(task_id)
    assert task.video_id is not None

    filtered_tasks = client.get("/api/tasks?status=completed&provider=sensevoice")
    assert filtered_tasks.status_code == 200
    assert len(filtered_tasks.json()["items"]) == 1

    metadata = client.get(f"/api/videos/{task.video_id}/metadata")
    assert metadata.status_code == 200
    assert metadata.json()["engine"] == "sensevoice"


class FakePipelineWithSegments(FakePipeline):
    def transcribe(self, source: str, *, prompt: str | None = None, output: Path | None = None, progress=None) -> TranscriptResult:
        result = super().transcribe(source, prompt=prompt, output=output, progress=progress)
        result.metadata = {
            **result.metadata,
            "source": {"kind": "bilibili", "url": source, "webpage_url": "https://www.bilibili.com/video/BV1xx411c7XD"},
            "download": {"title": "Demo Title", "uploader": "UP主", "duration": 42.0},
            "transcript_source": "asr",
            "segments": [
                {"start": 0.0, "end": 3.0, "text": "demo"},
                {"start": 31.0, "end": 33.0, "text": "text"},
            ],
        }
        return result


def test_api_document_segments_and_export(tmp_path: Path) -> None:
    app, service, _, _ = build_test_app(tmp_path)
    service.pipeline_factory = lambda provider, model: FakePipelineWithSegments(service.library.settings, provider, model)
    client = TestClient(app)

    task_id = client.post(
        "/api/tasks/transcribe",
        json={"source": "https://www.bilibili.com/video/BV1xx411c7XD", "provider": "whisper", "model": "small"},
    ).json()["task_id"]
    video_id = service.wait_for_task(task_id).video_id

    document = client.get(f"/api/videos/{video_id}/document").json()
    assert document["title"] == "Demo Title"
    assert document["uploader"] == "UP主"
    assert document["url"] == "https://www.bilibili.com/video/BV1xx411c7XD"
    assert document["has_timestamps"] is True

    segments = client.get(f"/api/videos/{video_id}/segments").json()
    assert segments["version_kind"] == "original"
    assert len(segments["segments"]) == 2

    txt = client.get(f"/api/videos/{video_id}/export?format=txt")
    assert txt.status_code == 200
    assert txt.text == "[00:00] demo\n\n[00:31] text\n"
    assert "filename*=UTF-8''Demo-Title.txt" in txt.headers["content-disposition"]

    srt = client.get(f"/api/videos/{video_id}/export?format=srt")
    assert srt.headers["content-type"].startswith("application/x-subrip")

    assert client.get(f"/api/videos/{video_id}/export?format=docx").status_code == 400
    assert client.get("/api/videos/999/export").status_code == 404

    client.put(f"/api/videos/{video_id}/transcript", json={"text": "edited text"})
    edited = client.get(f"/api/videos/{video_id}/segments").json()
    assert edited == {**edited, "has_timestamps": False, "version_kind": "edited", "segments": []}
    assert client.get(f"/api/videos/{video_id}/export?format=txt").text == "edited text\n"
    assert client.get(f"/api/videos/{video_id}/export?format=srt").status_code == 400


def test_api_provider_aware_defaults_and_custom_models(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from v2t.user_config import AppConfig
    _, service, database, library = build_test_app(tmp_path)
    config = AppConfig(default_model='medium')
    config.sensevoice.model_dir = '/models/sensevoice'
    config.volcengine.api_key = 'must-not-leak'
    app = create_app(task_service=service, database=database, library=library, config=config)
    calls = []
    def submit(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(id='new', status='queued')
    monkeypatch.setattr(service, 'submit_transcription', submit)
    client = TestClient(app)
    for options, provider, model in [({}, 'faster-whisper', 'medium'), ({'provider': 'volcengine'}, 'volcengine', 'bigmodel'),
                                      ({'provider': 'whisper'}, 'whisper', 'small'),
                                      ({'provider': 'sensevoice'}, 'sensevoice', '/models/sensevoice'),
                                      ({'model': '/custom/model'}, 'faster-whisper', '/custom/model')]:
        response = client.post('/api/tasks/transcribe', json={'source': 'BV1xx411c7XD', **options})
        assert response.status_code == 200
        assert calls[-1]['provider'] == provider
        assert calls[-1]['model'] == model
    assert client.post('/api/tasks/transcribe', json={'source': 'BV1xx411c7XD', 'provider': 'unknown'}).status_code == 400
    catalog = client.get('/api/models')
    assert catalog.status_code == 200
    assert 'must-not-leak' not in catalog.text
    groups = {group['provider']: group for group in catalog.json()['items']}
    assert groups['faster-whisper']['models'][0]['id'] == 'large-v3-turbo'
    assert groups['volcengine']['default_model'] == 'bigmodel'


def test_batch_uses_runtime_model_defaults(tmp_path):
    from v2t.user_config import AppConfig
    _, service, database, library = build_test_app(tmp_path)
    app = create_app(task_service=service, database=database, library=library, config=AppConfig(default_model='medium'))
    response = TestClient(app).post('/api/tasks/batch', json={'sources': ['BV1xx411c7XD'], 'provider': 'volcengine'})
    task = response.json()['items'][0]
    assert task['provider'] == 'volcengine' and task['model'] == 'bigmodel'
    service.wait_for_task(task['id'])


def test_model_inventory_refresh(tmp_path):
    from v2t.user_config import AppConfig
    _, service, database, library = build_test_app(tmp_path)
    config = AppConfig(default_provider='whisper', default_model=str(tmp_path / 'custom.pt'))
    config.sensevoice.model_dir = str(tmp_path / 'absent-sensevoice')
    app = create_app(task_service=service, database=database, library=library, config=config)
    client = TestClient(app)
    def custom(response):
        group = next(g for g in response.json()['items'] if g['provider'] == 'whisper')
        return next(m for m in group['models'] if m['id'] == config.default_model)
    assert custom(client.get('/api/models'))['cache_status'] == 'missing'
    Path(config.default_model).write_text('weights')
    assert custom(client.get('/api/models'))['cache_status'] == 'missing'
    assert custom(client.get('/api/models?refresh=true'))['cache_status'] == 'found'
    assert service.list_tasks() == []
