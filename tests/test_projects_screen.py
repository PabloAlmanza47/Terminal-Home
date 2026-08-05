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
from dashboard.app import TerminalHomeApp
from dashboard.models import RemoteProjectRegistration
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux as tmux_module
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
            return [str(option.prompt.plain)[2:] for option in option_list.options]

    labels = _run(scenario())
    assert all(label for label in labels)
    assert all(not label.startswith(("› ", "  ")) for label in labels)


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
    assert school_label[2:].startswith("example")  # marker precedes the friendly name
    assert work_label[2:].startswith("example")


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
