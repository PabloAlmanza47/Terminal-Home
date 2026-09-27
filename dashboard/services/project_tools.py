"""Best-effort availability checks for tools surfaced by Project Detail."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectTools:
    lazygit: bool
    editor_in_workspace: bool
    terminal_in_workspace: bool


def detect_project_tools(
    path: Path, *, has_editor_pane: bool, has_terminal_pane: bool
) -> ProjectTools:
    """Return contextual tools without invoking any external process."""
    del path  # reserved for future project-local tool configuration
    return ProjectTools(
        lazygit=shutil.which("lazygit") is not None,
        editor_in_workspace=has_editor_pane,
        terminal_in_workspace=has_terminal_pane,
    )
