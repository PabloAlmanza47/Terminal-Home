"""Focused tests for the noninteractive SSH transport."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

import pytest

from dashboard.services import ssh as ssh_module
from dashboard.services.ssh import (
    quote_remote_argument,
    represent_remote_command,
    run_interactive_ssh,
    run_ssh_command,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_runs_noninteractive_ssh_command_with_bounded_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        calls.append((args, kwargs))
        return _FakeCompletedProcess(7, "output", "remote error")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_ssh_command(
        "deploy@example.test",
        "printf 'hello world'",
        connection_timeout=12,
        execution_timeout=4.5,
        max_output_chars=4,
    )

    assert result.status == "command-failure"
    assert result.returncode == 7
    assert result.stdout == "outp"
    assert result.stderr == "remo"
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert result.succeeded is False
    assert calls == [
        (
            (
                [
                    "ssh",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=12",
                    "deploy@example.test",
                    "printf 'hello world'",
                ],
            ),
            {"capture_output": True, "text": True, "timeout": 4.5},
        )
    ]


def test_missing_ssh_is_structured_without_running_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: None)

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess should not run when ssh is missing")

    monkeypatch.setattr(ssh_module.subprocess, "run", fail_if_called)

    result = run_ssh_command("host", "pwd")

    assert result.status == "missing-ssh"
    assert result.returncode is None
    assert result.error == "The ssh executable was not found on PATH."


def test_execution_timeout_is_structured_and_output_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(
            cmd=args[0], timeout=2, output=b"abcdef", stderr=b"uvwxyz"
        )

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_ssh_command("host", "pwd", execution_timeout=2, max_output_chars=3)

    assert result.status == "timeout"
    assert result.returncode is None
    assert result.stdout == "abc"
    assert result.stderr == "uvw"
    assert result.stdout_truncated is True
    assert result.stderr_truncated is True
    assert result.error == "SSH command timed out after 2 seconds."


def test_file_not_found_during_start_is_handled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        raise FileNotFoundError("ssh")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_ssh_command("host", "pwd")

    assert result.status == "missing-ssh"
    assert result.error == "The ssh executable could not be started."


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected_status"),
    [
        (0, "", "success"),
        (1, "bash: line 1: missing-command: command not found", "command-failure"),
        (
            255,
            "ssh: connect to host example.test port 22: Connection refused",
            "connection-failure"
        ),
        (255, "Permission denied (publickey,password).", "authentication-failure"),
    ],
)
def test_classifies_common_ssh_results(
    monkeypatch: pytest.MonkeyPatch,
    returncode: int,
    stderr: str,
    expected_status: str,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode, "remote output", stderr)

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_ssh_command("example.test", "printf 'remote output'")

    assert result.status == expected_status
    assert result.returncode == returncode
    assert result.stdout == "remote output"
    assert result.stderr == stderr


def test_connection_error_without_standard_stderr_is_classified_by_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(255, "", "")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_ssh_command("example.test", "pwd")

    assert result.status == "connection-failure"
    assert result.returncode == 255


def test_remote_command_is_preserved_as_one_argv_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")
    captured_argv: list[str] = []

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        captured_argv.extend(args[0])
        return _FakeCompletedProcess(0, "", "")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)
    command = "printf '%s' \"spaces 'quotes' && $HOME; *.txt\""

    result = run_ssh_command("user@example.test", command)

    assert result.succeeded is True
    assert captured_argv[-1] == command
    assert len(captured_argv) == 7


@pytest.mark.parametrize(
    "command",
    [
        "echo hello world",
        "printf \"quoted value\"",
        "test -n '$HOME' && printf '; | &&'",
    ],
)
def test_remote_command_representation_is_safe_for_display(command: str) -> None:
    represented = represent_remote_command(command)

    assert represented == shlex.quote(command)
    assert represented != command


@pytest.mark.parametrize("request_tty", [True, False])
def test_interactive_ssh_builds_argv_and_inherits_terminal_streams(
    monkeypatch: pytest.MonkeyPatch,
    request_tty: bool,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        calls.append((args, kwargs))
        return _FakeCompletedProcess(0, "ignored", "ignored")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_interactive_ssh(
        "space host@example.test",
        "printf '%s' \"quotes && $HOME; *.txt\"",
        request_tty=request_tty,
        connection_timeout=14,
    )

    expected_argv = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=14",
    ]
    if request_tty:
        expected_argv.append("-t")
    expected_argv.extend(
        ["space host@example.test", "printf '%s' \"quotes && $HOME; *.txt\""]
    )

    assert result.succeeded is True
    assert calls == [
        (
            (expected_argv,),
            {"stdin": None, "stdout": None, "stderr": None},
        )
    ]


def test_interactive_ssh_preserves_nonzero_exit_and_classifies_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(23, "ignored", "ignored")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_interactive_ssh("host", "false", request_tty=False)

    assert result.status == "command-failure"
    assert result.returncode == 23
    assert result.succeeded is False


def test_interactive_ssh_classifies_ssh_connection_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(255, "ignored", "ignored")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)

    result = run_interactive_ssh("host", "pwd")

    assert result.status == "connection-failure"
    assert result.returncode == 255


def test_interactive_ssh_handles_missing_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: None)

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess should not run when ssh is missing")

    monkeypatch.setattr(ssh_module.subprocess, "run", fail_if_called)

    result = run_interactive_ssh("host", "pwd")

    assert result.status == "missing-ssh"
    assert result.returncode is None
    assert result.error == "The ssh executable was not found on PATH."


def test_structured_remote_arguments_can_be_composed_without_local_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: "/usr/bin/ssh")
    captured_argv: list[str] = []

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        captured_argv.extend(args[0])
        return _FakeCompletedProcess(0, "ignored", "ignored")

    monkeypatch.setattr(ssh_module.subprocess, "run", fake_run)
    remote_path = "/srv/Project With Spaces/$HOME"
    command = "cd " + quote_remote_argument(remote_path) + " && printf '%s' 'ok'"
    destination = "user@host with spaces"

    run_interactive_ssh(destination, command, request_tty=False)

    assert captured_argv[-2:] == [destination, command]
    assert captured_argv[-1] != remote_path
