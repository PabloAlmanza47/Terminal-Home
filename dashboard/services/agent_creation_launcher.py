"""Post-Textual execution and attach handoff for Agent creation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from dashboard.services.agent_creation import (
    AgentCreationRequest,
    AgentCreationResult,
    create_agent,
)
from dashboard.services.agent_deck import snapshot as agent_deck_snapshot
from dashboard.services.agent_deck_launcher import AgentDeckLaunchError, execute_agent_deck_attach

AgentCreator = Callable[[AgentCreationRequest], AgentCreationResult]
AgentAttacher = Callable[[str], None]
AgentVisibilityChecker = Callable[[str], bool | None]

_NOT_VISIBLE_MESSAGE = (
    "Agent created successfully, but Agent Deck has not reported it in the session list yet. "
    "Press F5 to refresh."
)


def _check_agent_deck_visibility(session_id: str) -> bool | None:
    """Perform one bounded, provider-backed visibility read.

    ``None`` means Agent Deck could not provide a trustworthy list. It is not
    a creation failure and is intentionally not retried or persisted.
    """
    snapshot = agent_deck_snapshot()
    if not snapshot.available:
        return None
    return any(session.id == session_id for session in snapshot.sessions)


class AgentCreationAttachError(AgentDeckLaunchError):
    """Creation succeeded, but handing the terminal to Agent Deck failed."""

    def __init__(self, result: AgentCreationResult, detail: str) -> None:
        self.result = result
        message = (
            f"Agent creation succeeded for session {result.session_id}, but attach failed: "
            f"{detail} Retry from Agent Hub using session ID {result.session_id}."
        )
        if result.visibility_warning:
            message = f"{message} {result.visibility_warning}"
        super().__init__(message)


def execute_agent_creation(
    request: AgentCreationRequest,
    *,
    creator: AgentCreator = create_agent,
    attacher: AgentAttacher = execute_agent_deck_attach,
    visibility_checker: AgentVisibilityChecker = _check_agent_deck_visibility,
) -> AgentCreationResult:
    """Run Stage 3 after Textual exits, then attach the exact new session."""
    result = creator(request)
    if not result.success:
        return result
    session_id = result.session_id
    if not session_id:
        return AgentCreationResult(
            False,
            resolved_path=result.resolved_path,
            worktree_path=result.worktree_path,
            error="Agent creation reported success without a session ID",
        )
    result = replace(result, title=request.task_name)
    try:
        visible = visibility_checker(session_id)
    except Exception:
        visible = None
    result = replace(
        result,
        visible_in_agent_hub=visible,
        visibility_warning=None if visible is True else _NOT_VISIBLE_MESSAGE,
    )
    try:
        attacher(session_id)
    except AgentDeckLaunchError as exc:
        raise AgentCreationAttachError(result, str(exc)) from exc
    return result
