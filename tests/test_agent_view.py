from __future__ import annotations

import subprocess

from dashboard.services.agent_view import (
    AgentViewRequest,
    agent_view_argv,
    agent_view_exists,
    focus_agent_view,
    open_agent_view,
)


def _request(session_id: str = "exact-session") -> AgentViewRequest:
    return AgentViewRequest(session_id, "terminal-home", "Agent View")


def test_agent_view_argv_uses_exact_id_and_direct_command() -> None:
    argv = agent_view_argv(_request("id.with_safe-chars_123"))

    assert argv[:9] == [
        "tmux",
        "new-window",
        "-t",
        "terminal-home",
        "-n",
        "Agent View",
        "-P",
        "-F",
        "#{window_id} #{pane_id}",
    ]
    assert argv[9] == "agent-deck session attach id.with_safe-chars_123"
    assert "remain-on-exit" not in argv
    assert "bash" not in argv


def test_open_agent_view_captures_ids_and_never_touches_agent_deck_resources() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1] == "has-session":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "@view %pane\n", "")

    result = open_agent_view(
        _request("same-exact-id"),
        runner=runner,
        tmux_available=lambda: True,
        agent_deck_available=lambda: True,
    )

    assert result.success is True
    assert result.window_id == "@view"
    assert result.pane_id == "%pane"
    assert len(calls) == 2
    assert calls[0] == ["tmux", "has-session", "-t", "terminal-home"]
    assert calls[1][-1] == "agent-deck session attach same-exact-id"
    assert not any("kill" in argument for call in calls for argument in call)


def test_open_agent_view_reports_missing_dependencies_and_workspace() -> None:
    request = _request()
    missing_tmux = open_agent_view(request, tmux_available=lambda: False)
    missing_deck = open_agent_view(
        request, tmux_available=lambda: True, agent_deck_available=lambda: False
    )
    missing_workspace = open_agent_view(
        request,
        runner=lambda argv: subprocess.CompletedProcess(argv, 1, "", "missing"),
        tmux_available=lambda: True,
        agent_deck_available=lambda: True,
    )

    assert "tmux" in (missing_tmux.error or "")
    assert "Agent Deck" in (missing_deck.error or "")
    assert "workspace" in (missing_workspace.error or "")


def test_view_existence_and_focus_use_only_known_window_id() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if argv[1] == "list-windows":
            return subprocess.CompletedProcess(argv, 0, "@known\n", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert agent_view_exists("@known", runner=runner) is True
    assert focus_agent_view("@known", runner=runner) is True
    assert calls[-1] == ["tmux", "select-window", "-t", "@known"]
    assert agent_view_exists("@gone", runner=runner) is False


def test_view_disappearance_and_new_window_failure_are_nonfatal() -> None:
    disappeared = agent_view_exists(
        "@gone",
        runner=lambda argv: subprocess.CompletedProcess(argv, 1, "", "no window"),
    )
    def failing_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[1] == "has-session":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 1, "", "new-window failed")

    failed = open_agent_view(
        _request(),
        runner=failing_runner,
        tmux_available=lambda: True,
        agent_deck_available=lambda: True,
    )

    assert disappeared is False
    assert failed.success is False
    assert "new-window failed" in (failed.error or "")
