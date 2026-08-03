"""Persists AppSettings (home screen presentation preferences) to disk.

Deliberately kept separate from dashboard.services.workspace_store: that
module stores project *data* (recreatable workspace definitions) under
XDG_DATA_HOME, while this stores the dashboard's own *preferences* under
XDG_CONFIG_HOME -- the conventional XDG split between data and config.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from dashboard.models.settings import AppSettings

_SETTINGS_FILENAME = "settings.json"
_APP_DIR_NAME = "terminal-home"


def default_settings_path() -> Path:
    """The default settings.json location under XDG_CONFIG_HOME (or its
    conventional fallback, ~/.config, when unset).
    """
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / _APP_DIR_NAME / _SETTINGS_FILENAME


def load_settings(settings_path: Path | None = None) -> AppSettings:
    """Load saved settings, falling back to defaults for a missing file,
    invalid JSON, or a non-object payload -- a broken settings file must
    never crash the dashboard or block it from starting. Individual
    malformed fields (e.g. an unrecognized theme) are handled per-field by
    AppSettings.from_dict, which never discards the rest of the file for one
    bad field.
    """
    settings_path = settings_path if settings_path is not None else default_settings_path()
    if not settings_path.exists():
        return AppSettings()
    try:
        data = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    return AppSettings.from_dict(data)


def save_settings(settings: AppSettings, settings_path: Path | None = None) -> None:
    """Persist *settings*, overwriting whatever was previously saved."""
    settings_path = settings_path if settings_path is not None else default_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings.to_dict(), indent=2))
