"""A read-only, structural description of what `th up <project>` would do
for one project -- computed from the same ProjectStatus the Continue
Project screen already uses, and never itself touching tmux or the
filesystem.

Kept separate from the CLI module so `th plan`'s renderer and (once it
exists) `th up`'s pre-action confirmation prompt share one implementation
rather than each re-deriving what "attach", "recreate", or "create
default" structurally means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dashboard.models import (
    LocalProjectLocation,
    PaneKind,
    PaneSpec,
    ProjectLocation,
    WorkspaceSpec,
)
from dashboard.services.projects import ProjectStatus
from dashboard.services.workspace_defaults import build_default_workspace

ACTION_ATTACH = "attach to existing session"
ACTION_CREATE_SAVED = "create saved workspace"
ACTION_RECREATE = "recreate saved workspace"
ACTION_CREATE_DEFAULT = "create default workspace"
ACTION_BLOCKED = "blocked"

SOURCE_RUNNING = "running tmux session"
SOURCE_SAVED = "saved workspace"
SOURCE_DEFAULT = "generated default"
SOURCE_INVALID = "invalid saved workspace metadata"
SOURCE_MISSING_DIRECTORY = "missing project directory"

_ATTACH_NOTE = "The running session is authoritative; no windows or panes would be recreated."


@dataclass(frozen=True, slots=True)
class WorkspacePlan:
    """What `th up` would structurally do for one project, without doing it.

    `workspace` is None only for the "attach to a session that's already
    running" case -- there is nothing to build, so nothing to render.
    """

    project_name: str
    project_path: Path | str
    project_location: ProjectLocation
    session_name: str
    action: str
    source: str
    workspace: WorkspaceSpec | None
    note: str | None = None

    @property
    def blocked(self) -> bool:
        return self.action == ACTION_BLOCKED


def build_workspace_plan(status: ProjectStatus) -> WorkspacePlan:
    """The plan for *status*'s project, following the same priority a real
    launch would: a running session wins, then a saved workspace, then a
    freshly generated (never persisted) default -- exactly the ordering
    dashboard.services.projects.build_launch_request already uses.
    """
    if status.session_running:
        return WorkspacePlan(
            project_name=status.project.name,
            project_path=status.canonical_path,
            project_location=LocalProjectLocation(status.canonical_path),
            session_name=status.expected_session_name,
            action=ACTION_ATTACH,
            source=SOURCE_RUNNING,
            workspace=None,
            note=_ATTACH_NOTE,
        )

    if status.workspace_metadata_error:
        return WorkspacePlan(
            project_name=status.project.name,
            project_path=status.canonical_path,
            project_location=LocalProjectLocation(status.canonical_path),
            session_name=status.expected_session_name,
            action=ACTION_BLOCKED,
            source=SOURCE_INVALID,
            workspace=None,
            note=(
                "Cannot launch because the saved workspace metadata could not be loaded.\n"
                "Use the dashboard to inspect, forget, or reconfigure the saved workspace."
            ),
        )

    if not status.project_dir_exists:
        return WorkspacePlan(
            project_name=status.project.name,
            project_path=status.canonical_path,
            project_location=LocalProjectLocation(status.canonical_path),
            session_name=status.expected_session_name,
            action=ACTION_BLOCKED,
            source=SOURCE_MISSING_DIRECTORY,
            workspace=None,
            note="Cannot launch because the project directory no longer exists.",
        )

    if status.saved_workspace is not None:
        return WorkspacePlan(
            project_name=status.project.name,
            project_path=status.canonical_path,
            project_location=LocalProjectLocation(status.canonical_path),
            session_name=status.expected_session_name,
            action=ACTION_CREATE_SAVED,
            source=SOURCE_SAVED,
            workspace=status.saved_workspace,
        )

    # Generated for display only -- never saved, never used to create
    # anything; build_default_workspace itself makes no filesystem or tmux
    # calls.
    default_workspace = build_default_workspace(
        status.project.name,
        LocalProjectLocation(status.canonical_path),
        status.expected_session_name,
    )
    return WorkspacePlan(
        project_name=status.project.name,
        project_path=status.canonical_path,
        project_location=LocalProjectLocation(status.canonical_path),
        session_name=status.expected_session_name,
        action=ACTION_CREATE_DEFAULT,
        source=SOURCE_DEFAULT,
        workspace=default_workspace,
    )


def build_workspace_plan_for_location(
    *,
    project_name: str,
    project_location: ProjectLocation,
    session_name: str,
    saved_workspace: WorkspaceSpec | None,
    session_running: bool,
) -> WorkspacePlan:
    """Build a read-only plan for a local or registered remote location.

    This is intentionally limited to saved-workspace precedence and the
    expected-session query result. It performs no filesystem or remote
    project inspection.
    """
    project_path: Path | str = (
        project_location.path
        if isinstance(project_location, LocalProjectLocation)
        else project_location.remote_path
    )
    if session_running:
        return WorkspacePlan(
            project_name=project_name,
            project_path=project_path,
            project_location=project_location,
            session_name=session_name,
            action=ACTION_ATTACH,
            source=SOURCE_RUNNING,
            workspace=None,
            note=_ATTACH_NOTE,
        )
    if saved_workspace is not None:
        return WorkspacePlan(
            project_name=project_name,
            project_path=project_path,
            project_location=project_location,
            session_name=session_name,
            action=ACTION_RECREATE,
            source=SOURCE_SAVED,
            workspace=saved_workspace,
        )
    workspace = build_default_workspace(project_name, project_location, session_name)
    return WorkspacePlan(
        project_name=project_name,
        project_path=project_path,
        project_location=project_location,
        session_name=session_name,
        action=ACTION_CREATE_DEFAULT,
        source=SOURCE_DEFAULT,
        workspace=workspace,
    )


def _format_pane(pane: PaneSpec, index: int) -> str:
    label = pane.display_name
    if pane.kind is PaneKind.CUSTOM_COMMAND and pane.custom_command:
        label = f"{label} — {pane.custom_command}"
    return f"  Pane {index}: {label}"


def _format_workspace(workspace: WorkspaceSpec) -> list[str]:
    lines: list[str] = []
    for window_index, window in enumerate(workspace.windows, start=1):
        if window_index > 1:
            lines.append("")
        lines.append(f"Window {window_index}: {window.window_name}")
        for pane_index, pane in enumerate(window.panes, start=1):
            lines.append(_format_pane(pane, pane_index))
    return lines


def format_plan(plan: WorkspacePlan) -> str:
    """Render *plan* as the plain-text block `th plan` prints -- a
    structural summary, never an exact future tmux argv list.
    """
    lines: list[str] = [
        f"Project: {plan.project_name}",
        f"Path: {plan.project_path}",
        f"Session: {plan.session_name}",
        f"Action: {plan.action}",
        f"Source: {plan.source}",
        "",
    ]
    if not isinstance(plan.project_location, LocalProjectLocation):
        lines.insert(
            2,
            f"Location: ssh:{plan.project_location.host_id}:{plan.project_location.remote_path}",
        )
    if plan.workspace is not None:
        lines.extend(_format_workspace(plan.workspace))
    elif plan.note is not None:
        lines.append(plan.note)
    return "\n".join(lines)
