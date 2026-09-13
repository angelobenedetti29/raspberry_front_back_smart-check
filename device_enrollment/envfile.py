"""Reading and surgical cleanup of explicitly selected env files.

Only the assignment(s) for the requested keys are removed. Comments, blank
lines and unrelated assignments are preserved byte-for-byte. No plaintext
backup of the previous file is created.
"""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from .atomic import write_atomic

_ASSIGN_RE = re.compile(
    r"^\s*(?:export\s+)?(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*="
)


def load_env_values(path: Path | None) -> dict[str, str]:
    """Parse ``KEY=VALUE`` assignments from ``path`` (no shell expansion)."""
    if path is None:
        return {}
    values: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        match = _ASSIGN_RE.match(line)
        if match is None:
            continue
        raw_value = line.split("=", 1)[1]
        values[match.group("key")] = raw_value.strip().strip('"').strip("'")
    return values


def remove_assignments(path: Path, keys: set[str]) -> int:
    """Remove every assignment for ``keys`` from ``path``.

    Returns the number of removed lines. The file is rewritten atomically while
    preserving its permission bits. Symlinked files are rejected.
    """
    if not path.exists():
        return 0
    if path.is_symlink():
        raise OSError(f"refusing to clean a symlinked env file: {path}")

    st = os.stat(path)
    mode = stat.S_IMODE(st.st_mode)
    original = path.read_text(encoding="utf-8")
    kept: list[str] = []
    removed = 0
    for line in original.splitlines(keepends=True):
        match = _ASSIGN_RE.match(line)
        if match is not None and match.group("key") in keys:
            removed += 1
            continue
        kept.append(line)

    if removed:
        updated = "".join(kept)
        if updated != original:
            write_atomic(path, updated.encode("utf-8"), mode=mode)
    return removed
