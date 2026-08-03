"""Textual Pilot tests driving the "Configure Workspace" flow for an
existing project (dashboard.screens.new_project reused in
WizardMode.EXISTING_CREATE via dashboard.screens.project_detail).

Unlike the New Project wizard, this flow must never create, rename, or
delete the project directory, and must never run `git init` -- these
tests assert that directly. No real tmux session is ever created or
attached to.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, SelectionList, Static

from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchAction, LaunchRequest, PaneKind
from dashboard.services import projects as projects_module
from dashboard.services import tmux as tmux_module
from dashboard.services.workspace_store import load_workspace

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(projects_module, "DEFAULT_PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(tmux_module, "generate_session_name", lambda name, existing=None: "demo")
    return projects_root


def _run(coro):
    return asyncio.run(coro)


async def _open_project_detail(pilot, project_name: str) -> None:
    await pilot.pause()
    await pilot.press("enter")  # Continue Project is the first main-menu item
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()

    option_list = pilot.app.screen.query_one("#project-list", OptionList)
    index = next(
        i
        for i in range(option_list.option_count)
        if option_list.get_option_at_index(i).id == project_name
    )
    option_list.highlighted = index
    option_list.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def _click(pilot, button_id: str) -> None:
    await pilot.click(button_id)
    await pilot.pause()


def test_configure_workspace_never_creates_or_renames_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    marker = project_path / "existing-file.txt"
    marker.write_text("already here")

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _click(pilot, "#action-configure")
            assert type(app.screen).__name__ == "WindowConfigScreen"
            # No Project Info step exists in this mode -- straight into
            # window/pane configuration, and the project name/path are
            # already fixed (never prompted for).

            app.screen.query_one("#window-name-input", Input).value = "main"
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.CODE_EDITOR
            )
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
    assert result.action is LaunchAction.CREATE
    assert result.workspace is not None
    assert result.workspace.project_path == project_path.resolve()
    assert result.workspace.windows[0].window_name == "main"

    # The directory was never touched: it still exists, was never
    # recreated, and its pre-existing contents (and lack of git) survive.
    assert project_path.is_dir()
    assert marker.exists()
    assert not (project_path / ".git").exists()

    saved = load_workspace(project_path)
    assert saved == result.workspace


def test_configure_workspace_review_hides_git_and_destination_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "demo").mkdir()

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _click(pilot, "#action-configure")

            app.screen.query_one("#window-name-input", Input).value = "main"
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.BLANK_TERMINAL
            )
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            await _click(pilot, "#finish-button")
            assert type(app.screen).__name__ == "ReviewScreen"

            review_text = "\n".join(
                str(s.render()) for s in app.screen.query(Static)
            )
        return review_text

    review_text = _run(scenario())
    assert "Git init" not in review_text
    assert "Configure Workspace" in review_text


def test_configure_workspace_rejects_duplicate_window_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "demo").mkdir()

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _click(pilot, "#action-configure")

            app.screen.query_one("#window-name-input", Input).value = "main"
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(
                PaneKind.BLANK_TERMINAL
            )
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            assert type(app.screen).__name__ == "WindowSummaryScreen"

            await _click(pilot, "#add-window-button")
            assert type(app.screen).__name__ == "WindowConfigScreen"
            app.screen.query_one("#window-name-input", Input).value = "main"
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(PaneKind.GIT)
            await pilot.pause()
            await _click(pilot, "#next-button")

            assert type(app.screen).__name__ == "WindowConfigScreen"
            error_text = str(app.screen.query_one("#wizard-error", Static).content)
        return error_text

    assert "already exists" in _run(scenario())


def test_cancelling_configure_workspace_returns_to_detail_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "demo").mkdir()

    async def scenario() -> tuple[str, object]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _click(pilot, "#action-configure")
            assert type(app.screen).__name__ == "WindowConfigScreen"

            await pilot.press("escape")
            await pilot.pause()
            screen_name = type(app.screen).__name__
        return screen_name, app.return_value

    screen_name, return_value = _run(scenario())
    assert screen_name == "ProjectDetailScreen"
    assert return_value is None
    assert load_workspace(projects_root_for(tmp_path) / "demo") is None


def projects_root_for(tmp_path: Path) -> Path:
    return tmp_path / "projects"
