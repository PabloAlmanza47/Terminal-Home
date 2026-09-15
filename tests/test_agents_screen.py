"""Focused tests for the read-only Agent Hub screen."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual.widgets import Input, OptionList, Static, TextArea

import dashboard.screens.agents as agents_module
import dashboard.screens.home as home_module
import dashboard.screens.new_agent as new_agent_module
import dashboard.services.projects as projects_module
from dashboard.app import TerminalHomeApp
from dashboard.models.projects_config import ProjectsConfig
from dashboard.screens.agents import AgentsScreen
from dashboard.services import tmux as tmux_module
from dashboard.services.agent_creation import AgentCreationResult
from dashboard.services.agent_deck import AgentDeckSession, AgentDeckSnapshot, AgentStatus
from dashboard.services.agent_hub import (
    AgentAssociation,
    AgentHubEntry,
    AgentHubSnapshot,
    AgentHubStatus,
)
from dashboard.services.agent_view import AgentViewRouteRequest
from dashboard.services.projects import Project, ProjectStatus
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


def test_agents_results_fill_popup_height_for_multiple_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    entries = tuple(
        _entry(str(index), f"Agent {index}", tmp_path, AgentHubStatus.WORKING)
        for index in range(2)
    )

    async def scenario() -> tuple[int, int, int]:
        app = TerminalHomeApp()
        async with app.run_test(size=(80, 30)) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, entries)))
            await pilot.wait_for_scheduled_animations()
            root = app.screen.query_one(".agents-screen-root")
            panel = app.screen.query_one(".agents-panel")
            results = app.screen.query_one("#agent-list", OptionList)
            return root.region.height, panel.region.height, results.region.height

    root_height, panel_height, results_height = _run(scenario())
    assert root_height > 10
    assert panel_height == root_height - 2
    assert results_height >= 2


def test_attention_mode_uses_popup_as_outer_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    entries = tuple(
        _entry(str(index), f"Agent {index}", tmp_path, AgentHubStatus.WORKING)
        for index in range(2)
    )
    monkeypatch.setattr(AgentsScreen, "action_refresh", lambda self: None)

    async def scenario() -> tuple[str, bool, bool, int, int]:
        app = TerminalHomeApp()
        async with app.run_test(size=(70, 25)) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, entries), attention_mode=True))
            await pilot.wait_for_scheduled_animations()
            root = app.screen.query_one(".agents-screen-root")
            panel = app.screen.query_one(".agents-panel")
            results = app.screen.query_one("#agent-list", OptionList)
            title = str(app.screen.query_one("#screen-title", Static).render())
            return (
                title,
                "agents-attention-panel" in panel.classes,
                "panel" in panel.classes,
                root.region.height,
                results.region.height,
            )

    title, popup_panel, has_fullscreen_panel, root_height, results_height = _run(scenario())
    assert title == "Active Agents"
    assert popup_panel is True
    assert has_fullscreen_panel is False
    assert root_height > 10
    assert results_height >= 2


def test_agents_screen_opens_and_cancels_new_agent_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True)))
            await pilot.wait_for_scheduled_animations()
            await pilot.press("n")
            await pilot.wait_for_scheduled_animations()
            opened = type(app.screen).__name__
            await pilot.press("escape")
            await pilot.wait_for_scheduled_animations()
            return opened, type(app.screen).__name__

    assert _run(scenario()) == ("AgentProjectScreen", "AgentsScreen")


def test_new_agent_task_precedes_workspace_and_preserves_edits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    project_path = tmp_path / "projects" / "Demo Project"
    project_path.mkdir(parents=True)
    status = ProjectStatus(
        project=Project("Demo Project", project_path),
        canonical_path=project_path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="demo-project",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )
    monkeypatch.setattr(
        new_agent_module,
        "validate_agent_creation_request",
        lambda request: AgentCreationResult(True, resolved_path=request.project_path),
    )

    async def scenario() -> tuple[str, str, str, str, str | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(new_agent_module.AgentProjectScreen(statuses=(status,)))
            await pilot.wait_for_scheduled_animations()
            project_list = app.screen.query_one("#new-agent-project-list", OptionList)
            project_list.focus()
            await pilot.press("enter")
            await pilot.wait_for_scheduled_animations()
            app.screen.query_one("#task-input", Input).value = "test-agent-flow"
            app.screen.query_one("#prompt-input", TextArea).text = (
                "Prompt: keep [exact]\nquotes=\"'\\\\; $HOME"
            )
            app.screen._next()
            await pilot.wait_for_scheduled_animations()
            branch = app.screen.query_one("#branch-input", Input).value
            worktree = app.screen.query_one("#worktree-input", Input).value
            app.screen.query_one("#branch-input", Input).value = "agent/custom-branch"
            app.screen.query_one("#worktree-input", Input).value = str(tmp_path / "custom path")
            app.screen._next()
            await pilot.wait_for_scheduled_animations()
            review = app.screen
            app.switch_screen(new_agent_module.AgentWorkspaceScreen(review.state))
            await pilot.wait_for_scheduled_animations()
            return (
                branch,
                worktree,
                app.screen.query_one("#branch-input", Input).value,
                app.screen.query_one("#worktree-input", Input).value,
                app.screen.state.prompt,
            )

    branch, worktree, preserved_branch, preserved_worktree, prompt = _run(scenario())
    assert branch == "agent/test-agent-flow"
    assert "demo-project" in worktree and "test-agent-flow" in worktree
    assert preserved_branch == "agent/custom-branch"
    assert preserved_worktree.endswith("custom path")
    assert prompt == "Prompt: keep [exact]\nquotes=\"'\\\\; $HOME"


@pytest.mark.parametrize("collisions", [{"task"}, {"task", "task-2"}])
def test_generated_defaults_get_deterministic_collision_suffixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, collisions: set[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    project_path = tmp_path / "projects" / "Demo Project"
    project_path.mkdir(parents=True)
    status = ProjectStatus(
        project=Project("Demo Project", project_path),
        canonical_path=project_path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="demo-project",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )

    def validate(request):
        suffix = request.worktree_path.name if request.worktree_path else ""
        if suffix in collisions:
            return AgentCreationResult(
                False, resolved_path=request.project_path,
                error=f"Worktree path already exists or is registered: {suffix}",
            )
        return AgentCreationResult(True, resolved_path=request.project_path)

    monkeypatch.setattr(new_agent_module, "validate_agent_creation_request", validate)

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(new_agent_module.AgentProjectScreen(statuses=(status,)))
            await pilot.wait_for_scheduled_animations()
            await pilot.press("enter")
            await pilot.wait_for_scheduled_animations()
            app.screen.query_one("#task-input", Input).value = "task"
            app.screen._next()
            await pilot.wait_for_scheduled_animations()
            app.screen._next()
            await pilot.wait_for_scheduled_animations()
            return (
                app.screen.state.branch_name,
                app.screen.state.worktree_path.name,
            )

    branch, path_name = _run(scenario())
    expected = "task-2" if len(collisions) == 1 else "task-3"
    assert branch == f"agent/{expected}"
    assert path_name == expected


def test_generated_paths_bound_long_task_names_and_keep_title_unchanged(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    status = ProjectStatus(
        project=Project("project", project_path),
        canonical_path=project_path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="project",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )
    title = "A" * 300
    state = new_agent_module.AgentWizardState(project=status, task_name=title)
    state.suggest_paths()
    assert state.task_name == title
    assert state.branch_name.startswith("agent/")
    assert len(state.branch_name.removeprefix("agent/")) <= 48
    assert state.worktree_path is not None
    assert len(state.worktree_path.name) <= 48


def test_invalid_generated_branch_is_rejected_before_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    status = ProjectStatus(
        project=Project("project", project_path),
        canonical_path=project_path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="project",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )
    monkeypatch.setattr(new_agent_module, "slugify", lambda _: "bad/name")
    monkeypatch.setattr(
        new_agent_module,
        "validate_agent_creation_request",
        lambda request: AgentCreationResult(False, error="Invalid branch name: agent/bad/name"),
    )

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            state = new_agent_module.AgentWizardState(project=status, task_name="bad")
            app.push_screen(new_agent_module.AgentWorkspaceScreen(state))
            await pilot.wait_for_scheduled_animations()
            app.screen._next()
            return str(app.screen.query_one("#wizard-error", Static).render())

    assert "Invalid branch name" in _run(scenario())


def test_escape_cancels_from_every_new_agent_screen(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    project_path.mkdir()
    status = ProjectStatus(
        project=Project("project", project_path),
        canonical_path=project_path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="project",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True)))
            await pilot.wait_for_scheduled_animations()
            state = new_agent_module.AgentWizardState(
                project=status,
                task_name="task",
                branch_name="agent/task",
                worktree_path=tmp_path / "task",
            )
            screens = (
                new_agent_module.AgentProjectScreen(statuses=(status,)),
                new_agent_module.AgentTaskScreen(state),
                new_agent_module.AgentWorkspaceScreen(state),
                new_agent_module.AgentReviewScreen(state),
            )
            names: list[str] = []
            for screen in screens:
                app.push_screen(screen)
                await pilot.wait_for_scheduled_animations()
                await pilot.press("escape")
                await pilot.wait_for_scheduled_animations()
                names.append(type(app.screen).__name__)
            return names

    assert _run(scenario()) == ["AgentsScreen"] * 4


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

    assert _run(attach()) == AgentViewRouteRequest("exact-session-id", None)

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


def test_large_agent_snapshot_renders_and_filters_without_projection_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    entries = tuple(
        _entry(
            f"session-{index}",
            f"Task {index}",
            tmp_path / f"worktree-{index}",
            AgentHubStatus.WORKING,
        )
        for index in range(100)
    )

    async def scenario() -> tuple[int, int]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            app.push_screen(AgentsScreen(AgentHubSnapshot(True, entries)))
            await pilot.wait_for_scheduled_animations()
            initial_count = app.screen.query_one("#agent-list", OptionList).option_count
            app.screen.query_one("#agent-filter", Input).value = "Task 99"
            await pilot.pause()
            filtered_count = app.screen.query_one("#agent-list", OptionList).option_count
            return initial_count, filtered_count

    assert _run(scenario()) == (100, 1)
