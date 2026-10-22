from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class WorkspaceLock:
    """One task executor per workspace; the OS releases ownership after a crash."""

    def __init__(self, workspace: Path) -> None:
        self.path = workspace.resolve() / ".task-service.lock"
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                if self.path.stat().st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"工作区已有转写服务正在运行：{self.path.parent}。请先关闭该工作区的 Web 或 CLI 服务，再重试。"
            ) from exc
        except BaseException:
            handle.close()
            raise
        self._file = handle

    def release(self) -> None:
        if self._file is not None:
            # Keep the lock file in place: unlinking can create two independently locked inodes.
            self._file.close()
            self._file = None
