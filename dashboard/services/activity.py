"""Shared, theme-neutral status presentation for project activity."""

from __future__ import annotations

from dataclasses import dataclass

from dashboard.services.agent_deck import AgentStatus
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


def codex_status(status: ProjectStatus) -> tuple[ActivityStatus, int]:
    sessions = [session for session in status.agent_sessions if session.tool.casefold() == "codex"]
    if not sessions:
        return ActivityStatus("—", "No Agent"), 0
    priority = {
        AgentStatus.ERROR: 0,
        AgentStatus.WAITING: 1,
        AgentStatus.RUNNING: 2,
        AgentStatus.IDLE: 3,
        AgentStatus.STOPPED: 4,
        AgentStatus.UNKNOWN: 5,
    }
    selected = min(sessions, key=lambda session: priority[session.status]).status
    presentation = {
        AgentStatus.ERROR: ActivityStatus("!", "Error"),
        AgentStatus.WAITING: ActivityStatus("◐", "Waiting"),
        AgentStatus.RUNNING: ActivityStatus("●", "Working"),
        AgentStatus.IDLE: ActivityStatus("○", "Idle"),
        AgentStatus.STOPPED: ActivityStatus("○", "Stopped"),
        AgentStatus.UNKNOWN: ActivityStatus("?", "Unknown"),
    }
    return presentation[selected], len(sessions)
