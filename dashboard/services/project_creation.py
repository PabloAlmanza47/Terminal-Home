"""Validation and filesystem operations for creating a new project directory
directly under ~/projects. Kept free of Textual imports so it can be unit
tested with plain tmp_path fixtures.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dashboard.models import (
    LaunchAction,
    LaunchRequest,
    LocalProjectLocation,
    WindowSpec,
    WorkspaceSpec,
    WorkspaceTemplate,
    WorkspaceValidationError,
    workspace_from_template,
)
from dashboard.services.template_store import (
    TemplateStoreError,
    find_template_by_name,
    load_templates_result,
)
from dashboard.services.tmux import generate_session_name, sanitize_session_name
from dashboard.services.workspace_store import (
    WorkspaceStoreError,
    WorkspaceStoreVersionError,
    ensure_workspace_store_writable,
    load_workspace_result_for_location,
    save_workspace,
)

DEFAULT_PROJECTS_ROOT = Path.home() / "projects"
_GIT_INIT_TIMEOUT_SECONDS = 10


class ProjectCreationError(Exception):
    """Raised when creating the project directory or initializing git fails."""


@dataclass(frozen=True, slots=True)
class ProjectCreationRequest:
    """UI-neutral input for creating a new local project."""

    project_name: str
    destination: Path
    init_git: bool = True
    launch: bool = True
    session_name: str | None = None
    windows: tuple[WindowSpec, ...] | None = None
    template_name: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedProject:
    """The durable result of a successful project creation."""

    workspace: WorkspaceSpec
    destination: Path
    workspace_label: str
    git_initialized: bool
    launch_request: LaunchRequest | None


def _template_for_name(name: str) -> WorkspaceTemplate:
    result = load_templates_result()
    if result.error:
        raise TemplateStoreError(result.error)
    template = find_template_by_name(name)
    if template is None:
        raise TemplateStoreError(f'Workspace template "{name}" was not found.')
    return template


def _workspace_for_request(
    request: ProjectCreationRequest, destination: Path
) -> tuple[WorkspaceSpec, str]:
    location = LocalProjectLocation(destination)
    if request.session_name:
        session_name = request.session_name
    elif request.launch:
        session_name = generate_session_name(request.project_name)
    else:
        session_name = sanitize_session_name(request.project_name)
    if request.template_name and request.windows is not None:
        raise ProjectCreationError(
            "Choose either a template or explicit workspace windows, not both."
        )
    if request.template_name:
        template = _template_for_name(request.template_name)
        return (
            workspace_from_template(
                template,
                project_name=request.project_name.strip(),
                project_location=location,
                session_name=session_name,
            ),
            template.name,
        )
    windows = request.windows
    if windows is None:
        from dashboard.services.workspace_defaults import default_workspace_windows

        windows = default_workspace_windows()
    try:
        return (
            WorkspaceSpec(
                project_name=request.project_name.strip(),
                project_location=location,
                session_name=session_name,
                windows=tuple(windows),
            ),
            "default" if request.windows is None else "custom",
        )
    except WorkspaceValidationError:
        raise


def create_project(request: ProjectCreationRequest) -> CreatedProject:
    """Create and persist one new local project using shared semantics.

    All checks that can avoid filesystem mutation happen before the directory
    is created. If a later operation fails, a directory created by this call
    is removed so a failed request never leaves a partial project behind.
    """
    destination = request.destination.expanduser().resolve()
    folder_name = destination.name
    validation = validate_new_project(request.project_name, folder_name, destination.parent)
    if not validation.is_valid:
        raise ProjectCreationError(" ".join(validation.errors))

    try:
        workspace, workspace_label = _workspace_for_request(request, destination)
        ensure_workspace_store_writable()
        existing = load_workspace_result_for_location(workspace.project_location)
    except (TemplateStoreError, WorkspaceStoreError, WorkspaceStoreVersionError) as exc:
        raise ProjectCreationError(str(exc)) from exc
    if existing.error:
        raise ProjectCreationError(existing.error)
    if existing.workspace is not None:
        raise ProjectCreationError(f"a workspace is already registered for {destination}")

    created = False
    try:
        create_project_directory(destination)
        created = True
        if request.init_git:
            init_git_repo(destination)
        save_workspace(workspace)
    except (OSError, ProjectCreationError, WorkspaceStoreError, WorkspaceStoreVersionError) as exc:
        if created:
            shutil.rmtree(destination, ignore_errors=True)
        raise ProjectCreationError(str(exc)) from exc

    launch_request = (
        LaunchRequest(workspace=workspace, init_git=request.init_git, action=LaunchAction.CREATE)
        if request.launch
        else None
    )
    return CreatedProject(
        workspace=workspace,
        destination=destination,
        workspace_label=workspace_label,
        git_initialized=request.init_git,
        launch_request=launch_request,
    )


def validate_project_name(project_name: str) -> str | None:
    """Return an error message if *project_name* is invalid, else None."""
    if not project_name.strip():
        return "Project name cannot be empty."
    return None


def validate_folder_name(folder_name: str) -> str | None:
    """Return an error message if *folder_name* is invalid, else None."""
    if not folder_name.strip():
        return "Folder name cannot be empty."
    if "/" in folder_name or "\\" in folder_name:
        return "Folder name cannot contain path separators."
    if folder_name in (".", ".."):
        return "Folder name cannot be '.' or '..'."
    return None


def resolve_destination(folder_name: str, root: Path | None = None) -> Path:
    """Resolve *folder_name* to an absolute path directly under *root*.

    Assumes validate_folder_name has already passed. Raises
    ProjectCreationError if the resolved path would not be a direct child
    of *root*.
    """
    root = (root if root is not None else DEFAULT_PROJECTS_ROOT).resolve()
    destination = (root / folder_name).resolve()
    if destination.parent != root:
        raise ProjectCreationError(f"'{folder_name}' must resolve directly under {root}.")
    return destination


@dataclass(frozen=True, slots=True)
class NewProjectValidation:
    """The result of validating a proposed new project name/folder pair."""

    errors: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_new_project(
    project_name: str, folder_name: str, root: Path | None = None
) -> NewProjectValidation:
    """Run every Step 1 validation rule and collect all resulting errors."""
    errors: list[str] = []

    name_error = validate_project_name(project_name)
    if name_error:
        errors.append(name_error)

    folder_error = validate_folder_name(folder_name)
    if folder_error:
        errors.append(folder_error)
    else:
        try:
            destination = resolve_destination(folder_name, root)
        except ProjectCreationError as exc:
            errors.append(str(exc))
        else:
            if destination.exists():
                errors.append(
                    f"'{destination}' already exists -- choose a different folder name."
                )

    return NewProjectValidation(errors=errors)


def create_project_directory(path: Path) -> None:
    """Create *path* as a brand-new directory.

    Never overwrites or merges into an existing directory -- raises if
    *path* already exists.
    """
    if path.exists():
        raise ProjectCreationError(f"'{path}' already exists.")
    path.mkdir(parents=True)


def init_git_repo(path: Path) -> None:
    """Run `git init` in *path*."""
    try:
        result = subprocess.run(
            ["git", "init"],
            cwd=path,
            capture_output=True,
            text=True,
            timeout=_GIT_INIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise ProjectCreationError(
            "git executable was not found; install Git or use --no-git."
        ) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectCreationError(f"Failed to run `git init` in {path}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ProjectCreationError(f"`git init` failed in {path}: {message}")
