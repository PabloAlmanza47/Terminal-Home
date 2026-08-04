from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import (
    LaunchAction,
    LocalProjectLocation,
    RemoteProjectRegistration,
    SshProjectLocation,
)
from dashboard.services import project_launch
from dashboard.services.project_launch import (
    ProjectLaunchPreparationError,
    prepare_project_launch,
    resolve_project_status,
)
from dashboard.services.project_selection import ProjectSelectionResult, RegisteredRemoteProject
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


def test_local_project_status_resolution_still_gathers_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = Project("demo", tmp_path)
    expected = _status(tmp_path)
    monkeypatch.setattr(
        project_launch,
        "resolve_project_selector",
        lambda selector, config: ProjectSelectionResult(project=local),
    )
    monkeypatch.setattr(
        project_launch,
        "gather_single_project_status",
        lambda project, config: expected,
    )

    result = resolve_project_status("demo")

    assert result.status is expected
    assert result.error is None


def test_remote_project_status_resolution_stops_before_local_gathering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = RegisteredRemoteProject(
        name="remote-demo",
        location=SshProjectLocation(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", "/srv/remote"
        ),
        registration=RemoteProjectRegistration(
            "6cd81f5d-9fe4-4c32-b17f-f88e5db754f4",
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            "remote-demo",
            "/srv/remote",
        ),
    )
    monkeypatch.setattr(
        project_launch,
        "resolve_project_selector",
        lambda selector, config: ProjectSelectionResult(project=remote),
    )
    monkeypatch.setattr(
        project_launch,
        "gather_single_project_status",
        lambda *args, **kwargs: pytest.fail("remote projects must not gather local status"),
    )

    result = resolve_project_status("ssh:c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3:/srv/remote")

    assert result.status is None
    assert result.error == "Remote project CLI launch integration is not available yet."
