"""Tests for dashboard.services.project_creation: validation, destination
resolution, directory creation, and git init. subprocess is mocked so these
never depend on a real git binary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from dashboard.services import project_creation as project_creation_module
from dashboard.services.project_creation import (
    NewProjectValidation,
    ProjectCreationError,
    create_project_directory,
    init_git_repo,
    resolve_destination,
    validate_folder_name,
    validate_new_project,
    validate_project_name,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- validate_project_name / validate_folder_name -----------------------------


def test_validate_project_name_rejects_empty() -> None:
    assert validate_project_name("") is not None
    assert validate_project_name("   ") is not None


def test_validate_project_name_accepts_nonempty() -> None:
    assert validate_project_name("My Project") is None


@pytest.mark.parametrize("folder_name", ["", "   ", "a/b", "a\\b", ".", ".."])
def test_validate_folder_name_rejects_invalid(folder_name: str) -> None:
    assert validate_folder_name(folder_name) is not None


def test_validate_folder_name_accepts_simple_name() -> None:
    assert validate_folder_name("my-project") is None


# --- resolve_destination -------------------------------------------------------


def test_resolve_destination_stays_under_root(tmp_path: Path) -> None:
    destination = resolve_destination("my-project", root=tmp_path)
    assert destination == (tmp_path / "my-project").resolve()
    assert destination.parent == tmp_path.resolve()


def test_resolve_destination_rejects_escaping_root(tmp_path: Path) -> None:
    with pytest.raises(ProjectCreationError):
        resolve_destination("..", root=tmp_path)


# --- validate_new_project -------------------------------------------------------


def test_validate_new_project_accepts_valid_input(tmp_path: Path) -> None:
    result = validate_new_project("My Project", "my-project", root=tmp_path)
    assert isinstance(result, NewProjectValidation)
    assert result.is_valid
    assert result.errors == []


def test_validate_new_project_rejects_existing_directory(tmp_path: Path) -> None:
    (tmp_path / "my-project").mkdir()
    result = validate_new_project("My Project", "my-project", root=tmp_path)
    assert not result.is_valid
    assert any("already exists" in error for error in result.errors)


def test_validate_new_project_collects_multiple_errors(tmp_path: Path) -> None:
    result = validate_new_project("", "a/b", root=tmp_path)
    assert not result.is_valid
    assert len(result.errors) == 2


# --- create_project_directory ---------------------------------------------------


def test_create_project_directory_creates_new_dir(tmp_path: Path) -> None:
    destination = tmp_path / "my-project"
    create_project_directory(destination)
    assert destination.is_dir()


def test_create_project_directory_refuses_to_overwrite_existing(tmp_path: Path) -> None:
    destination = tmp_path / "my-project"
    destination.mkdir()
    (destination / "keep-me.txt").write_text("do not delete")

    with pytest.raises(ProjectCreationError):
        create_project_directory(destination)

    assert (destination / "keep-me.txt").read_text() == "do not delete"


# --- init_git_repo ---------------------------------------------------------------


def test_init_git_repo_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        calls.append((args, kwargs))
        return _FakeCompletedProcess(returncode=0)

    monkeypatch.setattr(project_creation_module.subprocess, "run", fake_run)
    init_git_repo(tmp_path)

    assert calls[0][0][0] == ["git", "init"]
    assert calls[0][1]["cwd"] == tmp_path


def test_init_git_repo_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        return _FakeCompletedProcess(returncode=1, stderr="git: not found")

    monkeypatch.setattr(project_creation_module.subprocess, "run", fake_run)

    with pytest.raises(ProjectCreationError, match="git: not found"):
        init_git_repo(tmp_path)


def test_init_git_repo_raises_on_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> _FakeCompletedProcess:
        raise subprocess.TimeoutExpired(cmd="git init", timeout=10)

    monkeypatch.setattr(project_creation_module.subprocess, "run", fake_run)

    with pytest.raises(ProjectCreationError):
        init_git_repo(tmp_path)
