"""Textual Pilot tests for the Open Project list screen
(dashboard.screens.projects.ProjectsScreen).

Project scanning happens in a worker thread, so every scenario waits on
`pilot.app.workers.wait_for_complete()` after anything that triggers a
(re)scan. tmux is fully mocked; no real tmux session is ever queried,
created, or attached to.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList

import dashboard.screens.home as home_module
import dashboard.screens.projects as projects_module
from dashboard.app import TerminalHomeApp
from dashboard.models import RemoteProjectRegistration, SshProjectLocation
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux as tmux_module
from dashboard.services.project_selection import RegisteredRemoteProject
from dashboard.services.projects import Project, ProjectScanResult, ProjectStatus
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.remote_project_store import create_remote_project
from dashboard.services.system_info import SystemInfo

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the projects root and the workspace store at tmp_path, and
    replace every tmux call the background scan makes with a fake.
    """
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    save_projects_config(ProjectsConfig(roots=(projects_root,)))
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
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
    return projects_root


def _run(coro):
    async def run_with_owned_executor():
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="projects-screen-test")
        loop.set_default_executor(executor)
        try:
            return await coro
        finally:
            # Textual's thread workers use the loop's default executor. Own
            # and close it here rather than racing Python 3.12's implicit
            # asyncio.run() executor shutdown after run_test() exits.
            executor.shutdown(wait=True)

    return asyncio.run(run_with_owned_executor())


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
    return [
        str(option_list.get_option_at_index(i).id)
        for i in range(option_list.option_count)
        if option_list.get_option_at_index(i).id is not None
    ]


def _project_id(path: Path) -> str:
    """The stable option id a discovered project at *path* gets --
    canonical-path-derived, matching dashboard.services.projects.
    project_option_id, not project.name.
    """
    return str(path.resolve())


def _option_index(option_list: OptionList, option_id: str) -> int:
    return next(
        index
        for index, option in enumerate(option_list.options)
        if option.id == option_id
    )


def _visible_option_indices(option_list: OptionList) -> range:
    """Return option indexes covered by the mounted list viewport."""
    scroll_y = option_list.scroll_offset.y
    height = option_list.scrollable_content_region.height
    return range(scroll_y, min(option_list.option_count, scroll_y + height))


def test_lists_discovered_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()
    (projects_root / "beta").mkdir()

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            assert type(app.screen).__name__ == "ProjectsScreen"
            return _option_ids(pilot)

    expected = [_project_id(projects_root / "alpha"), _project_id(projects_root / "beta")]
    assert _run(scenario()) == expected


def test_category_header_remains_visible_when_navigation_wraps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrapping selection must reveal the first project's category context."""
    _isolate(monkeypatch, tmp_path)
    local_projects = tuple(
        Project(name, tmp_path / name)
        for name in ("configured-a", "configured-b", "unconfigured-a", "unconfigured-b")
    )

    def status(project: Project, *, configured: bool, running: bool = False) -> ProjectStatus:
        return ProjectStatus(
            project=project,
            canonical_path=project.path,
            project_dir_exists=True,
            is_git_repo=True,
            git_branch="main",
            saved_workspace=object() if configured else None,  # type: ignore[arg-type]
            workspace_metadata_error=None,
            expected_session_name=project.name,
            tmux_available=True,
            session_running=running,
            last_modified=None,
        )

    statuses = tuple(
        status(project, configured=index < 2, running=index == 2)
        for index, project in enumerate(local_projects)
    )
    registration = RemoteProjectRegistration(
        "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
        "d84aeefb-7c29-4c63-b39c-766d559df977",
        "remote-project",
        "/srv/remote-project",
    )
    remote = RegisteredRemoteProject(
        "remote-project",
        SshProjectLocation(registration.host_id, registration.remote_path),
        registration,
    )
    scan_result = ProjectScanResult(statuses, truncated=False, warnings=())
    monkeypatch.setattr(projects_module, "scan_all_projects", lambda: scan_result)
    monkeypatch.setattr(
        projects_module,
        "list_selectable_projects",
        lambda: (*local_projects, remote),
    )

    async def scenario() -> tuple[str, str, int, int, list[str], int, int, int]:
        app = TerminalHomeApp()
        async with app.run_test(size=(70, 12)) as pilot:
            await _open_projects_screen(pilot)
            screen = app.screen
            option_list = screen.query_one("#project-list", OptionList)
            option_list.focus()
            await pilot.pause()

            entry_ids = _option_ids(pilot)
            first_id = entry_ids[0]
            final_id = entry_ids[-1]
            option_objects = tuple(id(option) for option in option_list.options)
            option_list.highlighted = _option_index(option_list, final_id)
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            first_header_index = screen._category_header_index_by_entry_id[first_id]
            visible_after_down = list(_visible_option_indices(option_list))
            assert first_header_index in visible_after_down
            selected_after_down = str(
                option_list.get_option_at_index(option_list.highlighted).id
            )
            headers = [
                str(option.prompt.plain)
                for option in option_list.options
                if option.id is None
            ]
            header_options = [option for option in option_list.options if option.id is None]
            assert len(header_options) == 3
            assert all(option.disabled for option in header_options)

            # Crossing a category boundary applies the same rule without
            # rebuilding the list or changing the selected project.
            configured_last = entry_ids[1]
            option_list.highlighted = _option_index(option_list, configured_last)
            await pilot.press("down")
            await pilot.pause()
            next_id = entry_ids[2]
            next_header = screen._category_header_index_by_entry_id[next_id]
            assert str(option_list.get_option_at_index(option_list.highlighted).id) == next_id
            assert next_header in _visible_option_indices(option_list)

            option_list.highlighted = _option_index(option_list, first_id)
            await pilot.press("up")
            await pilot.pause()
            final_header_index = screen._category_header_index_by_entry_id[final_id]
            visible_after_up = list(_visible_option_indices(option_list))
            assert final_header_index in visible_after_up
            assert tuple(id(option) for option in option_list.options) == option_objects

            filter_box = screen.query_one("#project-filter", Input)
            filter_box.value = "remote-project"
            await pilot.pause()
            assert sum(option.id is None for option in option_list.options) == 1
            filter_box.value = ""
            await pilot.pause()
            assert sum(option.id is None for option in option_list.options) == 3
            return (
                selected_after_down,
                str(option_list.get_option_at_index(option_list.highlighted).id),
                first_header_index,
                final_header_index,
                headers,
                visible_after_down[0],
                visible_after_up[0],
                option_list.scrollable_content_region.height,
            )

    (
        selected_after_down,
        selected_after_up,
        first_header,
        final_header,
        headers,
        down_top,
        up_top,
        viewport_height,
    ) = _run(scenario())
    assert selected_after_down == _project_id(tmp_path / "configured-a")
    assert selected_after_up.startswith("ssh:")
    assert first_header in range(down_top, down_top + viewport_height)
    assert final_header in range(up_top, up_top + viewport_height)
    assert len(headers) == 3


def test_lists_registered_remote_projects_and_opens_offline_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            host_id,
            "remote-api",
            "/srv/Project With Spaces",
        )
    )

    async def scenario() -> tuple[list[str], list[str], str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            ids = _option_ids(pilot)
            labels = [
                str(option_list.get_option_at_index(i).prompt)
                for i in range(option_list.option_count)
            ]
            option_list.highlighted = 1
            option_list.focus()
            await pilot.press("enter")
            await pilot.pause()
            detail = app.screen
            return (
                ids,
                labels,
                str(detail.query_one("#detail-host").render()),
                str(detail.query_one("#detail-remote-status").render()),
            )

    ids, labels, host_text, status_text = _run(scenario())
    selector = f"ssh:{host_id}:/srv/Project With Spaces"
    assert ids == [selector]
    remote_label = next(label for label in labels if "[Remote]" in label)
    assert "/srv/Project With Spaces" in remote_label
    assert host_id in host_text
    assert "metadata only" in status_text


def test_excludes_terminal_home_and_hidden_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()
    (projects_root / "terminal-home").mkdir()
    (projects_root / ".config").mkdir()

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            return _option_ids(pilot)

    assert _run(scenario()) == [_project_id(projects_root / "alpha")]


def test_search_filters_the_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()
    (projects_root / "beta").mkdir()

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            app.screen.query_one("#project-filter", Input).value = "al"
            await pilot.pause()
            return _option_ids(pilot)

    assert _run(scenario()) == [_project_id(projects_root / "alpha")]


def test_repeated_filtering_keeps_project_names_at_one_marker_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    for name in ("alpha", "beta", "gamma"):
        (projects_root / name).mkdir()

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            filter_box = app.screen.query_one("#project-filter", Input)
            option_list = app.screen.query_one("#project-list", OptionList)
            for query in ("a", "be", "", "ga", "", "al", ""):
                filter_box.value = query
                await pilot.pause()
            return [
                str(option.prompt.plain)
                for option in option_list.options
                if option.id is not None
            ]

    labels = _run(scenario())
    assert all(label for label in labels)
    # Two cells belong to KeyboardOptionList's marker and two to the
    # Continue Project category-child indentation; neither may accumulate.
    assert all(label[:2] in {"› ", "  "} for label in labels)
    assert all(label[2:4] == "  " for label in labels)
    assert all(not label[4:].startswith("  ") for label in labels)


def test_escape_returns_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = TerminalHomeApp()
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
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            option_list.highlighted = 1
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
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            (projects_root / "beta").mkdir()

            await pilot.press("f5")
            await pilot.pause()
            await pilot.app.workers.wait_for_complete()
            await pilot.pause()

            return _option_ids(pilot)

    expected = [_project_id(projects_root / "alpha"), _project_id(projects_root / "beta")]
    assert _run(scenario()) == expected


def test_no_projects_found_shows_placeholder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> int:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            return option_list.option_count

    assert _run(scenario()) == 1  # the disabled "No projects found" placeholder


def test_continue_project_scans_every_configured_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continue Project must consume the same configured discovery result
    as Home -- neither screen loads or interprets configuration on its own.
    """
    projects_root = _isolate(monkeypatch, tmp_path)
    second_root = tmp_path / "second-root"
    second_root.mkdir()
    (second_root / "beta").mkdir()
    save_projects_config(ProjectsConfig(roots=(projects_root, second_root)))

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            return _option_ids(pilot)

    assert _run(scenario()) == [_project_id(second_root / "beta")]


def test_scan_warning_is_shown_and_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    missing_root = tmp_path / "does-not-exist"
    save_projects_config(ProjectsConfig(roots=(projects_root, missing_root)))

    async def scenario() -> tuple[str, int, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            warning = str(app.screen.query_one("#scan-warning").render())
            option_count = app.screen.query_one("#project-list", OptionList).option_count
            screen_name = type(app.screen).__name__
        return warning, option_count, screen_name

    warning, option_count, screen_name = _run(scenario())
    assert str(missing_root) in warning
    assert option_count == 1  # the (empty) good root still scans fine -- just a placeholder
    assert screen_name == "ProjectsScreen"  # a bad root never crashes the screen


# --- same-basename projects under different roots ---------------------------------


def _setup_duplicate_named_projects(tmp_path: Path, projects_root: Path) -> tuple[Path, Path]:
    school_root = tmp_path / "school"
    school_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir()
    (school_root / "example").mkdir()
    (work_root / "example").mkdir()
    save_projects_config(ProjectsConfig(roots=(projects_root, school_root, work_root)))
    return school_root / "example", work_root / "example"


def test_same_named_projects_under_different_roots_both_appear_distinguishably(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    school_example, work_example = _setup_duplicate_named_projects(tmp_path, projects_root)

    async def scenario() -> dict[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            return {
                str(option.id): str(option.prompt)
                for option in option_list.options
                if option.id is not None
            }

    labels = _run(scenario())
    school_id = _project_id(school_example)
    work_id = _project_id(work_example)

    # Requirement 1: both appear, and neither overwrote the other.
    assert school_id in labels
    assert work_id in labels
    assert school_id != work_id

    # Requirement 5: distinguishable, concise labels -- not identical, and
    # each names the root that makes it distinct.
    school_label = labels[school_id]
    work_label = labels[work_id]
    assert school_label != work_label
    assert "school" in school_label
    assert "work" in work_label
    # The marker and child indent precede the friendly name.
    assert school_label[4:].startswith("example")
    assert work_label[4:].startswith("example")


def test_selecting_either_same_named_project_opens_its_own_canonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    school_example, work_example = _setup_duplicate_named_projects(tmp_path, projects_root)

    async def select(target_id: str) -> Path:
        app = TerminalHomeApp()
        async with app.run_test(size=_SIZE) as pilot:
            await _open_projects_screen(pilot)
            option_list = app.screen.query_one("#project-list", OptionList)
            option_list.highlighted = _option_index(option_list, target_id)
            option_list.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "ProjectDetailScreen"
            return app.screen.project.path

    school_id = _project_id(school_example)
    work_id = _project_id(work_example)

    opened_school = _run(select(school_id))
    assert opened_school == school_example

    opened_work = _run(select(work_id))
    assert opened_work == work_example
    assert opened_work != opened_school
