"""Persists ProjectsConfig (which directories to scan for projects, how
deep, and any manually registered ones) under XDG_CONFIG_HOME, in a
dedicated projects.json -- separate from settings.json (dashboard.models.
settings.AppSettings, home-screen presentation preferences) and from
workspace_store.py (per-project saved tmux layouts, its own schema and
versioning). Same general resilience principles as those stores: a
missing, corrupt, or partially invalid file is never a crash, only a
fallback to defaults.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dashboard.models.projects_config import ProjectsConfig, ProjectsConfigValidationError
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource

_CONFIG_FILENAME = "projects.json"
_APP_DIR_NAME = "terminal-home"

PROJECTS_CONFIG_SCHEMA_VERSION = 1


def default_projects_config_path() -> Path:
    """The default projects.json location under XDG_CONFIG_HOME (or its
    conventional fallback, ~/.config, when unset) -- the same directory
    settings.json lives in, since both are Terminal Home's own
    configuration rather than per-project data.
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / _APP_DIR_NAME / _CONFIG_FILENAME


@dataclass(frozen=True, slots=True)
class ProjectsConfigLoadResult:
    value: ProjectsConfig
    source: LoadSource
    warning: str | None = None
    unsupported_version: bool = False


def _load_projects_config_file(path: Path) -> tuple[ProjectsConfig | None, bool]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, False
    if not isinstance(data, dict):
        return None, False
    version = data.get("schema_version")
    if isinstance(version, int) and version > PROJECTS_CONFIG_SCHEMA_VERSION:
        return None, True
    config = data.get("config")
    if not isinstance(config, dict):
        return None, False
    try:
        return ProjectsConfig.from_dict(config), False
    except (KeyError, TypeError, ValueError, ProjectsConfigValidationError):
        return None, False


def load_projects_config_result(config_path: Path | None = None) -> ProjectsConfigLoadResult:
    config_path = config_path if config_path is not None else default_projects_config_path()
    if not config_path.exists():
        return ProjectsConfigLoadResult(ProjectsConfig(), LoadSource.DEFAULT)

    config, unsupported = _load_projects_config_file(config_path)
    if config is not None:
        return ProjectsConfigLoadResult(config, LoadSource.PRIMARY)
    if unsupported:
        return ProjectsConfigLoadResult(
            ProjectsConfig(),
            LoadSource.DEFAULT,
            f"Project configuration {config_path} uses a newer schema; defaults are in use.",
            unsupported_version=True,
        )

    backup_path = backup_path_for(config_path)
    backup, backup_unsupported = (
        _load_projects_config_file(backup_path) if backup_path.exists() else (None, False)
    )
    if backup is not None and not backup_unsupported:
        warning = (
            f"Recovered project configuration from {backup_path} because "
            f"{config_path} could not be loaded."
        )
        return ProjectsConfigLoadResult(backup, LoadSource.BACKUP, warning)
    return ProjectsConfigLoadResult(ProjectsConfig(), LoadSource.DEFAULT)


def load_projects_config(config_path: Path | None = None) -> ProjectsConfig:
    """Load the saved project-discovery configuration, falling back to
    ProjectsConfig() defaults for a missing file, invalid JSON, a schema
    version newer than this build understands, or any recognized field
    being malformed -- a broken config file must never crash the
    dashboard or block it from starting.

    Unlike workspace_store's schema-version handling, a newer version
    here simply falls back to defaults rather than raising a distinct,
    UI-visible error: this file only ever holds reconstructible scanning
    preferences (roots, depth, exclusions), never launch-critical data,
    so there is nothing irreversible at stake in reinterpreting it as
    "not configured yet" instead.
    """
    return load_projects_config_result(config_path).value


def save_projects_config(config: ProjectsConfig, config_path: Path | None = None) -> None:
    """Persist *config*, overwriting whatever was previously saved."""
    config_path = config_path if config_path is not None else default_projects_config_path()
    if config_path.exists():
        _, unsupported = _load_projects_config_file(config_path)
        if unsupported:
            raise ProjectsConfigValidationError(
                "Project configuration uses a newer schema and cannot be overwritten."
            )
    envelope = {"schema_version": PROJECTS_CONFIG_SCHEMA_VERSION, "config": config.to_dict()}
    serialized = json.dumps(envelope, indent=2)
    loaded = json.loads(serialized)
    ProjectsConfig.from_dict(loaded["config"])
    existing, _ = _load_projects_config_file(config_path) if config_path.exists() else (None, False)
    atomic_write_text(config_path, serialized, preserve_existing=existing is not None)
