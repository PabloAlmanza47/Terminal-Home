"""Data and actions for the small Terminal Home workspace switcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from dashboard.services.agent_deck import AgentDeckSnapshot
from dashboard.services.projects import ProjectScanResult, ProjectStatus, project_option_id


@dataclass(frozen=True, slots=True)
class QuickSwitchEntry:
    status: ProjectStatus
    label: str
    option_id: str
    is_running: bool
    is_current: bool


def _agent_owned_sessions(snapshot: AgentDeckSnapshot | None) -> set[str]:
    if snapshot is None:
        return set()
    return {session.tmux_session for session in snapshot.sessions if session.tmux_session}


def _display_names(statuses: list[ProjectStatus]) -> dict[str, str]:
    counts: dict[str, int] = {}
    for status in statuses:
        counts[status.project.name] = counts.get(status.project.name, 0) + 1
    return {
        project_option_id(status): (
            status.project.name
            if counts[status.project.name] == 1
            else f"{status.project.name} — {status.canonical_path}"
        )
        for status in statuses
    }


def build_quick_switch_entries(
    scan: ProjectScanResult, current_session: str | None = None
) -> list[QuickSwitchEntry]:
    """Build active-then-recent rows from the normal project scan."""
    agent_sessions = _agent_owned_sessions(scan.agent_snapshot)
    statuses = [
        status for status in scan.statuses if status.expected_session_name not in agent_sessions
    ]
    names = _display_names(statuses)
    statuses.sort(
        key=lambda status: (
            not status.session_running,
            -(status.last_modified or datetime.min).timestamp()
            if status.last_modified is not None
            else 0,
            status.project.name.casefold(),
            str(status.canonical_path).casefold(),
        )
    )
    return [
        QuickSwitchEntry(
            status=status,
            label=names[project_option_id(status)],
            option_id=project_option_id(status),
            is_running=status.session_running,
            is_current=status.session_running and status.expected_session_name == current_session,
        )
        for status in statuses
    ]


def filter_quick_switch_entries(
    entries: list[QuickSwitchEntry], query: str
) -> list[QuickSwitchEntry]:
    """Case-insensitively filter by project name, path, or session name."""
    needle = query.strip().casefold()
    if not needle:
        return entries
    return [
        entry
        for entry in entries
        if needle in entry.label.casefold()
        or needle in str(entry.status.canonical_path).casefold()
        or needle in entry.status.expected_session_name.casefold()
    ]
