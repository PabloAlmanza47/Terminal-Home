"""Pure grouping rules for the Continue Project picker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

from dashboard.services.project_selection import RegisteredRemoteProject
from dashboard.services.projects import ProjectStatus

ProjectEntry: TypeAlias = ProjectStatus | RegisteredRemoteProject


@dataclass(frozen=True, slots=True)
class ProjectCategory:
    title: str
    entries: tuple[ProjectEntry, ...]


def project_category(entry: ProjectEntry) -> str:
    """Return the display group for one local or registered remote entry."""
    if isinstance(entry, RegisteredRemoteProject):
        return "Remote Projects"
    if entry.saved_workspace is not None or entry.workspace_metadata_error:
        return "Configured Projects"
    return "Not Configured"


def matches_project_query(entry: ProjectEntry, query: str) -> bool:
    """Match a project by name, location, session, or its visible state."""
    words = query.casefold().split()
    if not words:
        return True
    if isinstance(entry, RegisteredRemoteProject):
        fields = (entry.name, entry.selector, entry.location.remote_path, "remote")
    else:
        state = (
            "running"
            if entry.session_running
            else "saved configured"
            if entry.saved_workspace is not None
            else "default not configured"
        )
        fields = (
            entry.project.name,
            str(entry.canonical_path),
            entry.expected_session_name,
            state,
        )
    haystack = " ".join(fields).casefold()
    return all(word in haystack for word in words)


def group_project_entries(entries: list[ProjectEntry]) -> tuple[ProjectCategory, ...]:
    """Group entries in stable UI order, omitting empty categories."""
    order = ("Configured Projects", "Not Configured", "Remote Projects")
    grouped: dict[str, list[ProjectEntry]] = {title: [] for title in order}
    for entry in entries:
        grouped[project_category(entry)].append(entry)
    return tuple(ProjectCategory(title, tuple(grouped[title])) for title in order if grouped[title])
