"""Shared project resolution and launch preparation for ``th plan``/``th up``.

Resolution is read-only. ``prepare_project_launch`` is deliberately named and
documented as a mutating operation because it persists a generated default
workspace when one is required. It never invokes tmux.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dashboard.models import LaunchAction, LaunchRequest
from dashboard.services.project_selection import resolve_project_selector
from dashboard.services.projects import (
    ProjectStatus,
    build_launch_request,
    gather_single_project_status,
)
from dashboard.services.projects_config_store import load_projects_config_result
from dashboard.services.workspace_plan import (
    ACTION_ATTACH,
    ACTION_CREATE_DEFAULT,
    WorkspacePlan,
    build_workspace_plan,
)
from dashboard.services.workspace_store import save_workspace


@dataclass(frozen=True, slots=True)
class ResolvedProjectStatus:
    """Read-only selector resolution, status, and nonfatal warnings."""

    status: ProjectStatus | None
    error: str | None = None
    warnings: tuple[str, ...] = ()


def resolve_project_status(selector: str) -> ResolvedProjectStatus:
    """Resolve *selector* and gather status using one configuration snapshot."""
    config_result = load_projects_config_result()
    warnings = [config_result.warning] if config_result.warning else []
    selection = resolve_project_selector(selector, config=config_result.value)
    if selection.project is None:
        return ResolvedProjectStatus(None, selection.error, tuple(warnings))

    status = gather_single_project_status(selection.project, config=config_result.value)
    if status.workspace_metadata_warning:
        warnings.append(status.workspace_metadata_warning)
    return ResolvedProjectStatus(status, warnings=tuple(dict.fromkeys(warnings)))


class ProjectLaunchPreparationError(Exception):
    """A controlled reason a project cannot safely be prepared for launch."""


@dataclass(frozen=True, slots=True)
class PreparedProjectLaunch:
    plan: WorkspacePlan
    request: LaunchRequest
    persisted_default: bool


def prepare_project_launch(
    status: ProjectStatus, *, store_path: Path | None = None
) -> PreparedProjectLaunch:
    """Prepare a launch, persisting a generated default when necessary.

    This may mutate Terminal Home's workspace store, but never creates project
    directories or invokes tmux. Running sessions remain authoritative.
    """
    plan = build_workspace_plan(status)
    if plan.blocked:
        raise ProjectLaunchPreparationError(plan.note or "Project launch is blocked.")

    if status.session_running or status.saved_workspace is not None:
        return PreparedProjectLaunch(plan, build_launch_request(status), False)

    assert plan.action == ACTION_CREATE_DEFAULT
    assert plan.workspace is not None
    # Recheck immediately before the first mutation: the directory may have
    # disappeared after status gathering.
    if not status.canonical_path.is_dir():
        raise ProjectLaunchPreparationError(
            f"Cannot launch because the project directory no longer exists: "
            f"{status.canonical_path}"
        )
    save_workspace(plan.workspace, store_path=store_path)
    request = LaunchRequest(
        workspace=plan.workspace, init_git=False, action=LaunchAction.CREATE
    )
    return PreparedProjectLaunch(plan, request, True)


def launch_status_line(prepared: PreparedProjectLaunch) -> str:
    """The deterministic one-line handoff message printed by ``th up``."""
    plan = prepared.plan
    if plan.action == ACTION_ATTACH:
        return f"Attaching to tmux session '{plan.session_name}'..."
    if prepared.persisted_default:
        return f"Creating default workspace '{plan.session_name}'..."
    return f"Creating saved workspace '{plan.session_name}'..."
