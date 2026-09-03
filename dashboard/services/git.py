"""Read-only, machine-readable Git working-tree status."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_TIMEOUT_SECONDS = 3
_STATUS_COMMAND = ["status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"]
_DIFF_TIMEOUT_SECONDS = 3
_UNTRACKED_PREVIEW_LIMIT = 256 * 1024


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


@dataclass(frozen=True, slots=True)
class GitDiffResult:
    """Read-only diff data for one changed file."""

    repository_path: Path
    path: str
    old_path: str | None
    tracked: bool
    available: bool
    staged: str | None = None
    working_tree: str | None = None
    untracked_content: str | None = None
    truncated: bool = False
    binary: bool = False
    error: str | None = None


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


GitDiffRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def run_git_diff(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_DIFF_TIMEOUT_SECONDS)


def _safe_project_file(repository_path: Path, relative_path: str) -> Path | None:
    try:
        repository = repository_path.resolve()
        candidate = (repository / relative_path).resolve()
        candidate.relative_to(repository)
    except (OSError, ValueError):
        return None
    return candidate


def _diff_argv(
    repository_path: Path,
    change: GitFileChange,
    *,
    staged: bool,
) -> list[str]:
    command = ["git", "-C", str(repository_path), "diff"]
    if staged:
        command.append("--cached")
    command.extend(["--no-ext-diff", "--no-textconv", "--no-color", "--"])
    if change.old_path is not None:
        command.append(change.old_path)
    command.append(change.path)
    return command


def _run_one_diff(
    repository_path: Path,
    change: GitFileChange,
    *,
    staged: bool,
    runner: GitDiffRunner,
) -> tuple[str | None, str | None, bool]:
    try:
        result = runner(_diff_argv(repository_path, change, staged=staged))
    except subprocess.TimeoutExpired:
        return None, "Git diff timed out", False
    except OSError as exc:
        return None, str(exc), False
    if result.returncode != 0:
        return None, "Git diff unavailable", False
    text = result.stdout
    if "Binary files " in text or "GIT binary patch" in text:
        return None, "Binary file; text preview unavailable", True
    if len(text.encode("utf-8", errors="replace")) > _UNTRACKED_PREVIEW_LIMIT:
        return (
            text[:_UNTRACKED_PREVIEW_LIMIT]
            + "\n\n[Diff truncated; file is too large to display.]",
            None,
            False,
        )
    return text, None, False


def load_diff(
    repository_path: Path,
    change: GitFileChange,
    *,
    runner: GitDiffRunner = run_git_diff,
    read_file: Callable[[Path], bytes] | None = None,
) -> GitDiffResult:
    """Load staged/working-tree diff data for one status entry."""
    repository_path = repository_path.resolve()
    if runner is run_git_diff and shutil.which("git") is None:
        return GitDiffResult(
            repository_path, change.path, change.old_path, change.index_status != "?", False,
            error="Git is not installed",
        )
    if _safe_project_file(repository_path, change.path) is None or (
        change.old_path is not None and _safe_project_file(repository_path, change.old_path) is None
    ):
        return GitDiffResult(
            repository_path, change.path, change.old_path, change.index_status != "?", False,
            error="File path is outside the project",
        )
    untracked = change.index_status == "?" or change.worktree_status == "?"
    if untracked:
        candidate = _safe_project_file(repository_path, change.path)
        if candidate is None:
            return GitDiffResult(
                repository_path, change.path, change.old_path, False, False,
                error="File path is outside the project",
            )
        try:
            content = (read_file or Path.read_bytes)(candidate)
        except OSError as exc:
            return GitDiffResult(
                repository_path, change.path, change.old_path, False, False,
                error=f"Unable to read untracked file: {exc}",
            )
        if b"\0" in content:
            return GitDiffResult(
                repository_path, change.path, change.old_path, False, False,
                binary=True, error="Binary file; text preview unavailable",
            )
        truncated = len(content) > _UNTRACKED_PREVIEW_LIMIT
        content = content[:_UNTRACKED_PREVIEW_LIMIT]
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return GitDiffResult(
                repository_path, change.path, change.old_path, False, False,
                binary=True, error="Binary file; text preview unavailable",
            )
        if truncated:
            text += "\n\n[File truncated; preview is limited to 256 KiB.]"
        return GitDiffResult(
            repository_path, change.path, change.old_path, False, True,
            untracked_content=text, truncated=truncated,
        )

    staged_text = working_text = None
    if change.index_status not in {"."}:
        staged_text, error, binary = _run_one_diff(
            repository_path, change, staged=True, runner=runner
        )
        if error is not None:
            return GitDiffResult(
                repository_path, change.path, change.old_path, True, False,
                binary=binary, error=error,
            )
    if change.worktree_status not in {"."}:
        working_text, error, binary = _run_one_diff(
            repository_path, change, staged=False, runner=runner
        )
        if error is not None:
            return GitDiffResult(
                repository_path, change.path, change.old_path, True, False,
                staged=staged_text, binary=binary, error=error,
            )
    if not staged_text and not working_text:
        return GitDiffResult(
            repository_path, change.path, change.old_path, True, False,
            error="File has no current diff",
        )
    return GitDiffResult(
        repository_path, change.path, change.old_path, True, True,
        staged=staged_text, working_tree=working_text,
    )
