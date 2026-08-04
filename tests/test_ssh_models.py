from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from dashboard.models import (
    MAX_REMOTE_NAME_LENGTH,
    RemoteProjectRegistration,
    SshHost,
    SshModelValidationError,
    SshProjectLocation,
)

HOST_ID = "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3"
PROJECT_ID = "6cd81f5d-9fe4-4c32-b17f-f88e5db754f4"


@pytest.mark.parametrize(
    "destination", ["pi-dev", "example.com", "192.0.2.10", "pablo@example.com"]
)
def test_ssh_host_accepts_single_openssh_destinations(destination: str) -> None:
    assert SshHost(HOST_ID, "Development", destination).destination == destination


def test_ssh_host_normalizes_display_name_destination_and_uuid() -> None:
    host = SshHost(
        "{C27C7B67-8E3F-4EBC-8DCE-D66BE8FD1EA3}",
        "  Pi Development  ",
        "  pi-dev  ",
    )
    assert host.id == HOST_ID
    assert host.display_name == "Pi Development"
    assert host.destination == "pi-dev"


@pytest.mark.parametrize(
    "destination",
    [
        "",
        "   ",
        "-F",
        "-oProxyCommand=x",
        "host other",
        "user @host",
        "host\targ",
        "host\narg",
        "host\rarg",
        "host\x00arg",
        "host\u202earg",
    ],
)
def test_ssh_host_rejects_unsafe_destinations(destination: str) -> None:
    with pytest.raises(SshModelValidationError):
        SshHost(HOST_ID, "Development", destination)


@pytest.mark.parametrize(
    "display_name", ["", "   ", "bad\nname", "bad\tname", "bad\x00name", "bad\u202ename"]
)
def test_ssh_host_rejects_invalid_display_names(display_name: str) -> None:
    with pytest.raises(SshModelValidationError):
        SshHost(HOST_ID, display_name, "pi-dev")


def test_ssh_host_preserves_unicode_and_enforces_name_maximum() -> None:
    assert SshHost(HOST_ID, "  Pi Développement  ", "pi-dev").display_name == ("Pi Développement")
    assert SshHost(HOST_ID, "x" * MAX_REMOTE_NAME_LENGTH, "pi-dev")
    with pytest.raises(SshModelValidationError, match="exceed"):
        SshHost(HOST_ID, "x" * (MAX_REMOTE_NAME_LENGTH + 1), "pi-dev")


@pytest.mark.parametrize("host_id", ["invalid", 3, None])
def test_ssh_host_rejects_invalid_ids(host_id: object) -> None:
    with pytest.raises(SshModelValidationError, match="UUID"):
        SshHost(host_id, "Development", "pi-dev")  # type: ignore[arg-type]


def test_ssh_host_serialization_round_trip_has_no_credentials() -> None:
    host = SshHost(HOST_ID, "Pi Development", "pablo@pi-dev")
    data = {
        "id": HOST_ID,
        "display_name": "Pi Development",
        "destination": "pablo@pi-dev",
    }
    assert host.to_dict() == data
    assert set(data) == {"id", "display_name", "destination"}
    assert SshHost.from_dict(data) == host


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"id": HOST_ID, "display_name": "Pi"},
        {"id": HOST_ID, "display_name": "Pi", "destination": 3},
        {"id": HOST_ID, "display_name": "Pi", "destination": "pi", "password": "x"},
    ],
)
def test_ssh_host_rejects_malformed_serialized_shapes(data: dict[str, object]) -> None:
    with pytest.raises(SshModelValidationError):
        SshHost.from_dict(data)


def test_remote_project_registration_is_valid_trimmed_and_immutable() -> None:
    registration = RemoteProjectRegistration(
        PROJECT_ID, HOST_ID, "  API Server  ", "/srv/api server"
    )
    assert registration.name == "API Server"
    with pytest.raises(FrozenInstanceError):
        registration.name = "Other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("project_id", "host_id"),
    [("invalid", HOST_ID), (PROJECT_ID, "invalid"), (3, HOST_ID), (PROJECT_ID, None)],
)
def test_remote_registration_rejects_invalid_ids(project_id: object, host_id: object) -> None:
    with pytest.raises(SshModelValidationError, match="UUID"):
        RemoteProjectRegistration(  # type: ignore[arg-type]
            project_id, host_id, "API", "/srv/api"
        )


@pytest.mark.parametrize("name", ["", "   ", "bad\nname", "bad\x00name", "bad\u202ename"])
def test_remote_registration_rejects_invalid_names(name: str) -> None:
    with pytest.raises(SshModelValidationError):
        RemoteProjectRegistration(PROJECT_ID, HOST_ID, name, "/srv/api")


def test_remote_registration_name_maximum() -> None:
    assert RemoteProjectRegistration(PROJECT_ID, HOST_ID, "x" * MAX_REMOTE_NAME_LENGTH, "/srv/api")
    with pytest.raises(SshModelValidationError, match="exceed"):
        RemoteProjectRegistration(
            PROJECT_ID,
            HOST_ID,
            "x" * (MAX_REMOTE_NAME_LENGTH + 1),
            "/srv/api",
        )


@pytest.mark.parametrize(
    "remote_path",
    ["", "relative", "~/api", "/bad\x00path", "/bad\rpath", "/bad\npath", "/bad\u202epath"],
)
def test_remote_registration_rejects_invalid_paths(remote_path: str) -> None:
    with pytest.raises(SshModelValidationError):
        RemoteProjectRegistration(PROJECT_ID, HOST_ID, "API", remote_path)


def test_remote_registration_round_trip_and_location_conversion() -> None:
    remote_path = "/srv/π api/'quoted'/\"double\"/$HOME/`tick`;semi"
    registration = RemoteProjectRegistration(PROJECT_ID, HOST_ID, "API Server", remote_path)
    data = {
        "id": PROJECT_ID,
        "host_id": HOST_ID,
        "name": "API Server",
        "remote_path": remote_path,
    }
    assert registration.to_dict() == data
    restored = RemoteProjectRegistration.from_dict(data)
    assert restored == registration
    assert restored.id == PROJECT_ID
    assert restored.host_id == HOST_ID
    assert restored.location == SshProjectLocation(HOST_ID, remote_path)
    assert isinstance(restored.location.remote_path, str)
    assert not isinstance(restored.location.remote_path, Path)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"id": PROJECT_ID, "host_id": HOST_ID, "name": "API"},
        {"id": PROJECT_ID, "host_id": HOST_ID, "name": 3, "remote_path": "/srv/api"},
        {
            "id": PROJECT_ID,
            "host_id": HOST_ID,
            "name": "API",
            "remote_path": "/srv/api",
            "password": "x",
        },
    ],
)
def test_remote_registration_rejects_malformed_shapes(data: dict[str, object]) -> None:
    with pytest.raises(SshModelValidationError):
        RemoteProjectRegistration.from_dict(data)
