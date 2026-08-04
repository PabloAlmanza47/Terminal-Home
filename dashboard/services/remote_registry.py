"""Cross-store integrity operations for local remote-project metadata."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dashboard.models import RemoteProjectRegistration, SshHost
from dashboard.services.remote_project_store import (
    create_remote_project,
    delete_remote_project,
    load_remote_projects_result,
    update_remote_project,
)
from dashboard.services.ssh_host_store import delete_ssh_host, load_ssh_hosts_result


class RemoteRegistryError(Exception):
    """Base class for cross-store integrity errors."""


class MissingReferencedHostError(RemoteRegistryError):
    """Raised when a project references an unknown host."""


class HostStillReferencedError(RemoteRegistryError):
    """Raised when host removal would orphan registered projects."""


class RemoteRegistryUnavailableError(RemoteRegistryError):
    """Raised when store errors prevent a safe integrity decision."""


@dataclass(frozen=True, slots=True)
class RemoteRegistryIntegrityResult:
    hosts: tuple[SshHost, ...]
    projects: tuple[RemoteProjectRegistration, ...]
    orphaned_project_ids: tuple[str, ...]
    host_warning: str | None = None
    host_error: str | None = None
    project_warning: str | None = None
    project_error: str | None = None


def inspect_remote_registry_integrity(
    *, host_store_path: Path | None = None, project_store_path: Path | None = None
) -> RemoteRegistryIntegrityResult:
    """Load both stores and report, but never remove, orphan registrations."""

    host_result = load_ssh_hosts_result(host_store_path)
    project_result = load_remote_projects_result(project_store_path)
    host_ids = {host.id for host in host_result.hosts}
    orphaned = tuple(
        project.id for project in project_result.projects if project.host_id not in host_ids
    )
    return RemoteRegistryIntegrityResult(
        hosts=host_result.hosts,
        projects=project_result.projects,
        orphaned_project_ids=orphaned,
        host_warning=host_result.warning,
        host_error=host_result.error,
        project_warning=project_result.warning,
        project_error=project_result.error,
    )


def _require_host(host_id: str, host_store_path: Path | None) -> None:
    result = load_ssh_hosts_result(host_store_path)
    if result.error:
        raise RemoteRegistryUnavailableError(result.error)
    if not any(host.id == host_id for host in result.hosts):
        raise MissingReferencedHostError(f"SSH host {host_id} is not registered.")


def register_remote_project(
    registration: RemoteProjectRegistration,
    *,
    host_store_path: Path | None = None,
    project_store_path: Path | None = None,
) -> RemoteProjectRegistration:
    _require_host(registration.host_id, host_store_path)
    return create_remote_project(registration, project_store_path)


def update_registered_remote_project(
    registration_id: str,
    *,
    name: str,
    host_id: str | None = None,
    remote_path: str,
    host_store_path: Path | None = None,
    project_store_path: Path | None = None,
) -> RemoteProjectRegistration | None:
    project_result = load_remote_projects_result(project_store_path)
    if project_result.error:
        raise RemoteRegistryUnavailableError(project_result.error)
    target = next(
        (project for project in project_result.projects if project.id == registration_id), None
    )
    if target is None:
        return None
    replacement_host_id = target.host_id if host_id is None else host_id
    host_result = load_ssh_hosts_result(host_store_path)
    if host_result.error:
        raise RemoteRegistryUnavailableError(host_result.error)
    if replacement_host_id != target.host_id:
        _require_host(replacement_host_id, host_store_path)
    return update_remote_project(
        registration_id,
        name=name,
        host_id=replacement_host_id,
        remote_path=remote_path,
        store_path=project_store_path,
    )


def remove_registered_remote_project(
    registration_id: str, *, project_store_path: Path | None = None
) -> bool:
    return delete_remote_project(registration_id, project_store_path)


def remove_ssh_host(
    host_id: str,
    *,
    host_store_path: Path | None = None,
    project_store_path: Path | None = None,
) -> bool:
    project_result = load_remote_projects_result(project_store_path)
    if project_result.error:
        raise RemoteRegistryUnavailableError(project_result.error)
    references = [project for project in project_result.projects if project.host_id == host_id]
    if references:
        raise HostStillReferencedError(
            f"SSH host {host_id} is still referenced by {len(references)} remote project(s)."
        )
    return delete_ssh_host(host_id, host_store_path)
