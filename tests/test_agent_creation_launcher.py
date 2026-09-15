from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.services.agent_creation import (
    AgentCreationMode,
    AgentCreationRequest,
    AgentCreationResult,
)
from dashboard.services.agent_creation_launcher import (
    AgentCreationAttachError,
    execute_agent_creation,
)
from dashboard.services.agent_deck_launcher import AgentDeckLaunchError


def _request() -> AgentCreationRequest:
    return AgentCreationRequest(Path("/repo"), AgentCreationMode.CURRENT_CHECKOUT, "Fix", "codex")


def test_successful_creation_attaches_exact_session_id_after_creator() -> None:
    events: list[str] = []

    def creator(request: AgentCreationRequest) -> AgentCreationResult:
        events.append("create")
        return AgentCreationResult(
            True, session_id="exact-session", resolved_path=request.project_path
        )

    def attach(session_id: str) -> None:
        events.append(f"attach:{session_id}")

    result = execute_agent_creation(_request(), creator=creator, attacher=attach)

    assert result.success is True
    assert events == ["create", "attach:exact-session"]


def test_creation_failure_does_not_attach_or_leak_prompt() -> None:
    attached = False
    result = AgentCreationResult(
        False,
        error="Agent Deck failed",
        worktree_path=Path("/repo/agent-worktree"),
        cleanup_error="left in place; path: /repo/agent-worktree",
    )

    def attach(_: str) -> None:
        nonlocal attached
        attached = True

    actual = execute_agent_creation(_request(), creator=lambda _: result, attacher=attach)

    assert actual == result
    assert attached is False
    assert "prompt" not in (actual.error or "")


def test_attach_failure_preserves_successful_creation_and_session_id() -> None:
    created = AgentCreationResult(True, session_id="retry-me", resolved_path=Path("/repo"))

    with pytest.raises(AgentCreationAttachError) as caught:
        execute_agent_creation(
            _request(),
            creator=lambda _: created,
            attacher=lambda _: (_ for _ in ()).throw(AgentDeckLaunchError("status 3")),
        )

    assert caught.value.result == created
    assert "retry-me" in str(caught.value)
