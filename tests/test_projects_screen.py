"""Textual Pilot tests for the Open Project list screen
(dashboard.screens.projects.ProjectsScreen).

Project scanning happens in a worker thread, so every scenario waits on
`pilot.app.workers.wait_for_complete()` after anything that triggers a
(re)scan. tmux is fully mocked; no real tmux session is ever queried,
created, or attached to.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList

from dashboard.app import DevDashboardApp
from dashboard.services import projects as projects_module
from dashboard.services import tmux as tmux_module

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the projects root and the workspace store at tmp_path, and
    replace every tmux call the background scan makes with a fake.
    """
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(projects_module, "DEFAULT_PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    return projects_root


def _run(coro):
    return asyncio.run(coro)


async def _open_projects_screen(pilot) -> None:
    """From Home, select "Continue Project" (the first main-menu item), and
    wait for the background scan to complete.
    """
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def _option_ids(pilot) -> list[str]:
    option_list = pilot.app.screen.query_one("#project-list", OptionList)
    return [str(option_list.get_option_at_index(i).id) for i in range(option_list.option_count)]


def test_lists_discovered_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()
    (projects_root / "beta").mkdir()

    async def scenario() -> list[str]:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            assert type(app.screen).__name__ == "ProjectsScreen"
            return _option_ids(pilot)

    assert _run(scenario()) == ["alpha", "beta"]


def test_excludes_terminal_home_and_hidden_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()
    (projects_root / "terminal-home").mkdir()
    (projects_root / ".config").mkdir()

    async def scenario() -> list[str]:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            return _option_ids(pilot)

    assert _run(scenario()) == ["alpha"]


def test_search_filters_the_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()
    (projects_root / "beta").mkdir()

    async def scenario() -> list[str]:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            app.screen.query_one("#project-filter", Input).value = "al"
            await pilot.pause()
            return _option_ids(pilot)

    assert _run(scenario()) == ["alpha"]


def test_escape_returns_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "HomeScreen"


def test_enter_opens_project_detail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> str:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            option_list.highlighted = 0
            option_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ProjectDetailScreen"


def test_refresh_rescans_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> list[str]:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            (projects_root / "beta").mkdir()

            await pilot.press("f5")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            return _option_ids(pilot)

    assert _run(scenario()) == ["alpha", "beta"]


def test_no_projects_found_shows_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> int:
        app = DevDashboardApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            return option_list.option_count

    assert _run(scenario()) == 1  # the disabled "No projects found" placeholder
