from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.models import RemoteProjectRegistration
from dashboard.services.atomic_file import backup_path_for
from dashboard.services.load_result import LoadSource
from dashboard.services.remote_project_store import (
    REMOTE_PROJECT_STORE_SCHEMA_VERSION,
    DuplicateRemoteProjectIdError,
    DuplicateRemoteProjectLocationError,
    RemoteProjectStoreVersionError,
    create_remote_project,
    default_remote_project_store_path,
    delete_remote_project,
    get_remote_project,
    host_has_remote_projects,
    list_remote_projects_for_host,
    load_all_remote_projects,
    load_remote_projects_result,
    update_remote_project,
)

HOST_A = "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3"
HOST_B = "d84aeefb-7c29-4c63-b39c-766d559df977"
PROJECT_A = "6cd81f5d-9fe4-4c32-b17f-f88e5db754f4"
PROJECT_B = "760525f1-fdc9-49a7-99fa-2ff90f324bd9"
PROJECT_C = "906d817c-3cc8-44c2-8e98-b51abe716b55"


def _project(identity: str, host: str, name: str, path: str) -> RemoteProjectRegistration:
    return RemoteProjectRegistration(identity, host, name, path)


def test_default_path_xdg_fallback_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "config with spaces"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(root))
    assert default_remote_project_store_path() == root / "terminal-home/remote_projects.json"
    assert load_remote_projects_result().source is LoadSource.DEFAULT
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert (
        default_remote_project_store_path()
        == tmp_path / ".config/terminal-home/remote_projects.json"
    )


def test_crud_order_lookup_host_listing_and_stable_update(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    zulu = create_remote_project(_project(PROJECT_A, HOST_B, "Zulu", "/z"), path)
    alpha = create_remote_project(_project(PROJECT_B, HOST_A, "Alpha", "/a"), path)
    same_name = create_remote_project(_project(PROJECT_C, HOST_A, "Alpha", "/b"), path)
    assert load_all_remote_projects(path) == (alpha, same_name, zulu)
    assert get_remote_project(PROJECT_A, path) == zulu
    assert list_remote_projects_for_host(HOST_A, path) == (alpha, same_name)
    assert host_has_remote_projects(HOST_B, path)
    updated = update_remote_project(PROJECT_A, name="API", remote_path="/api", store_path=path)
    assert updated == _project(PROJECT_A, HOST_B, "API", "/api")
    assert updated.id == PROJECT_A and updated.host_id == HOST_B
    assert delete_remote_project("00000000-0000-0000-0000-000000000000", path) is False
    assert delete_remote_project(PROJECT_A, path) is True


def test_registration_uniqueness_and_same_path_on_other_host(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    create_remote_project(_project(PROJECT_A, HOST_A, "API", "/srv/api"), path)
    with pytest.raises(DuplicateRemoteProjectIdError):
        create_remote_project(_project(PROJECT_A, HOST_B, "Other", "/other"), path)
    with pytest.raises(DuplicateRemoteProjectLocationError):
        create_remote_project(_project(PROJECT_B, HOST_A, "Other", "/srv/api"), path)
    assert create_remote_project(_project(PROJECT_B, HOST_B, "API", "/srv/api"), path)


def test_project_envelope_metadata_only_and_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    create_remote_project(_project(PROJECT_A, HOST_B, "Zulu", "/z"), path)
    create_remote_project(_project(PROJECT_B, HOST_A, "Alpha", "/a"), path)
    data = json.loads(path.read_text())
    assert list(data) == ["schema_version", "projects"]
    assert data["schema_version"] == REMOTE_PROJECT_STORE_SCHEMA_VERSION
    assert [item["id"] for item in data["projects"]] == [PROJECT_B, PROJECT_A]
    assert set(data["projects"][0]) == {"id", "host_id", "name", "remote_path"}


def test_invalid_and_duplicate_records_are_isolated_and_orphans_load(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    orphan = _project(PROJECT_A, HOST_A, "Orphan", "/orphan")
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "projects": [orphan.to_dict(), orphan.to_dict(), {"bad": True}],
            }
        )
    )
    result = load_remote_projects_result(path)
    assert result.projects == (orphan,)
    assert "Skipped 2" in (result.warning or "")


def test_backup_recovery_corrupt_and_future_behavior(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    create_remote_project(_project(PROJECT_A, HOST_A, "One", "/one"), path)
    first = path.read_bytes()
    update_remote_project(PROJECT_A, name="Two", remote_path="/two", store_path=path)
    assert backup_path_for(path).read_bytes() == first
    path.write_text("bad")
    result = load_remote_projects_result(path)
    assert result.source is LoadSource.BACKUP and result.projects[0].name == "One"
    path.write_bytes(b"\xff")
    backup_path_for(path).write_text("bad")
    assert load_remote_projects_result(path).error
    create_remote_project(_project(PROJECT_B, HOST_A, "Replacement", "/replacement"), path)
    future = json.dumps({"schema_version": 2, "projects": []})
    path.write_text(future)
    result = load_remote_projects_result(path)
    assert result.unsupported_version and result.source is LoadSource.PRIMARY
    with pytest.raises(RemoteProjectStoreVersionError):
        create_remote_project(_project(PROJECT_B, HOST_A, "Nope", "/nope"), path)
    with pytest.raises(RemoteProjectStoreVersionError):
        update_remote_project(PROJECT_A, name="Nope", remote_path="/nope", store_path=path)
    with pytest.raises(RemoteProjectStoreVersionError):
        delete_remote_project(PROJECT_A, path)
    assert path.read_text() == future


def test_future_backup_and_atomic_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "projects.json"
    path.write_text("bad")
    backup_path_for(path).write_text(json.dumps({"schema_version": 2, "projects": []}))
    assert load_remote_projects_result(path).error
    path.unlink()
    backup_path_for(path).unlink()
    create_remote_project(_project(PROJECT_A, HOST_A, "One", "/one"), path)
    original = path.read_bytes()

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("write failed")

    monkeypatch.setattr("dashboard.services.remote_project_store.atomic_write_text", fail)
    with pytest.raises(OSError):
        update_remote_project(PROJECT_A, name="Two", remote_path="/two", store_path=path)
    assert path.read_bytes() == original
