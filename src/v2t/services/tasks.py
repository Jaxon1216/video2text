from __future__ import annotations

import os
import logging
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from v2t.storage.database import AppDatabase
from v2t.inputs import AUDIO_SUFFIXES, VIDEO_SUFFIXES, parse_source
from v2t.services.library import WorkspaceLibrary
from v2t.models import TaskRecord
from v2t.services.pipeline import V2TPipeline
from v2t.services import screenshots
from v2t.progress import ProgressCallback, ProgressReporter, TaskCancelled
from v2t.storage.workspace_lock import WorkspaceLock


PipelineFactory = Callable[[str, str], V2TPipeline]
logger = logging.getLogger(__name__)


def task_workers_from_env(default: int = 1) -> int:
    """Local ASR is CPU/GPU bound, so one worker is the sensible default; cloud-only setups can raise it."""
    try:
        value = int(os.getenv("V2T_TASK_WORKERS", "") or default)
    except ValueError:
        return default
    return max(1, value)


def _task_source(source: str) -> str:
    try:
        parsed = parse_source(source)
    except ValueError:
        return source  # Invalid inputs still become ordinary failed tasks with their original input.
    return str(parsed.path) if parsed.path is not None else source


class TaskService:
    def __init__(
        self, *, database: AppDatabase, library: WorkspaceLibrary, pipeline_factory: PipelineFactory,
        prefer_subtitles: bool = True,
    ) -> None:
        self.database = database
        self.library = library
        self.pipeline_factory = pipeline_factory
        self.prefer_subtitles = prefer_subtitles
        self._workspace_lock = WorkspaceLock(database.settings.workspace_root)
        self._workspace_lock.acquire()
        try:
            self.database.recover_interrupted_tasks()
            screenshots.clean_stale_screenshot_staging(database.settings)
            self.executor = ThreadPoolExecutor(max_workers=task_workers_from_env(), thread_name_prefix="v2t-task")
        except BaseException:
            self._workspace_lock.release()
            raise
        self._listeners: dict[str, list[ProgressCallback]] = {}
        self._futures: dict[str, Future[object]] = {}
        self._cancel_events: dict[str, Event] = {}
        self._lock = Lock()
        self._submit_lock = Lock()
        self._close_lock = Lock()
        self._closed = False
        self._logged_stages: dict[str, tuple[str, str]] = {}

    def close(self) -> None:
        """Stop accepting work, finish accepted tasks, then release workspace ownership."""
        with self._close_lock:
            with self._submit_lock:
                self._closed = True
            self.executor.shutdown(wait=True)
            self._workspace_lock.release()

    def ensure_indexed(self) -> None:
        self.library.index_existing_workspace()

    def submit_transcription(
        self,
        *,
        source: str,
        provider: str,
        model: str,
        prompt: str = "",
        force_asr: bool = False,
        include_screenshots: bool = False,
        listener: ProgressCallback | None = None,
    ) -> TaskRecord:
        return self._submit_transcription(
            source=source, provider=provider, model=model, prompt=prompt,
            force_asr=force_asr or not self.prefer_subtitles, listener=listener, include_screenshots=include_screenshots,
        )

    def _submit_transcription(
        self, *, source: str, provider: str, model: str, prompt: str, force_asr: bool,
        retranscribe_video_id: int | None = None, listener: ProgressCallback | None = None,
        include_screenshots: bool = False, resume_video_id: int | None = None,
    ) -> TaskRecord:
        with self._submit_lock:
            if self._closed:
                raise RuntimeError("转写服务已关闭，请重新启动后再提交任务")
            if resume_video_id is not None:
                latest = self.database.screenshot_task(resume_video_id)
                if latest and latest.status in {"queued", "running"}:
                    raise ValueError("这篇文字稿已有配图任务正在进行")
            task = self.database.create_task(
                kind="transcription", source_input=_task_source(source), provider=provider, model=model,
                prompt=prompt, force_asr=force_asr, retranscribe_video_id=retranscribe_video_id,
                include_screenshots=include_screenshots, video_id=resume_video_id,
            )
            if listener is not None:
                self.add_listener(task.id, listener)
            ProgressReporter(task.id, callback=self._handle_progress).queued("queued")
            cancel_event = Event()
            future = self.executor.submit(
                self._run_transcription, task, cancel_event,
            )
            with self._lock:
                self._futures[task.id] = future
                self._cancel_events[task.id] = cancel_event
            return task

    def submit_ordered_transcriptions(
        self, *, sources: list[str], provider: str, model: str, prompt: str = "", include_screenshots: bool = False,
    ) -> list[TaskRecord]:
        with self._submit_lock, self._lock:
            if self._closed:
                raise RuntimeError("转写服务已关闭，请重新启动后再提交任务")
            tasks = self.database.create_tasks(
                kind="transcription", sources=[_task_source(source) for source in sources],
                provider=provider, model=model, prompt=prompt, force_asr=not self.prefer_subtitles,
                include_screenshots=include_screenshots, queue_events=True,
            )
            for task in tasks:
                self._futures[task.id] = Future()
                self._cancel_events[task.id] = Event()
            try:
                self.executor.submit(self._run_ordered_transcriptions, tasks)
            except RuntimeError as exc:
                for task in tasks:
                    self._futures[task.id].cancel()
                    self.database.fail_task(task.id, error_message=str(exc))
                raise
            return tasks

    def _run_ordered_transcriptions(self, tasks: list[TaskRecord]) -> None:
        # One worker runs the entire selection; no worker waits for a predecessor.
        for task in tasks:
            with self._lock:
                future = self._futures[task.id]
                if not future.set_running_or_notify_cancel():
                    continue
                cancel_event = self._cancel_events[task.id]
            try:
                self._run_transcription(task, cancel_event)
            except Exception as exc:
                # _run_transcription has recorded the failure. Continue with the next selection.
                future.set_exception(exc)
            else:
                future.set_result(None)

    def submit_retranscription(
        self, *, video_id: int, provider: str, model: str, prompt: str = ""
    ) -> TaskRecord:
        source = self.library.prepare_retranscription_source(video_id)
        return self._submit_transcription(
            source=source, provider=provider, model=model, prompt=prompt, force_asr=True,
            retranscribe_video_id=video_id,
        )

    def retry_task(self, task_id: str) -> TaskRecord:
        task = self.database.get_task(task_id)
        if task is None:
            raise KeyError(task_id)
        if task.status not in ("failed", "cancelled"):
            raise ValueError("只有失败或已中断的任务可以重试")
        if not Path(task.source_input).expanduser().is_absolute():
            try:
                source = parse_source(task.source_input)
            except ValueError:
                local_source = Path(task.source_input).suffix.lower() in AUDIO_SUFFIXES | VIDEO_SUFFIXES
            else:
                local_source = source.path is not None
            if local_source:
                raise ValueError("旧任务使用相对本地路径，无法确定原文件位置，请使用绝对路径重新提交")
        if task.retranscribe_video_id is not None:
            self.library.prepare_retranscription_source(task.retranscribe_video_id)
        return self._submit_transcription(
            source=task.source_input, provider=task.provider, model=task.model,
            prompt=task.prompt, force_asr=task.force_asr,
            retranscribe_video_id=task.retranscribe_video_id,
            include_screenshots=task.include_screenshots,
            resume_video_id=task.video_id if task.include_screenshots else None,
        )

    def cancel_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            task = self.database.get_task(task_id)
            if task is None:
                raise KeyError(task_id)
            if task.status in ("completed", "failed"):
                raise ValueError(f"task already {task.status}")
            if task.status == "cancelled":
                return task
            event = self._cancel_events.get(task_id)
            if event is None:
                raise RuntimeError("task is not active in this process")
            event.set()
            future = self._futures.get(task_id)
            queued = future is not None and future.cancel()
            if queued:
                self.database.cancel_task(task_id)
            else:
                self.database.request_cancel(task_id)
        if queued:
            ProgressReporter(task_id, callback=self._handle_progress).cancelled()
        return self.database.get_task(task_id)

    def wait_for_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            future = self._futures.get(task_id)
        if future is not None:
            try:
                future.result()
            except CancelledError:
                pass
        task = self.database.get_task(task_id)
        if task is None:
            raise RuntimeError(f"task not found: {task_id}")
        return task

    def add_listener(self, task_id: str, callback: ProgressCallback) -> None:
        with self._lock:
            self._listeners.setdefault(task_id, []).append(callback)

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self.database.get_task(task_id)

    def list_tasks(self) -> list[TaskRecord]:
        return self.database.list_tasks()

    def _run_transcription(self, task: TaskRecord, cancel_event: Event) -> None:
        task_id = task.id
        reporter = ProgressReporter(task_id, callback=self._handle_progress, cancel_event=cancel_event)
        result = None
        video_id = task.video_id
        try:
            reporter.running("preparing", message="preparing")
            pipeline = self.pipeline_factory(task.provider, task.model)
            if video_id is None:
                pipeline.prefer_subtitles = not task.force_asr
                options = {"force_asr": True} if task.force_asr else {}
                result = pipeline.transcribe(task.source_input, prompt=task.prompt or None, progress=reporter, **options)
                reporter.running("indexing", message="indexing", stage_progress=0.5)
                with self._lock:
                    reporter.check_cancelled()
                    video_id = self.library.register_transcript_result(result)
                    if task.include_screenshots:
                        self.database.attach_transcript(task_id, video_id)
                    else:
                        self.database.complete_task(task_id, video_id=video_id, message="completed")
            message = self._illustrate(pipeline, task, video_id, reporter) if task.include_screenshots else "completed"
            reporter.completed(message)
        except TaskCancelled:
            if result is not None and video_id is None:
                result.transcript_path.unlink(missing_ok=True)
                result.metadata_path.unlink(missing_ok=True)
            self.database.cancel_task(task_id)
            reporter.cancelled()
        except Exception as exc:
            if cancel_event.is_set():
                if result is not None and video_id is None:
                    result.transcript_path.unlink(missing_ok=True)
                    result.metadata_path.unlink(missing_ok=True)
                self.database.cancel_task(task_id)
                reporter.cancelled()
                return
            self.database.fail_task(task_id, error_message=str(exc))
            reporter.failed(str(exc))
            raise

    def _illustrate(self, pipeline: V2TPipeline, task: TaskRecord, video_id: int, reporter: ProgressReporter) -> str:
        settings = self.database.settings
        video = self.database.get_video(video_id)
        key = screenshots.screenshot_key(Path(video["metadata_path"]))

        @contextmanager
        def publish_and_complete():
            with self._lock:
                reporter.check_cancelled()
                yield
                self.database.complete_task(task.id, video_id=video_id, message="completed")

        try:
            existing = screenshots.read_screenshots(settings, key)
            if existing and existing["status"] == "ready":
                # A restart may have interrupted the task just after the atomic image publish.
                with publish_and_complete():
                    pass
                return "completed"
            pipeline.illustrate(
                video["source_url"] or task.source_input, key, publish_guard=publish_and_complete,
                video_path=Path(video["video_path"]) if video["video_path"] else None, progress=reporter,
            )
        except TaskCancelled:
            raise
        except Exception as exc:
            message = f"文字稿已完成，配图失败：{exc}"
            with self._lock:
                reporter.check_cancelled()
                screenshots.record_screenshot_failure(settings, key, str(exc))
                self.database.complete_task(task.id, video_id=video_id, message=message)
            return message
        return "completed"

    def _handle_progress(self, snapshot) -> None:  # type: ignore[no-untyped-def]
        self.database.record_progress(snapshot)
        with self._lock:
            state = (snapshot.status, snapshot.stage)
            changed = self._logged_stages.get(snapshot.task_id) != state
            self._logged_stages[snapshot.task_id] = state
            if snapshot.status in ("completed", "failed", "cancelled"):
                self._logged_stages.pop(snapshot.task_id, None)
            callbacks = list(self._listeners.get(snapshot.task_id, []))
        if changed:
            logger.log(logging.ERROR if snapshot.status == "failed" else logging.INFO,
                       "task=%s stage=%s %s", snapshot.task_id, snapshot.stage, snapshot.message)
        for callback in callbacks:
            callback(snapshot)
