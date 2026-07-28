"""Cross-process project execution locks shared by TUI and channels."""

from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import os
from pathlib import Path
import threading
from typing import BinaryIO


class ProjectExecutionLock(AbstractContextManager["ProjectExecutionLock"]):
    """One re-entrant process lock backed by an operating-system file lock."""

    _thread_locks: dict[str, threading.RLock] = {}
    _registry_lock = threading.Lock()

    def __init__(self, project_root: str | Path) -> None:
        project = Path(project_root).resolve()
        digest = hashlib.sha256(str(project).casefold().encode("utf-8")).hexdigest()[:24]
        root = project / ".rook" / "locks"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"project-{digest}.lock"
        self._stream: BinaryIO | None = None
        key = str(self.path)
        with self._registry_lock:
            self._thread_lock = self._thread_locks.setdefault(key, threading.RLock())

    def __enter__(self) -> "ProjectExecutionLock":
        self._thread_lock.acquire()
        try:
            self._stream = self.path.open("a+b")
            stream = self._stream
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
            return self
        except BaseException:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
            self._thread_lock.release()
            raise

    def __exit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        try:
            if self._stream is not None:
                if os.name == "nt":
                    import msvcrt

                    self._stream.seek(0)
                    msvcrt.locking(self._stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    unlock_flag = fcntl.LOCK_UN  # type: ignore[attr-defined]
                    fcntl.flock(  # type: ignore[attr-defined]
                        self._stream.fileno(),
                        unlock_flag,
                    )
                self._stream.close()
                self._stream = None
        finally:
            self._thread_lock.release()


__all__ = ["ProjectExecutionLock"]
