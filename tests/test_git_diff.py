from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dashboard.services import git as git_module
from dashboard.services.git import GitFileChange, load_diff


def _runner_for(staged: str = "staged diff\n", working: str = "working diff\n"):
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, staged if "--cached" in argv else working, ""
        )

    return runner, calls


def test_load_diff_includes_staged_and_working_tree_sections() -> None:
    runner, calls = _runner_for()
    result = load_diff(
        Path("/tmp/repo"), GitFileChange("file.py", "M", "M"), runner=runner
    )

    assert result.available is True
    assert result.staged == "staged diff\n"
    assert result.working_tree == "working diff\n"
    assert any("--cached" in call for call in calls)
    assert any("--cached" not in call for call in calls)


def test_load_diff_handles_rename_and_deleted_files() -> None:
    runner, calls = _runner_for()
    renamed = load_diff(
        Path("/tmp/repo"), GitFileChange("new.py", "R", ".", "old.py"), runner=runner
    )
    deleted = load_diff(
        Path("/tmp/repo"), GitFileChange("gone.py", ".", "D"), runner=runner
    )

    assert renamed.old_path == "old.py"
    assert "old.py" in next(call for call in calls if "--cached" in call)
    assert deleted.working_tree == "working diff\n"


def test_load_diff_reads_safe_untracked_text_and_limits_preview(tmp_path: Path) -> None:
    change = GitFileChange("notes.txt", "?", "?")
    result = load_diff(
        tmp_path,
        change,
        read_file=lambda _: b"hello",
    )
    assert result.available is True
    assert result.untracked_content == "hello"

    large = load_diff(
        tmp_path,
        change,
        read_file=lambda _: b"x" * (256 * 1024 + 1),
    )
    assert large.truncated is True
    assert "truncated" in (large.untracked_content or "")


def test_load_diff_rejects_unsafe_and_binary_untracked_files(tmp_path: Path) -> None:
    outside = load_diff(
        tmp_path,
        GitFileChange("../secret.txt", "?", "?"),
        read_file=lambda _: b"secret",
    )
    binary = load_diff(
        tmp_path,
        GitFileChange("data.bin", "?", "?"),
        read_file=lambda _: b"\0binary",
    )
    assert "outside" in (outside.error or "")
    assert binary.binary is True
    assert "Binary file" in (binary.error or "")


def test_load_diff_handles_missing_git_timeout_and_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_module.shutil, "which", lambda _: None)
    missing = load_diff(Path("/tmp/repo"), GitFileChange("file.py", ".", "M"))
    assert missing.available is False

    def timeout(_: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired([], 3)

    timed_out = load_diff(Path("/tmp/repo"), GitFileChange("file.py", ".", "M"), runner=timeout)
    assert timed_out.available is False
    assert timed_out.error == "Git diff timed out"

    failed = load_diff(
        Path("/tmp/repo"),
        GitFileChange("file.py", ".", "M"),
        runner=lambda argv: subprocess.CompletedProcess(argv, 1, "", "error"),
    )
    assert failed.error == "Git diff unavailable"
