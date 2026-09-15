"""UI-independent, single-agent creation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dashboard.services.agent_deck import (
    AgentDeckCreateRequest,
    AgentDeckCreateResult,
    AgentDeckRunner,
    create_session,
    run_agent_deck_command,
)
from dashboard.services.git import GitRunner, GitStatus, load_status, run_git_status
from dashboard.services.git_worktree import (
    GitWorktreeInspection,
    GitWorktreeRunner,
    canonical_worktree_path,
    inspect_worktrees,
    is_branch_checked_out,
    run_git_worktree,
    worktree_for_path,
)
from dashboard.services.git_worktree_mutation import (
    GitMutationRunner,
    GitWorktreeMutationError,
    branch_exists,
    create_worktree,
    ensure_worktree_parent,
    path_is_within,
    remove_empty_directories,
    remove_worktree,
    run_git_mutation,
    valid_branch_name,
)


class AgentCreationMode(str, Enum):
    CURRENT_CHECKOUT = "current_checkout"
    NEW_WORKTREE = "new_worktree"


@dataclass(frozen=True, slots=True)
class AgentCreationRequest:
    project_path: Path
    mode: AgentCreationMode
    task_name: str
    tool: str
    prompt: str | None = None
    branch_name: str | None = None
    worktree_path: Path | None = None


@dataclass(frozen=True, slots=True)
class AgentCreationResult:
    success: bool
    session_id: str | None = None
    resolved_path: Path | None = None
    worktree_path: Path | None = None
    warning: str | None = None
    error: str | None = None
    cleanup_attempted: bool = False
    cleanup_succeeded: bool | None = None
    cleanup_error: str | None = None
    title: str | None = None
    visible_in_agent_hub: bool | None = None
    visibility_warning: str | None = None


_SUPPORTED_TOOLS = frozenset({"codex", "claude", "claude-code", "claude_code"})


def default_agent_worktree_root() -> Path:
    """Return the canonical Terminal Home-managed worktree root."""
    from dashboard.services.project_creation import DEFAULT_PROJECTS_ROOT

    return canonical_worktree_path(DEFAULT_PROJECTS_ROOT / "agent-worktrees")


def _failure(message: str, *, path: Path | None = None) -> AgentCreationResult:
    return AgentCreationResult(False, resolved_path=path, error=message)


def _validate_request(request: AgentCreationRequest) -> str | None:
    if not request.task_name.strip():
        return "Agent task name cannot be empty"
    if not request.tool.strip() or request.tool.casefold() not in _SUPPORTED_TOOLS:
        return f"Unsupported Agent Deck tool: {request.tool or '(empty)'}"
    if request.mode is AgentCreationMode.NEW_WORKTREE:
        if not request.branch_name or not request.branch_name.strip():
            return "A new worktree requires a non-empty branch name"
        if request.worktree_path is None or not str(request.worktree_path).strip():
            return "A new worktree requires a target path"
    return None


def _status(path: Path, runner: GitRunner) -> GitStatus:
    return load_status(path, runner=runner)


def _inspect(path: Path, runner: GitWorktreeRunner) -> GitWorktreeInspection:
    return inspect_worktrees(path, runner=runner)


def _preflight_source(
    path: Path,
    *,
    status_runner: GitRunner,
    inspection_runner: GitWorktreeRunner,
    allow_dirty: bool = False,
) -> tuple[Path | None, GitWorktreeInspection | None, str | None, str | None]:
    source = canonical_worktree_path(path)
    if not source.is_dir():
        return None, None, "Project path does not exist or is not a directory", None
    status = _status(source, status_runner)
    if status.is_repo is not True:
        return None, None, "Project path is not a local Git repository", None
    if status.available is not True:
        return None, None, status.error or "Git working-tree status is unavailable", None
    if status.clean is None:
        return None, None, status.error or "Git working-tree status is unavailable", None
    if not status.clean:
        if not allow_dirty:
            return None, None, "Agent creation requires a clean working tree", None
        dirty_warning = (
            "This checkout has uncommitted changes. The new worktree will start from the "
            "committed base; those changes will not be included."
        )
    else:
        dirty_warning = None
    inspection = _inspect(source, inspection_runner)
    if not inspection.available:
        return None, None, inspection.warning or "Git worktree inspection is unavailable", None
    if worktree_for_path(inspection, source) is None:
        return None, None, "Project path is not a registered Git worktree", None
    return source, inspection, None, dirty_warning


def _preflight_new_worktree(
    request: AgentCreationRequest,
    source: Path,
    inspection_runner: GitWorktreeRunner,
    mutation_runner: GitMutationRunner,
) -> tuple[Path | None, str | None]:
    assert request.branch_name is not None
    assert request.worktree_path is not None
    branch = request.branch_name.strip()
    target = canonical_worktree_path(request.worktree_path)
    if not valid_branch_name(source, branch, runner=mutation_runner):
        return None, f"Invalid branch name: {branch}"
    try:
        if branch_exists(source, branch, runner=mutation_runner):
            return None, f"Branch already exists: {branch}"
    except GitWorktreeMutationError as exc:
        return None, str(exc)
    inspection = _inspect(source, inspection_runner)
    if not inspection.available:
        return None, inspection.warning or "Git worktree inspection is unavailable"
    if is_branch_checked_out(inspection, branch):
        return None, f"Branch is already checked out: {branch}"
    if worktree_for_path(inspection, target) is not None or target.exists():
        return None, f"Worktree path already exists or is registered: {target}"
    if target == source:
        return None, "Worktree path must differ from the source checkout"
    managed_root = default_agent_worktree_root()
    if not target.parent.is_dir() and not path_is_within(target.parent, managed_root):
        return None, f"Worktree parent directory does not exist: {target.parent}"
    return target, None


def _deck_result(
    result: AgentDeckCreateResult,
    *,
    resolved_path: Path,
    worktree_path: Path | None = None,
    warning: str | None = None,
) -> AgentCreationResult:
    if result.success:
        return AgentCreationResult(
            True,
            session_id=result.session_id,
            resolved_path=resolved_path,
            worktree_path=worktree_path,
            warning=warning or result.warning,
        )
    return AgentCreationResult(
        False,
        resolved_path=resolved_path,
        worktree_path=worktree_path,
        error=result.error or "Agent Deck session creation failed",
        warning=warning,
    )


def _safe_cleanup(
    source: Path,
    target: Path,
    branch: str,
    *,
    inspection_runner: GitWorktreeRunner = run_git_worktree,
    status_runner: GitRunner,
    mutation_runner: GitMutationRunner,
) -> tuple[bool, str | None]:
    if not target.is_dir():
        return False, "cleanup skipped: created worktree path is no longer a directory"
    inspection = _inspect(source, inspection_runner)
    record = worktree_for_path(inspection, target)
    if record is None or record.branch != branch:
        return False, "cleanup skipped: worktree ownership could not be verified"
    status = _status(target, status_runner)
    if status.clean is not True:
        return False, "cleanup skipped: newly created worktree is not clean"
    try:
        remove_worktree(source, target, runner=mutation_runner)
    except GitWorktreeMutationError as exc:
        return False, str(exc)
    return True, None


def validate_agent_creation_request(
    request: AgentCreationRequest,
    *,
    status_runner: GitRunner = run_git_status,
    inspection_runner: GitWorktreeRunner = run_git_worktree,
    mutation_runner: GitMutationRunner = run_git_mutation,
) -> AgentCreationResult:
    """Perform the read-only convenience preflight used by the wizard."""
    error = _validate_request(request)
    source = canonical_worktree_path(request.project_path)
    if error:
        return _failure(error, path=source)
    preflight_source, inspection, error, warning = _preflight_source(
        source,
        status_runner=status_runner,
        inspection_runner=inspection_runner,
        allow_dirty=request.mode is AgentCreationMode.NEW_WORKTREE,
    )
    if error or preflight_source is None or inspection is None:
        return _failure(error or "Project preflight failed", path=preflight_source or source)
    source = preflight_source
    if request.mode is AgentCreationMode.CURRENT_CHECKOUT:
        return AgentCreationResult(True, resolved_path=source)
    target, error = _preflight_new_worktree(
        request, source, inspection_runner, mutation_runner
    )
    if error or target is None:
        return _failure(error or "Worktree preflight failed", path=source)
    return AgentCreationResult(
        True,
        resolved_path=target,
        worktree_path=target,
        warning=warning,
    )


def create_agent(
    request: AgentCreationRequest,
    *,
    status_runner: GitRunner = run_git_status,
    inspection_runner: GitWorktreeRunner = run_git_worktree,
    mutation_runner: GitMutationRunner = run_git_mutation,
    agent_runner: AgentDeckRunner = run_agent_deck_command,
) -> AgentCreationResult:
    """Validate and create exactly one Agent Deck session."""
    error = _validate_request(request)
    source = canonical_worktree_path(request.project_path)
    if error:
        return _failure(error, path=source)
    preflight_source, inspection, error, warning = _preflight_source(
        source,
        status_runner=status_runner,
        inspection_runner=inspection_runner,
        allow_dirty=request.mode is AgentCreationMode.NEW_WORKTREE,
    )
    if error or preflight_source is None or inspection is None:
        return _failure(error or "Project preflight failed", path=preflight_source or source)
    source = preflight_source
    if request.mode is AgentCreationMode.CURRENT_CHECKOUT:
        return _deck_result(
            create_session(
                AgentDeckCreateRequest(source, request.task_name, request.tool, request.prompt),
                runner=agent_runner,
            ),
            resolved_path=source,
        )

    target, error = _preflight_new_worktree(request, source, inspection_runner, mutation_runner)
    if error or target is None:
        return _failure(error or "Worktree preflight failed", path=source)
    # Repeat every mutable check immediately before the Git mutation.
    preflight_source, _inspection, error, warning = _preflight_source(
        source,
        status_runner=status_runner,
        inspection_runner=inspection_runner,
        allow_dirty=True,
    )
    if error or preflight_source is None:
        return _failure(error or "Source changed before worktree creation", path=source)
    source = preflight_source
    target, error = _preflight_new_worktree(request, source, inspection_runner, mutation_runner)
    if error or target is None:
        return _failure(error or "Worktree changed before creation", path=source)
    assert request.branch_name is not None
    created_parent_dirs: tuple[Path, ...] = ()
    try:
        created_parent_dirs = ensure_worktree_parent(
            target, default_agent_worktree_root()
        )
        # Directory creation is itself a race boundary; do not hand an
        # appeared target to Git.
        if target.exists():
            raise GitWorktreeMutationError(f"Worktree target already exists: {target}")
        created_path = create_worktree(
            source, target, request.branch_name.strip(), runner=mutation_runner
        )
    except GitWorktreeMutationError as exc:
        parent_cleanup_error = remove_empty_directories(created_parent_dirs)
        return AgentCreationResult(
            False,
            resolved_path=source,
            error=str(exc),
            cleanup_attempted=bool(created_parent_dirs),
            cleanup_succeeded=(
                not bool(parent_cleanup_error) if created_parent_dirs else None
            ),
            cleanup_error=parent_cleanup_error,
        )

    deck = create_session(
        AgentDeckCreateRequest(created_path, request.task_name, request.tool, request.prompt),
        runner=agent_runner,
    )
    result = _deck_result(
        deck,
        resolved_path=created_path,
        worktree_path=created_path,
        warning=warning,
    )
    if result.success:
        return result
    cleaned, cleanup_error = _safe_cleanup(
        source,
        created_path,
        request.branch_name.strip(),
        inspection_runner=inspection_runner,
        status_runner=status_runner,
        mutation_runner=mutation_runner,
    )
    parent_cleanup_error = (
        remove_empty_directories(created_parent_dirs) if cleaned else None
    )
    if parent_cleanup_error:
        cleanup_error = "; ".join(
            detail for detail in (cleanup_error, parent_cleanup_error) if detail
        )
    branch = request.branch_name.strip()
    if cleaned:
        cleanup_message = (
            f"Worktree was removed successfully. Branch '{branch}' was intentionally "
            "preserved. Retrying with the same generated branch may require choosing "
            "another branch."
        )
    else:
        cleanup_message = (
            f"Worktree cleanup did not complete. Branch '{branch}' was intentionally "
            "preserved. Retrying with the same generated branch may require choosing "
            "another branch."
        )
    return AgentCreationResult(
        False,
        resolved_path=result.resolved_path,
        worktree_path=result.worktree_path,
        error=f"{result.error}. {cleanup_message}",
        cleanup_attempted=True,
        cleanup_succeeded=cleaned,
        cleanup_error=(f"{cleanup_error}; path: {result.worktree_path}" if cleanup_error else None),
    )
