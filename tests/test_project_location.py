from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dashboard.models import (
    LocalProjectLocation,
    ProjectLocationKind,
    ProjectLocationValidationError,
    SshProjectLocation,
    project_location_from_dict,
)

HOST_ID = "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3"


def test_local_location_accepts_absolute_path_without_filesystem_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_access(*args: object, **kwargs: object) -> None:
        raise AssertionError("location construction accessed the filesystem")

    for method in ("resolve", "exists", "is_dir", "stat", "expanduser"):
        monkeypatch.setattr(Path, method, unexpected_access)

    location = LocalProjectLocation(Path("/not/required/to/exist"))
    assert location.kind is ProjectLocationKind.LOCAL
    assert location.path == Path("/not/required/to/exist")


def test_local_location_rejects_relative_and_non_path_values() -> None:
    with pytest.raises(ProjectLocationValidationError, match="absolute"):
        LocalProjectLocation(Path("relative/project"))
    with pytest.raises(ProjectLocationValidationError, match="Path"):
        LocalProjectLocation("/project")  # type: ignore[arg-type]


def test_local_location_serialization_round_trip() -> None:
    location = LocalProjectLocation(Path("/home/pablo/projects/example"))
    data = {"kind": "local", "path": "/home/pablo/projects/example"}
    assert location.to_dict() == data
    assert LocalProjectLocation.from_dict(data) == location
    restored = project_location_from_dict(data)
    assert restored == location
    assert isinstance(restored.path, Path)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"path": "/project"},
        {"kind": "unknown", "path": "/project"},
        {"kind": "local"},
        {"kind": "local", "path": 3},
        {"kind": "local", "path": "relative"},
        {"kind": "local", "path": "/project", "host_id": HOST_ID},
        {"host_id": HOST_ID, "remote_path": "/project"},
    ],
)
def test_location_parser_rejects_malformed_local_shapes(data: dict[str, object]) -> None:
    with pytest.raises(ProjectLocationValidationError):
        project_location_from_dict(data)


def test_ssh_location_normalizes_uuid_and_accepts_absolute_posix_path() -> None:
    location = SshProjectLocation("{C27C7B67-8E3F-4EBC-8DCE-D66BE8FD1EA3}", "/srv/project")
    assert location.kind is ProjectLocationKind.SSH
    assert location.host_id == HOST_ID


@pytest.mark.parametrize("host_id", ["not-a-uuid", 123, None])
def test_ssh_location_rejects_invalid_uuid(host_id: object) -> None:
    with pytest.raises(ProjectLocationValidationError, match="UUID"):
        SshProjectLocation(host_id, "/srv/project")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "remote_path",
    [
        "",
        "   ",
        "relative/path",
        "~/project",
        "C:\\project",
        "/bad\x00path",
        "/bad\rpath",
        "/bad\npath",
        "/bad\u0085path",
        "/bad\u202epath",
    ],
)
def test_ssh_location_rejects_invalid_remote_paths(remote_path: str) -> None:
    with pytest.raises(ProjectLocationValidationError):
        SshProjectLocation(HOST_ID, remote_path)


def test_ssh_location_preserves_literal_remote_path_and_round_trips() -> None:
    remote_path = "/home/π/api server/'quoted'/\"double\"/$HOME/`tick`;semi"
    location = SshProjectLocation(HOST_ID, remote_path)
    data = {"kind": "ssh", "host_id": HOST_ID, "remote_path": remote_path}
    assert location.remote_path == remote_path
    assert location.to_dict() == data
    assert SshProjectLocation.from_dict(data) == location
    restored = project_location_from_dict(data)
    assert restored == location
    assert isinstance(restored, SshProjectLocation)
    assert isinstance(restored.remote_path, str)
    assert not isinstance(restored.remote_path, Path)


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "ssh", "remote_path": "/project"},
        {"kind": "ssh", "host_id": HOST_ID},
        {"kind": "ssh", "host_id": HOST_ID, "remote_path": 3},
        {"kind": "ssh", "host_id": HOST_ID, "remote_path": "/x", "path": "/x"},
    ],
)
def test_location_parser_rejects_malformed_ssh_shapes(data: dict[str, object]) -> None:
    with pytest.raises(ProjectLocationValidationError):
        project_location_from_dict(data)


def test_locations_are_immutable() -> None:
    location = SshProjectLocation(HOST_ID, "/srv/project")
    with pytest.raises(FrozenInstanceError):
        location.remote_path = "/other"  # type: ignore[misc]
