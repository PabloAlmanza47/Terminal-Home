"""Discovers project directories under ~/projects for the Open Project
screen, and gathers each one's status (git, saved workspace, running tmux
session) for the project list and detail screens.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from dashboard.models import WorkspaceSpec
from dashboard.services import tmux
from dashboard.services.git_info import gather_git_info
from dashboard.services.workspace_store import load_workspace_result

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

    Names in *exclude*, and any hidden (dot-prefixed) directory, are
    skipped. A missing, unreadable, or otherwise inaccessible root yields
    an empty list rather than raising, since this is used directly to
    populate UI that must never crash the app.
    """
    root = root if root is not None else DEFAULT_PROJECTS_ROOT
    exclude = exclude if exclude is not None else DEFAULT_EXCLUDE

    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        return []

    projects: list[Project] = []
    for entry in entries:
        if entry.name in exclude or entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                projects.append(Project(name=entry.name, path=entry))
        except OSError:
            continue
    return projects


@dataclass(frozen=True, slots=True)
class ProjectStatus:
    """Everything the Open Project list and detail screens need to know
    about one project, gathered in a single best-effort pass. Nothing here
    ever raises -- every field degrades to a safe "unknown" value instead.
    """

    project: Project
    canonical_path: Path
    project_dir_exists: bool
    is_git_repo: bool
    git_branch: str | None
    saved_workspace: WorkspaceSpec | None
    workspace_metadata_error: str | None
    expected_session_name: str
    tmux_available: bool
    session_running: bool
    last_modified: datetime | None


def scan_all_projects(
    root: Path | None = None,
    store_path: Path | None = None,
) -> list[ProjectStatus]:
    """Discover every project under *root* and gather each one's status in
    a single pass, sharing one `tmux list-sessions` call across all of
    them instead of one `tmux has-session` per project.

    This is the single scanning implementation shared by the Open Project
    list screen and the home screen's recent-projects/active-sessions
    panels -- neither should re-implement project discovery on its own.
    """
    projects = discover_projects(root)
    running_sessions = {session.name for session in tmux.list_tmux_sessions()}
    return [
        gather_project_status(project, store_path=store_path, running_sessions=running_sessions)
        for project in projects
    ]


def gather_project_status(
    project: Project,
    *,
    store_path: Path | None = None,
    running_sessions: set[str] | None = None,
) -> ProjectStatus:
    """Gather *project*'s full status.

    *running_sessions*, when given, is the set of currently running tmux
    session names -- passing one pre-fetched set lets a caller checking
    many projects (the project list) make a single `tmux list-sessions`
    call instead of one `tmux has-session` per project. Left as None, this
    checks the one session it cares about directly (cheap for a single
    project, e.g. the detail screen).
    """
    canonical_path = project.path.resolve()
    project_dir_exists = project.path.is_dir()

    git_info = gather_git_info(project.path) if project_dir_exists else None

    load_result = load_workspace_result(project.path, store_path=store_path)
    saved_workspace = load_result.workspace
    if saved_workspace is not None and saved_workspace.project_path.resolve() != canonical_path:
        # The store is keyed by canonical path already, so this only
        # happens if the persisted record's own project_path field is
        # stale (e.g. hand-edited) -- correct it rather than let a launch
        # later `cd` into the wrong directory.
        saved_workspace = replace(saved_workspace, project_path=canonical_path)

    expected_session_name = (
        saved_workspace.session_name
        if saved_workspace is not None
        else tmux.sanitize_session_name(project.name)
    )

    tmux_available = tmux.is_tmux_installed()
    if running_sessions is not None:
        session_running = expected_session_name in running_sessions
    elif tmux_available:
        session_running = tmux.session_exists(expected_session_name)
    else:
        session_running = False

    try:
        last_modified = datetime.fromtimestamp(project.path.stat().st_mtime)
    except OSError:
        last_modified = None

    return ProjectStatus(
        project=project,
        canonical_path=canonical_path,
        project_dir_exists=project_dir_exists,
        is_git_repo=git_info.is_repo if git_info is not None else False,
        git_branch=git_info.branch if git_info is not None else None,
        saved_workspace=saved_workspace,
        workspace_metadata_error=load_result.error,
        expected_session_name=expected_session_name,
        tmux_available=tmux_available,
        session_running=session_running,
        last_modified=last_modified,
    )


class ProjectAction(str, Enum):
    """One button the Project Detail screen might offer, depending on
    ProjectStatus. RESUME and RECREATE both resolve to the same
    LaunchAction.ATTACH request -- the orchestration layer re-checks
    what's actually running at launch time regardless of which label the
    user saw, so the distinction is purely about what the button says.
    """

    RESUME = "resume"
    RECREATE = "recreate"
    OPEN_DEFAULT = "open_default"
    CONFIGURE = "configure"
    EDIT = "edit"
    RESET = "reset"
    FORGET = "forget"


def status_badge(status: ProjectStatus) -> str:
    """A single, human-readable status word for *status*, using the same
    priority order as primary_actions: a running session always wins,
    then corrupt metadata, then a saved workspace, then "nothing yet".
    """
    if status.session_running:
        return "Running"
    if status.workspace_metadata_error:
        return "Metadata Warning"
    if status.saved_workspace is not None:
        return "Saved Workspace"
    return "Not Configured"


def primary_actions(status: ProjectStatus) -> list[ProjectAction]:
    """The main call-to-action(s) for a project, in priority order.

    A running session always wins (attaching needs no filesystem access at
    all, so it's offered even if the directory has since vanished or the
    saved metadata is corrupt). After that: a vanished directory rules out
    every action that would need to create or read it; corrupt metadata
    offers a way out without ever guessing at its content; a saved
    workspace offers to recreate it; otherwise, the project has nothing
    saved yet.
    """
    if status.session_running:
        return [ProjectAction.RESUME]
    if not status.project_dir_exists:
        return []
    if status.workspace_metadata_error:
        return [ProjectAction.FORGET, ProjectAction.CONFIGURE]
    if status.saved_workspace is not None:
        return [ProjectAction.RECREATE]
    return [ProjectAction.OPEN_DEFAULT, ProjectAction.CONFIGURE]


def secondary_actions(status: ProjectStatus) -> list[ProjectAction]:
    """Additional metadata-only actions available alongside the primary
    one(s) above.
    """
    if status.saved_workspace is not None:
        return [ProjectAction.EDIT, ProjectAction.RESET, ProjectAction.FORGET]
    if status.workspace_metadata_error and status.session_running:
        return [ProjectAction.FORGET]
    return []
