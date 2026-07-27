import fcntl
import os
from pathlib import Path
from types import TracebackType


class InstanceLockError(RuntimeError):
    """Raised when the process cannot exclusively own the instance lock."""


class InstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "InstanceLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+", encoding="utf-8")
        except OSError as exc:
            raise InstanceLockError(f"cannot open instance lock {self.path}: {exc}") from exc

        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise InstanceLockError(
                f"another alphawave-taskd instance holds {self.path}"
            ) from exc
        except OSError as exc:
            handle.close()
            raise InstanceLockError(f"cannot acquire instance lock {self.path}: {exc}") from exc

        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError as exc:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            raise InstanceLockError(f"cannot initialize instance lock {self.path}: {exc}") from exc

        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
