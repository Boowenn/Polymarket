import atexit
import os
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows fallback
    msvcrt = None

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None


class ProcessLease:
    def __init__(self, name):
        safe_name = str(name or "runtime").strip().replace(" ", "_")
        self.path = Path(__file__).resolve().parent / f".{safe_name}.lock"
        self._handle = None
        self._acquired = False

    def acquire(self):
        if self._acquired:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            if os.name == "nt" and msvcrt is not None:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            elif fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:  # pragma: no cover - unsupported platform fallback
                raise OSError("no supported file-lock implementation")
            handle.seek(0)
            handle.truncate()
            handle.write(str(os.getpid()).encode("ascii", "replace"))
            handle.flush()
        except OSError:
            handle.close()
            return False

        self._handle = handle
        self._acquired = True
        atexit.register(self.release)
        return True

    def release(self):
        if not self._acquired or self._handle is None:
            return
        try:
            self._handle.seek(0)
            if os.name == "nt" and msvcrt is not None:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self._handle.close()
        finally:
            self._handle = None
            self._acquired = False
