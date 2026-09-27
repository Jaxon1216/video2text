from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from v2t.models import ProgressSnapshot


ProgressCallback = Callable[[ProgressSnapshot], None]


STAGE_RANGES: dict[str, tuple[float, float]] = {
    "queued": (0.0, 0.0),
    "preparing": (0.0, 0.05),
    "downloading": (0.05, 0.35),
    "extracting_audio": (0.35, 0.55),
    "transcribing": (0.55, 0.9),
    "writing_outputs": (0.9, 0.96),
    "indexing": (0.96, 0.99),
    "completed": (1.0, 1.0),
    "failed": (0.0, 0.0),
}


def overall_progress(stage: str, stage_progress: float | None = None) -> float:
    start, end = STAGE_RANGES.get(stage, (0.0, 0.0))
    if start == end:
        return start
    if stage_progress is None:
        return start
    bounded = max(0.0, min(1.0, stage_progress))
    return start + (end - start) * bounded


class ProgressReporter:
    def __init__(self, task_id: str, callback: ProgressCallback | None = None) -> None:
        self.task_id = task_id
        self.callback = callback
        self.snapshot = ProgressSnapshot(task_id=task_id, status="queued", stage="queued")

    def emit(
        self,
        *,
        status: str,
        stage: str,
        message: str = "",
        stage_progress: float | None = None,
        percent: float | None = None,
        indeterminate: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> ProgressSnapshot:
        snapshot = ProgressSnapshot(
            task_id=self.task_id,
            status=status,  # type: ignore[arg-type]
            stage=stage,
            message=message,
            percent=percent if percent is not None else overall_progress(stage, stage_progress),
            indeterminate=indeterminate,
            detail=detail or {},
        )
        self.snapshot = snapshot
        if self.callback is not None:
            self.callback(snapshot)
        return snapshot

    def queued(self, message: str = "") -> ProgressSnapshot:
        return self.emit(status="queued", stage="queued", message=message)

    def running(
        self,
        stage: str,
        *,
        message: str = "",
        stage_progress: float | None = None,
        indeterminate: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> ProgressSnapshot:
        return self.emit(
            status="running",
            stage=stage,
            message=message,
            stage_progress=stage_progress,
            indeterminate=indeterminate,
            detail=detail,
        )

    def completed(self, message: str = "") -> ProgressSnapshot:
        return self.emit(status="completed", stage="completed", message=message, percent=1.0)

    def failed(self, message: str = "") -> ProgressSnapshot:
        return self.emit(
            status="failed",
            stage="failed",
            message=message,
            percent=self.snapshot.percent,
            detail=replace(self.snapshot).detail,
        )
