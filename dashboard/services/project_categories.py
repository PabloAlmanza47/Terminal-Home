"""Pure grouping rules for the Continue Project picker."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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
        fields: tuple[str, ...] = (entry.name, entry.selector, entry.location.remote_path, "remote")
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
        worktree = entry.worktree
        if worktree is not None:
            dirty = {True: "clean", False: "dirty", None: ""}[worktree.clean]
            fields += ("worktree", worktree.branch or "detached", dirty)
    haystack = " ".join(fields).casefold()
    return all(word in haystack for word in words)


def group_project_entries(entries: list[ProjectEntry]) -> tuple[ProjectCategory, ...]:
    """Group entries in stable UI order, omitting empty categories."""
    order = ("Configured Projects", "Not Configured", "Remote Projects")
    grouped: dict[str, list[ProjectEntry]] = {title: [] for title in order}
    for entry in entries:
        grouped[project_category(entry)].append(entry)
    return tuple(ProjectCategory(title, tuple(grouped[title])) for title in order if grouped[title])


def _linked_parent(entry: ProjectEntry) -> Path | None:
    """The group parent's path for a linked (non-main) worktree, else None."""
    worktree = entry.worktree if isinstance(entry, ProjectStatus) else None
    if worktree is None or worktree.path == worktree.parent_path:
        return None
    return worktree.parent_path


def split_worktree_children(
    entries: list[ProjectEntry],
) -> tuple[list[ProjectEntry], dict[Path, list[ProjectStatus]]]:
    """Separate linked worktrees whose parent checkout is among *entries*.

    Returns the top-level entries (in their original order) and, per parent
    canonical path, its linked worktrees (also in order).  A linked worktree
    whose parent isn't present stays top-level, so nothing is ever hidden.
    """
    present = {entry.canonical_path for entry in entries if isinstance(entry, ProjectStatus)}
    top: list[ProjectEntry] = []
    children: dict[Path, list[ProjectStatus]] = {}
    for entry in entries:
        parent = _linked_parent(entry)
        if parent is not None and parent in present and isinstance(entry, ProjectStatus):
            children.setdefault(parent, []).append(entry)
        else:
            top.append(entry)
    return top, children


def with_worktree_parents(
    visible: list[ProjectEntry], all_entries: list[ProjectEntry]
) -> list[ProjectEntry]:
    """*visible* plus the parent checkout of any visible linked worktree.

    Keeps a filtered worktree shown beneath its repository instead of
    floating on its own.  Order follows *all_entries*.
    """
    wanted = {parent for entry in visible if (parent := _linked_parent(entry)) is not None}
    if not wanted:
        return visible
    keep = {id(entry) for entry in visible}
    return [
        entry
        for entry in all_entries
        if id(entry) in keep
        or (isinstance(entry, ProjectStatus) and entry.canonical_path in wanted)
    ]
