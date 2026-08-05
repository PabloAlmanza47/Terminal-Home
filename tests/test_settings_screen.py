"""Textual Pilot tests for the Settings screen (dashboard.screens.settings).

Each toggle persists immediately; these tests isolate XDG_CONFIG_HOME so
nothing ever touches a real settings file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.screen import Screen

from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.screens.settings import SettingsScreen
from dashboard.services.settings_store import default_settings_path, load_settings, save_settings
from dashboard.widgets import KeyboardActionList

_SIZE = (80, 24)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


class _HostApp(App[None]):
    def on_mount(self) -> None:
        self.push_screen(SettingsScreen())


def _run(coro):
    return asyncio.run(coro)


def test_defaults_shown_when_nothing_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[bool, bool, bool]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            return (
                screen.query_one("#artwork-checkbox").value,
                screen.query_one("#clock-checkbox").value,
                screen.query_one("#compact-checkbox").value,
            )

    artwork, clock, compact = _run(scenario())
    assert artwork is True
    assert clock is True
    assert compact is False


def test_toggling_artwork_checkbox_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#artwork-checkbox")
            await pilot.pause()

    _run(scenario())

    assert load_settings(default_settings_path()).artwork_enabled is False


def test_toggling_compact_checkbox_persists_layout_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#compact-checkbox")
            await pilot.pause()

    _run(scenario())

    assert load_settings(default_settings_path()).layout_mode is LayoutMode.COMPACT


def test_toggling_clock_checkbox_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#clock-checkbox")
            await pilot.pause()

    _run(scenario())

    assert load_settings(default_settings_path()).clock_visible is False


def test_existing_settings_are_loaded_on_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_settings(
        AppSettings(artwork_enabled=False, layout_mode=LayoutMode.COMPACT, clock_visible=False)
    )

    async def scenario() -> tuple[bool, bool, bool]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            return (
                screen.query_one("#artwork-checkbox").value,
                screen.query_one("#clock-checkbox").value,
                screen.query_one("#compact-checkbox").value,
            )

    artwork, clock, compact = _run(scenario())
    assert artwork is False
    assert clock is False
    assert compact is True


def test_malformed_settings_file_falls_back_to_defaults_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    settings_path = default_settings_path()
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json")

    async def scenario() -> bool:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return app.screen.query_one("#artwork-checkbox").value

    assert _run(scenario()) is True  # default, no traceback


def test_escape_returns_to_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    class _Host(Screen[None]):
        def compose(self) -> ComposeResult:
            return iter(())

    class _App(App[None]):
        def on_mount(self) -> None:
            self.push_screen(_Host())
            self.push_screen(SettingsScreen())

    async def scenario() -> str:
        app = _App()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "_Host"


def test_project_discovery_action_opens_project_discovery_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            actions = app.screen.query_one("#settings-actions", KeyboardActionList)
            actions.selected_index = 0
            actions.focus()
            await pilot.press("enter")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ProjectDiscoveryScreen"
