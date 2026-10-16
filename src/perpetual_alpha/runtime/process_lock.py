"""Kernel-owned local process lock. Closing/crashing releases it; never unlink a live lock inode."""
import os
from pathlib import Path


class ProcessLock:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.stream.write(b"0")
                    self.stream.flush()
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except OSError:
            self.stream.close()
            self.stream = None
            raise RuntimeError("Another process owns this state directory") from None

    def __exit__(self, *_):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
