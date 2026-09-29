from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from v2t.database import AppDatabase
from v2t.formatters import export_document
from v2t.inputs import parse_source, parse_source_list, safe_stem
from v2t.library import WorkspaceLibrary
from v2t.models import TaskRecord, TranscriptDocument
from v2t.tasks import TaskService
from v2t.user_config import ALL_PROVIDERS, AppConfig
from v2t.model_catalog import resolve_model
from v2t.model_inventory import ModelInventory

DEFAULT_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
FRONTEND_MISSING_HTML = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>video2text</title>
<body style="font-family: serif; max-width: 40rem; margin: 4rem auto; line-height: 1.8">
<h1>前端还没有构建</h1>
<p>API 已在运行（<a href="/docs">/docs</a>）。构建一次 Web 界面：</p>
<pre>cd web &amp;&amp; npm install &amp;&amp; npm run build</pre>
<p>开发时也可以运行 <code>npm run dev</code>，打开 Vite 给出的地址。</p>
</body></html>"""


def resolve_web_dist() -> Path | None:
    override = os.getenv("V2T_WEB_DIST")
    path = Path(override).expanduser() if override else DEFAULT_WEB_DIST
    return path if (path / "index.html").is_file() else None


class TranscribeTaskRequest(BaseModel):
    source: str
    provider: str | None = None
    model: str | None = None
    prompt: str = ""


class BatchTranscribeTaskRequest(BaseModel):
    sources: list[str] | None = None
    source_text: str | None = None
    provider: str | None = None
    model: str | None = None
    prompt: str = ""


class TranscriptUpdateRequest(BaseModel):
    text: str


class CategoryRequest(BaseModel):
    name: str | None = None
    category_id: int | None = None


class TagRequest(BaseModel):
    name: str | None = None
    tag_id: int | None = None


def create_app(
    *,
    task_service: TaskService,
    library: WorkspaceLibrary,
    database: AppDatabase,
    default_provider: str | None = None,
    default_model: str | None = None,
    enabled_providers: list[str] | None = None,
    config: AppConfig | None = None,
    web_dist: Path | None = None,
) -> FastAPI:
    runtime_config = deepcopy(config) if config is not None else AppConfig()
    if default_provider is not None or default_model is not None:
        selected_provider = default_provider or runtime_config.default_provider
        runtime_config.default_model = resolve_model(runtime_config, selected_provider, default_model)
        runtime_config.default_provider = selected_provider
    if enabled_providers is not None:
        runtime_config.enabled_providers = list(enabled_providers)

    def task_options(provider: str | None, model: str | None) -> tuple[str, str]:
        selected_provider = (provider or runtime_config.default_provider).strip().lower()
        try:
            selected_model = resolve_model(runtime_config, selected_provider, model)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if selected_provider == "sensevoice" and not selected_model:
            raise HTTPException(status_code=400, detail="请先配置 SenseVoice 本地模型目录")
        return selected_provider, selected_model

    inventory = ModelInventory(runtime_config)
    app = FastAPI(title="video2text")
    dist = web_dist if web_dist is not None else resolve_web_dist()

    @app.post("/api/tasks/transcribe")
    async def create_transcription_task(payload: TranscribeTaskRequest) -> JSONResponse:
        _validate_sources([payload.source])
        provider, model = task_options(payload.provider, payload.model)
        task = task_service.submit_transcription(
            source=payload.source,
            provider=provider,
            model=model,
            prompt=payload.prompt,
        )
        return JSONResponse({"task_id": task.id, "status": task.status})

    @app.post("/api/tasks/batch")
    async def create_batch_transcription_tasks(payload: BatchTranscribeTaskRequest) -> JSONResponse:
        source_chunks = list(payload.sources or [])
        if payload.source_text:
            source_chunks.append(payload.source_text)
        try:
            sources = parse_source_list("\n".join(source_chunks))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _validate_sources(sources)
        provider, model = task_options(payload.provider, payload.model)
        tasks = _submit_transcription_tasks(
            task_service,
            sources=sources,
            provider=provider,
            model=model,
            prompt=payload.prompt,
        )
        return JSONResponse(
            {
                "items": [asdict(task) for task in tasks],
                "count": len(tasks),
            }
        )

    @app.get("/api/tasks")
    async def list_tasks(
        status: str | None = Query(None),
        provider: str | None = Query(None),
    ) -> JSONResponse:
        return JSONResponse(
            {
                "items": [asdict(task) for task in task_service.list_tasks() if (status is None or task.status == status) and (provider is None or task.provider == provider)],
                "filters": {"status": status, "provider": provider},
            }
        )

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str) -> JSONResponse:
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(asdict(task))

    @app.get("/api/tasks/{task_id}/progress")
    async def get_task_progress(task_id: str) -> JSONResponse:
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse(asdict(task))

    @app.get("/api/tasks/{task_id}/events")
    async def get_task_events(task_id: str) -> JSONResponse:
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse({"items": database.list_task_events(task_id)})

    @app.get("/api/videos")
    async def list_videos_api(
        query: str | None = Query(None),
        category_id: int | None = Query(None),
        tag_id: int | None = Query(None),
    ) -> JSONResponse:
        return JSONResponse(
            {
                "items": database.list_videos(query=query, category_id=category_id, tag_id=tag_id),
                "filters": {
                    "query": query,
                    "category_id": category_id,
                    "tag_id": tag_id,
                },
            }
        )

    @app.get("/api/videos/{video_id}")
    async def get_video_api(video_id: int) -> JSONResponse:
        video = database.get_video(video_id)
        if video is None:
            raise HTTPException(status_code=404, detail="video not found")
        return JSONResponse(video)

    @app.get("/api/videos/{video_id}/transcript")
    async def get_video_transcript(video_id: int, version_id: int | None = Query(None)) -> JSONResponse:
        if version_id is None:
            return JSONResponse(library.load_active_transcript(video_id))
        return JSONResponse(library.load_transcript_version(video_id, version_id))

    def load_document_or_404(video_id: int) -> TranscriptDocument:
        if database.get_video(video_id) is None:
            raise HTTPException(status_code=404, detail="video not found")
        return library.load_document(video_id)

    @app.get("/api/videos/{video_id}/document")
    async def get_video_document(video_id: int) -> JSONResponse:
        document = load_document_or_404(video_id)
        return JSONResponse({**asdict(document), "has_timestamps": document.has_timestamps})

    @app.get("/api/videos/{video_id}/segments")
    async def get_video_segments(video_id: int) -> JSONResponse:
        document = load_document_or_404(video_id)
        return JSONResponse(
            {
                "video_id": video_id,
                "has_timestamps": document.has_timestamps,
                "version_kind": document.version_kind,
                "transcript_source": document.transcript_source,
                "segments": document.segments,
            }
        )

    @app.get("/api/videos/{video_id}/export")
    async def export_video(video_id: int, format: str = Query("txt")) -> Response:
        document = load_document_or_404(video_id)
        try:
            exported = export_document(document, format)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        filename = f"{safe_stem(document.title)}.{exported.extension}"
        return Response(
            content=exported.content,
            media_type=f"{exported.media_type}; charset=utf-8",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    @app.get("/api/videos/{video_id}/metadata")
    async def get_video_metadata(video_id: int) -> JSONResponse:
        return JSONResponse(library.load_video_metadata(video_id))

    @app.put("/api/videos/{video_id}/transcript")
    async def update_video_transcript(video_id: int, payload: TranscriptUpdateRequest) -> JSONResponse:
        version_id = library.save_edited_transcript(video_id, payload.text)
        return JSONResponse({"video_id": video_id, "version_id": version_id})

    @app.get("/api/videos/{video_id}/versions")
    async def list_video_versions(video_id: int) -> JSONResponse:
        versions = [asdict(version) for version in database.list_transcript_versions(video_id)]
        return JSONResponse({"items": versions})

    @app.get("/api/videos/{video_id}/versions/{version_id}")
    async def get_video_version(video_id: int, version_id: int) -> JSONResponse:
        return JSONResponse(library.load_transcript_version(video_id, version_id))

    @app.post("/api/videos/{video_id}/versions/{version_id}/activate")
    async def activate_video_version(video_id: int, version_id: int) -> JSONResponse:
        database.activate_transcript_version(video_id, version_id)
        return JSONResponse({"video_id": video_id, "version_id": version_id})

    @app.get("/api/categories")
    async def list_categories_api() -> JSONResponse:
        return JSONResponse({"items": database.list_categories()})

    @app.post("/api/categories")
    async def create_category_api(payload: CategoryRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        category = database.create_category(payload.name)
        return JSONResponse(category)

    @app.put("/api/categories/{category_id}")
    async def update_category_api(category_id: int, payload: CategoryRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        category = database.update_category(category_id, payload.name)
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")
        return JSONResponse(category)

    @app.delete("/api/categories/{category_id}")
    async def delete_category_api(category_id: int) -> JSONResponse:
        database.delete_category(category_id)
        return JSONResponse({"category_id": category_id})

    @app.post("/api/videos/{video_id}/category")
    async def assign_category_api(video_id: int, payload: CategoryRequest) -> JSONResponse:
        category_id = payload.category_id
        if category_id is None and payload.name:
            category = database.create_category(payload.name)
            category_id = int(category["id"])
        database.assign_category(video_id, category_id)
        return JSONResponse({"video_id": video_id, "category_id": category_id})

    @app.get("/api/tags")
    async def list_tags_api() -> JSONResponse:
        return JSONResponse({"items": database.list_tags()})

    @app.post("/api/tags")
    async def create_tag_api(payload: TagRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        tag = database.create_tag(payload.name)
        return JSONResponse(tag)

    @app.put("/api/tags/{tag_id}")
    async def update_tag_api(tag_id: int, payload: TagRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        tag = database.update_tag(tag_id, payload.name)
        if tag is None:
            raise HTTPException(status_code=404, detail="tag not found")
        return JSONResponse(tag)

    @app.delete("/api/tags/{tag_id}")
    async def delete_tag_api(tag_id: int) -> JSONResponse:
        database.delete_tag(tag_id)
        return JSONResponse({"tag_id": tag_id})

    @app.post("/api/videos/{video_id}/tags")
    async def add_video_tag_api(video_id: int, payload: TagRequest) -> JSONResponse:
        tag_id = payload.tag_id
        if tag_id is None and payload.name:
            tag = database.create_tag(payload.name)
            tag_id = int(tag["id"])
        if tag_id is None:
            raise HTTPException(status_code=400, detail="tag is required")
        database.add_video_tag(video_id, tag_id)
        return JSONResponse({"video_id": video_id, "tag_id": tag_id})

    @app.delete("/api/videos/{video_id}/tags/{tag_id}")
    async def remove_video_tag_api(video_id: int, tag_id: int) -> JSONResponse:
        database.remove_video_tag(video_id, tag_id)
        return JSONResponse({"video_id": video_id, "tag_id": tag_id})

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/api/config")
    async def get_config() -> JSONResponse:
        return JSONResponse(
            {
                "default_provider": runtime_config.default_provider,
                "default_model": runtime_config.default_model,
                "providers": list(ALL_PROVIDERS),
                "enabled_providers": runtime_config.enabled_providers,
            }
        )

    @app.get("/api/models")
    def get_models(refresh: bool = Query(False)) -> JSONResponse:
        return JSONResponse(inventory.snapshot(refresh=refresh))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        if dist is None:
            return HTMLResponse(FRONTEND_MISSING_HTML, status_code=503)
        root = dist.resolve()
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        # Client-side routes such as /videos/3 all render the same index.html.
        return FileResponse(root / "index.html")

    return app


def _validate_sources(sources: list[str]) -> None:
    for source in sources:
        try:
            parse_source(source)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"无法识别的输入：{source[:80]}（{exc}）") from exc


def _submit_transcription_tasks(
    task_service: TaskService,
    *,
    sources: list[str],
    provider: str,
    model: str,
    prompt: str = "",
) -> list[TaskRecord]:
    return [
        task_service.submit_transcription(
            source=source,
            provider=provider,
            model=model,
            prompt=prompt,
        )
        for source in sources
    ]
