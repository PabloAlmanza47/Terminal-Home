"""Shared, theme-neutral status presentation for project activity."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dashboard.services.agent_deck import AgentDeckSession, AgentStatus
from dashboard.services.projects import ProjectStatus


@dataclass(frozen=True, slots=True)
class ActivityStatus:
    glyph: str
    label: str


def workspace_status(status: ProjectStatus) -> ActivityStatus:
    if status.session_running:
        return ActivityStatus("●", "Running")
    if status.saved_workspace is not None:
        return ActivityStatus("○", "Stopped")
    return ActivityStatus("—", "Not Configured")


def server_status(status: ProjectStatus) -> ActivityStatus:
    return {
        "running": ActivityStatus("●", "Running"),
        "stopped": ActivityStatus("○", "Stopped"),
        "not_configured": ActivityStatus("—", "Not Configured"),
        "unknown": ActivityStatus("?", "Unknown"),
    }.get(status.server_status, ActivityStatus("?", "Unknown"))


_AGENT_STATUS_PRIORITY = {
    AgentStatus.ERROR: 0,
    AgentStatus.WAITING: 1,
    AgentStatus.RUNNING: 2,
    AgentStatus.IDLE: 3,
    AgentStatus.STOPPED: 4,
    AgentStatus.UNKNOWN: 5,
}

_AGENT_PRESENTATION = {
    AgentStatus.ERROR: ActivityStatus("!", "Error"),
    AgentStatus.WAITING: ActivityStatus("◐", "Waiting"),
    AgentStatus.RUNNING: ActivityStatus("●", "Working"),
    AgentStatus.IDLE: ActivityStatus("○", "Idle"),
    AgentStatus.STOPPED: ActivityStatus("○", "Stopped"),
    AgentStatus.UNKNOWN: ActivityStatus("?", "Unknown"),
}


def agent_display_name(tool: str) -> str:
    """Return a readable name for any current or future Agent Deck tool."""
    words = tool.replace("_", "-").split("-")
    return " ".join(word.capitalize() for word in words if word) or "Unknown"


def effective_agent_session(
    sessions: Iterable[AgentDeckSession],
) -> tuple[AgentDeckSession | None, int]:
    """Choose one deterministic Agent Deck session and return total count."""
    sessions = tuple(sessions)
    if not sessions:
        return None, 0
    selected = min(
        sessions,
        key=lambda session: (
            _AGENT_STATUS_PRIORITY.get(session.status, 5),
            session.tool.casefold(),
            session.title.casefold(),
            session.id,
        ),
    )
    return selected, len(sessions)


def agent_status(status: ProjectStatus) -> tuple[ActivityStatus, int, AgentDeckSession | None]:
    selected, count = effective_agent_session(status.agent_sessions)
    if selected is None:
        return ActivityStatus("—", "No Agent"), 0, None
    return _AGENT_PRESENTATION[selected.status], count, selected


# Kept as a compatibility alias for callers outside the current UI. New code
# should use the generic helper above.
def codex_status(status: ProjectStatus) -> tuple[ActivityStatus, int]:
    value, count, _ = agent_status(status)
    return value, count
