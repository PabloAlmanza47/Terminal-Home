"""Small atomic file-writing primitives for Terminal Home's JSON stores.

Atomic replacement prevents partial files, but deliberately does not provide
locking: concurrent valid saves remain last-writer-wins.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TEMP_PREFIX = ".terminal-home-"
_TEMP_SUFFIX = ".tmp"


def backup_path_for(path: Path) -> Path:
    """Return the single-generation backup path for *path*."""
    return path.with_name(f"{path.name}.bak")


def _write_temp(parent: Path, data: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=_TEMP_SUFFIX, dir=parent)
    temp_path = Path(name)
    complete = False
    try:
        with os.fdopen(descriptor, "wb") as temp_file:
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        complete = True
    finally:
        if not complete:
            temp_path.unlink(missing_ok=True)
    return temp_path


def _fsync_directory_best_effort(directory: Path) -> None:
    """Best-effort directory durability after replacement.

    At this point replacement has already happened, so an fsync failure cannot
    honestly be reported as an unchanged write. Some platforms/filesystems do
    not support directory fsync; suppress those post-commit failures.
    """
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            pass
    finally:
        os.close(descriptor)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    preserve_existing: bool,
    encoding: str = "utf-8",
) -> None:
    """Atomically replace *path* with *text* and optionally rotate its bytes.

    The caller owns store-specific validation and sets ``preserve_existing``
    only when the current primary is a valid generation. All temporary files
    live beside the destination so ``os.replace`` remains atomic.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    primary_temp: Path | None = None
    backup_temp: Path | None = None
    try:
        primary_temp = _write_temp(path.parent, text.encode(encoding))

        if preserve_existing and path.exists():
            previous = path.read_bytes()
            backup_temp = _write_temp(path.parent, previous)
            os.replace(backup_temp, backup_path_for(path))
            backup_temp = None

        os.replace(primary_temp, path)
        primary_temp = None
        _fsync_directory_best_effort(path.parent)
    finally:
        if primary_temp is not None:
            primary_temp.unlink(missing_ok=True)
        if backup_temp is not None:
            backup_temp.unlink(missing_ok=True)
