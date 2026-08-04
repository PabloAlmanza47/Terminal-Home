"""Read-only inspection of manually registered SSH projects."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dashboard.models import RemoteProjectRegistration, SshHost
from dashboard.services.ssh import SshCommandResult, quote_remote_argument, run_ssh_command
from dashboard.services.ssh_host_store import get_ssh_host

RemoteProjectInspectionStatus = Literal[
    "inspected",
    "missing-host",
    "connection-failure",
    "authentication-failure",
    "timeout",
    "missing-ssh",
    "inspection-failure",
]


@dataclass(frozen=True, slots=True)
class RemoteProjectInspectionResult:
    """Read-only facts and bounded diagnostics for one registered project."""

    status: RemoteProjectInspectionStatus
    project: RemoteProjectRegistration
    host: SshHost | None
    path_exists: bool | None
    path_is_directory: bool | None
    posix_compatible: bool | None
    tmux_available: bool | None
    stdout: str
    stderr: str
    returncode: int | None
    diagnostic: str | None = None


InspectionRunner = Callable[[str, str], SshCommandResult]


def inspect_registered_remote_project(
    project: RemoteProjectRegistration,
    *,
    host_store_path: Path | None = None,
    runner: InspectionRunner | None = None,
) -> RemoteProjectInspectionResult:
    """Inspect a registered remote project with one read-only SSH command.

    The host is resolved from the existing SSH host store.  An absent host is
    returned as ``missing-host`` so orphaned registrations remain inspectable.
    The optional runner exists only for tests and service-level composition.
    """
    host = get_ssh_host(project.host_id, host_store_path)
    if host is None:
        return _result_for_missing_host(project)

    command = _inspection_command(project.remote_path)
    ssh_result = (runner or _run_inspection)(host.destination, command)
    return _result_from_ssh(project, host, ssh_result)


def _run_inspection(destination: str, command: str) -> SshCommandResult:
    return run_ssh_command(destination, command)


def _inspection_command(remote_path: str) -> str:
    quoted_path = quote_remote_argument(remote_path)
    return (
        "printf '%s\\n' 'terminal-home-inspection-v1'; "
        f"if [ -e {quoted_path} ]; then printf '%s\\n' 'path_exists=1'; "
        "else printf '%s\\n' 'path_exists=0'; fi; "
        f"if [ -d {quoted_path} ]; then printf '%s\\n' 'path_is_directory=1'; "
        "else printf '%s\\n' 'path_is_directory=0'; fi; "
        "if command -v tmux >/dev/null 2>&1; then "
        "printf '%s\\n' 'tmux_available=1'; "
        "else printf '%s\\n' 'tmux_available=0'; fi"
    )


def _result_for_missing_host(project: RemoteProjectRegistration) -> RemoteProjectInspectionResult:
    return RemoteProjectInspectionResult(
        status="missing-host",
        project=project,
        host=None,
        path_exists=None,
        path_is_directory=None,
        posix_compatible=None,
        tmux_available=None,
        stdout="",
        stderr="",
        returncode=None,
        diagnostic=f"SSH host {project.host_id} is not registered.",
    )


def _result_from_ssh(
    project: RemoteProjectRegistration,
    host: SshHost,
    ssh_result: SshCommandResult,
) -> RemoteProjectInspectionResult:
    if ssh_result.status != "success":
        status: RemoteProjectInspectionStatus
        if ssh_result.status in {
            "connection-failure",
            "authentication-failure",
            "timeout",
            "missing-ssh",
        }:
            status = ssh_result.status
        else:
            status = "inspection-failure"
        return RemoteProjectInspectionResult(
            status=status,
            project=project,
            host=host,
            path_exists=None,
            path_is_directory=None,
            posix_compatible=None,
            tmux_available=None,
            stdout=ssh_result.stdout,
            stderr=ssh_result.stderr,
            returncode=ssh_result.returncode,
            diagnostic=ssh_result.error,
        )

    facts = _parse_inspection_output(ssh_result.stdout)
    if facts is None:
        return RemoteProjectInspectionResult(
            status="inspection-failure",
            project=project,
            host=host,
            path_exists=None,
            path_is_directory=None,
            posix_compatible=None,
            tmux_available=None,
            stdout=ssh_result.stdout,
            stderr=ssh_result.stderr,
            returncode=ssh_result.returncode,
            diagnostic="SSH inspection returned an unrecognized response.",
        )

    return RemoteProjectInspectionResult(
        status="inspected",
        project=project,
        host=host,
        path_exists=facts["path_exists"],
        path_is_directory=facts["path_is_directory"],
        posix_compatible=True,
        tmux_available=facts["tmux_available"],
        stdout=ssh_result.stdout,
        stderr=ssh_result.stderr,
        returncode=ssh_result.returncode,
    )


def _parse_inspection_output(output: str) -> dict[str, bool] | None:
    lines = output.splitlines()
    if not lines or lines[0] != "terminal-home-inspection-v1":
        return None
    facts: dict[str, bool] = {}
    for line in lines[1:]:
        key, separator, value = line.partition("=")
        if not separator or key not in {"path_exists", "path_is_directory", "tmux_available"}:
            continue
        if value not in {"0", "1"}:
            return None
        facts[key] = value == "1"
    required = {"path_exists", "path_is_directory", "tmux_available"}
    return facts if set(facts) == required else None
