"""Cheap, tolerant git status lookups for the Open Project screens.

Mirrors dashboard.services.tmux's style: every subprocess call is wrapped
so a missing `git`, a non-repository directory, or a slow/broken command
degrades to a plain "don't know" result rather than raising -- this is
purely informational status, never load-bearing for correctness.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_SUBPROCESS_TIMEOUT_SECONDS = 3


@dataclass(frozen=True, slots=True)
class GitInfo:
    """Whether *path* is a git repository, and its current branch if known.

    branch is None when the directory isn't a git repo, `git` isn't
    installed, HEAD is detached, or the lookup otherwise couldn't complete.
    """

    is_repo: bool
    branch: str | None


def gather_git_info(path: Path) -> GitInfo:
    """Best-effort git status for *path*, never raising."""
    if not (path / ".git").exists():
        return GitInfo(is_repo=False, branch=None)

    if shutil.which("git") is None:
        return GitInfo(is_repo=True, branch=None)

    try:
        result = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return GitInfo(is_repo=True, branch=None)

    if result.returncode != 0:
        return GitInfo(is_repo=True, branch=None)

    branch = result.stdout.strip()
    return GitInfo(is_repo=True, branch=branch or None)
