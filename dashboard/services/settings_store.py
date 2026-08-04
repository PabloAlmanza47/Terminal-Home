"""Persists AppSettings (home screen presentation preferences) to disk.

Deliberately kept separate from dashboard.services.workspace_store: that
module stores project *data* (recreatable workspace definitions) under
XDG_DATA_HOME, while this stores the dashboard's own *preferences* under
XDG_CONFIG_HOME -- the conventional XDG split between data and config.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dashboard.models.settings import AppSettings
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource

_SETTINGS_FILENAME = "settings.json"
_APP_DIR_NAME = "terminal-home"


def default_settings_path() -> Path:
    """The default settings.json location under XDG_CONFIG_HOME (or its
    conventional fallback, ~/.config, when unset).
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / _APP_DIR_NAME / _SETTINGS_FILENAME


@dataclass(frozen=True, slots=True)
class SettingsLoadResult:
    value: AppSettings
    source: LoadSource
    warning: str | None = None


def _load_settings_file(path: Path) -> AppSettings | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return AppSettings.from_dict(data)


def load_settings_result(settings_path: Path | None = None) -> SettingsLoadResult:
    settings_path = settings_path if settings_path is not None else default_settings_path()
    if not settings_path.exists():
        return SettingsLoadResult(AppSettings(), LoadSource.DEFAULT)

    settings = _load_settings_file(settings_path)
    if settings is not None:
        return SettingsLoadResult(settings, LoadSource.PRIMARY)

    backup_path = backup_path_for(settings_path)
    backup = _load_settings_file(backup_path) if backup_path.exists() else None
    if backup is not None:
        warning = (
            f"Recovered settings from {backup_path} because {settings_path} could not be loaded."
        )
        return SettingsLoadResult(backup, LoadSource.BACKUP, warning)
    return SettingsLoadResult(AppSettings(), LoadSource.DEFAULT)


def load_settings(settings_path: Path | None = None) -> AppSettings:
    """Load saved settings, falling back to defaults for a missing file,
    invalid JSON, or a non-object payload -- a broken settings file must
    never crash the dashboard or block it from starting. Individual
    malformed fields (e.g. an unrecognized theme) are handled per-field by
    AppSettings.from_dict, which never discards the rest of the file for one
    bad field.
    """
    return load_settings_result(settings_path).value


def save_settings(settings: AppSettings, settings_path: Path | None = None) -> None:
    """Persist *settings*, overwriting whatever was previously saved."""
    settings_path = settings_path if settings_path is not None else default_settings_path()
    serialized = json.dumps(settings.to_dict(), indent=2)
    # Serialization is complete and round-trip validated before touching disk.
    parsed = json.loads(serialized)
    AppSettings.from_dict(parsed)
    preserve_existing = settings_path.exists() and _load_settings_file(settings_path) is not None
    atomic_write_text(settings_path, serialized, preserve_existing=preserve_existing)
