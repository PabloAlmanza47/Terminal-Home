from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dashboard.services.agent_deck import (
    TERMINAL_HOME_AGENT_DECK_SOCKET,
    AgentDeckCreateRequest,
    AgentStatus,
    create_session,
    create_session_argv,
    is_valid_tmux_socket_name,
    normalize_project_path,
    parse_sessions,
    snapshot,
)


def _result(payload: object, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["agent-deck"], code, json.dumps(payload), "")


def test_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard.services.agent_deck as module
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert snapshot().available is False


def test_valid_json_and_multiple_sessions_for_one_project(tmp_path: Path) -> None:
    path = str(tmp_path / "project")
    result = snapshot(runner=lambda _: _result([
        {"id": "one", "title": "One", "path": path, "tool": "codex", "status": "running"},
        {"id": "two", "title": "Two", "path": path, "tool": "codex", "status": "waiting"},
    ]))
    assert result.available is True
    assert len(result.sessions) == 2
    assert result.sessions[1].status is AgentStatus.WAITING


def test_malformed_json_timeout_and_nonzero_are_safe() -> None:
    malformed = snapshot(runner=lambda _: subprocess.CompletedProcess([], 0, "{", ""))
    assert malformed.sessions == () and malformed.warning
    timeout = snapshot(runner=lambda _: (_ for _ in ()).throw(subprocess.TimeoutExpired([], 2)))
    assert timeout.warning
    failed = snapshot(runner=lambda _: subprocess.CompletedProcess([], 7, "", "failed"))
    assert failed.warning


def test_malformed_individual_session_is_skipped_and_paths_normalize(tmp_path: Path) -> None:
    path = tmp_path / "project"
    sessions = parse_sessions([
        {"id": "ok", "path": str(path), "tool": "codex", "status": "idle"},
        {"path": str(path), "tool": "codex", "status": "running"},
        {"id": "blank-path", "path": "   ", "tool": "codex", "status": "running"},
        "bad",
    ])
    assert len(sessions) == 1
    assert sessions[0].path == normalize_project_path(path)
    assert sessions[0].status is AgentStatus.IDLE


def test_unknown_status_has_internal_fallback() -> None:
    session = parse_sessions([{"id": "x", "path": "/tmp/x", "status": "future-state"}])[0]
    assert session.status is AgentStatus.UNKNOWN


def test_optional_fields_and_relative_symlinked_path_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "project with spaces"
    target.mkdir()
    link = tmp_path / "project-link"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    sessions = parse_sessions(
        [
            {"id": "minimal", "path": "project-link", "status": None},
            {"id": "named", "path": str(target), "title": None, "tool": None},
        ]
    )

    assert sessions[0].path == target.resolve()
    assert sessions[0].title == "minimal"
    assert sessions[0].tool == "unknown"
    assert sessions[0].status is AgentStatus.UNKNOWN
    assert sessions[1].title == "named"
    assert sessions[1].tool == "unknown"


def test_create_session_builds_exact_argv_and_parses_result(tmp_path: Path) -> None:
    request = AgentDeckCreateRequest(
        path=tmp_path / "project with spaces",
        title="Fix: wizard failures [urgent]",
        tool="codex",
        prompt="Inspect this path: /tmp/a b; keep it as one message.",
    )
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _result(
            {
                "success": True,
                "id": "created-1",
                "title": request.title,
                "path": str(request.path),
                "tool": request.tool,
            }
        )

    result = create_session(request, runner=runner)
    assert calls == [create_session_argv(request)]
    assert calls[0][-2:] == ["--message", request.prompt]
    assert result.success is True
    assert result.session_id == "created-1"
    assert result.path == normalize_project_path(request.path)


def test_create_session_without_prompt_omits_message() -> None:
    request = AgentDeckCreateRequest(Path("/tmp/project"), "Agent", "claude")
    assert create_session_argv(request) == [
        "agent-deck",
        "launch",
        "/tmp/project",
        "--title",
        "Agent",
        "--cmd",
        "claude",
        "--json",
    ]


def test_create_session_argv_adds_requested_tmux_socket() -> None:
    request = AgentDeckCreateRequest(
        Path("/tmp/project"), "Agent", "codex", tmux_socket=TERMINAL_HOME_AGENT_DECK_SOCKET
    )
    assert create_session_argv(request) == [
        "agent-deck",
        "launch",
        "/tmp/project",
        "--title",
        "Agent",
        "--cmd",
        "codex",
        "--json",
        "--tmux-socket",
        TERMINAL_HOME_AGENT_DECK_SOCKET,
    ]


@pytest.mark.parametrize(
    "socket_name",
    ["", " ", "socket name", "socket/name", r"socket\\name", ".", "..", "-socket", "a" * 65],
)
def test_invalid_tmux_socket_names_are_rejected(socket_name: str) -> None:
    assert is_valid_tmux_socket_name(socket_name) is False
    with pytest.raises(ValueError, match="Invalid Agent Deck tmux socket name"):
        AgentDeckCreateRequest(Path("/tmp/project"), "Agent", "codex", tmux_socket=socket_name)


def test_terminal_home_tmux_socket_name_is_valid() -> None:
    assert is_valid_tmux_socket_name(TERMINAL_HOME_AGENT_DECK_SOCKET) is True


def test_socket_provider_rejection_is_returned_normally() -> None:
    request = AgentDeckCreateRequest(
        Path("/tmp/project"), "Agent", "codex", tmux_socket=TERMINAL_HOME_AGENT_DECK_SOCKET
    )
    result = create_session(
        request,
        runner=lambda _: subprocess.CompletedProcess([], 1, "", "socket unavailable"),
    )
    assert result.success is False
    assert result.error and "socket unavailable" in result.error


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (subprocess.TimeoutExpired(["agent-deck"], 2), "timed out"),
        (subprocess.CompletedProcess([], 1, "", "provider collision"), "provider collision"),
    ],
)
def test_create_session_classifies_command_failures(
    failure: BaseException, expected: str
) -> None:
    request = AgentDeckCreateRequest(Path("/tmp/project"), "Agent", "codex")

    def runner(_: list[str]) -> subprocess.CompletedProcess[str]:
        if isinstance(failure, BaseException):
            raise failure
        return failure

    result = create_session(request, runner=runner)
    assert result.success is False
    assert result.error and expected in result.error


def test_create_session_handles_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard.services.agent_deck as module

    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    result = create_session(AgentDeckCreateRequest(Path("/tmp/project"), "Agent", "codex"))
    assert result.success is False
    assert result.error == "Agent Deck executable not found"


@pytest.mark.parametrize(
    "stdout, expected",
    [("{", "malformed JSON"), (json.dumps({"success": True}), "session id")],
)
def test_create_session_handles_bad_success_output(stdout: str, expected: str) -> None:
    result = create_session(
        AgentDeckCreateRequest(Path("/tmp/project"), "Agent", "codex"),
        runner=lambda _: subprocess.CompletedProcess([], 0, stdout, ""),
    )
    assert result.success is False
    assert result.error and expected in result.error


def test_create_session_surfaces_provider_error_without_prompt() -> None:
    prompt = "secret prompt that must not appear"
    result = create_session(
        AgentDeckCreateRequest(Path("/tmp/project"), "Agent", "codex", prompt),
        runner=lambda _: subprocess.CompletedProcess(
            [], 1, "", f"collision while handling {prompt}"
        ),
    )
    assert result.success is False
    assert result.error and "collision" in result.error
    assert prompt not in result.error
