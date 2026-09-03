"""Textual Pilot tests for the Project Detail screen
(dashboard.screens.project_detail.ProjectDetailScreen).

Every scenario isolates the workspace store under tmp_path and mocks every
tmux call -- no real tmux session is ever queried, created, or attached to,
and Forget/Reset never touch the project directory itself.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList, Static

import dashboard.screens.home as home_module
from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchAction, LaunchRequest, LocalProjectLocation
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux as tmux_module
from dashboard.services.pane_layout_store import (
    PaneLayout,
    has_saved_pane_layouts,
    save_pane_layouts_for_location,
)
from dashboard.services.projects import Project, build_launch_request, gather_project_status
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.system_info import SystemInfo
from dashboard.services.template_store import load_all_templates
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import (
    WORKSPACE_STORE_SCHEMA_VERSION,
    load_workspace,
    save_workspace,
)
from dashboard.widgets import KeyboardActionList

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    save_projects_config(ProjectsConfig(roots=(projects_root,)))
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
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
        executor = ThreadPoolExecutor(thread_name_prefix="project-detail-test")
        loop.set_default_executor(executor)
        try:
            return await coro
        finally:
            # Textual's thread workers use the loop's default executor. Own
            # and close it here rather than racing Python 3.12's implicit
            # asyncio.run() executor shutdown after run_test() exits.
            executor.shutdown(wait=True)

    return asyncio.run(run_with_owned_executor())


def _future_version_store_path(tmp_path: Path) -> Path:
    return tmp_path / "xdg-data" / "terminal-home" / "workspaces.json"


def _has_action(screen, action_id: str) -> bool:
    actions = screen.query_one("#project-actions", KeyboardActionList)
    return any(action.id == action_id and not action.disabled for action in actions.actions)


async def _activate_action(pilot, action_id: str) -> None:
    actions = pilot.app.screen.query_one("#project-actions", KeyboardActionList)
    actions.selected_index = next(
        index for index, action in enumerate(actions.actions) if action.id == action_id
    )
    await pilot.press("enter")
    await pilot.pause()


def _write_future_version_store(tmp_path: Path, workspaces: dict[str, object] | None = None) -> str:
    """Overwrite the store with a schema version one newer than this build
    understands, simulating a newer Terminal Home having written (or
    upgraded) it between this screen's initial scan and a button click
    actually running -- the same "recheck reality at action time" race
    every other action in this screen already has to tolerate. Returns the
    exact text written, for later "was the file left untouched" checks.
    """
    store_path = _future_version_store_path(tmp_path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": WORKSPACE_STORE_SCHEMA_VERSION + 1,
        "workspaces": workspaces or {},
    }
    text = json.dumps(envelope, indent=2)
    store_path.write_text(text)
    return text


def test_save_as_template_uses_saved_metadata_and_leaves_workspace_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[object, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            assert _has_action(app.screen, "action-save_template")
            await _activate_action(pilot, "action-save_template")
            await pilot.pause()
            app.screen.query_one("#template-name-input", Input).value = "Full Stack"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            feedback = str(app.screen.query_one("#detail-error", Static).render())
        return load_workspace(project_path), feedback

    unchanged, feedback = _run(scenario())
    templates = load_all_templates()
    assert unchanged == workspace
    assert len(templates) == 1
    assert templates[0].windows == workspace.windows
    assert "Saved template" in feedback


def test_unconfigured_project_cannot_save_as_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "demo").mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> bool:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            return _has_action(app.screen, "action-save_template")

    assert _run(scenario()) is False


async def _open_project_detail(pilot, project_name: str) -> None:
    """From Home: Open Project -> wait for scan -> select *project_name* by
    display name (not just index, so multi-project scenarios stay
    unambiguous). Matched via the option's displayed label rather than its
    id: the id is now derived from the project's canonical path (so two
    same-named projects under different roots never collide), not its name.
    """
    await pilot.pause()
    await pilot.press("left")  # Move from the default Recent Projects focus to Actions.
    await pilot.pause()
    await pilot.press("enter")  # Continue Project is the first main-menu item
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()

    option_list = pilot.app.screen.query_one("#project-list", OptionList)
    index = next(
        i
        for i in range(option_list.option_count)
            if str(option_list.get_option_at_index(i).prompt)[4:].startswith(project_name)
    )
    option_list.highlighted = index
    option_list.focus()
    await pilot.pause()
    await pilot.press("enter")
    await pilot.pause()


def _project_id(path: Path) -> str:
    """The stable option id a discovered project at *path* gets --
    canonical-path-derived, matching dashboard.services.projects.
    project_option_id, not project.name.
    """
    return str(path.resolve())


async def _open_project_detail_by_path(pilot, project_path: Path) -> None:
    """Like _open_project_detail, but selects unambiguously by canonical
    path -- needed when two projects share a display name, so matching by
    name prefix (as _open_project_detail does) would be ambiguous.
    """
    await pilot.pause()
    await pilot.press("left")  # Move from Recent Projects to Primary Actions.
    await pilot.pause()
    await pilot.press("enter")  # Continue Project is the first main-menu item
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()

    option_list = pilot.app.screen.query_one("#project-list", OptionList)
    target_id = _project_id(project_path)
    index = next(
        i
        for i in range(option_list.option_count)
        if option_list.get_option_at_index(i).id == target_id
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
            return _has_action(app.screen, "action-resume")

    assert _run(scenario()) is True


def test_shows_recreate_when_saved_workspace_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    save_workspace(
        build_default_workspace("demo", LocalProjectLocation(project_path.resolve()), "demo")
    )

    async def scenario() -> bool:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            return _has_action(app.screen, "action-recreate")

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
                _has_action(screen, "action-open_default"),
                _has_action(screen, "action-configure"),
            )

    has_default, has_configure = _run(scenario())
    assert has_default and has_configure


def test_resume_exits_with_attach_launch_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: name == "demo")

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-resume")
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
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-recreate")
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
            await _activate_action(pilot, "action-open_default")
            await pilot.pause()
        return app.return_value

    result = _run(scenario())
    assert isinstance(result, LaunchRequest)
    assert result.action is LaunchAction.CREATE
    assert result.workspace is not None
    assert result.workspace.windows[0].window_name == "code"

    saved = load_workspace(project_path)
    assert saved == result.workspace


def test_open_default_workspace_against_future_version_store_does_not_save_or_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(tmux_module, "generate_session_name", lambda name, existing=None: "demo")

    async def scenario() -> tuple[object, str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            assert _has_action(app.screen, "action-open_default")

            future_text = _write_future_version_store(tmp_path)

            await _activate_action(pilot, "action-open_default")
            await pilot.pause()
            await app.workers.wait_for_complete()

            error_text = str(app.screen.query_one("#detail-error", Static).render())
            screen_name = type(app.screen).__name__
        return app.return_value, error_text, screen_name, future_text

    return_value, error_text, screen_name, future_text = _run(scenario())

    assert return_value is None  # never falsely reports success
    assert screen_name == "ProjectDetailScreen"  # stays on a safe screen
    assert "newer" in error_text.lower()
    assert str(WORKSPACE_STORE_SCHEMA_VERSION + 1) in error_text
    assert _future_version_store_path(tmp_path).read_text() == future_text


def test_open_default_workspace_for_same_named_projects_uses_distinct_session_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two projects named "example" under different roots must each get a
    default workspace with its own, distinct tmux session name -- never
    both resolving to plain "example".
    """
    projects_root = _isolate(monkeypatch, tmp_path)
    school_root = tmp_path / "school"
    school_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir()
    school_path = school_root / "example"
    school_path.mkdir()
    work_path = work_root / "example"
    work_path.mkdir()
    save_projects_config(ProjectsConfig(roots=(projects_root, school_root, work_root)))
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def open_default(project_path: Path) -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail_by_path(pilot, project_path)
            await _activate_action(pilot, "action-open_default")
            await pilot.pause()
        return app.return_value

    school_result = _run(open_default(school_path))
    work_result = _run(open_default(work_path))

    assert isinstance(school_result, LaunchRequest)
    assert isinstance(work_result, LaunchRequest)
    assert school_result.workspace is not None
    assert work_result.workspace is not None
    assert school_result.workspace.session_name != work_result.workspace.session_name
    assert school_result.workspace.session_name.startswith("example-")
    assert work_result.workspace.session_name.startswith("example-")


def test_selecting_same_named_project_never_resumes_the_others_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Safety requirement: opening one of two same-named projects must
    never attach to the other's tmux session -- school has a saved,
    running session; work is unsaved and unrelated to it.
    """
    projects_root = _isolate(monkeypatch, tmp_path)
    school_root = tmp_path / "school"
    school_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir()
    school_path = school_root / "example"
    school_path.mkdir()
    work_path = work_root / "example"
    work_path.mkdir()
    save_projects_config(ProjectsConfig(roots=(projects_root, school_root, work_root)))

    school_workspace = build_default_workspace(
        "example", LocalProjectLocation(school_path.resolve()), "school-session"
    )
    save_workspace(school_workspace)

    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: name == "school-session")

    async def take_primary_action(project_path: Path) -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail_by_path(pilot, project_path)
            action_id = next(
                candidate
                for candidate in ("action-resume", "action-recreate", "action-open_default")
                if _has_action(app.screen, candidate)
            )
            await _activate_action(pilot, action_id)
            await pilot.pause()
        return app.return_value

    school_result = _run(take_primary_action(school_path))
    work_result = _run(take_primary_action(work_path))

    # school's own action correctly targets its own running session.
    assert isinstance(school_result, LaunchRequest)
    assert school_result.action is LaunchAction.ATTACH
    assert school_result.workspace == school_workspace

    # work is unrelated and unsaved -- it must never attach to school's
    # session, whatever action its own (distinct, unsaved) status offers.
    assert isinstance(work_result, LaunchRequest)
    work_session_name = (
        work_result.session_name
        if work_result.workspace is None
        else work_result.workspace.session_name
    )
    assert work_session_name != "school-session"
    assert work_session_name.startswith("example-")


def test_edit_workspace_saves_without_launching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    save_workspace(
        build_default_workspace("demo", LocalProjectLocation(project_path.resolve()), "demo")
    )
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[str, object]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-edit")
            await pilot.pause()
            assert type(app.screen).__name__ == "WindowSummaryScreen"

            actions = app.screen.query_one("#window-summary-actions", KeyboardActionList)
            actions.selected_index = 3
            actions.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "ReviewScreen"

            await pilot.press("enter")
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

    custom = WorkspaceSpec.for_local_project(
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
    save_pane_layouts_for_location(
        LocalProjectLocation(project_path.resolve()),
        {"custom": PaneLayout("custom", 1, "80x20,0,0,0")},
    )
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario_cancel() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-reset")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    unchanged = _run(scenario_cancel())
    assert unchanged == custom  # cancelling leaves the saved workspace untouched
    assert has_saved_pane_layouts(LocalProjectLocation(project_path.resolve()))

    async def scenario_confirm() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-reset")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    reset = _run(scenario_confirm())
    assert reset is not None
    assert reset.windows[0].window_name == "code"
    assert reset.session_name == "demo"  # session identity preserved
    assert not has_saved_pane_layouts(LocalProjectLocation(project_path.resolve()))


def test_reset_remembered_pane_sizes_is_separate_and_keeps_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    save_pane_layouts_for_location(
        LocalProjectLocation(project_path.resolve()),
        {"code": PaneLayout("code", 2, "custom")},
    )
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> None:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            assert _has_action(app.screen, "action-reset_pane_sizes")
            await _activate_action(pilot, "action-reset_pane_sizes")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.press("down", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()

    _run(scenario())
    assert load_workspace(project_path) == workspace
    assert not has_saved_pane_layouts(LocalProjectLocation(project_path.resolve()))


def test_reset_to_default_against_future_version_store_does_not_overwrite_or_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[object, str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            assert _has_action(app.screen, "action-reset")

            future_text = _write_future_version_store(
                tmp_path, {str(project_path.resolve()): workspace.to_dict()}
            )

            await _activate_action(pilot, "action-reset")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.press("down", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()

            error_text = str(app.screen.query_one("#detail-error", Static).render())
            screen_name = type(app.screen).__name__
        return app.return_value, error_text, screen_name, future_text

    return_value, error_text, screen_name, future_text = _run(scenario())

    assert return_value is None
    assert screen_name == "ProjectDetailScreen"
    assert "newer" in error_text.lower()
    assert _future_version_store_path(tmp_path).read_text() == future_text


def test_forget_workspace_removes_metadata_not_project_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    (project_path / "keep-me.txt").write_text("still here")
    save_workspace(
        build_default_workspace("demo", LocalProjectLocation(project_path.resolve()), "demo")
    )
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-forget")
            await pilot.pause()
            await pilot.press("down", "enter")
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
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> object:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "action-forget")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            await app.workers.wait_for_complete()
        return load_workspace(project_path)

    assert _run(scenario()) == workspace


def test_forget_workspace_against_future_version_store_does_not_overwrite_or_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[object, str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            assert _has_action(app.screen, "action-forget")

            future_text = _write_future_version_store(
                tmp_path, {str(project_path.resolve()): workspace.to_dict()}
            )

            await _activate_action(pilot, "action-forget")
            await pilot.pause()
            assert type(app.screen).__name__ == "ConfirmScreen"
            await pilot.press("down", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()

            error_text = str(app.screen.query_one("#detail-error", Static).render())
            screen_name = type(app.screen).__name__
        return app.return_value, error_text, screen_name, future_text

    return_value, error_text, screen_name, future_text = _run(scenario())

    assert return_value is None
    assert screen_name == "ProjectDetailScreen"
    assert "newer" in error_text.lower()
    assert _future_version_store_path(tmp_path).read_text() == future_text


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
            has_forget = _has_action(screen, "action-forget")
            has_configure = _has_action(screen, "action-configure")
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
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)

    async def scenario() -> tuple[str, object]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_project_detail(pilot, "demo")
            await _activate_action(pilot, "back-to-list")
            await pilot.pause()
            return type(app.screen).__name__, app.return_value

    screen_name, return_value = _run(scenario())
    assert screen_name == "ProjectsScreen"
    assert return_value is None
    assert load_workspace(project_path) == workspace
