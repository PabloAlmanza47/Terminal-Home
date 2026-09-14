"""Focused tests for the read-only Agent Hub screen."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual.widgets import Input, OptionList

import dashboard.screens.agents as agents_module
import dashboard.screens.home as home_module
import dashboard.services.projects as projects_module
from dashboard.app import TerminalHomeApp
from dashboard.models import AgentDeckAttachRequest
from dashboard.models.projects_config import ProjectsConfig
from dashboard.screens.agents import AgentsScreen
from dashboard.services import tmux as tmux_module
from dashboard.services.agent_deck import AgentDeckSession, AgentDeckSnapshot, AgentStatus
from dashboard.services.agent_hub import (
    AgentAssociation,
    AgentHubEntry,
    AgentHubSnapshot,
    AgentHubStatus,
)
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.system_info import SystemInfo

_SIZE = (100, 40)


def _run(coro):
    async def owned_executor():
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="agents-screen-test")
        loop.set_default_executor(executor)
        try:
            return await coro
        finally:
            executor.shutdown(wait=True)

    return asyncio.run(owned_executor())


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    save_projects_config(ProjectsConfig(roots=(projects_root,)))
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
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


def _entry(
    session_id: str,
    title: str,
    path: Path,
    status: AgentHubStatus,
    *,
    project_name: str | None = None,
) -> AgentHubEntry:
    association = (
        AgentAssociation.KNOWN_PROJECT
        if project_name is not None
        else AgentAssociation.UNREGISTERED_PATH
    )
    return AgentHubEntry(
        session_id=session_id,
        title=title,
        path=path,
        tool="codex",
        agent_status=AgentStatus.RUNNING,
        status=status,
        project_name=project_name,
        project_path=path if project_name is not None else None,
        workspace_session_name=None,
        association=association,
    )


def _labels(app: TerminalHomeApp) -> list[str]:
    option_list = app.screen.query_one("#agent-list", OptionList)
    return [str(option.prompt) for option in option_list.options]


def test_home_opens_view_all_agents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    path = tmp_path / "projects" / "demo"
    path.mkdir()
    sessions = tuple(
        AgentDeckSession(str(index), f"Agent {index}", path, "codex", AgentStatus.RUNNING)
        for index in range(5)
    )
    monkeypatch.setattr(
        projects_module,
        "agent_deck_snapshot",
        lambda: AgentDeckSnapshot(True, sessions),
    )
    async def scenario() -> tuple[str, list[str], int | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.app.workers.wait_for_complete()
            await pilot.wait_for_scheduled_animations()
            agents = app.screen.query_one("#active-agents-list", OptionList)
            agents.focus()
            await pilot.pause()
            agents.highlighted = agents.option_count - 1
            await pilot.press("enter")
            await pilot.pause()
            return (
                type(app.screen).__name__,
                [str(option.id) for option in agents.options],
                agents.highlighted,
            )

    screen_name, option_ids, highlighted = _run(scenario())
    assert screen_name == "AgentsScreen", (option_ids, highlighted)


def test_agents_render_context_statuses_and_search_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    known = tmp_path / "projects" / "terminal-home"
    outside = tmp_path / "other" / "agent-worktree"
    known.mkdir(parents=True)
    outside.mkdir(parents=True)
    entries = (
        _entry(
            "working", "Parser Fix", known, AgentHubStatus.WORKING, project_name="terminal-home"
        ),
        _entry(
            "waiting", "Release", known, AgentHubStatus.WAITING, project_name="terminal-home"
        ),
        _entry(
            "completed", "Docs", known, AgentHubStatus.COMPLETED, project_name="terminal-home"
        ),
        _entry("unknown", "Side Task", outside, AgentHubStatus.UNKNOWN),
    )

    async def scenario() -> tuple[list[str], list[str], list[str], list[str], list[str], list[str]]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, entries)))
            await pilot.wait_for_scheduled_animations()
            all_labels = _labels(app)
            search = app.screen.query_one("#agent-filter", Input)
            search.value = "terminal-home"
            await pilot.pause()
            project_labels = _labels(app)
            search.value = "waiting"
            await pilot.pause()
            status_labels = _labels(app)
            search.value = "agent-worktree"
            await pilot.pause()
            path_labels = _labels(app)
            search.value = "parser fix"
            await pilot.pause()
            title_labels = _labels(app)
            search.value = "does-not-exist"
            await pilot.pause()
            empty_labels = _labels(app)
            return (
                all_labels,
                project_labels,
                status_labels,
                path_labels,
                title_labels,
                empty_labels,
            )

    all_labels, project_labels, status_labels, path_labels, title_labels, empty_labels = _run(
        scenario()
    )
    combined = "\n".join(all_labels)
    assert all(glyph in combined for glyph in ("●", "◐", "✓", "?"))
    assert "terminal-home" in combined
    assert "Unregistered" in combined
    assert project_labels and all("terminal-home" in label for label in project_labels)
    assert len(status_labels) == 1 and "waiting" in status_labels[0]
    assert len(path_labels) == 1 and "agent-worktree" in path_labels[0]
    assert len(title_labels) == 1 and "Parser Fix" in title_labels[0]
    assert len(empty_labels) == 1 and "No agents match" in empty_labels[0]


@pytest.mark.parametrize(
    "snapshot, message",
    [
        (AgentHubSnapshot(False), "Agent Deck is unavailable"),
        (AgentHubSnapshot(True), "No Agent Deck sessions found"),
        (AgentHubSnapshot(True, warning="status may be stale"), "status may be stale"),
    ],
)
def test_agents_handle_unavailable_empty_and_warning_snapshots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    snapshot: AgentHubSnapshot,
    message: str,
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(snapshot))
            await pilot.wait_for_scheduled_animations()
            return _labels(app) + [str(app.screen.query_one("#agents-warning").render())]

    assert any(message in label for label in _run(scenario()))


def test_agent_attach_preserves_exact_session_id_and_escape_returns_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    entry = _entry("exact-session-id", "Attach Me", tmp_path, AgentHubStatus.WORKING)

    async def attach() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, (entry,))))
            await pilot.wait_for_scheduled_animations()
            app.screen.query_one("#agent-list", OptionList).focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        return app.return_value

    assert _run(attach()) == AgentDeckAttachRequest("exact-session-id")

    async def back() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, (entry,))))
            await pilot.wait_for_scheduled_animations()
            await pilot.press("escape")
            await pilot.wait_for_scheduled_animations()
            return type(app.screen).__name__

    assert _run(back()) == "HomeScreen"


def test_agent_refresh_replaces_snapshot_and_keeps_selection_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    old = _entry("stable", "Old title", tmp_path, AgentHubStatus.WORKING)
    new = _entry("stable", "New title", tmp_path, AgentHubStatus.WAITING)
    monkeypatch.setattr(
        agents_module,
        "load_agent_hub_snapshot",
        lambda _: AgentHubSnapshot(True, (new,)),
    )

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, (old,))))
            await pilot.wait_for_scheduled_animations()
            await pilot.press("f5")
            await pilot.app.workers.wait_for_complete()
            await pilot.wait_for_scheduled_animations()
            return _labels(app)

    labels = _run(scenario())
    assert len(labels) == 1
    assert "New title" in labels[0]
    assert "waiting" in labels[0]


def test_long_agent_rows_are_truncated_on_narrow_terminals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    entry = _entry(
        "long",
        "A very long title that needs truncation",
        tmp_path / "a" / "very-long-agent-working-directory",
        AgentHubStatus.UNKNOWN,
    )

    async def scenario() -> tuple[str, int]:
        app = TerminalHomeApp()
        async with app.run_test(size=(50, 24)) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, (entry,))))
            await pilot.wait_for_scheduled_animations()
            option_list = app.screen.query_one("#agent-list", OptionList)
            return str(option_list.get_option_at_index(0).prompt), option_list.content_region.width

    label, width = _run(scenario())
    assert "…" in label
    assert all(cell_len(line) <= width for line in label.splitlines()), (label, width)
