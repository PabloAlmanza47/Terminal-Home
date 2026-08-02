"""Discovers project directories under ~/projects for the Open Project screen."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The default projects root and the one directory we never want to list:
# this dashboard's own repo.
DEFAULT_PROJECTS_ROOT = Path.home() / "projects"
DEFAULT_EXCLUDE = {"terminal-home"}


@dataclass(frozen=True, slots=True)
class Project:
    """An immediate child directory of the projects root."""

    name: str
    path: Path


def discover_projects(
    root: Path | None = None,
    exclude: set[str] | None = None,
) -> list[Project]:
    """Return the immediate subdirectories of *root*, alphabetically sorted.

    Names in *exclude* are skipped. A missing, unreadable, or otherwise
    inaccessible root yields an empty list rather than raising, since this
    is used directly to populate UI that must never crash the app.
    """
    root = root if root is not None else DEFAULT_PROJECTS_ROOT
    exclude = exclude if exclude is not None else DEFAULT_EXCLUDE

    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return []

    projects: list[Project] = []
    for entry in entries:
        if entry.name in exclude:
            continue
        try:
            if entry.is_dir():
                projects.append(Project(name=entry.name, path=entry))
        except PermissionError:
            continue
    return projects
