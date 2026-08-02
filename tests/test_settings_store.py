"""Tests for settings persistence (dashboard.services.settings_store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.services.settings_store import (
    default_settings_path,
    load_settings,
    save_settings,
)


def test_load_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    settings = load_settings(tmp_path / "missing.json")
    assert settings == AppSettings()


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings = AppSettings(
        artwork_enabled=False, layout_mode=LayoutMode.COMPACT, clock_visible=False
    )

    save_settings(settings, settings_path)

    assert load_settings(settings_path) == settings


def test_save_settings_creates_parent_directories(tmp_path: Path) -> None:
    settings_path = tmp_path / "does" / "not" / "exist" / "settings.json"
    save_settings(AppSettings(), settings_path)
    assert settings_path.exists()


def test_load_settings_handles_invalid_json(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{not valid json")

    assert load_settings(settings_path) == AppSettings()


def test_load_settings_handles_json_that_is_not_an_object(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("[1, 2, 3]")

    assert load_settings(settings_path) == AppSettings()


def test_load_settings_handles_missing_fields(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text('{"artwork_enabled": true}')

    assert load_settings(settings_path) == AppSettings()


def test_load_settings_handles_invalid_layout_mode_value(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"artwork_enabled": true, "layout_mode": "bogus", "clock_visible": true}'
    )

    assert load_settings(settings_path) == AppSettings()


def test_default_settings_path_uses_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_settings_path() == tmp_path / "terminal-home" / "settings.json"


def test_default_settings_path_falls_back_to_dot_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".config" / "terminal-home" / "settings.json"
    assert default_settings_path() == expected


def test_settings_store_is_independent_of_workspace_store(tmp_path: Path) -> None:
    """Settings (preferences) and workspaces (project data) must never
    collide even when both default paths are derived from the same tmp_path.
    """
    from dashboard.services.workspace_store import default_store_path

    settings_default = default_settings_path()
    workspace_default = default_store_path()
    assert settings_default != workspace_default
