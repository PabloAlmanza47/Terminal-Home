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
from pathlib import Path

from dashboard.models.projects_config import ProjectsConfig, ProjectsConfigValidationError

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
    config_path = config_path if config_path is not None else default_projects_config_path()
    if not config_path.exists():
        return ProjectsConfig()
    try:
        data = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError):
        return ProjectsConfig()
    if not isinstance(data, dict):
        return ProjectsConfig()

    version = data.get("schema_version")
    if isinstance(version, int) and version > PROJECTS_CONFIG_SCHEMA_VERSION:
        return ProjectsConfig()

    config = data.get("config")
    if not isinstance(config, dict):
        return ProjectsConfig()
    try:
        return ProjectsConfig.from_dict(config)
    except (KeyError, TypeError, ValueError, ProjectsConfigValidationError):
        return ProjectsConfig()


def save_projects_config(config: ProjectsConfig, config_path: Path | None = None) -> None:
    """Persist *config*, overwriting whatever was previously saved."""
    config_path = config_path if config_path is not None else default_projects_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {"schema_version": PROJECTS_CONFIG_SCHEMA_VERSION, "config": config.to_dict()}
    config_path.write_text(json.dumps(envelope, indent=2))
