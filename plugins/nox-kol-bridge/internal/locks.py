"""POSIX file lock for reserve-then-call."""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

from internal.paths import nox_cache_root


@contextlib.contextmanager
def file_lock(name: str = "nox_global"):
    """Exclusive lock under cache root."""
    path = nox_cache_root() / f".{name}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
