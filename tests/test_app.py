"""Tests for TerminalHomeApp startup and theme persistence (dashboard.app).

Each test isolates XDG_CONFIG_HOME so nothing ever touches a real settings
file. Theme changes are simulated by setting `app.theme` directly, the same
reactive assignment Textual's built-in command-palette "Change Theme"
command performs (see textual.theme.ThemeProvider) -- so this exercises the
real persistence path without driving the fuzzy-search widget.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from dashboard.app import TerminalHomeApp
from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.screens.home import HomeScreen
from dashboard.services.settings_store import default_settings_path, load_settings, save_settings

_SIZE = (80, 24)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # These tests cover app/settings startup, not project discovery. Avoid
    # racing run_test teardown against Home's executor-backed scan worker;
    # dedicated HomeScreen tests exercise that worker and await it explicitly.
    monkeypatch.setattr(HomeScreen, "_start_scan", lambda self: None)


def _run(coro):
    return asyncio.run(coro)


def test_home_screen_mounts_with_no_settings_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "HomeScreen"
    assert not default_settings_path().exists()


def test_no_saved_theme_uses_the_normal_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        default_theme = app.theme
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return default_theme, app.theme

    default_before, theme_after = _run(scenario())
    assert theme_after == default_before


def test_saved_theme_is_applied_on_a_new_app_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def discover_alt_theme() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE):
            return next(name for name in app.available_themes if name != app.theme)

    alt_theme = _run(discover_alt_theme())
    save_settings(AppSettings(theme=alt_theme))

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return app.theme

    assert _run(scenario()) == alt_theme


def test_unavailable_saved_theme_falls_back_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_settings(AppSettings(theme="not-a-real-theme"))

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return app.theme, type(app.screen).__name__

    theme, screen_name = _run(scenario())
    assert theme != "not-a-real-theme"
    assert screen_name == "HomeScreen"


def test_malformed_settings_file_never_crashes_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    settings_path = default_settings_path()
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json")

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "HomeScreen"


def test_choosing_a_theme_updates_the_live_app_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            alt_theme = next(name for name in app.available_themes if name != app.theme)
            app.theme = alt_theme
            await pilot.pause()
            return alt_theme

    alt_theme = _run(scenario())
    assert load_settings(default_settings_path()).theme == alt_theme


def test_a_second_new_app_instance_loads_the_persisted_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def change_theme() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            alt_theme = next(name for name in app.available_themes if name != app.theme)
            app.theme = alt_theme
            await pilot.pause()
            return alt_theme

    alt_theme = _run(change_theme())

    async def reopen() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return app.theme

    assert _run(reopen()) == alt_theme


def test_changing_theme_does_not_erase_other_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_settings(
        AppSettings(artwork_enabled=False, layout_mode=LayoutMode.COMPACT, clock_visible=False)
    )

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            alt_theme = next(name for name in app.available_themes if name != app.theme)
            app.theme = alt_theme
            await pilot.pause()
            return alt_theme

    alt_theme = _run(scenario())
    saved = load_settings(default_settings_path())
    assert saved.theme == alt_theme
    assert saved.artwork_enabled is False
    assert saved.layout_mode is LayoutMode.COMPACT
    assert saved.clock_visible is False


def test_changing_another_setting_does_not_erase_the_saved_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_settings(AppSettings(theme="nord"))

    current = load_settings(default_settings_path())
    save_settings(replace(current, artwork_enabled=False))

    saved = load_settings(default_settings_path())
    assert saved.theme == "nord"
    assert saved.artwork_enabled is False


def test_save_failure_is_handled_nonfatally_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    notifications = []

    async def scenario() -> str:
        app = TerminalHomeApp()
        app.notify = lambda message, **kwargs: notifications.append((message, kwargs))
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                "dashboard.app.save_settings",
                lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
            )
            alt_theme = next(name for name in app.available_themes if name != app.theme)
            app.theme = alt_theme
            await pilot.pause()
            return type(app.screen).__name__

    screen_name = _run(scenario())
    assert screen_name == "HomeScreen"
    assert notifications
    assert notifications[0][1].get("severity") == "error"


def test_settings_backup_recovery_notifies_once_without_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    path = default_settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("broken")
    Path(f"{path}.bak").write_text(
        '{"artwork_enabled": false, "layout_mode": "compact", '
        '"clock_visible": true, "theme": null}'
    )
    before = path.read_bytes()
    notifications = []

    async def scenario() -> AppSettings:
        app = TerminalHomeApp()
        app.notify = lambda message, **kwargs: notifications.append((message, kwargs))
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return app.settings

    recovered = _run(scenario())
    assert recovered.artwork_enabled is False
    assert recovered.layout_mode is LayoutMode.COMPACT
    assert len(notifications) == 1
    assert notifications[0][1].get("severity") == "warning"
    assert path.read_bytes() == before
