"""The simple default workspace offered for a project with no saved
WorkspaceSpec, and used to reset a project back to that baseline.

Kept as its own tiny module (rather than inlined in a screen) so both
"Open Default Workspace" and "Reset to Default Workspace" build the exact
same shape from one place.
"""

from __future__ import annotations

from pathlib import Path

from dashboard.models import PANE_KIND_LABELS, PaneKind, PaneSpec, WindowSpec, WorkspaceSpec

DEFAULT_WINDOW_NAME = "code"


def default_workspace_windows() -> tuple[WindowSpec, ...]:
    """Return the built-in project-neutral default layout."""
    return (
        WindowSpec(
            window_name=DEFAULT_WINDOW_NAME,
            panes=(
                PaneSpec(
                    kind=PaneKind.CODE_EDITOR,
                    display_name=PANE_KIND_LABELS[PaneKind.CODE_EDITOR],
                ),
                PaneSpec(
                    kind=PaneKind.BLANK_TERMINAL,
                    display_name=PANE_KIND_LABELS[PaneKind.BLANK_TERMINAL],
                ),
            ),
        ),
    )


def build_default_workspace(
    project_name: str, project_path: Path, session_name: str
) -> WorkspaceSpec:
    """A single "code" window with a Code Editor pane and a Blank Terminal
    pane, side by side (the two-pane layout rule in dashboard.models.layout
    already lays out two panes as even-horizontal).
    """
    return WorkspaceSpec(
        project_name=project_name,
        project_path=project_path,
        session_name=session_name,
        windows=default_workspace_windows(),
    )
