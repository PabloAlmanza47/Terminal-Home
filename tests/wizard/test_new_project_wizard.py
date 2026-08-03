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
import json
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, SelectionList, Static

from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchRequest, PaneKind
from dashboard.screens.new_project import step_review as step_review_module
from dashboard.services import project_creation as project_creation_module
from dashboard.services.workspace_store import WORKSPACE_STORE_SCHEMA_VERSION, load_workspace

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the projects root and the workspace store at tmp_path."""
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(project_creation_module, "DEFAULT_PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    return projects_root


async def _open_new_project_wizard(pilot) -> None:
    """From Home, select "Create New Project" (2nd item in the main menu)."""
    await pilot.pause()
    await pilot.press("down", "enter")
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


def _future_version_store_path(tmp_path: Path) -> Path:
    return tmp_path / "xdg-data" / "terminal-home" / "workspaces.json"


def _write_future_version_store(tmp_path: Path) -> str:
    """A store one schema version newer than this build understands.
    Returns the exact text written."""
    store_path = _future_version_store_path(tmp_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"schema_version": WORKSPACE_STORE_SCHEMA_VERSION + 1, "workspaces": {}})
    store_path.write_text(text)
    return text


# --- Happy path -------------------------------------------------------------


def test_wizard_happy_path_produces_expected_launch_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
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


@pytest.mark.parametrize("init_git", [True, False], ids=["git-init-enabled", "git-init-disabled"])
def test_create_new_project_against_future_version_store_touches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, init_git: bool
) -> None:
    """A store-writability preflight runs before the project directory is
    created or `git init` is run, so a future-version store must leave no
    trace at all -- not even the directory -- regardless of whether git
    init was requested. save_workspace's own version check remains a
    second safeguard for the (unlikely) case the store changes again
    between this preflight and the save.
    """
    projects_root = _isolate(monkeypatch, tmp_path)
    git_init_calls: list[Path] = []
    monkeypatch.setattr(
        step_review_module, "init_git_repo", lambda path: git_init_calls.append(path)
    )

    async def scenario() -> tuple[object, str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_new_project_wizard(pilot)
            await _fill_step1(pilot, "Demo Project")
            if not init_git:
                await pilot.click("#git-init-checkbox")
                await pilot.pause()
            await _click(pilot, "#next-button")

            selection_list = app.screen.query_one("#pane-selection-list", SelectionList)
            selection_list.toggle(PaneKind.CODE_EDITOR)
            await pilot.pause()

            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            await _click(pilot, "#finish-button")
            assert type(app.screen).__name__ == "ReviewScreen"

            # Simulate a newer Terminal Home having written the store
            # before this flow's final "Create and Open" click.
            future_text = _write_future_version_store(tmp_path)

            await _click(pilot, "#create-button")

            error_text = str(app.screen.query_one("#wizard-error", Static).render())
            screen_name = type(app.screen).__name__
        return app.return_value, error_text, screen_name, future_text

    return_value, error_text, screen_name, future_text = _run(scenario())

    assert return_value is None  # no LaunchRequest was produced
    assert screen_name == "ReviewScreen"  # stays on the Review screen
    assert "newer" in error_text.lower()
    assert not (projects_root / "demo-project").exists()  # directory was never created
    assert git_init_calls == []  # git init was never invoked
    assert _future_version_store_path(tmp_path).read_text() == future_text  # store untouched
    assert load_workspace(projects_root / "demo-project") is None


# --- Max 4 pane selections ---------------------------------------------------


def test_wizard_prevents_more_than_four_pane_selections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
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
