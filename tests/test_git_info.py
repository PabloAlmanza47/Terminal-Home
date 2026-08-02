"""Tests for git status lookups (dashboard.services.git_info).

subprocess and shutil.which are monkeypatched so these tests never depend
on whether git is actually installed, or run it against a real repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from dashboard.services import git_info as git_info_module
from dashboard.services.git_info import gather_git_info


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def test_not_a_git_repository(tmp_path: Path) -> None:
    info = gather_git_info(tmp_path)
    assert info.is_repo is False
    assert info.branch is None


def test_git_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(git_info_module.shutil, "which", lambda name: None)

    info = gather_git_info(tmp_path)

    assert info.is_repo is True
    assert info.branch is None


def test_reports_current_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(git_info_module.shutil, "which", lambda name: "/usr/bin/git")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=0, stdout="main\n")

    monkeypatch.setattr(git_info_module.subprocess, "run", fake_run)

    info = gather_git_info(tmp_path)

    assert info.is_repo is True
    assert info.branch == "main"


def test_detached_head_reports_no_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(git_info_module.shutil, "which", lambda name: "/usr/bin/git")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=0, stdout="")

    monkeypatch.setattr(git_info_module.subprocess, "run", fake_run)

    info = gather_git_info(tmp_path)

    assert info.is_repo is True
    assert info.branch is None


def test_command_failure_reports_no_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(git_info_module.shutil, "which", lambda name: "/usr/bin/git")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=1, stdout="")

    monkeypatch.setattr(git_info_module.subprocess, "run", fake_run)

    info = gather_git_info(tmp_path)

    assert info.is_repo is True
    assert info.branch is None


def test_timeout_reports_no_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(git_info_module.shutil, "which", lambda name: "/usr/bin/git")

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(cmd="git", timeout=3)

    monkeypatch.setattr(git_info_module.subprocess, "run", fake_run)

    info = gather_git_info(tmp_path)

    assert info.is_repo is True
    assert info.branch is None
