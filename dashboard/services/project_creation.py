"""Validation and filesystem operations for creating a new project directory
directly under ~/projects. Kept free of Textual imports so it can be unit
tested with plain tmp_path fixtures.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROJECTS_ROOT = Path.home() / "projects"
_GIT_INIT_TIMEOUT_SECONDS = 10


class ProjectCreationError(Exception):
    """Raised when creating the project directory or initializing git fails."""


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
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectCreationError(f"Failed to run `git init` in {path}: {exc}") from exc
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise ProjectCreationError(f"`git init` failed in {path}: {message}")
