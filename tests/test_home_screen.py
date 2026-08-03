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
from pathlib import Path

import pytest
from textual.widgets import OptionList

import dashboard.screens.home as home_module
from dashboard.app import TerminalHomeApp
from dashboard.models import LaunchAction, LaunchRequest
from dashboard.services import projects as projects_module
from dashboard.services import tmux as tmux_module
from dashboard.services.projects import Project, build_launch_request, gather_project_status
from dashboard.services.tmux import TmuxSession
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import save_workspace

_NARROW = (80, 24)
_MEDIUM = (100, 30)
_WIDE = (140, 40)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    monkeypatch.setattr(projects_module, "DEFAULT_PROJECTS_ROOT", projects_root)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    return projects_root


def _run(coro):
    return asyncio.run(coro)


async def _wait_for_scan(pilot) -> None:
    await pilot.pause()
    await pilot.app.workers.wait_for_complete()
    await pilot.pause()


def _option_labels(option_list: OptionList) -> list[str]:
    return [str(option_list.get_option_at_index(i).prompt) for i in range(option_list.option_count)]


def _option_ids(option_list: OptionList) -> list[str | None]:
    return [option_list.get_option_at_index(i).id for i in range(option_list.option_count)]


# --- Default focus -----------------------------------------------------------


def test_default_focus_is_continue_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[int | None, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            menu = app.screen.query_one("#main-menu", OptionList)
            return menu.highlighted, app.focused is menu

    highlighted, is_focused = _run(scenario())
    assert highlighted == 0
    assert is_focused is True


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
            recent.highlighted = recent.option_count - 1  # "View All Projects" is last
            recent.focus()
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
    workspace = build_default_workspace("demo", project_path.resolve(), "demo")
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
    assert "beta" in ids


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

    async def scenario() -> tuple[int, int, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            calls_after_scan = scan_calls["n"]
            before = str(app.screen.query_one("#home-subtitle").render())

            await pilot.pause(2.2)  # let two clock ticks fire with no user action

            after = str(app.screen.query_one("#home-subtitle").render())
            return calls_after_scan, scan_calls["n"], before, after

    calls_before, calls_after, before_text, after_text = _run(scenario())
    assert calls_before == 1
    assert calls_after == 1  # unchanged: clock ticks never trigger a rescan
    assert before_text != after_text  # but the clock text itself did update


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
            for panel_id in ("panel-actions", "panel-recent", "panel-sessions", "panel-status"):
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


def test_artwork_hidden_on_short_terminal_even_when_setting_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> bool:
        app = TerminalHomeApp()
        async with app.run_test(size=_NARROW) as pilot:  # 24 rows: below the art threshold
            await _wait_for_scan(pilot)
            return bool(app.screen.query_one("#home-logo").display)

    assert _run(scenario()) is False


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


def test_tab_cycles_focus_through_panels_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    (projects_root / "alpha").mkdir()

    async def scenario() -> list[str | None]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            focused_ids: list[str | None] = []
            for _ in range(6):
                await pilot.press("tab")
                await pilot.pause()
                focused_ids.append(app.focused.id if app.focused else None)
            return focused_ids

    focused_ids = _run(scenario())
    assert all(fid is not None for fid in focused_ids)


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

    async def scenario() -> tuple[bool, bool]:
        app = TerminalHomeApp()
        async with app.run_test(size=_WIDE) as pilot:  # roomy enough for art to normally show
            await _wait_for_scan(pilot)
            logo_before = bool(app.screen.query_one("#home-logo").display)

            await pilot.press("5")
            await pilot.pause()
            assert type(app.screen).__name__ == "SettingsScreen"

            await pilot.click("#artwork-checkbox")
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
            subtitle_before = bool(app.screen.query_one("#home-subtitle").display)

            await pilot.press("5")
            await pilot.pause()
            await pilot.click("#clock-checkbox")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            subtitle_after = bool(app.screen.query_one("#home-subtitle").display)
            return subtitle_before, subtitle_after

    subtitle_before, subtitle_after = _run(scenario())
    assert subtitle_before is True
    assert subtitle_after is False


def test_enabling_compact_layout_reduces_recent_project_label_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects_root = _isolate(monkeypatch, tmp_path)
    project_path = projects_root / "demo"
    project_path.mkdir()
    save_workspace(build_default_workspace("demo", project_path.resolve(), "demo"))

    async def scenario() -> tuple[bool, list[str], list[str]]:
        app = TerminalHomeApp()
        async with app.run_test(size=_MEDIUM) as pilot:
            await _wait_for_scan(pilot)
            labels_expanded = _option_labels(
                app.screen.query_one("#recent-projects-list", OptionList)
            )

            await pilot.press("5")
            await pilot.pause()
            await pilot.click("#compact-checkbox")
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
    # The expanded label includes the saved-workspace status plus extra
    # decoration (branch/relative time); the compact one is just name+badge.
    assert labels_expanded != labels_compact
    paired = zip(labels_compact, labels_expanded)
    assert all(len(compact) <= len(expanded) for compact, expanded in paired)
