"""Persists WorkspaceSpecs to disk so a workspace can be recreated after its
tmux session disappears or WSL restarts. Stored under an XDG user-data
directory, keyed by canonical (resolved) project path -- never written
inside the user's own project directories.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dashboard.models import WorkspaceSpec

_STORE_FILENAME = "workspaces.json"
_APP_DIR_NAME = "terminal-home"


def default_store_path() -> Path:
    """The default workspaces.json location under XDG_DATA_HOME (or its
    conventional fallback, ~/.local/share, when unset).
    """
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _load_raw(store_path: Path) -> dict[str, object]:
    """Read the store file into a plain dict, tolerating any corruption --
    a missing file, invalid JSON, or a JSON value that isn't an object all
    yield an empty store rather than raising.
    """
    if not store_path.exists():
        return {}
    try:
        data = json.loads(store_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_workspace(spec: WorkspaceSpec, store_path: Path | None = None) -> None:
    """Persist *spec*, keyed by its canonical project path, merging into
    whatever else is already in the store.
    """
    store_path = store_path if store_path is not None else default_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    data = _load_raw(store_path)
    data[str(spec.project_path.resolve())] = spec.to_dict()
    store_path.write_text(json.dumps(data, indent=2))


def load_all_workspaces(store_path: Path | None = None) -> dict[str, WorkspaceSpec]:
    """Load every recoverable workspace from the store, keyed by canonical
    project path. Entries that fail to parse are silently skipped so one
    corrupt record can't take down the whole store.
    """
    store_path = store_path if store_path is not None else default_store_path()
    raw = _load_raw(store_path)

    workspaces: dict[str, WorkspaceSpec] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        try:
            workspaces[key] = WorkspaceSpec.from_dict(value)
        except (KeyError, TypeError, ValueError):
            continue
    return workspaces


def load_workspace(project_path: Path, store_path: Path | None = None) -> WorkspaceSpec | None:
    """Load the saved workspace for *project_path*, or None if there isn't
    one (missing, or dropped during malformed-data recovery).
    """
    workspaces = load_all_workspaces(store_path)
    return workspaces.get(str(project_path.resolve()))
