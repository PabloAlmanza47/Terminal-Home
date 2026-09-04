"""Focused tests for the data and selection semantics of ``th switch``."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dashboard.services.agent_deck import AgentDeckSession, AgentDeckSnapshot, AgentStatus
from dashboard.services.projects import Project, ProjectScanResult, ProjectStatus
from dashboard.services.quick_switch import (
    build_quick_switch_entries,
    filter_quick_switch_entries,
    format_quick_switch_row,
)


def _status(path: Path, *, running: bool, session: str | None = None) -> ProjectStatus:
    project = Project(path.name, path)
    return ProjectStatus(
        project=project, canonical_path=path.resolve(), project_dir_exists=True,
        is_git_repo=False, git_branch=None, saved_workspace=None,
        workspace_metadata_error=None, expected_session_name=session or path.name,
        tmux_available=True, session_running=running, last_modified=datetime(2026, 1, 1),
    )


def test_active_rows_are_first_and_current_is_marked(tmp_path: Path) -> None:
    stopped = _status(tmp_path / "old", running=False)
    active = _status(tmp_path / "active", running=True, session="active")
    entries = build_quick_switch_entries(ProjectScanResult((stopped, active), False, ()), "active")
    assert [entry.label for entry in entries] == ["active", "old"]
    assert entries[0].is_current is True
    assert entries[1].is_current is False


def test_entries_naturally_form_active_then_recent_sections(tmp_path: Path) -> None:
    active = _status(tmp_path / "active", running=True)
    recent = _status(tmp_path / "recent", running=False)
    entries = build_quick_switch_entries(ProjectScanResult((recent, active), False, ()))
    assert [entry.is_running for entry in entries] == [True, False]


def test_agent_deck_tmux_sessions_are_excluded(tmp_path: Path) -> None:
    agent = AgentDeckSession(
        "a1", "Agent", (tmp_path / "agent").resolve(), "claude", AgentStatus.RUNNING, "agent"
    )
    scan = ProjectScanResult(
        (_status(tmp_path / "agent", running=True, session="agent"),), False, (),
        agent_snapshot=AgentDeckSnapshot(True, (agent,)),
    )
    assert build_quick_switch_entries(scan) == []


def test_filter_matches_name_path_and_session(tmp_path: Path) -> None:
    entry = build_quick_switch_entries(
        ProjectScanResult(
            (_status(tmp_path / "School Project", running=False, session="school"),), False, ()
        )
    )[0]
    assert filter_quick_switch_entries([entry], "school") == [entry]
    assert filter_quick_switch_entries([entry], "school project") == [entry]
    assert filter_quick_switch_entries([entry], "does-not-exist") == []


def test_duplicate_names_use_canonical_path_ids(tmp_path: Path) -> None:
    first = _status(tmp_path / "one" / "demo", running=False)
    second = _status(tmp_path / "two" / "demo", running=False)
    entries = build_quick_switch_entries(ProjectScanResult((first, second), False, ()))
    assert len({entry.option_id for entry in entries}) == 2
    assert all("demo —" in entry.label for entry in entries)


def test_row_formatter_keeps_status_on_one_line_and_truncates_name(tmp_path: Path) -> None:
    entry = build_quick_switch_entries(
        ProjectScanResult(
            (_status(tmp_path / "a-very-long-project-name", running=True),), False, ()
        )
    )[0]
    row = format_quick_switch_row(entry, 30)
    assert "running" in row
    assert "…" in row
    assert "\n" not in row
