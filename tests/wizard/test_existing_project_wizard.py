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
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, SelectionList, Static

import dashboard.screens.home as home_module
from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchAction, LaunchRequest, PaneKind
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux as tmux_module
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.system_info import SystemInfo
from dashboard.services.workspace_store import WORKSPACE_STORE_SCHEMA_VERSION, load_workspace

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    save_projects_config(ProjectsConfig(roots=(projects_root,)))
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(tmux_module, "generate_session_name", lambda name, existing=None: "demo")
    monkeypatch.setattr(
        home_module,
        "gather_system_info",
        lambda: SystemInfo(
            hostname="test-host",
            operating_system="test-os",
            python_version="3.12",
            shell="/bin/test-shell",
            tmux_version="tmux test",
            disk_usage=None,
            memory_usage=None,
            wsl_distro=None,
        ),
    )
    return projects_root


def _run(coro):
    async def run_with_owned_executor():
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="existing-project-wizard-test")
        loop.set_default_executor(executor)
        try:
            return await coro
        finally:
            # Textual's thread workers use the loop's default executor. Own
            # and close it here rather than racing Python 3.12's implicit
            # asyncio.run() executor shutdown after run_test() exits.
            executor.shutdown(wait=True)

    return asyncio.run(run_with_owned_executor())


async def _open_project_detail(pilot, project_name: str) -> None:
    """Matched via the option's displayed label rather than its id: the id
    is now derived from the project's canonical path (so two same-named
    projects under different roots never collide), not its name.
    """
    await pilot.pause()
    await pilot.press("enter")  # Continue Project is the first main-menu item
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()

    option_list = pilot.app.screen.query_one("#project-list", OptionList)
    index = next(
        i
        for i in range(option_list.option_count)
        if str(option_list.get_option_at_index(i).prompt).startswith(project_name)
    )
    option_list.highlighted = index
    option_list.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


async def _click(pilot, button_id: str) -> None:
    await pilot.click(button_id)
    await pilot.pause()
    if (
        button_id == "#action-configure"
        and type(pilot.app.screen).__name__ == "WorkspaceStartScreen"
    ):
        await pilot.click("#continue-button")
        await pilot.pause()


def _future_version_store_path(tmp_path: Path) -> Path:
    return tmp_path / "xdg-data" / "terminal-home" / "workspaces.json"


def _write_future_version_store(tmp_path: Path) -> str:
    """A store one schema version newer than this build understands --
    simulating a newer Terminal Home having written it since this project
    was last scanned. Returns the exact text written."""
    store_path = _future_version_store_path(tmp_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"schema_version": WORKSPACE_STORE_SCHEMA_VERSION + 1, "workspaces": {}})
    store_path.write_text(text)
    return text


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
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(PaneKind.CODE_EDITOR)
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


def test_configure_workspace_save_against_future_version_store_does_not_overwrite_or_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()

    async def scenario() -> tuple[object, str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _click(pilot, "#action-configure")
            assert type(app.screen).__name__ == "WindowConfigScreen"

            app.screen.query_one("#window-name-input", Input).value = "main"
            app.screen.query_one("#pane-selection-list", SelectionList).toggle(PaneKind.CODE_EDITOR)
            await pilot.pause()
            await _click(pilot, "#next-button")
            await _click(pilot, "#continue-button")
            await _click(pilot, "#finish-button")
            assert type(app.screen).__name__ == "ReviewScreen"

            # Simulate a newer Terminal Home writing the store between this
            # flow starting and the final save actually running.
            future_text = _write_future_version_store(tmp_path)

            await _click(pilot, "#create-button")

            error_text = str(app.screen.query_one("#wizard-error", Static).render())
            screen_name = type(app.screen).__name__
        return app.return_value, error_text, screen_name, future_text

    return_value, error_text, screen_name, future_text = _run(scenario())

    assert return_value is None  # never falsely reports success
    assert screen_name == "ReviewScreen"  # stays on a safe screen
    assert "newer" in error_text.lower()
    assert _future_version_store_path(tmp_path).read_text() == future_text
    # The project directory itself is never touched by this flow either way.
    assert project_path.is_dir()
    assert load_workspace(project_path) is None


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

            review_text = "\n".join(str(s.render()) for s in app.screen.query(Static))
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
            assert type(app.screen).__name__ == "WorkspaceStartScreen"
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
