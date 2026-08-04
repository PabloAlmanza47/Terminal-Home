"""Reusable, project-neutral workspace template models and conversions."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from dashboard.models.project_location import ProjectLocation
from dashboard.models.workspace import WindowSpec, WorkspaceSpec, WorkspaceValidationError

MAX_TEMPLATE_NAME_LENGTH = 80


class TemplateValidationError(ValueError):
    """Raised when a workspace template's identity or name is invalid."""


def normalize_template_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise TemplateValidationError("Template name cannot be empty.")
    if len(normalized) > MAX_TEMPLATE_NAME_LENGTH:
        raise TemplateValidationError(
            f"Template name cannot exceed {MAX_TEMPLATE_NAME_LENGTH} characters."
        )
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise TemplateValidationError("Template name cannot contain control characters.")
    return normalized


@dataclass(frozen=True, slots=True)
class WorkspaceTemplate:
    """A named, stable, project-neutral copy of reusable window intent."""

    id: str
    name: str
    windows: tuple[WindowSpec, ...]

    def __post_init__(self) -> None:
        try:
            UUID(self.id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise TemplateValidationError("Template ID must be a valid UUID.") from exc
        normalized = normalize_template_name(self.name)
        object.__setattr__(self, "name", normalized)
        if not self.windows:
            raise TemplateValidationError("A template must contain at least one window.")
        names = [window.window_name for window in self.windows]
        if len(names) != len(set(names)):
            raise TemplateValidationError("Window names must be unique within a template.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "windows": [window.to_dict() for window in self.windows],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceTemplate:
        return cls(
            id=data["id"],
            name=data["name"],
            windows=tuple(WindowSpec.from_dict(window) for window in data["windows"]),
        )


def _copy_windows(windows: tuple[WindowSpec, ...]) -> tuple[WindowSpec, ...]:
    return tuple(WindowSpec.from_dict(window.to_dict()) for window in windows)


def template_from_workspace(
    workspace: WorkspaceSpec, name: str, *, template_id: str | None = None
) -> WorkspaceTemplate:
    return WorkspaceTemplate(
        id=template_id or str(uuid4()),
        name=name,
        windows=_copy_windows(workspace.windows),
    )


def workspace_from_template(
    template: WorkspaceTemplate,
    *,
    project_name: str,
    project_location: ProjectLocation,
    session_name: str,
) -> WorkspaceSpec:
    try:
        return WorkspaceSpec(
            project_name=project_name,
            project_location=project_location,
            session_name=session_name,
            windows=_copy_windows(template.windows),
        )
    except WorkspaceValidationError:
        raise
