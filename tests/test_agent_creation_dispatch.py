from __future__ import annotations

from pathlib import Path

import dashboard.app as app_module
from dashboard.services.agent_creation import (
    AgentCreationMode,
    AgentCreationRequest,
    AgentCreationResult,
)


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
