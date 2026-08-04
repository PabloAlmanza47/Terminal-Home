"""Versioned local configuration storage for remote project registrations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dashboard.models import RemoteProjectRegistration, SshModelValidationError
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource

REMOTE_PROJECT_STORE_SCHEMA_VERSION = 1
_APP_DIR_NAME = "terminal-home"
_STORE_FILENAME = "remote_projects.json"


class RemoteProjectStoreError(Exception):
    """Base class for user-facing remote project store errors."""


class DuplicateRemoteProjectIdError(RemoteProjectStoreError):
    """Raised when a registration ID is already stored."""


class DuplicateRemoteProjectLocationError(RemoteProjectStoreError):
    """Raised when a host/path pair is already registered."""


class RemoteProjectStoreVersionError(RemoteProjectStoreError):
    """Raised when a newer store version blocks a mutation."""


class _CorruptStoreError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class RemoteProjectLoadResult:
    projects: tuple[RemoteProjectRegistration, ...]
    source: LoadSource = LoadSource.DEFAULT
    warning: str | None = None
    error: str | None = None
    unsupported_version: bool = False


def default_remote_project_store_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _parse_file(path: Path) -> list[object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _CorruptStoreError from exc
    if not isinstance(data, dict):
        raise _CorruptStoreError
    version = data.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        if version > REMOTE_PROJECT_STORE_SCHEMA_VERSION:
            raise RemoteProjectStoreVersionError(
                f"Remote project store schema version {version} is newer than this version "
                "of Terminal Home supports."
            )
    if (
        version != REMOTE_PROJECT_STORE_SCHEMA_VERSION
        or isinstance(version, bool)
        or not isinstance(data.get("projects"), list)
    ):
        raise _CorruptStoreError
    return data["projects"]


def _sort(
    projects: list[RemoteProjectRegistration],
) -> tuple[RemoteProjectRegistration, ...]:
    return tuple(
        sorted(
            projects,
            key=lambda item: (item.host_id, item.name.casefold(), item.remote_path, item.id),
        )
    )


def load_remote_projects_result(store_path: Path | None = None) -> RemoteProjectLoadResult:
    path = store_path or default_remote_project_store_path()
    if not path.exists():
        return RemoteProjectLoadResult(())
    try:
        raw = _parse_file(path)
        source = LoadSource.PRIMARY
        warning = None
    except RemoteProjectStoreVersionError as exc:
        return RemoteProjectLoadResult(
            (), LoadSource.PRIMARY, error=str(exc), unsupported_version=True
        )
    except _CorruptStoreError:
        backup = backup_path_for(path)
        try:
            raw = _parse_file(backup)
        except RemoteProjectStoreVersionError as exc:
            return RemoteProjectLoadResult(
                (), error=f"Remote project backup cannot be loaded: {exc}"
            )
        except _CorruptStoreError:
            return RemoteProjectLoadResult(
                (),
                error=(
                    f"Remote project store {path} could not be loaded, and no valid "
                    "backup is available."
                ),
            )
        source = LoadSource.BACKUP
        warning = f"Recovered remote project data from {backup} because {path} could not be loaded."

    projects: list[RemoteProjectRegistration] = []
    invalid = 0
    ids: set[str] = set()
    locations: set[tuple[str, str]] = set()
    for value in raw:
        if not isinstance(value, dict):
            invalid += 1
            continue
        try:
            project = RemoteProjectRegistration.from_dict(value)
        except (KeyError, TypeError, SshModelValidationError):
            invalid += 1
            continue
        location = (project.host_id, project.remote_path)
        if project.id in ids or location in locations:
            invalid += 1
            continue
        ids.add(project.id)
        locations.add(location)
        projects.append(project)
    if invalid:
        suffix = f"Skipped {invalid} invalid remote project record(s)."
        warning = f"{warning} {suffix}" if warning else suffix
    return RemoteProjectLoadResult(_sort(projects), source, warning)


def load_all_remote_projects(
    store_path: Path | None = None,
) -> tuple[RemoteProjectRegistration, ...]:
    return load_remote_projects_result(store_path).projects


def get_remote_project(
    registration_id: str, store_path: Path | None = None
) -> RemoteProjectRegistration | None:
    return next(
        (
            project
            for project in load_all_remote_projects(store_path)
            if project.id == registration_id
        ),
        None,
    )


def list_remote_projects_for_host(
    host_id: str, store_path: Path | None = None
) -> tuple[RemoteProjectRegistration, ...]:
    return tuple(
        project for project in load_all_remote_projects(store_path) if project.host_id == host_id
    )


def host_has_remote_projects(host_id: str, store_path: Path | None = None) -> bool:
    return any(project.host_id == host_id for project in load_all_remote_projects(store_path))


def _load_for_write(path: Path) -> list[RemoteProjectRegistration]:
    if not path.exists():
        return []
    try:
        _parse_file(path)
    except RemoteProjectStoreVersionError:
        raise
    except _CorruptStoreError:
        result = load_remote_projects_result(path)
        return list(result.projects)
    result = load_remote_projects_result(path)
    if result.error:
        raise RemoteProjectStoreError(result.error)
    return list(result.projects)


def _write(path: Path, projects: list[RemoteProjectRegistration]) -> None:
    envelope = {
        "schema_version": REMOTE_PROJECT_STORE_SCHEMA_VERSION,
        "projects": [project.to_dict() for project in _sort(projects)],
    }
    serialized = json.dumps(envelope, indent=2)
    json.loads(serialized)
    preserve = False
    if path.exists():
        try:
            _parse_file(path)
        except _CorruptStoreError:
            pass
        else:
            preserve = True
    atomic_write_text(path, serialized, preserve_existing=preserve)


def create_remote_project(
    project: RemoteProjectRegistration, store_path: Path | None = None
) -> RemoteProjectRegistration:
    path = store_path or default_remote_project_store_path()
    projects = _load_for_write(path)
    if any(item.id == project.id for item in projects):
        raise DuplicateRemoteProjectIdError(
            f"A remote project with ID {project.id} already exists."
        )
    if any(
        (item.host_id, item.remote_path) == (project.host_id, project.remote_path)
        for item in projects
    ):
        raise DuplicateRemoteProjectLocationError(
            "That remote host and project path are already registered."
        )
    projects.append(project)
    _write(path, projects)
    return project


def update_remote_project(
    registration_id: str,
    *,
    name: str,
    remote_path: str,
    host_id: str | None = None,
    store_path: Path | None = None,
) -> RemoteProjectRegistration | None:
    path = store_path or default_remote_project_store_path()
    projects = _load_for_write(path)
    target = next((item for item in projects if item.id == registration_id), None)
    if target is None:
        return None
    replacement = RemoteProjectRegistration(
        target.id, target.host_id if host_id is None else host_id, name, remote_path
    )
    if any(
        item.id != target.id
        and (item.host_id, item.remote_path) == (replacement.host_id, replacement.remote_path)
        for item in projects
    ):
        raise DuplicateRemoteProjectLocationError(
            "That remote host and project path are already registered."
        )
    _write(path, [replacement if item.id == target.id else item for item in projects])
    return replacement


def delete_remote_project(registration_id: str, store_path: Path | None = None) -> bool:
    path = store_path or default_remote_project_store_path()
    projects = _load_for_write(path)
    remaining = [project for project in projects if project.id != registration_id]
    if len(remaining) == len(projects):
        return False
    _write(path, remaining)
    return True
