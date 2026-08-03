"""Tests for the structural, read-only launch plan
(dashboard.services.workspace_plan) that backs `th plan` -- and, later,
`th up`'s confirmation prompt. Every test builds a ProjectStatus directly
rather than scanning a real filesystem tree; this module never touches
tmux or disk itself, so nothing here needs to either.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services import workspace_store
from dashboard.services.projects import Project, ProjectStatus
from dashboard.services.workspace_plan import (
    ACTION_ATTACH,
    ACTION_CREATE_DEFAULT,
    ACTION_CREATE_SAVED,
    SOURCE_DEFAULT,
    SOURCE_RUNNING,
    SOURCE_SAVED,
    build_workspace_plan,
    format_plan,
)


def _status(
    tmp_path: Path,
    *,
    name: str = "demo",
    session_running: bool = False,
    saved_workspace: WorkspaceSpec | None = None,
    expected_session_name: str = "demo",
) -> ProjectStatus:
    return ProjectStatus(
        project=Project(name=name, path=tmp_path),
        canonical_path=tmp_path,
        project_dir_exists=True,
        is_git_repo=False,
        git_branch=None,
        saved_workspace=saved_workspace,
        workspace_metadata_error=None,
        expected_session_name=expected_session_name,
        tmux_available=True,
        session_running=session_running,
        last_modified=None,
    )


def _workspace(project_path: Path, session_name: str = "demo") -> WorkspaceSpec:
    return WorkspaceSpec(
        project_name="demo",
        project_path=project_path,
        session_name=session_name,
        windows=(
            WindowSpec(
                window_name="editor",
                panes=(PaneSpec(kind=PaneKind.CODE_EDITOR, display_name="Code Editor"),),
            ),
            WindowSpec(
                window_name="server",
                panes=(
                    PaneSpec(
                        kind=PaneKind.CUSTOM_COMMAND,
                        display_name="Dev Server",
                        custom_command="npm run dev",
                    ),
                ),
            ),
        ),
    )


def test_running_session_plan(tmp_path: Path) -> None:
    status = _status(tmp_path, session_running=True, expected_session_name="demo")

    plan = build_workspace_plan(status)

    assert plan.action == ACTION_ATTACH
    assert plan.source == SOURCE_RUNNING
    assert plan.workspace is None
    assert plan.note is not None

    rendered = format_plan(plan)
    assert "Action: attach to existing session" in rendered
    assert "Source: running tmux session" in rendered
    assert "authoritative" in rendered


def test_saved_workspace_plan(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, session_name="demo")
    status = _status(
        tmp_path, saved_workspace=workspace, expected_session_name=workspace.session_name
    )

    plan = build_workspace_plan(status)

    assert plan.action == ACTION_CREATE_SAVED
    assert plan.source == SOURCE_SAVED
    assert plan.workspace == workspace


def test_default_workspace_plan(tmp_path: Path) -> None:
    status = _status(tmp_path, saved_workspace=None, expected_session_name="demo")

    plan = build_workspace_plan(status)

    assert plan.action == ACTION_CREATE_DEFAULT
    assert plan.source == SOURCE_DEFAULT
    assert plan.workspace is not None
    assert plan.workspace.session_name == "demo"


def test_structural_windows_and_panes_are_rendered(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    status = _status(tmp_path, saved_workspace=workspace, expected_session_name="demo")

    rendered = format_plan(build_workspace_plan(status))

    assert "Window 1: editor" in rendered
    assert "Pane 1: Code Editor" in rendered
    assert "Window 2: server" in rendered
    assert "Pane 1: Dev Server — npm run dev" in rendered


def test_default_plan_is_never_persisted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = []
    monkeypatch.setattr(workspace_store, "save_workspace", lambda *a, **k: saved.append(1))
    status = _status(tmp_path, saved_workspace=None, expected_session_name="demo")

    build_workspace_plan(status)

    assert saved == []
    assert not (tmp_path / "workspaces.json").exists()


def test_saved_session_name_is_preserved(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, session_name="custom-session")
    status = _status(
        tmp_path, saved_workspace=workspace, expected_session_name="custom-session"
    )

    plan = build_workspace_plan(status)

    assert plan.session_name == "custom-session"
    assert plan.workspace is not None
    assert plan.workspace.session_name == "custom-session"


def test_collision_derived_session_name_is_preserved_for_default_plan(tmp_path: Path) -> None:
    collision_name = "example-a1b2c3d4"
    status = _status(
        tmp_path, saved_workspace=None, expected_session_name=collision_name
    )

    plan = build_workspace_plan(status)

    assert plan.session_name == collision_name
    assert plan.workspace is not None
    assert plan.workspace.session_name == collision_name


def test_repeated_plan_is_equivalent_and_has_no_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    saved = []
    monkeypatch.setattr(workspace_store, "save_workspace", lambda *a, **k: saved.append(1))
    status = _status(tmp_path, saved_workspace=None, expected_session_name="demo")

    first = format_plan(build_workspace_plan(status))
    second = format_plan(build_workspace_plan(status))

    assert first == second
    assert saved == []
