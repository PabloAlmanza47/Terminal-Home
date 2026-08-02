"""Tests for project discovery and status logic (dashboard.services.projects)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services import projects as projects_module
from dashboard.services.projects import (
    Project,
    ProjectAction,
    ProjectStatus,
    discover_projects,
    gather_project_status,
    primary_actions,
    secondary_actions,
)
from dashboard.services.workspace_store import save_workspace


def _make_tree(tmp_path: Path, dirs: list[str], files: list[str] | None = None) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    for name in dirs:
        (root / name).mkdir()
    for name in files or []:
        (root / name).touch()
    return root


def test_discovers_immediate_child_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "beta"])

    projects = discover_projects(root=root, exclude=set())

    assert [p.name for p in projects] == ["alpha", "beta"]
    assert all(isinstance(p, Project) for p in projects)
    assert projects[0].path == root / "alpha"


def test_excludes_terminal_home_by_default(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "terminal-home", "beta"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["alpha", "beta"]


def test_ignores_files_only_lists_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"], files=["notes.txt", "README.md"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["alpha"]


def test_sorted_case_insensitively(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["Zebra", "apple", "Banana"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["apple", "Banana", "Zebra"]


def test_missing_root_returns_empty_list(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert discover_projects(root=missing) == []


def test_root_that_is_a_file_returns_empty_list(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-directory"
    not_a_dir.touch()

    assert discover_projects(root=not_a_dir) == []


def test_custom_exclude_set(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "beta", "gamma"])

    projects = discover_projects(root=root, exclude={"beta", "gamma"})

    assert [p.name for p in projects] == ["alpha"]


def test_excludes_hidden_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", ".git", ".config", "beta"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["alpha", "beta"]


# --- gather_project_status ----------------------------------------------------


def _workspace(project_path: Path, session_name: str = "demo") -> WorkspaceSpec:
    return WorkspaceSpec(
        project_name="demo",
        project_path=project_path,
        session_name=session_name,
        windows=(
            WindowSpec(
                window_name="main",
                panes=(PaneSpec(kind=PaneKind.CODE_EDITOR, display_name="Code Editor"),),
            ),
        ),
    )


def test_status_when_session_running_and_workspace_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    workspace = _workspace(project_path, session_name="demo-session")
    save_workspace(workspace, store_path=store_path)
    project = Project(name="demo", path=project_path)

    status = gather_project_status(
        project, store_path=store_path, running_sessions={"demo-session"}
    )

    assert status.saved_workspace == workspace
    assert status.session_running is True
    assert status.expected_session_name == "demo-session"
    assert status.workspace_metadata_error is None


def test_status_when_workspace_saved_but_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    workspace = _workspace(project_path, session_name="demo-session")
    save_workspace(workspace, store_path=store_path)
    project = Project(name="demo", path=project_path)

    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace == workspace
    assert status.session_running is False


def test_status_when_nothing_saved_and_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    project = Project(name="demo", path=project_path)

    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace is None
    assert status.session_running is False
    assert status.workspace_metadata_error is None


def test_status_running_session_with_no_saved_workspace_uses_deterministic_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orphaned running session (e.g. right after Forget Saved Workspace)
    is still detected -- matched only by the exact deterministic slug, never
    a fuzzy/similar name.
    """
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "My Demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    project = Project(name="My Demo", path=project_path)

    status = gather_project_status(
        project, store_path=store_path, running_sessions={"My-Demo"}
    )

    assert status.saved_workspace is None
    assert status.expected_session_name == "My-Demo"
    assert status.session_running is True


def test_status_normalizes_stale_project_path_in_saved_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    # Saved under the real canonical path, but with a stale project_path
    # field inside the record itself (as if hand-edited or moved).
    stale = _workspace(tmp_path / "old-location", session_name="demo-session")
    save_workspace(stale, store_path=store_path)
    import json

    data = json.loads(store_path.read_text())
    data[str(project_path.resolve())] = data.pop(str((tmp_path / "old-location").resolve()))
    store_path.write_text(json.dumps(data))

    project = Project(name="demo", path=project_path)
    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace is not None
    assert status.saved_workspace.project_path == project_path.resolve()


def test_status_reports_malformed_metadata_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    import json

    store_path.write_text(json.dumps({str(project_path.resolve()): {"project_name": "bad"}}))

    project = Project(name="demo", path=project_path)
    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace is None
    assert status.workspace_metadata_error is not None


def test_status_when_project_directory_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    store_path = tmp_path / "workspaces.json"
    project = Project(name="demo", path=tmp_path / "does-not-exist")

    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.project_dir_exists is False
    assert status.git_branch is None
    assert status.is_git_repo is False


def test_status_when_tmux_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: False)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    project = Project(name="demo", path=project_path)

    status = gather_project_status(project, store_path=store_path)

    assert status.tmux_available is False
    assert status.session_running is False


# --- primary_actions / secondary_actions ---------------------------------------


def _status(
    *,
    session_running: bool = False,
    project_dir_exists: bool = True,
    workspace_metadata_error: str | None = None,
    saved_workspace: WorkspaceSpec | None = None,
) -> ProjectStatus:
    return ProjectStatus(
        project=Project(name="demo", path=Path("/tmp/demo")),
        canonical_path=Path("/tmp/demo"),
        project_dir_exists=project_dir_exists,
        is_git_repo=False,
        git_branch=None,
        saved_workspace=saved_workspace,
        workspace_metadata_error=workspace_metadata_error,
        expected_session_name="demo",
        tmux_available=True,
        session_running=session_running,
        last_modified=None,
    )


def test_primary_action_running_session_offers_resume() -> None:
    assert primary_actions(_status(session_running=True)) == [ProjectAction.RESUME]


def test_primary_action_saved_workspace_not_running_offers_recreate(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert primary_actions(_status(saved_workspace=workspace)) == [ProjectAction.RECREATE]


def test_primary_action_nothing_saved_offers_default_and_configure() -> None:
    assert primary_actions(_status()) == [ProjectAction.OPEN_DEFAULT, ProjectAction.CONFIGURE]


def test_primary_action_corrupt_metadata_offers_forget_and_configure() -> None:
    actions = primary_actions(_status(workspace_metadata_error="bad data"))
    assert actions == [ProjectAction.FORGET, ProjectAction.CONFIGURE]


def test_primary_action_running_session_wins_over_corrupt_metadata() -> None:
    actions = primary_actions(
        _status(session_running=True, workspace_metadata_error="bad data")
    )
    assert actions == [ProjectAction.RESUME]


def test_primary_action_missing_directory_offers_nothing_when_not_running() -> None:
    assert primary_actions(_status(project_dir_exists=False)) == []


def test_primary_action_missing_directory_still_offers_resume_when_running() -> None:
    actions = primary_actions(_status(session_running=True, project_dir_exists=False))
    assert actions == [ProjectAction.RESUME]


def test_secondary_actions_for_saved_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    actions = secondary_actions(_status(saved_workspace=workspace))
    assert actions == [ProjectAction.EDIT, ProjectAction.RESET, ProjectAction.FORGET]


def test_secondary_actions_empty_when_nothing_saved() -> None:
    assert secondary_actions(_status()) == []


def test_secondary_actions_offers_forget_for_corrupt_metadata_with_running_session() -> None:
    actions = secondary_actions(
        _status(session_running=True, workspace_metadata_error="bad data")
    )
    assert actions == [ProjectAction.FORGET]
