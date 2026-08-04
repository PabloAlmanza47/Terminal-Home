from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import LaunchAction, LocalProjectLocation
from dashboard.services import project_launch
from dashboard.services.project_launch import (
    ProjectLaunchPreparationError,
    prepare_project_launch,
)
from dashboard.services.projects import Project, ProjectStatus
from dashboard.services.workspace_defaults import build_default_workspace


def _status(
    project_path: Path,
    *,
    running: bool = False,
    saved: bool = False,
    exists: bool = True,
    metadata_error: str | None = None,
    session_name: str = "demo",
) -> ProjectStatus:
    workspace = (
        build_default_workspace("demo", LocalProjectLocation(project_path), session_name)
        if saved
        else None
    )
    return ProjectStatus(
        project=Project("demo", project_path),
        canonical_path=project_path,
        project_dir_exists=exists,
        is_git_repo=False,
        git_branch=None,
        saved_workspace=workspace,
        workspace_metadata_error=metadata_error,
        expected_session_name=session_name,
        tmux_available=True,
        session_running=running,
        last_modified=None,
    )


@pytest.mark.parametrize("saved", [False, True])
def test_running_session_prepares_attach_without_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, saved: bool
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(project_launch, "save_workspace", lambda *a, **k: calls.append(1))
    status = _status(
        tmp_path,
        running=True,
        saved=saved,
        exists=False,
        metadata_error="corrupt" if not saved else None,
    )

    prepared = prepare_project_launch(status)

    assert prepared.request.action is LaunchAction.ATTACH
    assert prepared.persisted_default is False
    assert calls == []
    if not saved:
        assert prepared.request.workspace is None
        assert prepared.request.session_name == "demo"


def test_stopped_saved_workspace_uses_attach_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(project_launch, "save_workspace", lambda *a, **k: calls.append(1))
    status = _status(tmp_path, saved=True)

    prepared = prepare_project_launch(status)

    assert prepared.request.action is LaunchAction.ATTACH
    assert prepared.request.workspace is status.saved_workspace
    assert not prepared.persisted_default
    assert calls == []


def test_default_is_saved_before_create_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[tuple[str, object]] = []
    monkeypatch.setattr(
        project_launch,
        "save_workspace",
        lambda workspace, store_path=None: events.append(("save", workspace)),
    )
    status = _status(tmp_path, session_name="demo-collision")

    prepared = prepare_project_launch(status)

    assert events == [("save", prepared.request.workspace)]
    assert prepared.request.action is LaunchAction.CREATE
    assert prepared.persisted_default
    assert prepared.request.workspace is not None
    assert prepared.request.workspace.session_name == "demo-collision"


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (lambda path: _status(path, metadata_error="bad metadata"), "metadata"),
        (lambda path: _status(path, exists=False), "directory"),
    ],
)
def test_stopped_blocked_status_never_saves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status,
    message: str,
) -> None:
    calls: list[object] = []
    monkeypatch.setattr(project_launch, "save_workspace", lambda *a, **k: calls.append(1))
    with pytest.raises(ProjectLaunchPreparationError, match=message):
        prepare_project_launch(status(tmp_path))
    assert calls == []


def test_save_failure_propagates_before_any_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        project_launch,
        "save_workspace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        prepare_project_launch(_status(tmp_path))


def test_directory_disappearing_after_scan_blocks_before_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    status = _status(project, exists=True)
    project.rmdir()
    calls: list[object] = []
    monkeypatch.setattr(project_launch, "save_workspace", lambda *a, **k: calls.append(1))
    with pytest.raises(ProjectLaunchPreparationError, match="no longer exists"):
        prepare_project_launch(status)
    assert calls == []
