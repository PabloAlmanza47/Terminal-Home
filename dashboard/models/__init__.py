"""Reusable, Textual-independent data models for project workspaces.

These describe *what* a tmux workspace should look like (session, windows,
panes) without knowing how to build or render it. Both the New Project
wizard and any future "launch existing project" flow build these same
models and hand them to dashboard.services.workspace_launcher.
"""

from __future__ import annotations

from dashboard.models.template import (
    MAX_TEMPLATE_NAME_LENGTH,
    TemplateValidationError,
    WorkspaceTemplate,
    normalize_template_name,
    template_from_workspace,
    workspace_from_template,
)
from dashboard.models.workspace import (
    MAX_PANES_PER_WINDOW,
    PANE_KIND_LABELS,
    LaunchAction,
    LaunchRequest,
    PaneKind,
    PaneSpec,
    WindowSpec,
    WorkspaceSpec,
    WorkspaceValidationError,
)

__all__ = [
    "LaunchAction",
    "LaunchRequest",
    "MAX_PANES_PER_WINDOW",
    "PANE_KIND_LABELS",
    "PaneKind",
    "PaneSpec",
    "WindowSpec",
    "WorkspaceSpec",
    "WorkspaceValidationError",
    "MAX_TEMPLATE_NAME_LENGTH",
    "TemplateValidationError",
    "WorkspaceTemplate",
    "normalize_template_name",
    "template_from_workspace",
    "workspace_from_template",
]
