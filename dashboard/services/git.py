"""Read-only, machine-readable Git working-tree status."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_SECONDS = 3
_STATUS_COMMAND = ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"]


@dataclass(frozen=True, slots=True)
class GitFileChange:
    """One porcelain-v2 changed path; statuses retain index and worktree sides."""

    path: str
    index_status: str
    worktree_status: str
    old_path: str | None = None

    @property
    def indicator(self) -> str:
        if self.index_status == "?" or self.worktree_status == "?":
            return "?"
        if self.index_status == "!" or self.worktree_status == "!":
            return "!"
        return f"{self.index_status}{self.worktree_status}".replace("..", "") or "?"


@dataclass(frozen=True, slots=True)
class GitStatus:
    is_repo: bool | None
    branch: str | None
    detached: bool
    changes: tuple[GitFileChange, ...] = ()
    staged_count: int = 0
    modified_count: int = 0
    untracked_count: int = 0
    available: bool = True
    error: str | None = None

    @property
    def clean(self) -> bool | None:
        if self.is_repo is not True:
            return None
        return not self.changes


GitRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _parse_change(record: str, following: str | None = None) -> GitFileChange | None:
    kind = record.split(" ", 1)[0] if record else ""
    fields = record.split(" ", 11 if kind == "u" else 8)
    kind = fields[0] if fields else ""
    if kind not in {"1", "2", "u"} or len(fields) < 9:
        return None
    xy = fields[1]
    if len(xy) != 2:
        return None
    path = fields[11] if kind == "u" else fields[8]
    if kind == "2" and " " in path:
        _, path = path.split(" ", 1)
    old_path = following if kind == "2" else None
    return GitFileChange(path, xy[0], xy[1], old_path)


def parse_status(output: str) -> GitStatus:
    records = output.split("\0")
    branch: str | None = None
    detached = False
    changes: list[GitFileChange] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            detached = value == "(detached)"
            branch = None if detached else value
            continue
        if record.startswith(("1 ", "u ")):
            change = _parse_change(record)
        elif record.startswith("2 "):
            following = records[index] if index < len(records) else None
            index += 1 if following is not None else 0
            change = _parse_change(record, following)
        else:
            # Porcelain-v2 untracked/ignored records are `? path` / `! path`.
            if record[:2] in {"? ", "! "}:
                change = GitFileChange(record[2:], record[0], record[0])
            else:
                change = None
        if change is not None:
            changes.append(change)

    staged = sum(change.index_status not in {".", "?", "!"} for change in changes)
    modified = sum(change.worktree_status not in {".", "?", "!"} for change in changes)
    untracked = sum(change.index_status == "?" for change in changes)
    return GitStatus(
        is_repo=True,
        branch=branch,
        detached=detached,
        changes=tuple(changes),
        staged_count=staged,
        modified_count=modified,
        untracked_count=untracked,
    )


def run_git_status(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)


def load_status(path: Path, *, runner: GitRunner = run_git_status) -> GitStatus:
    if runner is run_git_status and shutil.which("git") is None:
        return GitStatus(None, None, False, available=False, error="Git is not installed")
    try:
        result = runner(["git", "-C", str(path), *_STATUS_COMMAND])
    except subprocess.TimeoutExpired:
        return GitStatus(None, None, False, available=True, error="Git status timed out")
    except OSError as exc:
        return GitStatus(None, None, False, available=False, error=str(exc))
    if result.returncode != 0:
        not_repo = "not a git repository" in (result.stderr or "").casefold()
        return GitStatus(False if not_repo else None, None, False, error="Git status unavailable")
    return parse_status(result.stdout)
