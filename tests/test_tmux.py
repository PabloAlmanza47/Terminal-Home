"""Tests for tmux session parsing (dashboard.services.tmux).

subprocess and shutil.which are monkeypatched so these tests don't depend
on whether tmux -- or any tmux sessions -- actually exist on the runner.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

from dashboard.services import tmux as tmux_module
from dashboard.services.tmux import (
    TmuxSession,
    get_tmux_version,
    is_tmux_installed,
    list_tmux_sessions,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


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
