"""Read-only projection of Agent Deck sessions for Terminal Home."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dashboard.services.agent_deck import (
    AgentDeckSession,
    AgentDeckSnapshot,
    AgentStatus,
)
from dashboard.services.agent_deck import snapshot as agent_deck_snapshot
from dashboard.services.projects import ProjectStatus


class AgentHubStatus(str, Enum):
    """The smallest status vocabulary Terminal Home can derive reliably."""

    WORKING = "working"
    WAITING = "waiting"
    COMPLETED = "completed"
    UNKNOWN = "unknown"


class AgentAssociation(str, Enum):
    """How confidently an Agent Deck path maps to Terminal Home."""

    KNOWN_PROJECT = "known_project"
    UNREGISTERED_PATH = "unregistered_path"
    MISSING_PATH = "missing_path"


@dataclass(frozen=True, slots=True)
class AgentHubEntry:
    """One Agent Deck session projected into Terminal Home's vocabulary."""

    session_id: str
    title: str
    path: Path
    tool: str
    agent_status: AgentStatus
    status: AgentHubStatus
    project_name: str | None
    project_path: Path | None
    workspace_session_name: str | None
    association: AgentAssociation


@dataclass(frozen=True, slots=True)
class AgentHubSnapshot:
    """One read-only Agent Hub refresh, including provider health."""

    available: bool
    entries: tuple[AgentHubEntry, ...] = ()
    warning: str | None = None


_STATUS_ORDER = {
    AgentHubStatus.WAITING: 0,
    AgentHubStatus.COMPLETED: 1,
    AgentHubStatus.WORKING: 2,
    AgentHubStatus.UNKNOWN: 3,
}


def normalize_status(status: AgentStatus) -> AgentHubStatus:
    """Map only statuses already normalized by the Agent Deck adapter."""
    if status is AgentStatus.RUNNING:
        return AgentHubStatus.WORKING
    if status is AgentStatus.WAITING:
        return AgentHubStatus.WAITING
    if status is AgentStatus.IDLE:
        return AgentHubStatus.COMPLETED
    return AgentHubStatus.UNKNOWN


def _project_by_path(statuses: Iterable[ProjectStatus]) -> dict[Path, ProjectStatus]:
    return {status.canonical_path.resolve(): status for status in statuses}


def _entry(
    session: AgentDeckSession,
    projects: dict[Path, ProjectStatus],
) -> AgentHubEntry:
    project = projects.get(session.path)
    if project is not None:
        return AgentHubEntry(
            session_id=session.id,
            title=session.title,
            path=session.path,
            tool=session.tool,
            agent_status=session.status,
            status=normalize_status(session.status),
            project_name=project.project.name,
            project_path=project.canonical_path,
            workspace_session_name=project.expected_session_name,
            association=AgentAssociation.KNOWN_PROJECT,
        )

    association = (
        AgentAssociation.MISSING_PATH
        if not session.path.is_dir()
        else AgentAssociation.UNREGISTERED_PATH
    )
    return AgentHubEntry(
        session_id=session.id,
        title=session.title,
        path=session.path,
        tool=session.tool,
        agent_status=session.status,
        status=normalize_status(session.status),
        project_name=None,
        project_path=None,
        workspace_session_name=None,
        association=association,
    )


def _entry_sort_key(entry: AgentHubEntry) -> tuple[int, str, str, str]:
    return (
        _STATUS_ORDER[entry.status],
        (entry.project_name or str(entry.path)).casefold(),
        entry.title.casefold(),
        entry.session_id,
    )


def build_agent_hub_snapshot(
    agent_snapshot: AgentDeckSnapshot,
    project_statuses: Iterable[ProjectStatus],
) -> AgentHubSnapshot:
    """Project an existing Agent Deck snapshot onto known projects.

    Matching is exact on normalized canonical paths. Session IDs remain the
    only identity used by callers for later attachment; titles, project names,
    and tmux session names are display context only.
    """
    projects = _project_by_path(project_statuses)
    entries = tuple(
        sorted(
            (_entry(session, projects) for session in agent_snapshot.sessions),
            key=_entry_sort_key,
        )
    )
    return AgentHubSnapshot(
        available=agent_snapshot.available,
        entries=entries,
        warning=agent_snapshot.warning,
    )


def load_agent_hub_snapshot(
    project_statuses: Iterable[ProjectStatus],
    *,
    agent_snapshot: AgentDeckSnapshot | None = None,
) -> AgentHubSnapshot:
    """Read Agent Deck through the existing optional integration and project.

    This function performs no writes and inherits the existing adapter's
    timeout, defensive parsing, and unavailable-provider behavior.
    """
    return build_agent_hub_snapshot(
        agent_snapshot if agent_snapshot is not None else agent_deck_snapshot(),
        project_statuses,
    )
