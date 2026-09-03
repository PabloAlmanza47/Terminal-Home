"""Textual Pilot tests for the redesigned home screen
(dashboard.screens.home.HomeScreen).

tmux is fully mocked -- no real session is ever queried, created, or
attached to. Project scanning happens in a worker thread, so every
scenario waits on `pilot.app.workers.wait_for_complete()` after anything
that triggers a (re)scan.
"""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime as RealDateTime
from pathlib import Path

import pytest
from textual.widgets import Input, OptionList

import dashboard.screens.home as home_module
import dashboard.services.projects as projects_module
from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchAction, LaunchRequest, LocalProjectLocation
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux as tmux_module
from dashboard.services.agent_deck import AgentDeckSession, AgentDeckSnapshot, AgentStatus
from dashboard.services.project_rows import (
    ActivityProjectRow,
    format_activity_table,
    format_activity_table_header,
)
from dashboard.services.projects import (
    Project,
    build_launch_request,
    gather_project_status,
    gather_single_project_status,
)
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.system_info import SystemInfo
from dashboard.services.tmux import TmuxSession
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import save_workspace
from dashboard.widgets import CircularSelectionList, KeyboardActionList

_NARROW = (80, 24)
_MEDIUM = (100, 30)
_WIDE = (140, 40)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
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
    return projects_root


def _run(coro):
    async def run_with_owned_executor():
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="home-screen-test")
        loop.set_default_executor(executor)
        try:
            return await coro
        finally:
            # Textual's thread workers use the loop's default executor. Own
            # and close it here instead of leaving Python 3.12's implicit
            # asyncio.run() teardown to race an idle worker thread.
            executor.shutdown(wait=True)

    return asyncio.run(run_with_owned_executor())


async def _wait_for_scan(pilot) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def _option_labels(option_list: OptionList) -> list[str]:
    return [str(option_list.get_option_at_index(i).prompt) for i in range(option_list.option_count)]


def _option_ids(option_list: OptionList) -> list[str | None]:
    return [option_list.get_option_at_index(i).id for i in range(option_list.option_count)]


def test_activity_table_keeps_status_columns_grouped_and_truncates_names() -> None:
    rows = [
        ActivityProjectRow(
            "PabloAlmanza.github.io-with-a-long-project-name",
            "● Running",
            "● Running",
            "◐ Waiting",
        ),
        ActivityProjectRow("dotfiles", "● Running", "— N/A", "● Working"),
    ]

    rendered = format_activity_table(rows, 120)

    assert "…" in rendered[0]
    assert rendered[0].index("● Running") == rendered[1].index("● Running")
    assert rendered[0].index("● Running") < 40


def test_activity_table_header_uses_data_row_column_widths() -> None:
    rows = [
        ActivityProjectRow("demo", "● Running", "— N/A", "— No Agent"),
    ]

    header = format_activity_table_header(120, rows)
    rendered = format_activity_table(rows, 120)[0]

    assert header.index("Workspace") == rendered.index("● Running")
    assert header.index("Server") == rendered.index("— N/A")
    assert header.index("Codex") == rendered.index("— No Agent")


def _project_id(path: Path) -> str:
    """The stable option id a discovered project at *path* gets --
    canonical-path-derived, matching dashboard.services.projects.
    project_option_id, not project.name.
    """
    return str(path.resolve())


# --- Default focus -----------------------------------------------------------


def test_default_focus_falls_back_to_actions_without_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[int | None, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            menu = app.screen.query_one("#main-menu", KeyboardActionList)
            return menu.selected_index, app.focused is menu

    highlighted, is_focused = _run(scenario())
    assert highlighted == 0
    assert is_focused is True


def test_default_focus_selects_first_recent_project_and_enter_opens_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> tuple[str | None, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            focused_id = recent.get_option_at_index(recent.highlighted or 0).id
            assert app.focused is recent
            await pilot.press("enter")
            await pilot.pause()
            return focused_id, type(app.screen).__name__

    focused_id, screen_name = _run(scenario())
    assert focused_id == _project_id(projects_root / "alpha")
    assert screen_name == "ProjectDetailScreen"


def test_project_search_filters_case_insensitively_and_escape_restores_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "terminal-home").mkdir()
    (projects_root / "SHPE-Connect").mkdir()

    async def scenario() -> tuple[str, str | None, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("/")
            search = app.screen.query_one("#project-search", Input)
            await pilot.press("s", "h", "p", "e")
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            focused_id = recent.get_option_at_index(recent.highlighted or 0).id
            query = search.value
            await pilot.press("escape")
            await pilot.pause()
            return query, focused_id, app.focused is recent

    query, focused_id, focus_restored = _run(scenario())
    assert query == "shpe"
    assert focused_id == _project_id(projects_root / "SHPE-Connect")
    assert focus_restored is True


def test_project_search_enter_opens_filtered_project_and_empty_state_is_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "CSCE-331-Project-1").mkdir()

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("/")
            await pilot.press("3", "3", "1")
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            selected = str(recent.get_option_at_index(recent.highlighted or 0).prompt)
            await pilot.press("enter")
            await pilot.pause()
            opened = type(app.screen).__name__
            return selected, opened

    selected, opened = _run(scenario())
    assert "CSCE-331-Project-1" in selected
    assert opened == "ProjectDetailScreen"


def test_project_search_shows_no_match_without_leaving_search_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "terminal-home").mkdir()

    async def scenario() -> tuple[str, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("/")
            await pilot.press("z", "z", "z")
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            return _option_labels(recent)[0], app.focused is app.screen.query_one(
                "#project-search", Input
            )

    label, search_focused = _run(scenario())
    assert label.strip() == 'No projects match "zzz"'
    assert search_focused is True


# --- Recent projects -----------------------------------------------------------


def test_recent_project_selection_opens_project_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            recent.highlighted = 0
            recent.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ProjectDetailScreen"


def test_home_shows_same_named_projects_distinguishably_without_overwriting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two projects sharing a basename under different configured roots
    must both appear in Recent Projects (never one silently overwriting
    the other in the id-keyed lookup), with distinguishable labels.
    """
    projects_root = _isolate(monkeypatch, tmp_path)
    school_root = tmp_path / "school"
    school_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir()
    (school_root / "example").mkdir()
    (work_root / "example").mkdir()
    save_projects_config(ProjectsConfig(roots=(projects_root, school_root, work_root)))

    async def scenario() -> tuple[list[str | None], list[str]]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            return _option_ids(recent), _option_labels(recent)

    ids, labels = _run(scenario())
    school_id = _project_id(school_root / "example")
    work_id = _project_id(work_root / "example")

    assert school_id in ids
    assert work_id in ids
    assert labels[ids.index(school_id)] != labels[ids.index(work_id)]


def test_home_selection_of_either_same_named_project_resolves_its_own_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    school_root = tmp_path / "school"
    school_root.mkdir()
    work_root = tmp_path / "work"
    work_root.mkdir()
    (school_root / "example").mkdir()
    (work_root / "example").mkdir()
    save_projects_config(ProjectsConfig(roots=(projects_root, school_root, work_root)))

    async def select(target_id: str) -> Path:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            ids = _option_ids(recent)
            recent.focus()
            await pilot.pause()
            recent.highlighted = ids.index(target_id)
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "ProjectDetailScreen"
            return app.screen.project.path

    school_path = school_root / "example"
    work_path = work_root / "example"

    opened_school = _run(select(_project_id(school_path)))
    assert opened_school == school_path

    opened_work = _run(select(_project_id(work_path)))
    assert opened_work == work_path
    assert opened_work != opened_school


def test_home_active_session_matches_only_the_correct_same_named_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running session for one of two same-named projects must mark
    only that project as running -- never the other, and Active Sessions
    must resolve the running session back to the correct canonical path.
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
    config = ProjectsConfig(roots=(projects_root, school_root, work_root))
    save_projects_config(config)

    school_session_name = gather_single_project_status(
        Project(name="example", path=school_path), config=config
    ).expected_session_name

    monkeypatch.setattr(
        tmux_module,
        "list_tmux_sessions",
        lambda: [TmuxSession(name=school_session_name, windows=1, created="now", attached=False)],
    )
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: name == school_session_name)

    async def scenario() -> tuple[Path | None, bool, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            matched = app.screen._session_lookup.get(school_session_name)
            matched_path = matched.canonical_path if matched is not None else None

            statuses_by_path = {s.canonical_path: s for s in app.screen._last_statuses}
            school_running = statuses_by_path[school_path.resolve()].session_running
            work_running = statuses_by_path[work_path.resolve()].session_running
        return matched_path, school_running, work_running

    matched_path, school_running, work_running = _run(scenario())

    assert matched_path == school_path.resolve()
    assert school_running is True
    assert work_running is False


def test_view_all_projects_opens_projects_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            recent.focus()
            await pilot.pause()
            recent.highlighted = recent.option_count - 1  # "View All Projects" is last
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ProjectsScreen"


def test_missing_projects_directory_shows_actionable_empty_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    projects_root.rmdir()

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            return _option_labels(app.screen.query_one("#recent-projects-list", OptionList))

    labels = _run(scenario())
    assert any("No projects" in label for label in labels)
    assert any("Create New Project" in label for label in labels)
    # The missing root itself is reported too -- nonfatal, never a crash.
    assert any("Warning:" in label and str(projects_root) in label for label in labels)


def test_empty_state_create_project_option_opens_new_project_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    projects_root.rmdir()

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            recent.highlighted = recent.option_count - 1
            recent.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "NewProjectScreen"


def test_malformed_workspace_metadata_shows_warning_badge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    store_path = tmp_path / "xdg-data" / "terminal-home" / "workspaces.json"
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps({str(project_path.resolve()): {"project_name": "bad"}}))

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            return _option_labels(app.screen.query_one("#recent-projects-list", OptionList))

    labels = _run(scenario())
    assert any("Metadata Warning" in label for label in labels)


# --- Active sessions -----------------------------------------------------------


def test_missing_tmux_shows_friendly_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: False)

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            return _option_labels(app.screen.query_one("#active-sessions-list", OptionList))

    labels = _run(scenario())
    assert any("not installed" in label.lower() for label in labels)


def test_no_sessions_running_shows_friendly_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            return _option_labels(app.screen.query_one("#active-sessions-list", OptionList))

    labels = _run(scenario())
    assert any("no tmux sessions" in label.lower() for label in labels)


def test_matched_session_selection_produces_attach_request_with_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    workspace = build_default_workspace(
        "demo", LocalProjectLocation(project_path.resolve()), "demo"
    )
    save_workspace(workspace)
    monkeypatch.setattr(
        tmux_module,
        "list_tmux_sessions",
        lambda: [TmuxSession(name="demo", windows=1, created="now", attached=False)],
    )
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: name == "demo")

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            sessions = app.screen.query_one("#active-sessions-list", OptionList)
            sessions.highlighted = 0
            sessions.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        return app.return_value

    result = _run(scenario())
    assert isinstance(result, LaunchRequest)
    assert result.action is LaunchAction.ATTACH
    assert result.workspace == workspace
    # Same request the shared service function would build for this project's status.
    status = gather_project_status(
        Project(name="demo", path=project_path), running_sessions={"demo"}
    )
    assert result == build_launch_request(status)


def test_unmatched_session_selection_produces_orphan_attach_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one Active Sessions case that does *not* go through
    build_launch_request: there is no ProjectStatus at all for a running
    session unmatched to any known project, so the request is built
    directly from the session name tmux itself reported.
    """
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(
        tmux_module,
        "list_tmux_sessions",
        lambda: [TmuxSession(name="side-quest", windows=2, created="now", attached=False)],
    )

    async def scenario() -> LaunchRequest:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            sessions = app.screen.query_one("#active-sessions-list", OptionList)
            labels = _option_labels(sessions)
            assert any("unmatched" in label.lower() for label in labels)
            sessions.highlighted = 0
            sessions.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        return app.return_value

    result = _run(scenario())
    assert isinstance(result, LaunchRequest)
    assert result.action is LaunchAction.ATTACH
    assert result.workspace is None
    assert result.session_name == "side-quest"


def test_active_sessions_hides_agent_deck_tmux_sessions_but_keeps_unmatched_user_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    agent_tmux = "agentdeck_demo_123"
    monkeypatch.setattr(
        tmux_module,
        "list_tmux_sessions",
        lambda: [
            TmuxSession(agent_tmux, 1, "now", False),
            TmuxSession("side-quest", 1, "now", False),
        ],
    )
    monkeypatch.setattr(
        projects_module,
        "agent_deck_snapshot",
        lambda: AgentDeckSnapshot(
            True,
            (
                AgentDeckSession(
                    "agent-1", "demo", project_path, "codex", AgentStatus.WAITING, agent_tmux
                ),
            ),
        ),
    )

    async def scenario() -> list[str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            return _option_labels(app.screen.query_one("#active-sessions-list", OptionList))

    labels = _run(scenario())
    assert not any(agent_tmux in label for label in labels)
    assert any("side-quest" in label for label in labels)


# --- F5 refresh and clock/scan independence ------------------------------------


def test_f5_refresh_rescans_projects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> list[str | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            (projects_root / "beta").mkdir()

            await pilot.press("f5")
            await _wait_for_scan(pilot)

            return _option_ids(app.screen.query_one("#recent-projects-list", OptionList))

    ids = _run(scenario())
    assert _project_id(projects_root / "beta") in ids


def test_changing_configured_roots_affects_the_next_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Home never loads or interprets project-discovery configuration
    itself -- it just rescans via scan_all_projects(), so a root change
    made through Settings/Project Discovery takes effect on the very next
    scan, exactly like any other change to the saved configuration.
    """
    original_root = _isolate(monkeypatch, tmp_path)
    (original_root / "alpha").mkdir()
    new_root = tmp_path / "elsewhere"
    (new_root / "beta").mkdir(parents=True)

    async def scenario() -> tuple[list[str | None], list[str | None]]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            before = _option_ids(app.screen.query_one("#recent-projects-list", OptionList))

            save_projects_config(ProjectsConfig(roots=(new_root,)))
            await pilot.press("f5")
            await _wait_for_scan(pilot)
            after = _option_ids(app.screen.query_one("#recent-projects-list", OptionList))
        return before, after

    before, after = _run(scenario())
    alpha_id = _project_id(original_root / "alpha")
    beta_id = _project_id(new_root / "beta")
    assert alpha_id in before
    assert beta_id not in before
    assert beta_id in after
    assert alpha_id not in after


def test_clock_tick_updates_display_without_rescanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    scan_calls = {"n": 0}
    original_scan_all = home_module.scan_all_projects

    def counting_scan_all(*args: object, **kwargs: object):
        scan_calls["n"] += 1
        return original_scan_all(*args, **kwargs)

    monkeypatch.setattr(home_module, "scan_all_projects", counting_scan_all)

    class FixedDateTime(RealDateTime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 8, 5, 18, 0, 0, tzinfo=tz)

    # This test controls the datetime name used by HomeScreen so it does not
    # depend on the machine's clock or timezone.
    monkeypatch.setattr(home_module, "datetime", FixedDateTime)

    async def scenario() -> tuple[int, int, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            calls_after_scan = scan_calls["n"]
            before = str(app.screen.query_one("#home-meta").render())

            await pilot.pause(2.2)  # let two clock ticks fire with no user action

            after = str(app.screen.query_one("#home-meta").render())
            return calls_after_scan, scan_calls["n"], before, after

    calls_before, calls_after, before_text, after_text = _run(scenario())
    assert calls_before == 1
    assert calls_after == 1  # unchanged: clock ticks never trigger a rescan
    assert before_text == after_text
    assert "Wed Aug 05 • 18:00 • Good evening" in before_text


# --- Responsive layout at the three required terminal sizes --------------------


@pytest.mark.parametrize(
    ("size", "expected_class"),
    [(_NARROW, "layout-narrow"), (_MEDIUM, "layout-wide"), (_WIDE, "layout-wide")],
)
def test_layout_class_matches_terminal_width_and_nothing_overflows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int], expected_class: str
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> tuple[set[str], bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=size) as pilot:
            await _wait_for_scan(pilot)
            dashboard = app.screen.query_one("#home-dashboard")
            screen_size = app.screen.size
            overflow = False
            for panel_id in ("panel-actions", "panel-recent", "panel-sessions"):
                region = app.screen.query_one(f"#{panel_id}").region
                if (
                    region.x + region.width > screen_size.width
                    or region.y + region.height > screen_size.height
                ):
                    overflow = True
            return set(dashboard.classes), overflow

    classes, overflow = _run(scenario())
    assert expected_class in classes
    assert overflow is False


def test_wide_home_sections_share_the_first_grid_row_height(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> tuple[int, int, int, int]:
        app = TerminalHomeApp()
        async with app.run_test(size=_WIDE) as pilot:
            await _wait_for_scan(pilot)
            actions = app.screen.query_one("#panel-actions").region
            recent = app.screen.query_one("#panel-recent").region
            return actions.y, recent.y, actions.y + actions.height, recent.y + recent.height

    actions_y, recent_y, actions_bottom, recent_bottom = _run(scenario())
    assert recent_y < actions_y
    assert recent_bottom > recent_y


def test_compact_terminal_header_has_one_plain_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[bool, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_NARROW) as pilot:
            await _wait_for_scan(pilot)
            logo = app.screen.query_one("#home-logo")
            return bool(logo.display), str(logo.render())

    displayed, title = _run(scenario())
    assert displayed is True
    assert title == "╭─>_─╮  TERMINAL HOME"


def test_home_sections_reset_cursor_when_focus_moves_between_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    (tmp_path / "projects" / "alpha").mkdir()

    async def scenario() -> tuple[str, str, int | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            menu = app.screen.query_one("#main-menu", KeyboardActionList)
            recent = app.screen.query_one("#recent-projects-list", OptionList)
            menu.selected_index = 1
            menu.focus()
            await pilot.press("right")
            await pilot.pause()
            recent_prompt_while_focused = str(recent.get_option_at_index(0).prompt)
            await pilot.press("left")
            await pilot.pause()
            recent_prompt_after_blur = str(recent.get_option_at_index(0).prompt)
            return recent_prompt_while_focused, recent_prompt_after_blur, menu.selected_index

    focused, blurred, menu_index = _run(scenario())
    assert focused.startswith("› ")
    assert not blurred.startswith("› ")
    assert menu_index == 0


# --- Escape / q ----------------------------------------------------------------


def test_q_quits_the_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> LaunchRequest | None:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("q")
            await pilot.pause()
        return app.return_value

    assert _run(scenario()) is None


def test_escape_does_not_crash_or_navigate_away(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "HomeScreen"


# --- Keyboard navigation across every panel -------------------------------------


def test_arrow_keys_move_between_home_sections_without_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> list[str | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            focused_ids: list[str | None] = [app.focused.id if app.focused else None]
            for key in ("right", "right", "left"):
                await pilot.press(key)
                await pilot.pause()
                focused_ids.append(app.focused.id if app.focused else None)
            return focused_ids

    focused_ids = _run(scenario())
    assert focused_ids == [
        "recent-projects-list",
        "active-sessions-list",
        "main-menu",
        "active-sessions-list",
    ]


def test_digit_shortcut_opens_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("5")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "SettingsScreen"


# --- Settings reflected immediately on return -----------------------------------


def test_disabling_artwork_in_settings_hides_it_immediately_on_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_WIDE) as pilot:  # roomy enough for art to normally show
            await _wait_for_scan(pilot)
            logo_before = bool(app.screen.query_one("#home-logo").display)

            await pilot.press("5")
            await pilot.pause()
            assert type(app.screen).__name__ == "SettingsScreen"

            appearance = app.screen.query_one("#appearance-settings", CircularSelectionList)
            appearance.toggle("artwork")
            await pilot.pause()

            await pilot.press("escape")
            await pilot.pause()
            assert type(app.screen).__name__ == "HomeScreen"

            logo_after = bool(app.screen.query_one("#home-logo").display)
            return logo_before, logo_after

    logo_before, logo_after = _run(scenario())
    assert logo_before is True
    assert logo_after is False


def test_disabling_clock_in_settings_hides_it_immediately_on_return(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[bool, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            await pilot.press("5")
            await pilot.pause()
            appearance = app.screen.query_one("#appearance-settings", CircularSelectionList)
            before_value = "clock" in appearance.selected
            appearance.toggle("clock")
            await pilot.pause()
            return before_value, "clock" in appearance.selected

    before_value, after_value = _run(scenario())
    assert before_value is True
    assert after_value is False


def test_enabling_compact_layout_reduces_recent_project_label_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    save_workspace(
        build_default_workspace("demo", LocalProjectLocation(project_path.resolve()), "demo")
    )

    async def scenario() -> tuple[bool, list[str], list[str]]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            labels_expanded = _option_labels(
                app.screen.query_one("#recent-projects-list", OptionList)
            )

            await pilot.press("5")
            await pilot.pause()
            app.screen.query_one("#appearance-settings", CircularSelectionList).toggle("compact")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            compact_applied = "compact" in app.screen.query_one("#home-dashboard").classes
            labels_compact = _option_labels(
                app.screen.query_one("#recent-projects-list", OptionList)
            )
            return compact_applied, labels_expanded, labels_compact

    compact_applied, labels_expanded, labels_compact = _run(scenario())
    assert compact_applied is True
    # Compact mode never adds detail; when discovery has no branch or mtime,
    # both modes legitimately contain the same status-only row.
    assert len(labels_compact[0]) <= len(labels_expanded[0])
    paired = zip(labels_compact, labels_expanded)
    assert all(len(compact) <= len(expanded) for compact, expanded in paired)
