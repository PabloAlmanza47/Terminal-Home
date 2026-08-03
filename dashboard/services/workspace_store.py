"""Persists WorkspaceSpecs to disk so a workspace can be recreated after its
tmux session disappears or WSL restarts. Stored under an XDG user-data
directory, keyed by canonical (resolved) project path -- never written
inside the user's own project directories.

On disk, the store is a versioned envelope:

    {"schema_version": 1, "workspaces": {"<canonical path>": {...}, ...}}

A store written before versioning existed is a flat dict of the same
entries with no envelope at all -- that legacy shape is still read
correctly (see _parse_store), but is never rewritten just because it was
read. It's only migrated to the envelope the next time something is
actually saved or forgotten, since every write in this module goes through
_write_store, which always emits the current envelope.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dashboard.models import WorkspaceSpec

_STORE_FILENAME = "workspaces.json"
_APP_DIR_NAME = "terminal-home"

WORKSPACE_STORE_SCHEMA_VERSION = 1


class WorkspaceStoreVersionError(Exception):
    """Raised when a store file's schema_version is newer than
    WORKSPACE_STORE_SCHEMA_VERSION -- the one case that must never be
    silently misread as the current version. load_workspace_result turns
    this into a friendly .error message; save_workspace/forget_workspace
    let it propagate rather than risk overwriting a store from a newer
    version of Terminal Home they don't know how to interpret.
    """


def default_store_path() -> Path:
    """The default workspaces.json location under XDG_DATA_HOME (or its
    conventional fallback, ~/.local/share, when unset).
    """
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _parse_store(store_path: Path) -> dict[str, object]:
    """The raw, unvalidated workspace entries in *store_path*, keyed by
    canonical project path -- the one place both the current envelope and
    the legacy unversioned flat-dict format are understood.

    Tolerates a missing file, invalid JSON, or a JSON value that isn't an
    object by returning an empty dict, same as every other tolerant read
    in this module. Raises WorkspaceStoreVersionError -- and only that --
    when schema_version is present and newer than
    WORKSPACE_STORE_SCHEMA_VERSION.
    """
    if not store_path.exists():
        return {}
    try:
        data = json.loads(store_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    if "schema_version" not in data and "workspaces" not in data:
        # Legacy, unversioned format: every top-level key is itself a
        # canonical-path -> workspace-dict entry.
        return data

    version = data.get("schema_version")
    if isinstance(version, int) and version > WORKSPACE_STORE_SCHEMA_VERSION:
        raise WorkspaceStoreVersionError(
            f"Workspace store schema version {version} is newer than this "
            "version of Terminal Home supports."
        )
    workspaces = data.get("workspaces")
    return workspaces if isinstance(workspaces, dict) else {}


def ensure_workspace_store_writable(store_path: Path | None = None) -> None:
    """Raise WorkspaceStoreVersionError if this build cannot safely write
    *store_path* -- a read-only preflight for callers about to take other
    irreversible action (creating a project directory, running `git init`)
    that would need to be undone if the store then turned out to be from a
    newer, unwritable version of Terminal Home.

    Performs no write of its own -- reuses the same centralized parsing
    _parse_store already applies everywhere else, so there is exactly one
    place that decides what counts as an unsupported schema version.
    Returns normally for a missing store, a valid legacy store, a valid
    current-version store, or any other state _parse_store already treats
    as safely (re)writable. The actual write path (save_workspace /
    forget_workspace) still re-parses and can still raise on its own,
    since the store could change between this preflight and that write.
    """
    store_path = store_path if store_path is not None else default_store_path()
    _parse_store(store_path)


def _write_store(store_path: Path, entries: dict[str, object]) -> None:
    """Write *entries* as the current versioned envelope. The only place
    that writes the store file, so every save/forget rewrites (and
    thereby migrates) the whole file to WORKSPACE_STORE_SCHEMA_VERSION --
    never eagerly, only as a side effect of a real save or forget.
    """
    envelope = {"schema_version": WORKSPACE_STORE_SCHEMA_VERSION, "workspaces": entries}
    store_path.write_text(json.dumps(envelope, indent=2))


def save_workspace(spec: WorkspaceSpec, store_path: Path | None = None) -> None:
    """Persist *spec*, keyed by its canonical project path, merging into
    whatever else is already in the store.
    """
    store_path = store_path if store_path is not None else default_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    entries = dict(_parse_store(store_path))
    entries[str(spec.project_path.resolve())] = spec.to_dict()
    _write_store(store_path, entries)


def load_all_workspaces(store_path: Path | None = None) -> dict[str, WorkspaceSpec]:
    """Load every recoverable workspace from the store, keyed by canonical
    project path. Entries that fail to parse are silently skipped so one
    corrupt record can't take down the whole store.
    """
    store_path = store_path if store_path is not None else default_store_path()
    try:
        raw = _parse_store(store_path)
    except WorkspaceStoreVersionError:
        # A store from a newer version of Terminal Home must never be
        # silently misread as the current version -- degrade to "nothing
        # loaded", the same fail-safe already applied to corrupt JSON.
        return {}

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


@dataclass(frozen=True, slots=True)
class WorkspaceLoadResult:
    """The outcome of looking up one project's saved workspace, keeping
    "nothing saved" distinguishable from "something was saved but it's
    corrupt" -- the Project Detail screen shows a friendly warning (and an
    option to forget the bad entry) only in the latter case.
    """

    workspace: WorkspaceSpec | None
    error: str | None = None


def load_workspace_result(
    project_path: Path, store_path: Path | None = None
) -> WorkspaceLoadResult:
    """Like load_workspace, but reports *why* nothing came back when the
    store has an entry for *project_path* that failed to parse, or when
    the store itself is a schema version newer than this build understands.
    """
    store_path = store_path if store_path is not None else default_store_path()
    try:
        raw = _parse_store(store_path)
    except WorkspaceStoreVersionError as exc:
        return WorkspaceLoadResult(workspace=None, error=str(exc))

    key = str(project_path.resolve())
    if key not in raw:
        return WorkspaceLoadResult(workspace=None, error=None)

    value = raw[key]
    if not isinstance(value, dict):
        return WorkspaceLoadResult(
            workspace=None, error="Saved workspace data is not in the expected format."
        )
    try:
        return WorkspaceLoadResult(workspace=WorkspaceSpec.from_dict(value), error=None)
    except (KeyError, TypeError, ValueError) as exc:
        return WorkspaceLoadResult(workspace=None, error=f"Saved workspace data is invalid: {exc}")


def forget_workspace(project_path: Path, store_path: Path | None = None) -> bool:
    """Remove *project_path*'s saved workspace entry, if any.

    Only ever touches terminal-home's own metadata store -- never the
    project directory, its git data, or any tmux session. Returns whether
    an entry was actually removed.
    """
    store_path = store_path if store_path is not None else default_store_path()
    entries = dict(_parse_store(store_path))
    key = str(project_path.resolve())
    if key not in entries:
        return False
    del entries[key]
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _write_store(store_path, entries)
    return True
