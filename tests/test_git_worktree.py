from __future__ import annotations

import subprocess
from pathlib import Path

from dashboard.services.git_worktree import (
    GitWorktreeInspection,
    inspect_worktrees,
    is_branch_checked_out,
    is_path_worktree,
    is_primary_checkout,
    parse_worktree_list,
    preflight_worktree_branch,
    preflight_worktree_path,
    worktree_for_path,
)


def _result(output: str, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["git"], code, output, "")


def test_single_checkout_and_injectable_runner(tmp_path: Path) -> None:
    source = tmp_path / "repo"
    output = f"worktree {source}\nHEAD abc123\nbranch refs/heads/main\n"
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _result(output)

    result = inspect_worktrees(source, runner=runner)

    assert calls == [["git", "-C", str(source.resolve()), "worktree", "list", "--porcelain"]]
    item = result.worktrees[0]
    assert item.path == source.resolve()
    assert item.head == "abc123"
    assert item.branch == "main"
    assert item.detached is False
    assert item.bare is False
    assert item.is_primary is True
    assert result.source_is_primary is True


def test_multiple_worktrees_same_repo_have_distinct_branches(tmp_path: Path) -> None:
    main = tmp_path / "repo"
    feature = tmp_path / "repo feature"
    output = (
        f"worktree {main}\nHEAD aaa\nbranch refs/heads/main\n\n"
        f"worktree {feature}\nHEAD bbb\nbranch refs/heads/feature\n"
    )
    result = inspect_worktrees(main, runner=lambda _: _result(output))

    assert [item.path for item in result.worktrees] == [main, feature]
    assert is_branch_checked_out(result, "refs/heads/feature")
    assert is_branch_checked_out(result, "feature")
    assert preflight_worktree_branch(result, "main") is False
    assert preflight_worktree_branch(result, "new-agent") is True


def test_detached_worktree_and_path_with_spaces() -> None:
    path = Path("/srv/agent worktrees/detached copy")
    result = parse_worktree_list(f"worktree {path}\nHEAD deadbeef\n")

    assert result[0].path == path
    assert result[0].head == "deadbeef"
    assert result[0].branch is None
    assert result[0].detached is True


def test_locked_prunable_and_bare_metadata() -> None:
    output = (
        "worktree /repo\nHEAD aaa\nbranch refs/heads/main\n"
        "locked because an agent is using it\nprunable stale metadata\n\n"
        "worktree /repo.git\nbare\n"
    )
    result = parse_worktree_list(output)

    locked, bare = result
    assert locked.locked is True
    assert locked.lock_reason == "because an agent is using it"
    assert locked.prunable is True
    assert locked.prune_reason == "stale metadata"
    assert bare.bare is True
    assert bare.detached is False
    assert bare.is_primary is None


def test_malformed_and_incomplete_records_are_safe() -> None:
    output = (
        "HEAD orphaned\nbranch refs/heads/ignored\n\n"
        "worktree /valid\nHEAD abc\nunknown field\n\n"
        "worktree\nHEAD not-a-record\n\n"
        "worktree /incomplete\nbranch refs/heads/topic\n"
    )

    result = parse_worktree_list(output)

    assert [item.path for item in result] == [Path("/valid"), Path("/incomplete")]
    assert result[1].branch == "topic"
    assert result[1].head is None
    assert result[1].detached is False


def test_empty_output_is_empty() -> None:
    assert parse_worktree_list("") == ()
    assert parse_worktree_list("\n\n") == ()


def test_requested_path_collision_uses_canonical_path_only(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    other = tmp_path / "nested" / "existing"
    result = GitWorktreeInspection(
        tmp_path, parse_worktree_list(f"worktree {existing}\nHEAD abc\n")
    )

    assert is_path_worktree(result, existing / ".." / "existing")
    assert worktree_for_path(result, existing) is not None
    assert preflight_worktree_path(result, existing) is False
    assert preflight_worktree_path(result, other) is True
    assert is_primary_checkout(result, existing) is True
    assert is_primary_checkout(result, other) is None


def test_nonzero_runner_result_is_read_only_failure() -> None:
    result = inspect_worktrees(
        Path("/repo"), runner=lambda argv: _result("", code=128)
    )

    assert result.available is True
    assert result.worktrees == ()
    assert result.warning == "Git worktree inspection failed"
