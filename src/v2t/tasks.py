from __future__ import annotations

import os
import logging
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from v2t.database import AppDatabase
from v2t.inputs import AUDIO_SUFFIXES, VIDEO_SUFFIXES, parse_source
from v2t.library import WorkspaceLibrary
from v2t.models import TaskRecord
from v2t.pipeline import V2TPipeline
from v2t.progress import ProgressCallback, ProgressReporter, TaskCancelled
from v2t.workspace_lock import WorkspaceLock


PipelineFactory = Callable[[str, str], V2TPipeline]
logger = logging.getLogger(__name__)


def task_workers_from_env(default: int = 1) -> int:
    """Local ASR is CPU/GPU bound, so one worker is the sensible default; cloud-only setups can raise it."""
    try:
        value = int(os.getenv("V2T_TASK_WORKERS", "") or default)
    except ValueError:
        return default
    return max(1, value)


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
        listener: ProgressCallback | None = None,
    ) -> TaskRecord:
        return self._submit_transcription(
            source=source, provider=provider, model=model, prompt=prompt,
            force_asr=force_asr or not self.prefer_subtitles, listener=listener,
        )

    def _submit_transcription(
        self, *, source: str, provider: str, model: str, prompt: str, force_asr: bool,
        retranscribe_video_id: int | None = None, listener: ProgressCallback | None = None,
    ) -> TaskRecord:
        with self._submit_lock:
            if self._closed:
                raise RuntimeError("转写服务已关闭，请重新启动后再提交任务")
            try:
                parsed_source = parse_source(source)
            except ValueError:
                pass  # Invalid inputs still become ordinary failed tasks with their original input.
            else:
                if parsed_source.path is not None:
                    source = str(parsed_source.path)
            task = self.database.create_task(
                kind="transcription", source_input=source, provider=provider, model=model,
                prompt=prompt, force_asr=force_asr, retranscribe_video_id=retranscribe_video_id,
            )
            if listener is not None:
                self.add_listener(task.id, listener)
            ProgressReporter(task.id, callback=self._handle_progress).queued("queued")
            cancel_event = Event()
            future = self.executor.submit(
                self._run_transcription, task.id, source, provider, model, prompt, cancel_event, force_asr
            )
            with self._lock:
                self._futures[task.id] = future
                self._cancel_events[task.id] = cancel_event
            return task

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

    def _run_transcription(self, task_id: str, source: str, provider: str, model: str, prompt: str,
                           cancel_event: Event, force_asr: bool) -> None:
        reporter = ProgressReporter(task_id, callback=self._handle_progress, cancel_event=cancel_event)
        result = None
        try:
            reporter.running("preparing", message="preparing")
            pipeline = self.pipeline_factory(provider, model)
            # The persisted task owns this choice, including after configuration changes on restart.
            pipeline.prefer_subtitles = not force_asr
            options = {"force_asr": True} if force_asr else {}
            result = pipeline.transcribe(source, prompt=prompt or None, progress=reporter, **options)
            reporter.running("indexing", message="indexing", stage_progress=0.5)
            with self._lock:
                reporter.check_cancelled()
                video_id = self.library.register_transcript_result(result)
                self.database.complete_task(task_id, video_id=video_id, message="completed")
            reporter.completed("completed")
        except TaskCancelled:
            if result is not None:
                result.transcript_path.unlink(missing_ok=True)
                result.metadata_path.unlink(missing_ok=True)
            self.database.cancel_task(task_id)
            reporter.cancelled()
        except Exception as exc:
            if cancel_event.is_set():
                if result is not None:
                    result.transcript_path.unlink(missing_ok=True)
                    result.metadata_path.unlink(missing_ok=True)
                self.database.cancel_task(task_id)
                reporter.cancelled()
                return
            self.database.fail_task(task_id, error_message=str(exc))
            reporter.failed(str(exc))
            raise

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
