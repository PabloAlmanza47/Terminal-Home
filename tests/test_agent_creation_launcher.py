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

    result = execute_agent_creation(
        _request(), creator=creator, attacher=attach, visibility_checker=lambda _: True
    )

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
            visibility_checker=lambda _: True,
        )

    assert caught.value.result.success is True
    assert caught.value.result.session_id == created.session_id
    assert caught.value.result.title == "Fix"
    assert caught.value.result.visible_in_agent_hub is True
    assert "retry-me" in str(caught.value)


def test_visible_creation_retains_safe_result_context() -> None:
    request = _request()
    result = execute_agent_creation(
        request,
        creator=lambda _: AgentCreationResult(
            True, session_id="visible", resolved_path=Path("/canonical/worktree")
        ),
        attacher=lambda _: None,
        visibility_checker=lambda session_id: session_id == "visible",
    )

    assert result.success is True
    assert result.title == request.task_name
    assert result.session_id == "visible"
    assert result.resolved_path == Path("/canonical/worktree")
    assert result.visible_in_agent_hub is True
    assert result.visibility_warning is None


def test_delayed_visibility_is_success_and_does_not_expose_prompt() -> None:
    request = AgentCreationRequest(
        Path("/repo"), AgentCreationMode.CURRENT_CHECKOUT, "Fix parser", "codex", "SECRET PROMPT"
    )
    attached: list[str] = []
    result = execute_agent_creation(
        request,
        creator=lambda _: AgentCreationResult(
            True, session_id="delayed", resolved_path=Path("/repo")
        ),
        attacher=attached.append,
        visibility_checker=lambda _: False,
    )

    assert result.success is True
    assert result.visible_in_agent_hub is False
    assert result.visibility_warning is not None
    assert "Press F5" in result.visibility_warning
    assert "SECRET PROMPT" not in str(result)
    assert attached == ["delayed"]


def test_visibility_list_failure_is_nonfatal_and_attach_retry_is_clear() -> None:
    request = _request()
    with pytest.raises(AgentCreationAttachError) as caught:
        execute_agent_creation(
            request,
            creator=lambda _: AgentCreationResult(
                True, session_id="list-failed", resolved_path=Path("/repo")
            ),
            attacher=lambda _: (_ for _ in ()).throw(AgentDeckLaunchError("attach unavailable")),
            visibility_checker=lambda _: None,
        )

    message = str(caught.value)
    assert "list-failed" in message
    assert "Press F5" in message
    assert "attach unavailable" in message
