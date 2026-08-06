"""Bounded, symlink-safe helpers for local project indicator files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_READ = 1_048_576


def is_regular_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def is_directory(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_dir()
    except OSError:
        return False


def read_bounded(path: Path) -> tuple[str | None, str | None]:
    if not is_regular_file(path):
        return None, None
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_READ + 1)
    except (OSError, UnicodeError) as exc:
        return None, f"{path.name} unreadable: {exc.__class__.__name__}"
    if len(data) > MAX_READ:
        return None, f"{path.name} exceeds the {MAX_READ} byte inspection limit"
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, f"{path.name} is not valid UTF-8"


def load_json_object(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    text, error = read_bounded(path)
    if error or text is None:
        return None, error
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"{path.name} is malformed JSON at line {exc.lineno}"
    if not isinstance(value, dict):
        return None, f"{path.name} must contain a JSON object"
    return value, None
