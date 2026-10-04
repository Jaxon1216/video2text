# Retranscription Service Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Move retranscription preparation and submission out of the FastAPI route while preserving its HTTP behavior and legacy WAV reuse.

**Architecture:** `WorkspaceLibrary.prepare_retranscription_source(video_id)` owns source lookup and best-effort legacy audio import. `TaskService.submit_retranscription(...)` calls that method and submits a forced-ASR task. The route validates model options, maps missing videos to HTTP 404, and returns the task ID.

**Tech Stack:** Python 3.10–3.12, FastAPI, SQLite, pytest.

## Global Constraints

- Keep SQLite + `ThreadPoolExecutor`; do not introduce Redis, a message queue, or a new database.
- Downloader, Transcriber, and Pipeline boundaries stay unchanged.
- Tests must not access the network.
- Ordinary transcription stays subtitle-first; retranscription remains forced ASR and saves a separate result.
- Update `AGENTS.md` and `docs/architecture.md` for module responsibility changes.

---

## File map

- `src/v2t/library.py`: add the workspace operation that checks the video, parses its source, and imports matching old WAV files.
- `src/v2t/tasks.py`: add the public service method that submits a retranscription task.
- `src/v2t/web.py`: delegate the route to `TaskService` and translate `KeyError`/`ValueError` to HTTP status codes.
- `tests/test_tasks.py`: test direct service behavior, including missing videos and forced ASR.
- `tests/test_web.py`: retain existing HTTP and legacy-cache regression tests.
- `AGENTS.md`, `docs/architecture.md`: document the new boundary.

### Task 1: Retranscription use case

**Files:**
- Modify: `tests/test_tasks.py`
- Modify: `src/v2t/library.py`
- Modify: `src/v2t/tasks.py`
- Modify: `src/v2t/web.py:205-224`
- Modify: `AGENTS.md`
- Modify: `docs/architecture.md`

**Interfaces:**
- Consumes: `AppDatabase.get_video(int)`, `WorkspaceLibrary.load_video_metadata(int)`, `AudioCache.import_legacy(SourceRef, Path, dict)`, `TaskService.submit_transcription(...)`.
- Produces: `WorkspaceLibrary.prepare_retranscription_source(video_id: int) -> str`; `TaskService.submit_retranscription(*, video_id: int, provider: str, model: str, prompt: str = "") -> TaskRecord`.

- [x] **Step 1: Write the failing service tests**

Add the following to `tests/test_tasks.py`:

```python
import pytest


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
    service.executor.shutdown()


def test_submit_retranscription_rejects_missing_video_without_creating_task(tmp_path):
    settings = Settings.from_workspace(tmp_path)
    database = AppDatabase(settings)
    service = TaskService(database=database, library=WorkspaceLibrary(settings, database), pipeline_factory=None)
    with pytest.raises(KeyError, match="999"):
        service.submit_retranscription(video_id=999, provider="whisper", model="small")
    assert database.list_tasks() == []
    service.executor.shutdown()
```

- [x] **Step 2: Verify red**

Run: `/Users/eastonjiang/code/ideas/video2text/.venv/bin/python -m pytest tests/test_tasks.py -q`
Expected: the two new tests fail because `TaskService.submit_retranscription` is absent.

- [x] **Step 3: Implement the workspace and service methods**

In `src/v2t/library.py`, import `AudioCache`, `parse_source`, and `REMOTE_SOURCE_KINDS`, then add:

```python
def prepare_retranscription_source(self, video_id: int) -> str:
    video = self.database.get_video(video_id)
    if video is None:
        raise KeyError(video_id)
    source_input = str(video["source_input"])
    source = parse_source(source_input)
    if source.kind in REMOTE_SOURCE_KINDS and video.get("audio_path"):
        try:
            metadata = self.load_video_metadata(video_id)
            AudioCache(self.settings).import_legacy(source, Path(str(video["audio_path"])), metadata)
        except (OSError, ValueError):
            pass
    return source_input
```

In `src/v2t/tasks.py`, add:

```python
def submit_retranscription(
    self, *, video_id: int, provider: str, model: str, prompt: str = ""
) -> TaskRecord:
    source = self.library.prepare_retranscription_source(video_id)
    return self.submit_transcription(
        source=source, provider=provider, model=model, prompt=prompt, force_asr=True
    )
```

Replace the retranscription route body in `src/v2t/web.py` with:

```python
if database.get_video(video_id) is None:
    raise HTTPException(status_code=404, detail="video not found")
provider, model = task_options(payload.provider, payload.model)
try:
    task = task_service.submit_retranscription(
        video_id=video_id, provider=provider, model=model, prompt=payload.prompt
    )
except KeyError as exc:
    raise HTTPException(status_code=404, detail="video not found") from exc
except ValueError as exc:
    raise HTTPException(status_code=400, detail=str(exc)) from exc
return JSONResponse({"task_id": task.id, "status": task.status})
```

Remove `AudioCache` and `Path` imports from `web.py` if no other route uses them; `parse_source` remains used by `_validate_sources`.

- [x] **Step 4: Verify green and regressions**

Run: `/Users/eastonjiang/code/ideas/video2text/.venv/bin/python -m pytest tests/test_tasks.py tests/test_web.py -q`
Expected: all selected tests pass; existing retranscription endpoint and legacy WAV tests remain green.

- [x] **Step 5: Update documentation and commit**

Update the `tasks.py` and `library.py` descriptions in `AGENTS.md` and `docs/architecture.md` to say retranscription preparation is in the library and task submission is in TaskService. Then run `/Users/eastonjiang/code/ideas/video2text/.venv/bin/python -m pytest -q` (expected: baseline suite stays green) and commit with `refactor: move retranscription workflow into services`.

## Self-review

- Coverage: direct service success/missing-video cases, existing HTTP and WAV-cache tests, docs.
- Interfaces: `prepare_retranscription_source` returns the raw input string consumed by `submit_transcription`.
- Scope: no new ASR, API shape, storage engine, or deployment behavior.
