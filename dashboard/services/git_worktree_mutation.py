"""Narrow Git worktree mutations used by Agent creation orchestration."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from dashboard.services.git_worktree import canonical_worktree_path

_TIMEOUT_SECONDS = 3
GitMutationRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
DirectoryMaker = Callable[[Path], None]


class GitWorktreeMutationError(Exception):
    """A requested worktree mutation was rejected by Git or the OS."""


def path_is_within(path: Path, root: Path) -> bool:
    """Return whether canonical *path* is root-contained (including root)."""
    try:
        canonical_worktree_path(path).relative_to(canonical_worktree_path(root))
    except ValueError:
        return False
    return True


def ensure_worktree_parent(
    worktree_path: Path,
    managed_root: Path,
    *,
    mkdir: DirectoryMaker = lambda path: path.mkdir(),
) -> tuple[Path, ...]:
    """Create only missing parents for a target under the managed root.

    The target itself is deliberately never created here.  Returned paths are
    the directories this call created, in outermost-to-innermost order.
    """
    target = canonical_worktree_path(worktree_path)
    root = canonical_worktree_path(managed_root)
    parent = target.parent
    if not path_is_within(parent, root):
        if parent.is_dir():
            return ()
        raise GitWorktreeMutationError(
            f"Worktree parent directory does not exist: {parent}"
        )
    if target.exists():
        raise GitWorktreeMutationError(f"Worktree target already exists: {target}")
    if parent.is_dir():
        return ()
    if parent.exists():
        raise GitWorktreeMutationError(f"Worktree parent is not a directory: {parent}")

    missing: list[Path] = []
    cursor = parent
    while not cursor.exists():
        missing.append(cursor)
        if cursor == cursor.parent:
            raise GitWorktreeMutationError(f"Cannot create worktree parent: {parent}")
        cursor = cursor.parent
    if not cursor.is_dir():
        raise GitWorktreeMutationError(f"Worktree parent is not a directory: {cursor}")

    created: list[Path] = []
    for directory in reversed(missing):
        try:
            mkdir(directory)
        except FileExistsError:
            if not directory.is_dir():
                raise GitWorktreeMutationError(
                    f"Worktree parent is not a directory: {directory}"
                ) from None
        except OSError as exc:
            raise GitWorktreeMutationError(
                f"Could not create worktree parent {directory}: {exc}"
            ) from exc
        else:
            created.append(directory)
    return tuple(created)


def remove_empty_directories(directories: tuple[Path, ...]) -> str | None:
    """Best-effort removal of only directories created by this operation."""
    for directory in reversed(directories):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            return f"managed parent cleanup failed at {directory}: {exc}"
    return None


def run_git_mutation(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)


def create_worktree(
    source_path: Path,
    worktree_path: Path,
    branch_name: str,
    *,
    runner: GitMutationRunner = run_git_mutation,
) -> Path:
    """Create one new-branch worktree, returning its canonical path."""
    source = canonical_worktree_path(source_path)
    target = canonical_worktree_path(worktree_path)
    argv = [
        "git", "-C", str(source), "worktree", "add", "-b", branch_name, str(target), "HEAD"
    ]
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitWorktreeMutationError(f"Git worktree creation failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = "Git worktree creation failed"
        if detail:
            message = f"{message}: {detail[:500]}"
        raise GitWorktreeMutationError(message)
    return target


def remove_worktree(
    source_path: Path,
    worktree_path: Path,
    *,
    runner: GitMutationRunner = run_git_mutation,
) -> None:
    """Remove a clean worktree without force and never delete its branch."""
    source = canonical_worktree_path(source_path)
    target = canonical_worktree_path(worktree_path)
    argv = ["git", "-C", str(source), "worktree", "remove", str(target)]
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitWorktreeMutationError(f"Git worktree cleanup failed: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = "Git worktree cleanup failed"
        if detail:
            message = f"{message}: {detail[:500]}"
        raise GitWorktreeMutationError(message)


def branch_exists(
    source_path: Path,
    branch_name: str,
    *,
    runner: GitMutationRunner = run_git_mutation,
) -> bool:
    """Check a local branch without changing Git state."""
    source = canonical_worktree_path(source_path)
    argv = [
        "git",
        "-C",
        str(source),
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{branch_name}",
    ]
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitWorktreeMutationError(f"Git branch inspection failed: {exc}") from exc
    if result.returncode not in {0, 1}:
        raise GitWorktreeMutationError("Git branch inspection failed")
    return result.returncode == 0


def valid_branch_name(
    source_path: Path,
    branch_name: str,
    *,
    runner: GitMutationRunner = run_git_mutation,
) -> bool:
    """Ask Git whether a proposed branch name is valid."""
    source = canonical_worktree_path(source_path)
    argv = ["git", "-C", str(source), "check-ref-format", "--branch", branch_name]
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GitWorktreeMutationError(f"Git branch validation failed: {exc}") from exc
    if result.returncode not in {0, 1}:
        raise GitWorktreeMutationError("Git branch validation failed")
    return result.returncode == 0
