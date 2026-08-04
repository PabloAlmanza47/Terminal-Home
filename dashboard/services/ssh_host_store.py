"""Versioned local configuration storage for SSH host metadata."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dashboard.models import SshHost, SshModelValidationError
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource

SSH_HOST_STORE_SCHEMA_VERSION = 1
_APP_DIR_NAME = "terminal-home"
_STORE_FILENAME = "ssh_hosts.json"


class SshHostStoreError(Exception):
    """Base class for user-facing SSH host store errors."""


class DuplicateSshHostIdError(SshHostStoreError):
    """Raised when an SSH host ID is already stored."""


class DuplicateSshHostNameError(SshHostStoreError):
    """Raised when an SSH host display name is already stored."""


class SshHostStoreVersionError(SshHostStoreError):
    """Raised when a newer store version blocks a mutation."""


class _CorruptStoreError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class SshHostLoadResult:
    hosts: tuple[SshHost, ...]
    source: LoadSource = LoadSource.DEFAULT
    warning: str | None = None
    error: str | None = None
    unsupported_version: bool = False


def default_ssh_host_store_path() -> Path:
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _parse_file(path: Path) -> list[object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _CorruptStoreError from exc
    if not isinstance(data, dict):
        raise _CorruptStoreError
    version = data.get("schema_version")
    if isinstance(version, int) and not isinstance(version, bool):
        if version > SSH_HOST_STORE_SCHEMA_VERSION:
            raise SshHostStoreVersionError(
                f"SSH host store schema version {version} is newer than this version "
                "of Terminal Home supports."
            )
    if (
        version != SSH_HOST_STORE_SCHEMA_VERSION
        or isinstance(version, bool)
        or not isinstance(data.get("hosts"), list)
    ):
        raise _CorruptStoreError
    return data["hosts"]


def _sort(hosts: list[SshHost]) -> tuple[SshHost, ...]:
    return tuple(sorted(hosts, key=lambda item: (item.display_name.casefold(), item.id)))


def load_ssh_hosts_result(store_path: Path | None = None) -> SshHostLoadResult:
    path = store_path or default_ssh_host_store_path()
    if not path.exists():
        return SshHostLoadResult(())
    try:
        raw = _parse_file(path)
        source = LoadSource.PRIMARY
        warning = None
    except SshHostStoreVersionError as exc:
        return SshHostLoadResult((), LoadSource.PRIMARY, error=str(exc), unsupported_version=True)
    except _CorruptStoreError:
        backup = backup_path_for(path)
        try:
            raw = _parse_file(backup)
        except SshHostStoreVersionError as exc:
            return SshHostLoadResult((), error=f"SSH host backup cannot be loaded: {exc}")
        except _CorruptStoreError:
            return SshHostLoadResult(
                (),
                error=(
                    f"SSH host store {path} could not be loaded, and no valid backup is available."
                ),
            )
        source = LoadSource.BACKUP
        warning = f"Recovered SSH host data from {backup} because {path} could not be loaded."

    hosts: list[SshHost] = []
    invalid = 0
    ids: set[str] = set()
    names: set[str] = set()
    for value in raw:
        if not isinstance(value, dict):
            invalid += 1
            continue
        try:
            host = SshHost.from_dict(value)
        except (KeyError, TypeError, SshModelValidationError):
            invalid += 1
            continue
        folded = host.display_name.casefold()
        if host.id in ids or folded in names:
            invalid += 1
            continue
        ids.add(host.id)
        names.add(folded)
        hosts.append(host)
    if invalid:
        suffix = f"Skipped {invalid} invalid SSH host record(s)."
        warning = f"{warning} {suffix}" if warning else suffix
    return SshHostLoadResult(_sort(hosts), source, warning)


def load_all_ssh_hosts(store_path: Path | None = None) -> tuple[SshHost, ...]:
    return load_ssh_hosts_result(store_path).hosts


def get_ssh_host(host_id: str, store_path: Path | None = None) -> SshHost | None:
    return next((host for host in load_all_ssh_hosts(store_path) if host.id == host_id), None)


def find_ssh_host_by_name(name: str, store_path: Path | None = None) -> SshHost | None:
    if not isinstance(name, str):
        return None
    folded = name.strip().casefold()
    return next(
        (host for host in load_all_ssh_hosts(store_path) if host.display_name.casefold() == folded),
        None,
    )


def _load_for_write(path: Path) -> list[SshHost]:
    if not path.exists():
        return []
    try:
        _parse_file(path)
    except SshHostStoreVersionError:
        raise
    except _CorruptStoreError:
        result = load_ssh_hosts_result(path)
        return list(result.hosts)
    result = load_ssh_hosts_result(path)
    if result.error:
        raise SshHostStoreError(result.error)
    return list(result.hosts)


def _write(path: Path, hosts: list[SshHost]) -> None:
    envelope = {
        "schema_version": SSH_HOST_STORE_SCHEMA_VERSION,
        "hosts": [host.to_dict() for host in _sort(hosts)],
    }
    serialized = json.dumps(envelope, indent=2)
    json.loads(serialized)
    preserve = False
    if path.exists():
        try:
            _parse_file(path)
        except _CorruptStoreError:
            pass
        else:
            preserve = True
    atomic_write_text(path, serialized, preserve_existing=preserve)


def create_ssh_host(host: SshHost, store_path: Path | None = None) -> SshHost:
    path = store_path or default_ssh_host_store_path()
    hosts = _load_for_write(path)
    if any(item.id == host.id for item in hosts):
        raise DuplicateSshHostIdError(f"An SSH host with ID {host.id} already exists.")
    if any(item.display_name.casefold() == host.display_name.casefold() for item in hosts):
        raise DuplicateSshHostNameError(f'An SSH host named "{host.display_name}" already exists.')
    hosts.append(host)
    _write(path, hosts)
    return host


def update_ssh_host(
    host_id: str,
    *,
    display_name: str,
    destination: str,
    store_path: Path | None = None,
) -> SshHost | None:
    path = store_path or default_ssh_host_store_path()
    hosts = _load_for_write(path)
    target = next((item for item in hosts if item.id == host_id), None)
    if target is None:
        return None
    replacement = SshHost(target.id, display_name, destination)
    if any(
        item.id != target.id and item.display_name.casefold() == replacement.display_name.casefold()
        for item in hosts
    ):
        raise DuplicateSshHostNameError(
            f'An SSH host named "{replacement.display_name}" already exists.'
        )
    _write(path, [replacement if item.id == target.id else item for item in hosts])
    return replacement


def delete_ssh_host(host_id: str, store_path: Path | None = None) -> bool:
    path = store_path or default_ssh_host_store_path()
    hosts = _load_for_write(path)
    remaining = [host for host in hosts if host.id != host_id]
    if len(remaining) == len(hosts):
        return False
    _write(path, remaining)
    return True
