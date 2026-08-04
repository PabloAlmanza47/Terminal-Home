from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.models import RemoteProjectRegistration, SshHost
from dashboard.services.remote_project_store import (
    create_remote_project,
    delete_remote_project,
    get_remote_project,
)
from dashboard.services.remote_registry import (
    HostStillReferencedError,
    MissingReferencedHostError,
    RemoteRegistryUnavailableError,
    inspect_remote_registry_integrity,
    register_remote_project,
    remove_ssh_host,
    update_registered_remote_project,
)
from dashboard.services.ssh_host_store import create_ssh_host, get_ssh_host, update_ssh_host

HOST = "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3"
MISSING_HOST = "d84aeefb-7c29-4c63-b39c-766d559df977"
PROJECT = "6cd81f5d-9fe4-4c32-b17f-f88e5db754f4"


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "hosts.json", tmp_path / "projects.json"


def test_register_update_and_remove_with_reference_integrity(tmp_path: Path) -> None:
    hosts, projects = _paths(tmp_path)
    create_ssh_host(SshHost(HOST, "Pi", "pi-dev"), hosts)
    registration = RemoteProjectRegistration(PROJECT, HOST, "API", "/srv/api")
    assert (
        register_remote_project(registration, host_store_path=hosts, project_store_path=projects)
        == registration
    )
    updated = update_registered_remote_project(
        PROJECT,
        name="API Server",
        remote_path="/srv/api-server",
        host_store_path=hosts,
        project_store_path=projects,
    )
    assert updated is not None and updated.id == PROJECT and updated.host_id == HOST
    with pytest.raises(HostStillReferencedError):
        remove_ssh_host(HOST, host_store_path=hosts, project_store_path=projects)
    assert get_ssh_host(HOST, hosts) is not None
    assert delete_remote_project(PROJECT, projects)
    assert remove_ssh_host(HOST, host_store_path=hosts, project_store_path=projects)


def test_missing_host_is_rejected_without_project_write(tmp_path: Path) -> None:
    hosts, projects = _paths(tmp_path)
    registration = RemoteProjectRegistration(PROJECT, MISSING_HOST, "API", "/srv/api")
    with pytest.raises(MissingReferencedHostError):
        register_remote_project(registration, host_store_path=hosts, project_store_path=projects)
    assert not projects.exists()


def test_host_update_preserves_references(tmp_path: Path) -> None:
    hosts, projects = _paths(tmp_path)
    create_ssh_host(SshHost(HOST, "Pi", "pi-dev"), hosts)
    registration = RemoteProjectRegistration(PROJECT, HOST, "API", "/srv/api")
    register_remote_project(registration, host_store_path=hosts, project_store_path=projects)
    updated = update_ssh_host(
        HOST, display_name="Pi Development", destination="user@pi", store_path=hosts
    )
    result = inspect_remote_registry_integrity(host_store_path=hosts, project_store_path=projects)
    assert updated is not None and updated.id == HOST
    assert result.orphaned_project_ids == ()


def test_orphans_remain_loaded_and_are_reported(tmp_path: Path) -> None:
    hosts, projects = _paths(tmp_path)
    orphan = RemoteProjectRegistration(PROJECT, MISSING_HOST, "Orphan", "/srv/orphan")
    create_remote_project(orphan, projects)
    result = inspect_remote_registry_integrity(host_store_path=hosts, project_store_path=projects)
    assert result.projects == (orphan,)
    assert result.orphaned_project_ids == (PROJECT,)
    assert get_remote_project(PROJECT, projects) == orphan


def test_unavailable_stores_block_integrity_sensitive_operations(tmp_path: Path) -> None:
    hosts, projects = _paths(tmp_path)
    hosts.write_text(json.dumps({"schema_version": 2, "hosts": []}))
    registration = RemoteProjectRegistration(PROJECT, HOST, "API", "/srv/api")
    with pytest.raises(RemoteRegistryUnavailableError):
        register_remote_project(registration, host_store_path=hosts, project_store_path=projects)
    hosts.write_text(json.dumps({"schema_version": 1, "hosts": []}))
    projects.write_text("bad")
    with pytest.raises(RemoteRegistryUnavailableError):
        remove_ssh_host(HOST, host_store_path=hosts, project_store_path=projects)


def test_registry_operations_do_not_touch_unrelated_paths(tmp_path: Path) -> None:
    hosts, projects = _paths(tmp_path)
    unrelated = tmp_path / "workspaces.json"
    unrelated.write_text("unchanged")
    create_ssh_host(SshHost(HOST, "Pi", "pi-dev"), hosts)
    register_remote_project(
        RemoteProjectRegistration(PROJECT, HOST, "API", "/srv/api"),
        host_store_path=hosts,
        project_store_path=projects,
    )
    assert unrelated.read_text() == "unchanged"
