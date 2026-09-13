"""Same-directory atomic file writes with fsync of file and parent directory."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _fsync_directory(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_atomic(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Persist ``data`` at ``path`` via tempfile + fsync + atomic replace.

    The temporary file is created in the same directory so ``os.replace`` is
    atomic on POSIX. The parent directory is fsynced so the rename survives a
    crash. No backup copy of the previous contents is kept.
    """
    directory = path.parent
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".tmp-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        os.fchmod(fd, mode)
    except BaseException:
        os.close(fd)
        tmp_path.unlink(missing_ok=True)
        raise
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
        _fsync_directory(directory)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
