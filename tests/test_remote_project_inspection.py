"""Focused tests for registered remote project inspection."""

from __future__ import annotations

from pathlib import Path

from dashboard.models import RemoteProjectRegistration, SshHost
from dashboard.services.remote_project_inspection import (
    inspect_registered_remote_project,
)
from dashboard.services.ssh import SshCommandResult, quote_remote_argument
from dashboard.services.ssh_host_store import create_ssh_host

HOST_ID = "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3"
PROJECT_ID = "6cd81f5d-9fe4-4c32-b17f-f88e5db754f4"


def _project(path: str = "/srv/Project With Spaces/$HOME/'quoted'") -> RemoteProjectRegistration:
    return RemoteProjectRegistration(PROJECT_ID, HOST_ID, "API", path)


def _host_store(tmp_path: Path) -> Path:
    path = tmp_path / "hosts.json"
    create_ssh_host(SshHost(HOST_ID, "Development", "user@example.test"), path)
    return path


def _success(stdout: str) -> SshCommandResult:
    return SshCommandResult(
        status="success",
        stdout=stdout,
        stderr="",
        returncode=0,
    )


def test_inspection_uses_one_quoted_read_only_command_and_reports_facts(
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_runner(destination: str, command: str) -> SshCommandResult:
        calls.append((destination, command))
        return _success(
            "terminal-home-inspection-v1\n"
            "path_exists=1\n"
            "path_is_directory=1\n"
            "tmux_available=1\n"
        )

    project = _project()
    result = inspect_registered_remote_project(
        project,
        host_store_path=_host_store(tmp_path),
        runner=fake_runner,
    )

    assert result.status == "inspected"
    assert result.host is not None
    assert result.path_exists is True
    assert result.path_is_directory is True
    assert result.posix_compatible is True
    assert result.tmux_available is True
    assert len(calls) == 1
    assert calls[0][0] == "user@example.test"
    assert "printf" in calls[0][1]
    assert "command -v tmux" in calls[0][1]
    assert quote_remote_argument(project.remote_path) in calls[0][1]


def test_inspection_reports_missing_host_without_ssh_call(tmp_path: Path) -> None:
    calls = 0

    def fail_runner(destination: str, command: str) -> SshCommandResult:
        nonlocal calls
        calls += 1
        raise AssertionError("missing hosts must not invoke SSH")

    result = inspect_registered_remote_project(
        _project("/srv/orphan"),
        host_store_path=tmp_path / "missing-hosts.json",
        runner=fail_runner,
    )

    assert result.status == "missing-host"
    assert result.host is None
    assert result.diagnostic is not None
    assert calls == 0


def test_inspection_maps_transport_failures_and_preserves_diagnostics(
    tmp_path: Path,
) -> None:
    statuses = ("connection-failure", "authentication-failure", "timeout", "missing-ssh")
    host_store = _host_store(tmp_path)

    for status in statuses:
        diagnostic = f"diagnostic for {status}"

        def fake_runner(
                destination: str, 
                command: str, 
                *, 
                _status: str = status
            ) -> SshCommandResult:
            return SshCommandResult(
                status=_status,  # type: ignore[arg-type]
                stdout="bounded stdout",
                stderr="bounded stderr",
                returncode=255,
                error=diagnostic,
            )

        result = inspect_registered_remote_project(
            _project("/srv/api"),
            host_store_path=host_store,
            runner=fake_runner,
        )

        assert result.status == status
        assert result.stdout == "bounded stdout"
        assert result.stderr == "bounded stderr"
        assert result.returncode == 255
        assert result.diagnostic == diagnostic


def test_inspection_reports_missing_or_non_directory_path(tmp_path: Path) -> None:
    def fake_runner(destination: str, command: str) -> SshCommandResult:
        return _success(
            "terminal-home-inspection-v1\n"
            "path_exists=0\n"
            "path_is_directory=0\n"
            "tmux_available=0\n"
        )

    result = inspect_registered_remote_project(
        _project("/srv/missing"),
        host_store_path=_host_store(tmp_path),
        runner=fake_runner,
    )

    assert result.status == "inspected"
    assert result.path_exists is False
    assert result.path_is_directory is False
    assert result.posix_compatible is True
    assert result.tmux_available is False


def test_unrecognized_success_output_is_structured_failure(tmp_path: Path) -> None:
    def fake_runner(destination: str, command: str) -> SshCommandResult:
        return _success("unexpected output\n")

    result = inspect_registered_remote_project(
        _project("/srv/api"),
        host_store_path=_host_store(tmp_path),
        runner=fake_runner,
    )

    assert result.status == "inspection-failure"
    assert result.stdout == "unexpected output\n"
    assert result.path_exists is None
