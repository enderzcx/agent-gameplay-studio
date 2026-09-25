"""Atomic private JSON and persistent OS file locking for a local job directory.
Locks are kernel-held and NEVER unlinked, renamed, or inferred dead from a PID.
Local APFS/NTFS/ext4 only; network filesystems are not certified.
Cloud self-test: Linux, five tests incl spawn concurrency/crash. Windows untested.
"""
from __future__ import annotations
import contextlib
import errno
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

class StateIOError(RuntimeError):
    pass

def _private_dir(root: Path) -> Path:
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise StateIOError("job directory must be a real directory")
    return root

def _path(root: Path, name: str) -> Path:
    if not isinstance(name, str) or name in ("", ".", "..") or any(c in name for c in "/\\\0"):
        raise StateIOError("invalid state filename")
    return _private_dir(root) / name

def _regular(fd: int) -> None:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise StateIOError("state/lock must be a regular file")

@contextlib.contextmanager
def local_lock(root: Path, name: str, timeout: float = 10.0):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
        raise StateIOError("lock timeout must be finite and non-negative")
    path = _path(root, name)
    if path.is_symlink():
        raise StateIOError("symlink lock refused")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    acquired = False
    try:
        _regular(fd)
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        if os.name == "nt":
            import msvcrt
            def take():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            def release():
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            def take():
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            def release():
                fcntl.flock(fd, fcntl.LOCK_UN)
        deadline = time.monotonic() + timeout
        while True:
            try:
                take()
                acquired = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise StateIOError("job is busy; no lock was removed") from exc
                time.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
        live = os.stat(path, follow_symlinks=False)
        own = os.fstat(fd)
        if (live.st_dev, live.st_ino) != (own.st_dev, own.st_ino):
            raise StateIOError("lock path changed during acquisition")
        yield
    finally:
        if acquired:
            release()
        os.close(fd)

def publish_json(root: Path, name: str, value: object, *, create: bool = False) -> None:
    """Caller holds local_lock for RMW; create also provides no-clobber publication."""
    path = _path(root, name)
    if path.is_symlink():
        raise StateIOError("symlink state refused")
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=".state-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        if create:
            os.link(tmp, path)
        else:
            os.replace(tmp, path)
        if os.name != "nt":
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass

def load_json(root: Path, name: str):
    path = _path(root, name)
    if path.is_symlink():
        raise StateIOError("symlink state refused")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    def bad_constant(value):
        raise StateIOError("non-finite JSON constant: " + value)
    try:
        _regular(fd)
        with os.fdopen(fd, "r", encoding="utf-8") as src:
            fd = -1
            return json.load(src, parse_constant=bad_constant)
    finally:
        if fd >= 0:
            os.close(fd)
