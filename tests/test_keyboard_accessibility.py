"""Keyboard-only smoke coverage for the Phase 12 interaction contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Input

from dashboard.app import TerminalHomeApp
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.help import HelpScreen
from dashboard.screens.home import HomeScreen
from dashboard.screens.projects import ProjectsScreen
from dashboard.widgets import KeyboardActionList


def _run(coro):
    return asyncio.run(coro)


def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(HomeScreen, "_start_scan", lambda self: None)
    monkeypatch.setattr(ProjectsScreen, "_start_scan", lambda self: None)


def test_help_overlay_is_keyboard_openable_and_restores_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            original = type(app.focused).__name__
            await pilot.press("?")
            await pilot.pause()
            assert isinstance(app.screen, HelpScreen)
            assert app.focused.id == "help-actions"
            await pilot.press("escape")
            await pilot.pause()
            return original, type(app.screen).__name__, type(app.focused).__name__

    original, screen, focused = _run(scenario())
    assert (original, screen, focused) == ("KeyboardActionList", "HomeScreen", original)


def test_global_shortcuts_open_screens_and_escape_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(monkeypatch, tmp_path)

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            names = [type(app.screen).__name__]
            await pilot.press("p")
            await pilot.pause()
            names.append(type(app.screen).__name__)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            names.append(type(app.screen).__name__)
            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            names.append(type(app.screen).__name__)
            return names

    assert _run(scenario()) == [
        "HomeScreen",
        "ProjectsScreen",
        "SettingsScreen",
        "NewProjectScreen",
    ]


def test_editable_input_blocks_global_shortcuts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.press("n")
            await pilot.pause()
            field = app.screen.query_one("#project-name-input", Input)
            field.focus()
            await pilot.press("p")
            await pilot.press("q")
            await pilot.pause()
            return type(app.screen).__name__, field.value

    assert _run(scenario()) == ("NewProjectScreen", "pq")


def test_arrow_and_space_activate_home_menu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, int | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            menu = app.screen.query_one("#main-menu")
            await pilot.press("down")
            assert menu.selected_index == 1
            await pilot.press("space")
            await pilot.pause()
            return type(app.screen).__name__, menu.selected_index

    assert _run(scenario()) == ("NewProjectScreen", 1)


def test_confirmation_focuses_safe_cancel_and_escape_restores_focus() -> None:
    from textual.app import App, ComposeResult
    from textual.screen import Screen

    class Host(Screen[None]):
        def compose(self) -> ComposeResult:
            yield Button("Open", id="open")

    class HostApp(App[None]):
        def on_mount(self) -> None:
            self.push_screen(Host())

    async def scenario() -> tuple[str, str]:
        app = HostApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            app.push_screen(ConfirmScreen("Delete?"))
            await pilot.pause()
            modal_focus = app.focused.id or ""
            await pilot.press("escape")
            await pilot.pause()
            return modal_focus, type(app.focused).__name__

    assert _run(scenario()) == ("confirm-actions", "Button")


def test_new_project_wizard_reaches_review_without_mouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.press("n")
            await pilot.press(*list("Keyboard Project"))
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")  # default blank workspace
            await pilot.pause()
            pane_selection = app.screen.query_one("#pane-selection-list")
            pane_selection.focus()
            await pilot.press("space")
            actions = app.screen.query_one("#window-config-actions", KeyboardActionList)
            actions.selected_index = 1
            actions.focus()
            await pilot.press("enter")
            await pilot.pause()
            preview = app.screen.query_one("#preview-actions", KeyboardActionList)
            preview.selected_index = 1
            preview.focus()
            await pilot.press("enter")
            await pilot.pause()
            summary = app.screen.query_one("#window-summary-actions", KeyboardActionList)
            summary.selected_index = 3
            summary.focus()
            await pilot.press("enter")  # finish workspace
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ReviewScreen"
