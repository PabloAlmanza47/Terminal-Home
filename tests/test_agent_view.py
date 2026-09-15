from __future__ import annotations

import subprocess

from dashboard.services.agent_view import (
    AgentViewRequest,
    AgentViewRouteRequest,
    AgentViewRuntime,
    agent_view_argv,
    agent_view_exists,
    discover_agent_views,
    focus_agent_view,
    open_agent_view,
    route_agent_view,
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
    assert len(calls) == 4
    assert calls[0] == ["tmux", "has-session", "-t", "terminal-home"]
    assert calls[1][-1] == "agent-deck session attach same-exact-id"
    assert calls[2] == [
        "tmux", "set-window-option", "-t", "@view", "@terminal_home_agent_view", "1"
    ]
    assert calls[3] == [
        "tmux", "set-window-option", "-t", "@view",
        "@terminal_home_agent_session_id", "same-exact-id",
    ]
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


def test_discovery_accepts_only_complete_marked_rows() -> None:
    output = (
        "terminal-home\t@one\tAgent View\t1\texact\n"
        "terminal-home\t@unmarked\tOther\t0\texact\n"
        "terminal-home\t@bad\tAgent View\t1\t\n"
        "malformed\n"
    )
    result = discover_agent_views(
        runner=lambda argv: subprocess.CompletedProcess(argv, 0, output, "")
    )

    assert result == {
        "exact": (AgentViewRuntime("exact", "terminal-home", "@one", "Agent View"),)
    }


def test_route_prefers_current_view_and_does_not_create_another(monkeypatch) -> None:
    import dashboard.services.agent_view as module

    existing = AgentViewRuntime("exact", "current", "@view", "Renamed View")
    monkeypatch.setattr(
        module,
        "discover_agent_views",
        lambda runner: {"exact": (existing,)},
    )
    monkeypatch.setattr(module.tmux, "current_client_session", lambda runner: "current")
    monkeypatch.setattr(
        module,
        "focus_agent_view",
        lambda window_id, runner: window_id == "@view",
    )
    monkeypatch.setattr(
        "dashboard.services.workspace_store.load_all_workspaces",
        lambda: {"project": type("Workspace", (), {"session_name": "current"})()},
    )

    result = route_agent_view(AgentViewRouteRequest("exact"), runner=lambda _: None)

    assert result.routed is True
    assert result.view == existing


def test_route_creates_one_view_when_missing_and_focuses_exact_window(monkeypatch) -> None:
    import dashboard.services.agent_view as module

    monkeypatch.setattr(module, "discover_agent_views", lambda runner: {})
    monkeypatch.setattr(module.tmux, "current_client_session", lambda runner: "current")
    monkeypatch.setattr(
        "dashboard.services.workspace_store.load_all_workspaces",
        lambda: {"project": type("Workspace", (), {"session_name": "current"})()},
    )
    monkeypatch.setattr(module.tmux, "session_exists", lambda name, runner: True)
    created: list[AgentViewRequest] = []

    def create(request: AgentViewRequest):
        created.append(request)
        return module.AgentViewResult(True, request.session_id, request.workspace_session, "@new")

    monkeypatch.setattr(module, "focus_agent_view", lambda window_id, runner: window_id == "@new")
    result = route_agent_view(
        AgentViewRouteRequest("exact", "associated"),
        runner=lambda _: None,
        view_creator=create,
    )

    assert result.routed is True
    assert created == [AgentViewRequest("exact", "current")]
    assert result.view == AgentViewRuntime("exact", "current", "@new", "Agent View")
