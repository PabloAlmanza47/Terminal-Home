from __future__ import annotations

from pathlib import Path

import dashboard.app as app_module
from dashboard.services.agent_creation import (
    AgentCreationMode,
    AgentCreationRequest,
    AgentCreationResult,
)
from dashboard.services.agent_view import AgentViewRouteRequest, AgentViewRouteResult


def test_agent_creation_is_executed_only_after_textual_run_returns(
    monkeypatch,
) -> None:
    events: list[str] = []
    request = AgentCreationRequest(
        Path("/repo"), AgentCreationMode.CURRENT_CHECKOUT, "Fix", "codex"
    )

    class FakeApp:
        def __init__(self) -> None:
            pass

        def run(self):
            events.append("textual-exited")
            if events.count("textual-exited") == 1:
                return request
            return None

    def execute(value: AgentCreationRequest) -> AgentCreationResult:
        assert events == ["textual-exited"]
        assert value is request
        events.append("creation-executed")
        return AgentCreationResult(True, session_id="created")

    monkeypatch.setattr(app_module, "TerminalHomeApp", FakeApp)
    monkeypatch.setattr(app_module, "execute_agent_creation", execute)

    app_module.main()

    assert events == ["textual-exited", "creation-executed", "textual-exited"]


def test_agent_view_route_precedes_exact_attach_fallback(monkeypatch) -> None:
    request = AgentViewRouteRequest("exact-session", "terminal-home")
    events: list[str] = []

    class FakeApp:
        def run(self):
            if not events:
                events.append("textual-exited")
                return request
            return None

    monkeypatch.setattr(app_module, "TerminalHomeApp", FakeApp)
    monkeypatch.setattr(
        app_module,
        "route_agent_view",
        lambda value: (events.append(f"route:{value.session_id}") or
                       AgentViewRouteResult(False, reason="no view")),
    )
    monkeypatch.setattr(
        app_module,
        "execute_agent_deck_attach",
        lambda session_id: events.append(f"attach:{session_id}"),
    )

    app_module.main()

    assert events == ["textual-exited", "route:exact-session", "attach:exact-session"]


def test_agent_view_route_success_does_not_use_direct_attach(monkeypatch) -> None:
    request = AgentViewRouteRequest("exact-session")
    events: list[str] = []

    class FakeApp:
        def run(self):
            if not events:
                return request
            return None

    monkeypatch.setattr(app_module, "TerminalHomeApp", FakeApp)
    monkeypatch.setattr(
        app_module,
        "route_agent_view",
        lambda value: (events.append(f"route:{value.session_id}") or
                       AgentViewRouteResult(True)),
    )
    monkeypatch.setattr(
        app_module,
        "execute_agent_deck_attach",
        lambda session_id: events.append(f"attach:{session_id}"),
    )

    app_module.main()

    assert events == ["route:exact-session"]
