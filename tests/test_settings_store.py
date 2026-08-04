"""Tests for settings persistence (dashboard.services.settings_store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.services.load_result import LoadSource
from dashboard.services.settings_store import (
    default_settings_path,
    load_settings,
    load_settings_result,
    save_settings,
)


def test_atomic_rotation_and_backup_recovery_are_observable(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    first = AppSettings(theme="nord")
    second = AppSettings(theme="dracula", artwork_enabled=False)
    save_settings(first, path)
    first_bytes = path.read_bytes()
    save_settings(second, path)
    assert Path(f"{path}.bak").read_bytes() == first_bytes

    path.write_text("{broken")
    primary_before = path.read_bytes()
    backup_before = Path(f"{path}.bak").read_bytes()
    result = load_settings_result(path)
    assert result.value == first
    assert result.source is LoadSource.BACKUP
    assert result.warning and str(path) in result.warning and f"{path}.bak" in result.warning
    assert path.read_bytes() == primary_before
    assert Path(f"{path}.bak").read_bytes() == backup_before


def test_invalid_primary_and_backup_default_without_repair(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff")
    Path(f"{path}.bak").write_text("broken")
    result = load_settings_result(path)
    assert result.value == AppSettings()
    assert result.source is LoadSource.DEFAULT


def test_malformed_optional_theme_stays_on_primary(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    save_settings(AppSettings(theme="nord", artwork_enabled=False), path)
    path.write_text(
        '{"artwork_enabled": true, "layout_mode": "compact", '
        '"clock_visible": false, "theme": 4}'
    )
    result = load_settings_result(path)
    assert result.source is LoadSource.PRIMARY
    assert result.value.artwork_enabled is True
    assert result.value.clock_visible is False


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


def test_theme_round_trips_through_the_store(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings = AppSettings(theme="nord")

    save_settings(settings, settings_path)

    assert load_settings(settings_path) == settings


def test_load_settings_handles_legacy_file_without_theme_key(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"artwork_enabled": true, "layout_mode": "expanded", "clock_visible": true}'
    )

    assert load_settings(settings_path) == AppSettings()


def test_load_settings_malformed_theme_preserves_other_fields(tmp_path: Path) -> None:
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        '{"artwork_enabled": false, "layout_mode": "compact", '
        '"clock_visible": false, "theme": 123}'
    )

    loaded = load_settings(settings_path)

    assert loaded.artwork_enabled is False
    assert loaded.layout_mode is LayoutMode.COMPACT
    assert loaded.clock_visible is False
    assert loaded.theme is None


def test_saving_one_setting_preserves_all_others(tmp_path: Path) -> None:
    from dataclasses import replace

    settings_path = tmp_path / "settings.json"
    save_settings(
        AppSettings(
            artwork_enabled=False,
            layout_mode=LayoutMode.COMPACT,
            clock_visible=False,
            theme="nord",
        ),
        settings_path,
    )

    updated = replace(load_settings(settings_path), theme="dracula")
    save_settings(updated, settings_path)

    reloaded = load_settings(settings_path)
    assert reloaded.theme == "dracula"
    assert reloaded.artwork_enabled is False
    assert reloaded.layout_mode is LayoutMode.COMPACT
    assert reloaded.clock_visible is False


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
