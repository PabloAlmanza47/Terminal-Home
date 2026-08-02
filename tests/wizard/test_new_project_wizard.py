"""Textual Pilot tests driving the full New Project wizard.

Each test isolates the project root (DEFAULT_PROJECTS_ROOT) and the
workspace store (XDG_DATA_HOME) under pytest's tmp_path, so nothing ever
touches the real ~/projects or the real XDG data directory. No real tmux
session is created or attached to -- the wizard only ever produces a
LaunchRequest; dashboard.services.workspace_launcher (which would run
tmux) is never invoked from these tests.

There's no pytest-asyncio dependency here: each test is a plain sync
function that drives an async body via asyncio.run(), the same pattern
Textual's own examples use for ad-hoc scripts.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, SelectionList, Static

from dashboard.app import DevDashboardApp
from dashboard.models import LaunchRequest, PaneKind
from dashboard.services import project_creation as project_creation_module
from dashboard.services.workspace_store import load_workspace

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the projects root and the workspace store at tmp_path."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(project_creation_module, "DEFAULT_PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    return projects_root


async def _open_new_project_wizard(pilot) -> None:
    """From Home, select "Create New Project" (3rd item in the main menu)."""
    await pilot.pause()
    await pilot.press("down", "down", "enter")
    await pilot.pause()


async def _fill_step1(pilot, project_name: str) -> None:
    screen = pilot.app.screen
    screen.query_one("#project-name-input", Input).value = project_name
    await pilot.pause()


async def _click(pilot, button_id: str) -> None:
    await pilot.click(button_id)
    await pilot.pause()


def _run(coro):
    return asyncio.run(coro)


# --- Happy path -------------------------------------------------------------


def test_wizard_happy_path_produces_expected_launch_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)

    async def scenario() -> LaunchRequest:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            assert type(app.screen).__name__ == "NewProjectScreen"

            await _fill_step1(pilot, "Demo Project")
            assert app.screen.query_one("#folder-name-input", Input).value == "demo-project"

            await _click(pilot, "#next-button")
            assert type(app.screen).__name__ == "WindowConfigScreen"
            assert app.screen.query_one("#window-name-input", Input).value == "main"

            selection_list = app.screen.query_one("#pane-selection-list", SelectionList)
            selection_list.toggle(PaneKind.CODE_EDITOR)
            selection_list.toggle(PaneKind.GIT)
            await pilot.pause()

            await _click(pilot, "#next-button")
            assert type(app.screen).__name__ == "LayoutPreviewScreen"

            await _click(pilot, "#continue-button")
            assert type(app.screen).__name__ == "WindowSummaryScreen"

            await _click(pilot, "#finish-button")
            assert type(app.screen).__name__ == "ReviewScreen"

            await _click(pilot, "#create-button")

        return app.return_value

    result = _run(scenario())

    assert isinstance(result, LaunchRequest)
    workspace = result.workspace
    assert workspace.project_name == "Demo Project"
    assert workspace.project_path == projects_root / "demo-project"
    assert workspace.project_path.is_dir()
    assert (workspace.project_path / ".git").is_dir()  # git init defaults to on
    assert len(workspace.windows) == 1
    assert workspace.windows[0].window_name == "main"
    assert [p.kind for p in workspace.windows[0].panes] == [PaneKind.CODE_EDITOR, PaneKind.GIT]

    saved = load_workspace(workspace.project_path)
    assert saved == workspace


# --- Max 4 pane selections ---------------------------------------------------


def test_wizard_prevents_more_than_four_pane_selections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Demo Project")
            await _click(pilot, "#next-button")

            screen = app.screen
            selection_list = screen.query_one("#pane-selection-list", SelectionList)
            for kind in [
                PaneKind.CODE_EDITOR,
                PaneKind.CLAUDE_CODE,
                PaneKind.GIT,
                PaneKind.FILE_TREE,
                PaneKind.TEST_TERMINAL,
            ]:
                selection_list.toggle(kind)
                await pilot.pause()

            assert len(screen._panes) == 4
            error_text = str(screen.query_one("#wizard-error", Static).content)
            assert "up to 4" in error_text

    _run(scenario())


# --- Returning to a prior step preserves state -------------------------------


def test_going_back_to_step1_preserves_window_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Original Name")
            await _click(pilot, "#next-button")

            screen = app.screen
            selection_list = screen.query_one("#pane-selection-list", SelectionList)
            selection_list.toggle(PaneKind.CODE_EDITOR)
            selection_list.toggle(PaneKind.GIT)
            await pilot.pause()
            assert len(screen._panes) == 2

            await _click(pilot, "#back-button")
            assert type(app.screen).__name__ == "NewProjectScreen"

            await _fill_step1(pilot, "Renamed Project")
            await _click(pilot, "#next-button")

            screen = app.screen
            assert type(screen).__name__ == "WindowConfigScreen"
            assert [p.kind for p in screen._panes] == [PaneKind.CODE_EDITOR, PaneKind.GIT]
            assert screen.query_one("#window-name-input", Input).value == "main"

    _run(scenario())


# --- Duplicate window names ---------------------------------------------------


def test_wizard_rejects_duplicate_window_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Demo Project")
            await _click(pilot, "#next-button")

            screen = app.screen
            screen.query_one("#pane-selection-list", SelectionList).toggle(PaneKind.BLANK_TERMINAL)
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            assert type(app.screen).__name__ == "WindowSummaryScreen"

            await _click(pilot, "#add-window-button")
            assert type(app.screen).__name__ == "WindowConfigScreen"

            screen = app.screen
            screen.query_one("#window-name-input", Input).value = "main"
            screen.query_one("#pane-selection-list", SelectionList).toggle(PaneKind.GIT)
            await pilot.pause()
            await _click(pilot, "#next-button")

            # Still on WindowConfigScreen -- the duplicate name was rejected.
            assert type(app.screen).__name__ == "WindowConfigScreen"
            error_text = str(app.screen.query_one("#wizard-error", Static).content)
            assert "already exists" in error_text

    _run(scenario())


# --- Add / edit / remove windows, and the one-window floor -------------------


def test_add_edit_and_remove_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Demo Project")
            await _click(pilot, "#next-button")

            app.screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.BLANK_TERMINAL
            )
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            assert type(app.screen).__name__ == "WindowSummaryScreen"

            # Removing the only window is refused.
            await _click(pilot, "#remove-window-button")
            error_text = str(app.screen.query_one("#wizard-error", Static).content)
            assert "at least one window" in error_text

            # Add a second window named "tests".
            await _click(pilot, "#add-window-button")
            assert type(app.screen).__name__ == "WindowConfigScreen"
            assert app.screen.query_one("#window-name-input", Input).value == ""
            app.screen.query_one("#window-name-input", Input).value = "tests"
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.TEST_TERMINAL
            )
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")

            option_list = app.screen.query_one("#window-list", OptionList)
            assert option_list.option_count == 2

            # Edit the second window's name.
            option_list.highlighted = 1
            await _click(pilot, "#edit-window-button")
            assert type(app.screen).__name__ == "WindowConfigScreen"
            assert app.screen.query_one("#window-name-input", Input).value == "tests"
            app.screen.query_one("#window-name-input", Input).value = "test-suite"
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")

            option_list = app.screen.query_one("#window-list", OptionList)
            option_list.highlighted = 1
            await _click(pilot, "#remove-window-button")
            option_list = app.screen.query_one("#window-list", OptionList)
            assert option_list.option_count == 1

    _run(scenario())


# --- Cancellation creates nothing --------------------------------------------


def test_cancelling_the_wizard_creates_no_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Abandoned Project")
            await _click(pilot, "#next-button")
            assert type(app.screen).__name__ == "WindowConfigScreen"

            await pilot.press("escape")
            await pilot.pause()

        assert app.return_value is None

    _run(scenario())

    assert list(projects_root.iterdir()) == []


def test_cancelling_from_review_creates_no_project_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Abandoned Project")
            await _click(pilot, "#next-button")

            app.screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.BLANK_TERMINAL
            )
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            await _click(pilot, "#finish-button")
            assert type(app.screen).__name__ == "ReviewScreen"

            await _click(pilot, "#cancel-button")
            assert type(app.screen).__name__ == "HomeScreen"

        assert app.return_value is None

    _run(scenario())

    assert list(projects_root.iterdir()) == []


# --- Custom command pane ------------------------------------------------------


def test_custom_command_pane_requires_name_and_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Demo Project")
            await _click(pilot, "#next-button")

            screen = app.screen
            screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.CUSTOM_COMMAND
            )
            await pilot.pause()
            await pilot.pause()
            assert screen.query_one("#custom-command-fields").display is True

            await _click(pilot, "#next-button")
            # Rejected: no name/command entered yet.
            assert type(app.screen).__name__ == "WindowConfigScreen"
            error_text = str(app.screen.query_one("#wizard-error", Static).content)
            assert "custom command" in error_text.lower()

            app.screen.query_one("#custom-name-input", Input).value = "Docs"
            await pilot.pause()
            app.screen.query_one("#custom-command-input", Input).value = "mkdocs serve"
            await pilot.pause()
            await _click(pilot, "#next-button")
            assert type(app.screen).__name__ == "LayoutPreviewScreen"

    _run(scenario())
