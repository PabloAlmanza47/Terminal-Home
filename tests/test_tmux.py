"""Tests for tmux session parsing (dashboard.services.tmux).

subprocess and shutil.which are monkeypatched so these tests don't depend
on whether tmux -- or any tmux sessions -- actually exist on the runner.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from dashboard.services import tmux as tmux_module
from dashboard.services.ssh import SshCommandResult, quote_remote_argument
from dashboard.services.tmux import (
    SshTmuxCommandRunner,
    TmuxSession,
    get_tmux_version,
    is_tmux_installed,
    list_tmux_panes,
    list_tmux_sessions,
    run_local_tmux_command,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_is_tmux_installed_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: "/usr/bin/tmux")
    assert is_tmux_installed() is True


def test_is_tmux_installed_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: None)
    assert is_tmux_installed() is False


def test_list_sessions_when_tmux_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: None)
    assert list_tmux_sessions() == []


def test_list_sessions_when_no_server_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: "/usr/bin/tmux")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=1, stdout="")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)
    assert list_tmux_sessions() == []


def test_list_sessions_parses_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: "/usr/bin/tmux")
    fake_stdout = (
        "work\t3\tMon Aug  1 09:00:00 2026\t1\n"
        "side-project\t1\tMon Aug  1 10:15:00 2026\t0\n"
    )

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=0, stdout=fake_stdout)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    sessions = list_tmux_sessions()

    assert sessions == [
        TmuxSession(name="work", windows=3, created="Mon Aug  1 09:00:00 2026", attached=True),
        TmuxSession(
            name="side-project", windows=1, created="Mon Aug  1 10:15:00 2026", attached=False
        ),
    ]


def test_list_sessions_handles_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: "/usr/bin/tmux")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(cmd="tmux", timeout=3)

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)
    assert list_tmux_sessions() == []


def test_get_tmux_version_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: None)
    assert get_tmux_version() is None


def test_get_tmux_version_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: "/usr/bin/tmux")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=0, stdout="tmux 3.4\n")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)
    assert get_tmux_version() == "tmux 3.4"


def test_local_runner_preserves_argv_output_and_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        calls.append((args[0], kwargs))
        return _FakeCompletedProcess(returncode=4, stdout="out", stderr="err")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    result = run_local_tmux_command(["tmux", "display-message", "name with spaces"])

    assert result.returncode == 4
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert calls == [
        (
            ["tmux", "display-message", "name with spaces"],
            {"capture_output": True, "text": True, "timeout": 3},
        )
    ]


def test_list_sessions_accepts_fake_runner_without_running_local_tmux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: None)
    calls: list[list[str]] = []

    def fake_runner(argv: list[str]) -> _FakeCompletedProcess:
        calls.append(argv)
        return _FakeCompletedProcess(stdout="fake\t1\tcreated\t0\n")

    assert list_tmux_sessions(runner=fake_runner) == [
        TmuxSession(name="fake", windows=1, created="created", attached=False)
    ]
    assert calls == [["tmux", "list-sessions", "-F", tmux_module._LIST_FORMAT]]


def test_existing_query_callers_work_without_a_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module.shutil, "which", lambda name: "/usr/bin/tmux")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        if args[0] == ["tmux", "-V"]:
            return _FakeCompletedProcess(stdout="tmux 3.4\n")
        return _FakeCompletedProcess(stdout="")

    monkeypatch.setattr(tmux_module.subprocess, "run", fake_run)

    assert get_tmux_version() == "tmux 3.4"
    assert list_tmux_sessions() == []


def test_list_panes_parses_runtime_fields() -> None:
    def fake_runner(argv: list[str]) -> _FakeCompletedProcess:
        assert argv[1:3] == ["list-panes", "-a"]
        return _FakeCompletedProcess(
            stdout="demo\tmain\tserver\tnpm\t0\n"
            "demo\tmain\tdead-server\tnpm\t1\n"
            "broken\n"
        )

    assert list_tmux_panes(runner=fake_runner) == [
        tmux_module.TmuxPaneRuntime("demo", "main", "server", "npm", False),
        tmux_module.TmuxPaneRuntime("demo", "main", "dead-server", "npm", True),
    ]


def test_ssh_runner_quotes_each_tmux_argument_and_keeps_destination_separate() -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def fake_ssh(destination: str, command: str, **options: object) -> SshCommandResult:
        calls.append((destination, command, options))
        return SshCommandResult(status="success", stdout="out", stderr="err", returncode=0)

    runner = SshTmuxCommandRunner("user@remote.example", ssh_runner=fake_ssh)
    argv = ["tmux", "send-keys", "-t", "pane with spaces", "echo 'quoted' && $HOME", "Enter"]

    result = runner(argv)

    assert result.args == argv
    assert result.returncode == 0
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert calls == [
        (
            "user@remote.example",
            " ".join(
                quote_remote_argument(argument)
                for argument in [
                    "tmux",
                    "send-keys",
                    "-t",
                    "pane with spaces",
                    "echo 'quoted' && $HOME",
                    "Enter",
                ]
            ),
            {"connection_timeout": 3, "execution_timeout": 3},
        )
    ]


@pytest.mark.parametrize(
    ("status", "returncode"),
    [
        ("connection-failure", 255),
        ("authentication-failure", 255),
        ("command-failure", 17),
    ],
)
def test_ssh_runner_preserves_remote_failure_exit_codes(
    status: str,
    returncode: int,
) -> None:
    def fake_ssh(destination: str, command: str, **options: object) -> SshCommandResult:
        return SshCommandResult(
            status=status,  # type: ignore[arg-type]
            stdout="remote out",
            stderr="remote err",
            returncode=returncode,
        )

    result = SshTmuxCommandRunner("host", ssh_runner=fake_ssh)(["tmux", "-V"])

    assert result.returncode == returncode
    assert result.stdout == "remote out"
    assert result.stderr == "remote err"


def test_ssh_runner_preserves_timeout_exception() -> None:
    def fake_ssh(destination: str, command: str, **options: object) -> SshCommandResult:
        return SshCommandResult(
            status="timeout",
            stdout="partial out",
            stderr="partial err",
            returncode=None,
        )

    with pytest.raises(subprocess.TimeoutExpired) as raised:
        SshTmuxCommandRunner("host", execution_timeout=2, ssh_runner=fake_ssh)(
            ["tmux", "list-sessions"]
        )

    assert raised.value.timeout == 2
    assert raised.value.output == "partial out"
    assert raised.value.stderr == "partial err"


def test_ssh_runner_preserves_missing_ssh_exception() -> None:
    def fake_ssh(destination: str, command: str, **options: object) -> SshCommandResult:
        return SshCommandResult(
            status="missing-ssh",
            stdout="",
            stderr="",
            returncode=None,
            error="ssh missing",
        )

    with pytest.raises(FileNotFoundError, match="ssh missing"):
        SshTmuxCommandRunner("host", ssh_runner=fake_ssh)(["tmux", "-V"])


def test_ssh_runner_rejects_missing_exit_code_for_other_failures() -> None:
    def fake_ssh(destination: str, command: str, **options: object) -> SshCommandResult:
        return SshCommandResult(
            status="connection-failure",
            stdout="",
            stderr="connection failed",
            returncode=None,
            error="connection failed",
        )

    with pytest.raises(tmux_module.TmuxCommandError, match="connection failed"):
        SshTmuxCommandRunner("host", ssh_runner=fake_ssh)(["tmux", "-V"])
