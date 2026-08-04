"""Versioned, location-aware persistence for reusable workspace metadata.

Schema 2 keys each workspace by a SHA-256 digest of its canonical project
location. Legacy unversioned and schema-1 local-path stores remain readable
and migrate only as part of a successful save or forget mutation.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dashboard.models import (
    LocalProjectLocation,
    ProjectLocation,
    SshProjectLocation,
    WorkspaceSpec,
)
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource

_STORE_FILENAME = "workspaces.json"
_APP_DIR_NAME = "terminal-home"
WORKSPACE_STORE_SCHEMA_VERSION = 2


class WorkspaceStoreError(Exception):
    """Base class for workspace persistence failures safe to show users."""


class WorkspaceStoreVersionError(WorkspaceStoreError):
    """Raised when an unsupported future store must be preserved."""


class _WorkspaceStoreCorruptError(Exception):
    pass


class _Generation(str, Enum):
    LEGACY = "legacy"
    V1 = "v1"
    V2 = "v2"


@dataclass(frozen=True, slots=True)
class _ParsedFile:
    entries: dict[str, object]
    generation: _Generation


@dataclass(frozen=True, slots=True)
class _WorkspaceCollectionResult:
    workspaces: dict[str, WorkspaceSpec]
    invalid_keys: frozenset[str]
    source: LoadSource
    generation: _Generation | None = None
    warning: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceLoadResult:
    workspace: WorkspaceSpec | None
    error: str | None = None
    source: LoadSource = LoadSource.DEFAULT
    warning: str | None = None


def default_store_path() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _canonical_location(location: ProjectLocation) -> ProjectLocation:
    if isinstance(location, LocalProjectLocation):
        return LocalProjectLocation(location.path.resolve())
    if isinstance(location, SshProjectLocation):
        return location
    raise TypeError("Unsupported project location type.")


def workspace_storage_key(location: ProjectLocation) -> str:
    """Return a stable full SHA-256 key for a canonical project location."""

    canonical = _canonical_location(location)
    encoded = json.dumps(
        canonical.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return f"loc-{hashlib.sha256(encoded).hexdigest()}"


def _parse_store_file(path: Path) -> _ParsedFile:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _WorkspaceStoreCorruptError from exc
    if not isinstance(data, dict):
        raise _WorkspaceStoreCorruptError
    if "schema_version" not in data and "workspaces" not in data:
        return _ParsedFile(data, _Generation.LEGACY)
    if set(data) != {"schema_version", "workspaces"}:
        raise _WorkspaceStoreCorruptError
    version = data["schema_version"]
    if type(version) is int and version > WORKSPACE_STORE_SCHEMA_VERSION:
        raise WorkspaceStoreVersionError(
            f"Workspace store schema version {version} is newer than this version "
            "of Terminal Home supports."
        )
    if type(version) is not int or version not in (1, 2):
        raise _WorkspaceStoreCorruptError
    entries = data["workspaces"]
    if not isinstance(entries, dict):
        raise _WorkspaceStoreCorruptError
    generation = _Generation.V1 if version == 1 else _Generation.V2
    return _ParsedFile(entries, generation)


def _legacy_workspace(key: str, value: object) -> WorkspaceSpec:
    if not isinstance(value, dict) or not isinstance(key, str):
        raise ValueError("Legacy workspace record is malformed.")
    outer_path = Path(key)
    if not outer_path.is_absolute():
        raise ValueError("Legacy workspace key is not an absolute path.")
    payload_path = value.get("project_path")
    if not isinstance(payload_path, str):
        raise ValueError("Legacy workspace project path is invalid.")
    if Path(payload_path).resolve() != outer_path.resolve():
        raise ValueError("Legacy workspace key and project path do not match.")
    if set(value) != {"project_name", "project_path", "session_name", "windows"}:
        raise ValueError("Legacy workspace record has invalid fields.")
    current = {
        "project_name": value["project_name"],
        "project_location": LocalProjectLocation(outer_path.resolve()).to_dict(),
        "session_name": value["session_name"],
        "windows": value["windows"],
    }
    return WorkspaceSpec.from_dict(current)


def _decode(parsed: _ParsedFile, source: LoadSource) -> _WorkspaceCollectionResult:
    workspaces: dict[str, WorkspaceSpec] = {}
    invalid_keys: set[str] = set()
    invalid = 0
    for outer_key, value in parsed.entries.items():
        try:
            if parsed.generation is _Generation.V2:
                if not isinstance(value, dict):
                    raise ValueError("Workspace record is malformed.")
                workspace = WorkspaceSpec.from_dict(value)
                expected_key = workspace_storage_key(workspace.project_location)
                if outer_key != expected_key:
                    raise ValueError("Workspace key does not match its location.")
            else:
                workspace = _legacy_workspace(outer_key, value)
                expected_key = workspace_storage_key(workspace.project_location)
            if expected_key in workspaces:
                raise ValueError("Duplicate canonical workspace location.")
        except (KeyError, TypeError, ValueError):
            invalid += 1
            invalid_keys.add(outer_key)
            continue
        workspaces[expected_key] = workspace
    warning = f"Skipped {invalid} invalid workspace record(s)." if invalid else None
    return _WorkspaceCollectionResult(
        workspaces, frozenset(invalid_keys), source, parsed.generation, warning
    )


def _load_collection(path: Path) -> _WorkspaceCollectionResult:
    if not path.exists():
        return _WorkspaceCollectionResult({}, frozenset(), LoadSource.DEFAULT)
    try:
        parsed = _parse_store_file(path)
    except WorkspaceStoreVersionError as exc:
        return _WorkspaceCollectionResult({}, frozenset(), LoadSource.PRIMARY, error=str(exc))
    except _WorkspaceStoreCorruptError:
        backup = backup_path_for(path)
        try:
            parsed = _parse_store_file(backup)
        except WorkspaceStoreVersionError as exc:
            return _WorkspaceCollectionResult(
                {},
                frozenset(),
                LoadSource.DEFAULT,
                error=f"Workspace backup cannot be loaded: {exc}",
            )
        except _WorkspaceStoreCorruptError:
            return _WorkspaceCollectionResult(
                {},
                frozenset(),
                LoadSource.DEFAULT,
                error=(
                    f"Workspace store {path} could not be loaded, and no valid backup is available."
                ),
            )
        result = _decode(parsed, LoadSource.BACKUP)
        recovered = f"Recovered workspace data from {backup} because {path} could not be loaded."
        warning = f"{recovered} {result.warning}" if result.warning else recovered
        return _WorkspaceCollectionResult(
            result.workspaces,
            result.invalid_keys,
            result.source,
            result.generation,
            warning,
        )
    return _decode(parsed, LoadSource.PRIMARY)


def _load_for_write(path: Path) -> _WorkspaceCollectionResult:
    result = _load_collection(path)
    if result.error:
        if path.exists():
            try:
                _parse_store_file(path)
            except WorkspaceStoreVersionError:
                raise
            except _WorkspaceStoreCorruptError:
                pass
        raise WorkspaceStoreError(result.error)
    return result


def ensure_workspace_store_writable(store_path: Path | None = None) -> None:
    """Validate writability without creating, repairing, or migrating data."""

    _load_for_write(store_path or default_store_path())


def _write_store(
    path: Path, workspaces: dict[str, WorkspaceSpec], *, preserve_existing: bool
) -> None:
    ordered = {key: workspaces[key].to_dict() for key in sorted(workspaces)}
    envelope = {"schema_version": WORKSPACE_STORE_SCHEMA_VERSION, "workspaces": ordered}
    serialized = json.dumps(envelope, indent=2, ensure_ascii=False)
    parsed = json.loads(serialized)
    if parsed.get("schema_version") != 2 or not isinstance(parsed.get("workspaces"), dict):
        raise WorkspaceStoreError("Serialized workspace store is invalid.")
    atomic_write_text(path, serialized, preserve_existing=preserve_existing)


def save_workspace(spec: WorkspaceSpec, store_path: Path | None = None) -> None:
    path = store_path or default_store_path()
    result = _load_for_write(path)
    canonical_location = _canonical_location(spec.project_location)
    canonical_spec = WorkspaceSpec(
        spec.project_name, canonical_location, spec.session_name, spec.windows
    )
    workspaces = dict(result.workspaces)
    workspaces[workspace_storage_key(canonical_location)] = canonical_spec
    _write_store(
        path,
        workspaces,
        preserve_existing=result.source is LoadSource.PRIMARY,
    )


def _compatibility_map(workspaces: dict[str, WorkspaceSpec]) -> dict[str, WorkspaceSpec]:
    result: dict[str, WorkspaceSpec] = {}
    for storage_key, workspace in workspaces.items():
        if isinstance(workspace.project_location, LocalProjectLocation):
            result[str(workspace.project_location.path.resolve())] = workspace
        else:
            result[storage_key] = workspace
    return result


def load_all_workspaces(store_path: Path | None = None) -> dict[str, WorkspaceSpec]:
    result = _load_collection(store_path or default_store_path())
    if result.error:
        return {}
    return _compatibility_map(result.workspaces)


def load_workspace_for_location(
    location: ProjectLocation, store_path: Path | None = None
) -> WorkspaceSpec | None:
    return load_workspace_result_for_location(location, store_path).workspace


def load_workspace(project_path: Path, store_path: Path | None = None) -> WorkspaceSpec | None:
    return load_workspace_for_location(LocalProjectLocation(project_path), store_path)


def load_workspace_result_for_location(
    location: ProjectLocation, store_path: Path | None = None
) -> WorkspaceLoadResult:
    path = store_path or default_store_path()
    result = _load_collection(path)
    if result.error:
        return WorkspaceLoadResult(None, result.error, result.source, result.warning)
    key = workspace_storage_key(location)
    workspace = result.workspaces.get(key)
    if workspace is not None:
        return WorkspaceLoadResult(workspace, source=result.source, warning=result.warning)

    # Preserve the existing detailed error for a malformed target record.
    canonical = _canonical_location(location)
    legacy_key = str(canonical.path) if isinstance(canonical, LocalProjectLocation) else None
    if key in result.invalid_keys or (legacy_key is not None and legacy_key in result.invalid_keys):
        return WorkspaceLoadResult(
            None,
            "Saved workspace data is invalid.",
            result.source,
            result.warning,
        )
    return WorkspaceLoadResult(None, source=result.source, warning=result.warning)


def load_workspace_result(
    project_path: Path, store_path: Path | None = None
) -> WorkspaceLoadResult:
    return load_workspace_result_for_location(LocalProjectLocation(project_path), store_path)


def forget_workspace_for_location(
    location: ProjectLocation, store_path: Path | None = None
) -> bool:
    path = store_path or default_store_path()
    result = _load_for_write(path)
    key = workspace_storage_key(location)
    if key not in result.workspaces:
        return False
    remaining = dict(result.workspaces)
    del remaining[key]
    _write_store(
        path,
        remaining,
        preserve_existing=result.source is LoadSource.PRIMARY,
    )
    return True


def forget_workspace(project_path: Path, store_path: Path | None = None) -> bool:
    return forget_workspace_for_location(LocalProjectLocation(project_path), store_path)
