"""Data and actions for the small Terminal Home workspace switcher."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rich.cells import cell_len

from dashboard.services.agent_deck import AgentDeckSnapshot
from dashboard.services.projects import ProjectScanResult, ProjectStatus, project_option_id


@dataclass(frozen=True, slots=True)
class QuickSwitchEntry:
    status: ProjectStatus
    label: str
    option_id: str
    is_running: bool
    is_current: bool


def _truncate_label(value: str, width: int) -> str:
    """Fit a project label to terminal cells, preserving both ends."""
    if width <= 0:
        return ""
    if cell_len(value) <= width:
        return value
    if width == 1:
        return "…"
    left_budget = max(1, (width - 1) // 3)
    left: list[str] = []
    used = 0
    for character in value:
        cells = cell_len(character)
        if used + cells > left_budget:
            break
        left.append(character)
        used += cells
    right: list[str] = []
    used_right = 0
    for character in reversed(value):
        cells = cell_len(character)
        if used_right + cells > width - used - 1:
            break
        right.append(character)
        used_right += cells
    return "".join(left) + "…" + "".join(reversed(right))


def format_quick_switch_row(entry: QuickSwitchEntry, width: int) -> str:
    """Format one single-line, cell-width-aware Quick Switch row."""
    width = max(1, width)
    status = "● current" if entry.is_current else (
        "● running" if entry.is_running else "○ stopped"
    )
    name_width = max(1, width - cell_len(status) - 2)
    name = _truncate_label(entry.label, name_width)
    return f"{name}{' ' * max(0, name_width - cell_len(name))}  {status}"


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
    # The workspace from which the popup was opened remains selectable even
    # when Agent Deck reports metadata for that same session. Agent-owned
    # sessions are still omitted from the switcher when they are not the
    # current Terminal Home workspace.
    statuses = [
        status
        for status in scan.statuses
        if status.expected_session_name not in agent_sessions
        or status.expected_session_name == current_session
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
