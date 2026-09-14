"""Read-only inspection of Git linked worktrees.

This module intentionally has no worktree mutation operations.  The command
runner is injectable so all parsing and preflight decisions can be tested
without invoking Git.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_SECONDS = 3
_WORKTREE_COMMAND = ["worktree", "list", "--porcelain"]

GitWorktreeRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def canonical_worktree_path(path: str | Path) -> Path:
    """Canonicalize a path for exact path-based comparisons."""
    return Path(os.path.realpath(os.path.expanduser(str(path))))


@dataclass(frozen=True, slots=True)
class GitWorktree:
    """One worktree record from Git's porcelain output."""

    path: Path
    head: str | None
    branch: str | None
    detached: bool
    bare: bool
    locked: bool = False
    lock_reason: str | None = None
    prunable: bool = False
    prune_reason: str | None = None
    is_primary: bool | None = None


@dataclass(frozen=True, slots=True)
class GitWorktreeInspection:
    """A read-only inspection result and its provider health."""

    source_path: Path
    worktrees: tuple[GitWorktree, ...] = ()
    available: bool = True
    warning: str | None = None

    @property
    def source_is_primary(self) -> bool | None:
        source = canonical_worktree_path(self.source_path)
        for worktree in self.worktrees:
            if worktree.path == source:
                return worktree.is_primary
        return None


def _branch_name(value: str) -> str:
    return value.removeprefix("refs/heads/")


def parse_worktree_list(output: str) -> tuple[GitWorktree, ...]:
    """Parse porcelain records, retaining only records with a worktree path.

    Git separates records with blank lines.  The parser also starts a new
    record whenever another ``worktree`` header appears, making truncated or
    concatenated output safe to process.  Unknown fields are ignored.
    """
    records: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    def finish() -> None:
        nonlocal current
        if current is not None and isinstance(current.get("path"), str):
            records.append(current)
        current = None

    for line in output.splitlines():
        if not line:
            finish()
            continue
        key, separator, value = line.partition(" ")
        if key == "worktree":
            finish()
            if value:
                current = {"path": value}
            continue
        if current is None:
            continue
        if key == "bare" and not separator:
            current["bare"] = True
            continue
        if not separator:
            continue
        if key == "HEAD":
            current["head"] = value or None
        elif key == "branch":
            current["branch"] = _branch_name(value) if value else None
        elif key == "bare":
            current["bare"] = True
        elif key == "locked":
            current["locked"] = True
            current["lock_reason"] = value or None
        elif key == "prunable":
            current["prunable"] = True
            current["prune_reason"] = value or None
    finish()

    worktrees: list[GitWorktree] = []
    primary_assigned = False
    for record in records:
        path_value = record["path"]
        if not isinstance(path_value, str):
            continue
        path = canonical_worktree_path(path_value)
        bare = bool(record.get("bare", False))
        is_primary = None if bare else not primary_assigned
        if not bare:
            primary_assigned = True
        branch = record.get("branch")
        head = record.get("head")
        lock_reason = record.get("lock_reason")
        prune_reason = record.get("prune_reason")
        worktrees.append(
            GitWorktree(
                path=path,
                head=head if isinstance(head, str) else None,
                branch=branch if isinstance(branch, str) else None,
                detached=not bare and branch is None and head is not None,
                bare=bare,
                locked=bool(record.get("locked", False)),
                lock_reason=lock_reason if isinstance(lock_reason, str) else None,
                prunable=bool(record.get("prunable", False)),
                prune_reason=prune_reason if isinstance(prune_reason, str) else None,
                is_primary=is_primary,
            )
        )
    return tuple(worktrees)


def run_git_worktree(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)


def inspect_worktrees(
    source_path: Path,
    *,
    runner: GitWorktreeRunner = run_git_worktree,
) -> GitWorktreeInspection:
    """Inspect worktrees for *source_path* without changing Git state."""
    source = canonical_worktree_path(source_path)
    if runner is run_git_worktree and shutil.which("git") is None:
        return GitWorktreeInspection(source, available=False, warning="Git is not installed")
    try:
        result = runner(["git", "-C", str(source), *_WORKTREE_COMMAND])
    except subprocess.TimeoutExpired:
        return GitWorktreeInspection(
            source, available=True, warning="Git worktree inspection timed out"
        )
    except OSError as exc:
        return GitWorktreeInspection(source, available=False, warning=str(exc))
    if result.returncode != 0:
        return GitWorktreeInspection(
            source, available=True, warning="Git worktree inspection failed"
        )
    return GitWorktreeInspection(source, parse_worktree_list(result.stdout))


def worktree_for_path(
    inspection: GitWorktreeInspection, requested_path: str | Path
) -> GitWorktree | None:
    """Return the worktree at *requested_path*, matching canonical paths only."""
    requested = canonical_worktree_path(requested_path)
    return next((item for item in inspection.worktrees if item.path == requested), None)


def is_path_worktree(inspection: GitWorktreeInspection, requested_path: str | Path) -> bool:
    return worktree_for_path(inspection, requested_path) is not None


def is_branch_checked_out(inspection: GitWorktreeInspection, branch: str) -> bool:
    """Check branch identity, never display names or paths."""
    normalized = _branch_name(branch)
    return any(item.branch == normalized for item in inspection.worktrees)


def is_primary_checkout(
    inspection: GitWorktreeInspection, requested_path: str | Path
) -> bool | None:
    worktree = worktree_for_path(inspection, requested_path)
    return None if worktree is None else worktree.is_primary


def preflight_worktree_path(
    inspection: GitWorktreeInspection, requested_path: str | Path
) -> bool:
    """Return whether a requested worktree path is available for later use."""
    return not is_path_worktree(inspection, requested_path)


def preflight_worktree_branch(inspection: GitWorktreeInspection, branch: str) -> bool:
    """Return whether a branch is not currently checked out anywhere."""
    return not is_branch_checked_out(inspection, branch)
