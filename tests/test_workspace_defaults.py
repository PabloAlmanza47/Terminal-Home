"""Tests for the default WorkspaceSpec builder (dashboard.services.workspace_defaults)."""

from __future__ import annotations

from pathlib import Path

from dashboard.models import PaneKind
from dashboard.services.workspace_defaults import build_default_workspace


def test_default_workspace_has_one_code_window(tmp_path: Path) -> None:
    workspace = build_default_workspace("Demo", tmp_path, "demo")

    assert workspace.project_name == "Demo"
    assert workspace.project_path == tmp_path
    assert workspace.session_name == "demo"
    assert len(workspace.windows) == 1
    assert workspace.windows[0].window_name == "code"


def test_default_workspace_has_editor_and_blank_terminal_panes(tmp_path: Path) -> None:
    workspace = build_default_workspace("Demo", tmp_path, "demo")

    panes = workspace.windows[0].panes
    assert [pane.kind for pane in panes] == [PaneKind.CODE_EDITOR, PaneKind.BLANK_TERMINAL]


def test_default_workspace_is_valid_and_serializable(tmp_path: Path) -> None:
    from dashboard.models import WorkspaceSpec

    workspace = build_default_workspace("Demo", tmp_path, "demo")
    restored = WorkspaceSpec.from_dict(workspace.to_dict())

    assert restored == workspace
