"""Tests for the reusable workspace models (dashboard.models.workspace)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import (
    MAX_PANES_PER_WINDOW,
    LaunchRequest,
    PaneKind,
    PaneSpec,
    WindowSpec,
    WorkspaceSpec,
    WorkspaceValidationError,
)


def _pane(kind: PaneKind = PaneKind.BLANK_TERMINAL, name: str = "Blank Terminal") -> PaneSpec:
    return PaneSpec(kind=kind, display_name=name)


# --- PaneSpec ----------------------------------------------------------------


def test_pane_spec_requires_display_name() -> None:
    with pytest.raises(WorkspaceValidationError):
        PaneSpec(kind=PaneKind.BLANK_TERMINAL, display_name="   ")


def test_pane_spec_custom_command_requires_command() -> None:
    with pytest.raises(WorkspaceValidationError):
        PaneSpec(kind=PaneKind.CUSTOM_COMMAND, display_name="Docs", custom_command="")


def test_pane_spec_custom_command_accepts_command() -> None:
    pane = PaneSpec(
        kind=PaneKind.CUSTOM_COMMAND, display_name="Docs", custom_command="mkdocs serve"
    )
    assert pane.custom_command == "mkdocs serve"


def test_pane_spec_round_trips_through_dict() -> None:
    pane = PaneSpec(kind=PaneKind.GIT, display_name="Git")
    assert PaneSpec.from_dict(pane.to_dict()) == pane


# --- WindowSpec ----------------------------------------------------------------


def test_window_spec_requires_nonempty_name() -> None:
    with pytest.raises(WorkspaceValidationError):
        WindowSpec(window_name="  ", panes=(_pane(),))


@pytest.mark.parametrize("pane_count", [1, 2, 3, 4])
def test_window_spec_accepts_one_to_four_panes(pane_count: int) -> None:
    panes = tuple(_pane(name=f"pane-{i}") for i in range(pane_count))
    window = WindowSpec(window_name="main", panes=panes)
    assert len(window.panes) == pane_count


def test_window_spec_rejects_zero_panes() -> None:
    with pytest.raises(WorkspaceValidationError):
        WindowSpec(window_name="main", panes=())


def test_window_spec_rejects_more_than_four_panes() -> None:
    panes = tuple(_pane(name=f"pane-{i}") for i in range(MAX_PANES_PER_WINDOW + 1))
    with pytest.raises(WorkspaceValidationError):
        WindowSpec(window_name="main", panes=panes)


def test_window_spec_preserves_pane_order() -> None:
    panes = (
        _pane(PaneKind.CODE_EDITOR, "Code Editor"),
        _pane(PaneKind.GIT, "Git"),
        _pane(PaneKind.FILE_TREE, "File Tree"),
    )
    window = WindowSpec(window_name="main", panes=panes)
    assert window.panes == panes
    assert [p.display_name for p in window.panes] == ["Code Editor", "Git", "File Tree"]


def test_window_spec_round_trips_through_dict() -> None:
    window = WindowSpec(window_name="main", panes=(_pane(PaneKind.GIT, "Git"), _pane()))
    assert WindowSpec.from_dict(window.to_dict()) == window


# --- WorkspaceSpec -------------------------------------------------------------


def test_workspace_spec_requires_at_least_one_window(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceSpec(
            project_name="demo", project_path=tmp_path, session_name="demo", windows=()
        )


def test_workspace_spec_requires_absolute_path() -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceSpec(
            project_name="demo",
            project_path=Path("relative/path"),
            session_name="demo",
            windows=(WindowSpec(window_name="main", panes=(_pane(),)),),
        )


def test_workspace_spec_requires_project_name(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceSpec(
            project_name="  ",
            project_path=tmp_path,
            session_name="demo",
            windows=(WindowSpec(window_name="main", panes=(_pane(),)),),
        )


def test_workspace_spec_rejects_duplicate_window_names(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceValidationError):
        WorkspaceSpec(
            project_name="demo",
            project_path=tmp_path,
            session_name="demo",
            windows=(
                WindowSpec(window_name="main", panes=(_pane(),)),
                WindowSpec(window_name="main", panes=(_pane(),)),
            ),
        )


def test_workspace_spec_preserves_window_order(tmp_path: Path) -> None:
    windows = (
        WindowSpec(window_name="main", panes=(_pane(),)),
        WindowSpec(window_name="tests", panes=(_pane(),)),
    )
    workspace = WorkspaceSpec(
        project_name="demo", project_path=tmp_path, session_name="demo", windows=windows
    )
    assert workspace.windows == windows


def test_workspace_spec_round_trips_through_dict(tmp_path: Path) -> None:
    windows = (
        WindowSpec(
            window_name="main",
            panes=(_pane(PaneKind.CODE_EDITOR, "Code Editor"), _pane(PaneKind.GIT, "Git")),
        ),
    )
    workspace = WorkspaceSpec(
        project_name="demo", project_path=tmp_path, session_name="demo", windows=windows
    )
    restored = WorkspaceSpec.from_dict(workspace.to_dict())
    assert restored == workspace
    assert isinstance(restored.project_path, Path)


def test_launch_request_bundles_workspace_and_git_flag(tmp_path: Path) -> None:
    workspace = WorkspaceSpec(
        project_name="demo",
        project_path=tmp_path,
        session_name="demo",
        windows=(WindowSpec(window_name="main", panes=(_pane(),)),),
    )
    request = LaunchRequest(workspace=workspace, init_git=True)
    assert request.workspace is workspace
    assert request.init_git is True
