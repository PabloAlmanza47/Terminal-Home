"""Shared project resolution and launch preparation for ``th plan``/``th up``.

Resolution is read-only. ``prepare_project_launch`` is deliberately named and
documented as a mutating operation because it persists a generated default
workspace when one is required. It never invokes tmux.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dashboard.models import LaunchAction, LaunchRequest
from dashboard.services.project_selection import RegisteredRemoteProject, resolve_project_selector
from dashboard.services.projects import (
    Project,
    ProjectStatus,
    build_launch_request,
    gather_single_project_status,
)
from dashboard.services.projects_config_store import load_projects_config_result
from dashboard.services.tmux import (
    TmuxCommandError,
    resolve_tmux_runner,
    sanitize_session_name,
    session_exists,
)
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_plan import (
    ACTION_ATTACH,
    ACTION_CREATE_DEFAULT,
    WorkspacePlan,
    build_workspace_plan,
    build_workspace_plan_for_location,
)
from dashboard.services.workspace_store import (
    load_workspace_result_for_location,
    save_workspace,
)


@dataclass(frozen=True, slots=True)
class ResolvedProjectStatus:
    """Read-only selector resolution, status, and nonfatal warnings."""

    status: ProjectStatus | None
    error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedProjectPlan:
    """The read-only plan resolution result used by ``th plan``."""

    plan: WorkspacePlan | None
    error: str | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResolvedProjectLaunch:
    """Selector resolution and launch preparation for the CLI ``up`` path."""

    prepared: PreparedProjectLaunch | None
    error: str | None = None
    warnings: tuple[str, ...] = ()


def resolve_project_status(selector: str) -> ResolvedProjectStatus:
    """Resolve *selector* and gather status using one configuration snapshot."""
    config_result = load_projects_config_result()
    warnings = [config_result.warning] if config_result.warning else []
    selection = resolve_project_selector(selector, config=config_result.value)
    if selection.project is None:
        return ResolvedProjectStatus(None, selection.error, tuple(warnings))
    if isinstance(selection.project, RegisteredRemoteProject):
        return ResolvedProjectStatus(
            None,
            "Remote project CLI launch integration is not available yet.",
            tuple(warnings),
        )
    assert isinstance(selection.project, Project)

    status = gather_single_project_status(selection.project, config=config_result.value)
    if status.workspace_metadata_warning:
        warnings.append(status.workspace_metadata_warning)
    return ResolvedProjectStatus(status, warnings=tuple(dict.fromkeys(warnings)))


def resolve_project_plan(selector: str) -> ResolvedProjectPlan:
    """Resolve and build a local or registered-remote plan without mutation."""
    config_result = load_projects_config_result()
    warnings = [config_result.warning] if config_result.warning else []
    selection = resolve_project_selector(selector, config=config_result.value)
    if selection.project is None:
        return ResolvedProjectPlan(None, selection.error, tuple(warnings))

    if isinstance(selection.project, Project):
        status_result = resolve_project_status(selector)
        if status_result.status is None:
            return ResolvedProjectPlan(None, status_result.error, status_result.warnings)
        return ResolvedProjectPlan(
            build_workspace_plan(status_result.status),
            warnings=tuple(dict.fromkeys(status_result.warnings)),
        )

    remote = selection.project
    location = remote.location
    saved_result = load_workspace_result_for_location(location)
    warnings.extend(filter(None, (saved_result.warning,)))
    if saved_result.error:
        return ResolvedProjectPlan(None, saved_result.error, tuple(warnings))

    workspace = saved_result.workspace
    if workspace is None:
        session_name = sanitize_session_name(remote.name)
        workspace = build_default_workspace(remote.name, location, session_name)
    else:
        session_name = workspace.session_name

    runner_result = resolve_tmux_runner(workspace)
    if runner_result.error is not None or runner_result.runner is None:
        return ResolvedProjectPlan(
            None,
            runner_result.error.message
            if runner_result.error is not None
            else "Unable to resolve the SSH tmux runner.",
            tuple(warnings),
        )
    try:
        running = session_exists(session_name, runner=runner_result.runner)
    except (FileNotFoundError, OSError, TmuxCommandError) as exc:
        return ResolvedProjectPlan(
            None, f"Unable to query remote tmux session: {exc}", tuple(warnings)
        )

    return ResolvedProjectPlan(
        build_workspace_plan_for_location(
            project_name=remote.name,
            project_location=location,
            session_name=session_name,
            saved_workspace=saved_result.workspace,
            session_running=running,
        ),
        warnings=tuple(dict.fromkeys(warnings)),
    )


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


def prepare_project_launch_for_selector(selector: str) -> ResolvedProjectLaunch:
    """Resolve a local or registered remote selector and prepare its launch.

    Local projects use the established status/preparation flow. Remote
    projects use their saved location-aware workspace or an in-memory default,
    persist only that new default, and always hand the existing launcher an
    attach-style request so it can recheck and create when necessary.
    """
    selection = resolve_project_selector(selector)
    if selection.project is None:
        return ResolvedProjectLaunch(None, selection.error)

    if isinstance(selection.project, Project):
        resolved = resolve_project_status(selector)
        if resolved.status is None:
            return ResolvedProjectLaunch(None, resolved.error, resolved.warnings)
        try:
            prepared = prepare_project_launch(resolved.status)
        except (OSError, ProjectLaunchPreparationError) as exc:
            return ResolvedProjectLaunch(None, str(exc), resolved.warnings)
        return ResolvedProjectLaunch(prepared, warnings=resolved.warnings)

    resolved_plan = resolve_project_plan(selector)
    if resolved_plan.plan is None:
        return ResolvedProjectLaunch(
            None, resolved_plan.error, resolved_plan.warnings
        )
    plan = resolved_plan.plan
    if plan.blocked:
        return ResolvedProjectLaunch(None, plan.note or "Project launch is blocked.")

    workspace = plan.workspace
    if workspace is None:
        workspace = build_default_workspace(
            plan.project_name, plan.project_location, plan.session_name
        )
    persisted_default = plan.action == ACTION_CREATE_DEFAULT
    if persisted_default:
        save_workspace(workspace)
    request = LaunchRequest(
        workspace=workspace,
        init_git=False,
        action=LaunchAction.CREATE if persisted_default else LaunchAction.ATTACH,
    )
    return ResolvedProjectLaunch(
        PreparedProjectLaunch(plan, request, persisted_default),
        warnings=resolved_plan.warnings,
    )


def launch_status_line(prepared: PreparedProjectLaunch) -> str:
    """The deterministic one-line handoff message printed by ``th up``."""
    plan = prepared.plan
    if plan.action == ACTION_ATTACH:
        return f"Attaching to tmux session '{plan.session_name}'..."
    if prepared.persisted_default:
        return f"Creating default workspace '{plan.session_name}'..."
    return f"Creating saved workspace '{plan.session_name}'..."
