from __future__ import annotations

import subprocess

import pytest

from dashboard.services import agent_deck_launcher as module
from dashboard.services.agent_deck import attach_argv


def test_attach_command_uses_stable_session_id() -> None:
    assert attach_argv("session-123") == ["agent-deck", "session", "attach", "session-123"]


def test_attach_runs_after_tui_with_inherited_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.execute_agent_deck_attach("session-123")
    assert calls == [
        (["agent-deck", "session", "attach", "session-123"],
         {"stdin": None, "stdout": None, "stderr": None})
    ]


def test_attach_nonzero_is_structured_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 3)
    )
    with pytest.raises(module.AgentDeckLaunchError, match="status 3"):
        module.execute_agent_deck_attach("session-123")
