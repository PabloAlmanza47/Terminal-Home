"""Textual Pilot tests for the Project Detail screen
(dashboard.screens.project_detail.ProjectDetailScreen).

Every scenario isolates the workspace store under tmp_path and mocks every
tmux call -- no real tmux session is ever queried, created, or attached to,
and Forget/Reset never touch the project directory itself.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.widgets import Button, OptionList

from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchAction, LaunchRequest
from dashboard.services import projects as projects_module
from dashboard.services import tmux as tmux_module
from dashboard.services.projects import Project, build_launch_request, gather_project_status
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import load_workspace, save_workspace

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(projects_module, "DEFAULT_PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    return projects_root


def _run(coro):
    return asyncio.run(coro)


async def _open_project_detail(pilot, project_name: str) -> None:
    """From Home: Open Project -> wait for scan -> select *project_name* by
    name (not just index, so multi-project scenarios stay unambiguous).
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
        if option_list.get_option_at_index(i).id == project_name
    )
    option_list.highlighted = index
    option_list.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def test_shows_resume_when_session_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: name == "demo")

    async def scenario() -> bool:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            assert type(app.screen).__name__ == "ProjectDetailScreen"
            return app.screen.query("#action-resume").first(Button) is not None

    assert _run(scenario()) is True


def test_shows_recreate_when_saved_workspace_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    save_workspace(build_default_workspace("demo", project_path.resolve(), "demo"))

    async def scenario() -> bool:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            return app.screen.query("#action-recreate").first(Button) is not None

    assert _run(scenario()) is True


def test_shows_default_and_configure_when_nothing_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "demo").mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[bool, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            screen = app.screen
            return (
                screen.query("#action-open_default").first(Button) is not None,
                screen.query("#action-configure").first(Button) is not None,
            )

    has_default, has_configure = _run(scenario())
    assert has_default and has_configure


def test_resume_exits_with_attach_launch_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace("demo", project_path.resolve(), "demo")
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: name == "demo")

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-resume")
            await pilot.pause()
        return app.return_value

    result = _run(scenario())
    assert isinstance(result, LaunchRequest)
    assert result.action is LaunchAction.ATTACH
    assert result.workspace == workspace
    # Same request the shared service function would build for this project's status.
    status = gather_project_status(Project(name="demo", path=project_path))
    assert result == build_launch_request(status)


def test_recreate_exits_with_attach_launch_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace("demo", project_path.resolve(), "demo")
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-recreate")
            await pilot.pause()
        return app.return_value

    result = _run(scenario())
    assert isinstance(result, LaunchRequest)
    assert result.action is LaunchAction.ATTACH
    assert result.workspace == workspace
    # Same request the shared service function would build for this project's status.
    status = gather_project_status(Project(name="demo", path=project_path))
    assert result == build_launch_request(status)


def test_open_default_workspace_saves_and_exits_with_create_launch_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(tmux_module, "generate_session_name", lambda name, existing=None: "demo")

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-open_default")
            await pilot.pause()
        return app.return_value

    result = _run(scenario())
    assert isinstance(result, LaunchRequest)
    assert result.action is LaunchAction.CREATE
    assert result.workspace is not None
    assert result.workspace.windows[0].window_name == "code"

    saved = load_workspace(project_path)
    assert saved == result.workspace


def test_edit_workspace_saves_without_launching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    save_workspace(build_default_workspace("demo", project_path.resolve(), "demo"))
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[str, object]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-edit")
            await pilot.pause()
            assert type(app.screen).__name__ == "WindowSummaryScreen"

            await pilot.click("#finish-button")
            await pilot.pause()
            assert type(app.screen).__name__ == "ReviewScreen"

            await pilot.click("#create-button")
            await pilot.pause()
            screen_name = type(app.screen).__name__
        return screen_name, app.return_value

    screen_name, return_value = _run(scenario())
    assert screen_name == "ProjectDetailScreen"  # never launched
    assert return_value is None
    # Directory and git are never touched by editing.
    assert project_path.is_dir()
    assert not (project_path / ".git").exists()


def test_reset_to_default_requires_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec

    custom = WorkspaceSpec(
        project_name="demo",
        project_path=project_path.resolve(),
        session_name="demo",
        windows=(
            WindowSpec(
                window_name="custom",
                panes=(PaneSpec(kind=PaneKind.GIT, display_name="Git"),),
            ),
        ),
    )
    save_workspace(custom)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario_cancel() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-reset")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.click("#cancel-button")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    unchanged = _run(scenario_cancel())
    assert unchanged == custom  # cancelling leaves the saved workspace untouched

    async def scenario_confirm() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-reset")
            await pilot.pause()
            await pilot.click("#confirm-button")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    reset = _run(scenario_confirm())
    assert reset is not None
    assert reset.windows[0].window_name == "code"
    assert reset.session_name == "demo"  # session identity preserved


def test_forget_workspace_removes_metadata_not_project_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    (project_path / "keep-me.txt").write_text("still here")
    save_workspace(build_default_workspace("demo", project_path.resolve(), "demo"))
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-forget")
            await pilot.pause()
            await pilot.click("#confirm-button")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    assert _run(scenario()) is None
    assert (project_path / "keep-me.txt").exists()
    assert project_path.is_dir()


def test_forget_workspace_cancelled_keeps_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace("demo", project_path.resolve(), "demo")
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#action-forget")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    assert _run(scenario()) == workspace


def test_malformed_metadata_offers_forget_and_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    store_path = tmp_path / "xdg-data" / "terminal-home" / "workspaces.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps({str(project_path.resolve()): {"project_name": "bad"}}))

    async def scenario() -> tuple[bool, bool, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            screen = app.screen
            has_forget = screen.query("#action-forget").first(Button) is not None
            has_configure = screen.query("#action-configure").first(Button) is not None
            error_text = str(screen.query_one("#detail-metadata-error").render())
        return has_forget, has_configure, error_text

    has_forget, has_configure, error_text = _run(scenario())
    assert has_forget and has_configure
    assert "invalid" in error_text.lower() or "warning" in error_text.lower()


def test_escape_returns_to_project_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "demo").mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ProjectsScreen"


def test_back_to_list_button_makes_no_metadata_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace("demo", project_path.resolve(), "demo")
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[str, object]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await pilot.click("#back-to-list-button")
            await pilot.pause()
            return type(app.screen).__name__, app.return_value

    screen_name, return_value = _run(scenario())
    assert screen_name == "ProjectsScreen"
    assert return_value is None
    assert load_workspace(project_path) == workspace
