from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dashboard.services import git as git_module
from dashboard.services.git import GitStatus, load_status, parse_status


def _record(code: str, path: str, *, extra: str = "") -> str:
    return f"1 {code} N... 0000000 0000000 0000000 0000000 0000000 {path}{extra}\0"


def test_clean_repository() -> None:
    status = parse_status("# branch.head main\0")
    assert status.is_repo is True
    assert status.branch == "main"
    assert status.clean is True


def test_modified_staged_untracked_and_deleted_files() -> None:
    output = (
        "# branch.head main\0"
        + _record(".M", "modified.py")
        + _record("M.", "staged.py")
        + _record(".D", "deleted.py")
        + "? untracked.py\0"
    )
    status = parse_status(output)
    assert [change.indicator for change in status.changes] == [".M", "M.", ".D", "?"]
    assert status.staged_count == 1
    assert status.modified_count == 2
    assert status.untracked_count == 1


def test_rename_preserves_old_and_new_path() -> None:
    status = parse_status(
        "# branch.head main\0"
        "2 R. N... 100644 100644 100644 abc def R100 new.py\0old.py\0"
    )
    assert status.changes[0].indicator == "R."
    assert status.changes[0].path == "new.py"
    assert status.changes[0].old_path == "old.py"


def test_staged_and_unstaged_state_is_preserved() -> None:
    status = parse_status("# branch.head main\0" + _record("MM", "both.py"))
    assert status.changes[0].index_status == "M"
    assert status.changes[0].worktree_status == "M"
    assert status.staged_count == 1
    assert status.modified_count == 1


def test_unmerged_conflict_is_preserved() -> None:
    status = parse_status(
        "# branch.head main\0"
        "u UU N... 100644 100644 100644 100644 abc def ghi jkl conflict.py\0"
    )
    assert status.changes[0].indicator == "UU"
    assert status.changes[0].path == "conflict.py"


def test_detached_head() -> None:
    status = parse_status("# branch.head (detached)\0")
    assert status.detached is True
    assert status.branch is None


def test_non_git_directory() -> None:
    result = load_status(
        Path("/tmp/not-a-repo"),
        runner=lambda _: subprocess.CompletedProcess([], 128, "", "fatal: not a git repository"),
    )
    assert result == GitStatus(False, None, False, error="Git status unavailable")


def test_missing_git_timeout_and_nonzero_fail_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(git_module.shutil, "which", lambda _: None)
    missing = load_status(tmp_path)
    assert missing.available is False

    timeout = load_status(
        tmp_path, runner=lambda _: (_ for _ in ()).throw(subprocess.TimeoutExpired([], 3))
    )
    assert timeout.error == "Git status timed out"

    failed = load_status(
        tmp_path, runner=lambda _: subprocess.CompletedProcess([], 2, "", "error")
    )
    assert failed.is_repo is None


def test_malformed_records_are_ignored() -> None:
    status = parse_status("# branch.head main\0garbage\01 bad\0")
    assert status.changes == ()
