"""Read-only inventory of the Git worktrees behind discovered projects.

Given the directories project discovery already found, this groups the ones
that belong to the same repository and finds that repository's other linked
worktrees, wherever they live on disk.  It never crawls the filesystem, never
fetches, and never changes Git state:

* repository identity is Git's common directory
  (``git rev-parse --git-common-dir``), never a folder-name guess;
* membership comes only from ``git worktree list --porcelain``, run once per
  repository;
* per-worktree status (branch, detached HEAD, dirty, upstream) reuses
  :func:`dashboard.services.git.load_status` with ``GIT_OPTIONAL_LOCKS=0``,
  run in a small thread pool.

Every failure (no ``git``, broken metadata, a timeout, a missing worktree
path) degrades to "not grouped" or a warning, never an exception.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from dashboard.services.git import GitStatus, load_status
from dashboard.services.git_worktree import (
    GitWorktree,
    GitWorktreeRunner,
    canonical_worktree_path,
    inspect_worktrees,
    run_git_worktree,
)

# Bounds so one unusual repository can't make the picker slow: worktrees
# listed per repository, and concurrent `git status` calls.
MAX_WORKTREES_PER_REPOSITORY = 64
_STATUS_WORKERS = 8

StatusLoader = Callable[[Path], GitStatus]


def _run_status_without_optional_locks(argv: list[str]) -> subprocess.CompletedProcess[str]:
    # Background status must never take index.lock or rewrite the index while
    # the user runs Git in the same checkout, and must never run a program a
    # repository's own config names (core.fsmonitor) just because the picker
    # listed it.
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    argv = [argv[0], "-c", "core.fsmonitor=false", *argv[1:]]
    return subprocess.run(argv, capture_output=True, text=True, timeout=3, env=env)


def load_status_read_only(path: Path) -> GitStatus:
    return load_status(path, runner=_run_status_without_optional_locks)


@dataclass(frozen=True, slots=True)
class ProjectWorktree:
    """One checkout's place in its repository, plus read-only status."""

    repository: Path  # canonical Git common directory: the repository identity
    parent_path: Path  # canonical path of the group's parent (main) checkout
    path: Path
    is_main: bool
    branch: str | None
    detached: bool
    head: str | None
    clean: bool | None  # None: status unknown
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    locked: bool = False

    @property
    def upstream_warning(self) -> str | None:
        """A mismatch worth flagging, or None.

        A missing upstream is not a warning: it never implies unpushed
        commits.  A branch tracking a differently named branch (typically a
        feature branch tracking ``origin/main``) is, and so is an upstream Git
        can no longer find.
        """
        if self.branch is None or self.upstream is None:
            return None
        tracked = self.upstream.split("/", 1)[1] if "/" in self.upstream else self.upstream
        if tracked != self.branch:
            return f"tracks {self.upstream}"
        if self.ahead is None:
            return f"upstream {self.upstream} not found"
        return None


@dataclass(frozen=True, slots=True)
class WorktreeInventory:
    """Worktree facts keyed by canonical checkout path."""

    worktrees: dict[Path, ProjectWorktree]
    warnings: tuple[str, ...] = ()


def repository_common_dir(
    path: Path, *, runner: GitWorktreeRunner = run_git_worktree
) -> Path | None:
    """Canonical Git common directory for the checkout at *path*, or None."""
    if not (path / ".git").exists():
        return None
    try:
        result = runner(["git", "-C", str(path), "rev-parse", "--git-common-dir"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return None
    # Relative output is relative to *path* (the -C directory).
    return canonical_worktree_path(os.path.join(path, value))


def _checkout_records(worktrees: tuple[GitWorktree, ...]) -> list[GitWorktree]:
    return [item for item in worktrees if not item.bare]


def inventory_worktrees(
    project_paths: Iterable[Path],
    *,
    runner: GitWorktreeRunner = run_git_worktree,
    status_loader: StatusLoader = load_status_read_only,
    max_per_repository: int = MAX_WORKTREES_PER_REPOSITORY,
) -> WorktreeInventory:
    """Group *project_paths* by repository and list each repository's worktrees.

    The result covers every existing checkout of every repository reached by
    at least one of *project_paths*, including linked worktrees outside the
    configured roots.  Paths that aren't the top of a checkout Git lists
    (plain directories, subdirectories of a repository, broken metadata) are
    simply absent.
    """
    if runner is run_git_worktree and shutil.which("git") is None:
        return WorktreeInventory({})

    warnings: list[str] = []
    repositories: dict[Path, Path] = {}  # common dir -> first project path reaching it
    for path in project_paths:
        common = repository_common_dir(path, runner=runner)
        if common is not None:
            repositories.setdefault(common, path)

    pending: list[tuple[Path, Path, GitWorktree, bool]] = []
    seen: set[Path] = set()
    for common, source in repositories.items():
        inspection = inspect_worktrees(source, runner=runner)
        if inspection.warning:
            warnings.append(f"{source.name}: {inspection.warning}")
        checkouts = _checkout_records(inspection.worktrees)
        if not checkouts:
            continue
        # Git always lists the main worktree first; a bare repository's main
        # entry has no checkout, so its first linked worktree heads the group.
        first = inspection.worktrees[0]
        parent = checkouts[0].path
        missing = 0
        listed = 0
        for item in checkouts:
            if item.path in seen:
                continue
            if item.prunable or not item.path.is_dir():
                missing += 1
                continue
            if listed >= max_per_repository:
                warnings.append(
                    f"{parent.name}: showing {max_per_repository} worktrees; more are not listed"
                )
                break
            listed += 1
            seen.add(item.path)
            pending.append((common, parent, item, item.path == first.path and not first.bare))
        if missing:
            noun = "worktree path is" if missing == 1 else "worktree paths are"
            warnings.append(f"{parent.name}: {missing} {noun} missing (Git lists it as prunable)")

    def describe(entry: tuple[Path, Path, GitWorktree, bool]) -> ProjectWorktree:
        common, parent, item, is_main = entry
        status = status_loader(item.path)
        return ProjectWorktree(
            repository=common,
            parent_path=parent,
            path=item.path,
            is_main=is_main,
            branch=item.branch,
            detached=item.detached,
            head=item.head,
            clean=status.clean,
            upstream=status.upstream,
            ahead=status.ahead,
            behind=status.behind,
            locked=item.locked,
        )

    with ThreadPoolExecutor(max_workers=_STATUS_WORKERS) as pool:
        described = list(pool.map(describe, pending))
    return WorktreeInventory(
        {item.path: item for item in described}, tuple(dict.fromkeys(warnings))
    )
