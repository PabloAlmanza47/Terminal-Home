"""Post-Textual execution and attach handoff for Agent creation."""

from __future__ import annotations

from collections.abc import Callable

from dashboard.services.agent_creation import (
    AgentCreationRequest,
    AgentCreationResult,
    create_agent,
)
from dashboard.services.agent_deck_launcher import AgentDeckLaunchError, execute_agent_deck_attach

AgentCreator = Callable[[AgentCreationRequest], AgentCreationResult]
AgentAttacher = Callable[[str], None]


class AgentCreationAttachError(AgentDeckLaunchError):
    """Creation succeeded, but handing the terminal to Agent Deck failed."""

    def __init__(self, result: AgentCreationResult, detail: str) -> None:
        self.result = result
        super().__init__(
            f"Agent creation succeeded for session {result.session_id}, but attach failed: "
            f"{detail} Retry from Agent Hub using session ID {result.session_id}."
        )


def execute_agent_creation(
    request: AgentCreationRequest,
    *,
    creator: AgentCreator = create_agent,
    attacher: AgentAttacher = execute_agent_deck_attach,
) -> AgentCreationResult:
    """Run Stage 3 after Textual exits, then attach the exact new session."""
    result = creator(request)
    if not result.success:
        return result
    if not result.session_id:
        return AgentCreationResult(
            False,
            resolved_path=result.resolved_path,
            worktree_path=result.worktree_path,
            error="Agent creation reported success without a session ID",
        )
    try:
        attacher(result.session_id)
    except AgentDeckLaunchError as exc:
        raise AgentCreationAttachError(result, str(exc)) from exc
    return result
