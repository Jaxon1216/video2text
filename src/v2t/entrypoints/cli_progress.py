from __future__ import annotations

from threading import Lock

from tqdm import tqdm

from v2t.i18n import tr
from v2t.models import ProgressSnapshot


class TqdmTaskRenderer:
    def __init__(self, language: str) -> None:
        self.language = language
        self._bar = tqdm(total=100, leave=False, dynamic_ncols=True)
        self._lock = Lock()
        self._last_stage = ""

    def __call__(self, snapshot: ProgressSnapshot) -> None:
        with self._lock:
            stage_label = tr(self.language, f"progress_stage_{snapshot.stage}")
            message = tr(self.language, f"progress_message_{snapshot.message}")
            description = stage_label if message == f"progress_message_{snapshot.message}" else f"{stage_label} | {message}"

            if snapshot.stage != self._last_stage:
                self._bar.write(description)
                self._last_stage = snapshot.stage

            self._bar.set_description_str(description)
            target = int(max(0.0, min(1.0, snapshot.percent)) * 100)
            if target < self._bar.n:
                self._bar.reset()
            self._bar.n = target
            self._bar.refresh()

            if snapshot.status in {"completed", "failed", "cancelled"}:
                self._bar.leave = True
                self._bar.refresh()
                self._bar.close()
