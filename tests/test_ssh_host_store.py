from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.models import SshHost
from dashboard.services.atomic_file import backup_path_for
from dashboard.services.load_result import LoadSource
from dashboard.services.ssh_host_store import (
    SSH_HOST_STORE_SCHEMA_VERSION,
    DuplicateSshHostIdError,
    DuplicateSshHostNameError,
    SshHostStoreVersionError,
    create_ssh_host,
    default_ssh_host_store_path,
    delete_ssh_host,
    find_ssh_host_by_name,
    get_ssh_host,
    load_all_ssh_hosts,
    load_ssh_hosts_result,
    update_ssh_host,
)

HOST_A = "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3"
HOST_B = "d84aeefb-7c29-4c63-b39c-766d559df977"


def _host(identity: str, name: str, destination: str = "pi-dev") -> SshHost:
    return SshHost(identity, name, destination)


def test_default_path_xdg_fallback_and_missing_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "config with spaces"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    assert default_ssh_host_store_path() == xdg / "terminal-home" / "ssh_hosts.json"
    assert load_ssh_hosts_result().source is LoadSource.DEFAULT
    assert not default_ssh_host_store_path().exists()
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert default_ssh_host_store_path() == tmp_path / ".config/terminal-home/ssh_hosts.json"


def test_create_lookup_order_update_case_only_and_delete(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "ssh_hosts.json"
    zulu = create_ssh_host(_host(HOST_A, "zulu"), path)
    alpha = create_ssh_host(_host(HOST_B, "Alpha", "example.com"), path)
    assert load_all_ssh_hosts(path) == (alpha, zulu)
    assert get_ssh_host(HOST_A, path) == zulu
    assert find_ssh_host_by_name(" ALPHA ", path) == alpha
    updated = update_ssh_host(
        HOST_B, display_name="aLPHA", destination="user@example.com", store_path=path
    )
    assert updated == SshHost(HOST_B, "aLPHA", "user@example.com")
    assert updated.id == alpha.id
    assert delete_ssh_host("00000000-0000-0000-0000-000000000000", path) is False
    assert delete_ssh_host(HOST_A, path) is True


def test_host_uniqueness_rules(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    create_ssh_host(_host(HOST_A, "Pi"), path)
    with pytest.raises(DuplicateSshHostIdError):
        create_ssh_host(_host(HOST_A, "Other"), path)
    with pytest.raises(DuplicateSshHostNameError):
        create_ssh_host(_host(HOST_B, "pI", "other-alias"), path)
    create_ssh_host(_host(HOST_B, "Other", "pi-dev"), path)
    with pytest.raises(DuplicateSshHostNameError):
        update_ssh_host(HOST_B, display_name="PI", destination="other-alias", store_path=path)


def test_host_envelope_is_deterministic_and_contains_no_credentials(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    create_ssh_host(_host(HOST_A, "Zulu"), path)
    create_ssh_host(_host(HOST_B, "alpha"), path)
    data = json.loads(path.read_text())
    assert list(data) == ["schema_version", "hosts"]
    assert data["schema_version"] == SSH_HOST_STORE_SCHEMA_VERSION
    assert [item["display_name"] for item in data["hosts"]] == ["alpha", "Zulu"]
    assert set(data["hosts"][0]) == {"id", "display_name", "destination"}


def test_invalid_and_duplicate_host_records_are_isolated(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    valid = _host(HOST_A, "Pi")
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "hosts": [valid.to_dict(), valid.to_dict(), {"password": "secret"}],
            }
        )
    )
    original = path.read_bytes()
    result = load_ssh_hosts_result(path)
    assert result.hosts == (valid,)
    assert "Skipped 2" in (result.warning or "")
    assert path.read_bytes() == original


def test_backup_rotation_and_corrupt_primary_recovery_without_repair(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    create_ssh_host(_host(HOST_A, "One"), path)
    first = path.read_bytes()
    update_ssh_host(HOST_A, display_name="Two", destination="pi-two", store_path=path)
    assert backup_path_for(path).read_bytes() == first
    path.write_bytes(b"not json")
    corrupt = path.read_bytes()
    backup = backup_path_for(path).read_bytes()
    result = load_ssh_hosts_result(path)
    assert result.source is LoadSource.BACKUP
    assert result.hosts[0].display_name == "One"
    assert path.read_bytes() == corrupt
    assert backup_path_for(path).read_bytes() == backup


def test_corrupt_or_future_stores_are_preserved(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    path.write_bytes(b"\xff")
    assert load_ssh_hosts_result(path).error
    create_ssh_host(_host(HOST_A, "Replacement"), path)
    assert not backup_path_for(path).exists()
    future = json.dumps({"schema_version": 2, "hosts": []})
    path.write_text(future)
    backup_path_for(path).write_text(json.dumps({"schema_version": 1, "hosts": []}))
    result = load_ssh_hosts_result(path)
    assert result.unsupported_version and result.source is LoadSource.PRIMARY
    with pytest.raises(SshHostStoreVersionError):
        create_ssh_host(_host(HOST_A, "Nope"), path)
    with pytest.raises(SshHostStoreVersionError):
        update_ssh_host(HOST_A, display_name="Nope", destination="nope", store_path=path)
    with pytest.raises(SshHostStoreVersionError):
        delete_ssh_host(HOST_A, path)
    assert path.read_text() == future


def test_future_backup_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "hosts.json"
    path.write_text("bad")
    backup_path_for(path).write_text(json.dumps({"schema_version": 2, "hosts": []}))
    result = load_ssh_hosts_result(path)
    assert result.hosts == () and result.error


def test_atomic_write_failure_preserves_valid_primary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "hosts.json"
    create_ssh_host(_host(HOST_A, "One"), path)
    original = path.read_bytes()

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("write failed")

    monkeypatch.setattr("dashboard.services.ssh_host_store.atomic_write_text", fail)
    with pytest.raises(OSError, match="write failed"):
        update_ssh_host(HOST_A, display_name="Two", destination="two", store_path=path)
    assert path.read_bytes() == original
