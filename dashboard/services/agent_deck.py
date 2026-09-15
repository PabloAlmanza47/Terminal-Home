"""Optional, best-effort integration with the Agent Deck CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_TIMEOUT = 2.0
TERMINAL_HOME_AGENT_DECK_SOCKET = "terminal-home-agent-deck"
_TMUX_SOCKET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


class AgentStatus(str, Enum):
    RUNNING = "running"
    WAITING = "waiting"
    IDLE = "idle"
    STOPPED = "stopped"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AgentDeckSession:
    id: str
    title: str
    path: Path
    tool: str
    status: AgentStatus
    tmux_session: str | None = None
    profile: str | None = None
    raw_status: str | None = None


@dataclass(frozen=True, slots=True)
class AgentDeckSnapshot:
    available: bool
    sessions: tuple[AgentDeckSession, ...] = ()
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class AgentDeckCreateRequest:
    """Transient inputs for creating one Agent Deck session."""

    path: Path
    title: str
    tool: str
    prompt: str | None = None
    tmux_socket: str | None = None

    def __post_init__(self) -> None:
        if not str(self.path).strip():
            raise ValueError("Agent Deck session path cannot be empty")
        if not self.title.strip():
            raise ValueError("Agent Deck session title cannot be empty")
        if not self.tool.strip():
            raise ValueError("Agent Deck session tool cannot be empty")
        if self.tmux_socket is not None and not is_valid_tmux_socket_name(self.tmux_socket):
            raise ValueError(f"Invalid Agent Deck tmux socket name: {self.tmux_socket!r}")


@dataclass(frozen=True, slots=True)
class AgentDeckCreateResult:
    """Structured result from Agent Deck's create-and-start operation."""

    success: bool
    session_id: str | None = None
    title: str | None = None
    path: Path | None = None
    tool: str | None = None
    warning: str | None = None
    error: str | None = None


AgentDeckRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def is_valid_tmux_socket_name(value: str) -> bool:
    """Return whether *value* is a portable tmux ``-L`` socket basename."""
    return isinstance(value, str) and _TMUX_SOCKET_PATTERN.fullmatch(value) is not None


def normalize_project_path(value: str | Path) -> Path:
    """Return a comparable absolute path without requiring it to exist."""
    return Path(os.path.realpath(os.path.expanduser(str(value))))


def normalize_status(value: Any) -> tuple[AgentStatus, str | None]:
    raw = str(value).strip().casefold() if value is not None else ""
    aliases = {
        "working": AgentStatus.RUNNING,
        "active": AgentStatus.RUNNING,
        "pending": AgentStatus.WAITING,
        "paused": AgentStatus.WAITING,
        "complete": AgentStatus.IDLE,
        "dead": AgentStatus.STOPPED,
        "failed": AgentStatus.ERROR,
    }
    try:
        return aliases.get(raw, AgentStatus(raw)), raw or None
    except ValueError:
        return AgentStatus.UNKNOWN, raw or None


def _parse_session(value: Any) -> AgentDeckSession | None:
    if not isinstance(value, dict):
        return None
    identifier = value.get("id")
    path = value.get("path")
    if (
        not isinstance(identifier, str)
        or not identifier.strip()
        or not isinstance(path, str)
        or not path.strip()
    ):
        return None
    status, raw_status = normalize_status(value.get("status"))
    return AgentDeckSession(
        id=identifier,
        title=str(value.get("title") or identifier),
        path=normalize_project_path(path),
        tool=str(value.get("tool") or "unknown"),
        status=status,
        tmux_session=(
            value.get("tmux_session") if isinstance(value.get("tmux_session"), str) else None
        ),
        profile=value.get("profile") if isinstance(value.get("profile"), str) else None,
        raw_status=raw_status,
    )


def parse_sessions(payload: Any) -> tuple[AgentDeckSession, ...]:
    """Parse the v1.15 top-level array, retaining healthy entries only."""
    entries = payload.get("sessions") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        return ()
    return tuple(session for item in entries if (session := _parse_session(item)) is not None)


def run_agent_deck_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT)


def create_session_argv(request: AgentDeckCreateRequest) -> list[str]:
    """Build Agent Deck's structured create-and-start command."""
    argv = [
        "agent-deck",
        "launch",
        str(request.path),
        "--title",
        request.title,
        "--cmd",
        request.tool,
        "--json",
    ]
    if request.prompt:
        argv.extend(("--message", request.prompt))
    if request.tmux_socket is not None:
        argv.extend(("--tmux-socket", request.tmux_socket))
    return argv


def _redacted_provider_detail(result: subprocess.CompletedProcess[str], prompt: str | None) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    if prompt:
        detail = detail.replace(prompt, "[initial prompt redacted]")
    return detail[:500]


def _create_result_from_payload(payload: Any, prompt: str | None) -> AgentDeckCreateResult:
    if not isinstance(payload, dict):
        return AgentDeckCreateResult(False, error="Agent Deck returned unexpected JSON")
    if payload.get("success") is False:
        detail = payload.get("error")
        error = str(detail).strip() if detail else "Agent Deck rejected session creation"
        if prompt:
            error = error.replace(prompt, "[initial prompt redacted]")
        return AgentDeckCreateResult(False, error=error[:500])
    identifier = payload.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return AgentDeckCreateResult(False, error="Agent Deck did not return a session id")
    raw_path = payload.get("path")
    return AgentDeckCreateResult(
        True,
        session_id=identifier,
        title=payload.get("title") if isinstance(payload.get("title"), str) else None,
        path=normalize_project_path(raw_path) if isinstance(raw_path, str) and raw_path else None,
        tool=payload.get("tool") if isinstance(payload.get("tool"), str) else None,
    )


def create_session(
    request: AgentDeckCreateRequest,
    *,
    runner: AgentDeckRunner = run_agent_deck_command,
) -> AgentDeckCreateResult:
    """Create exactly one session through Agent Deck's supported CLI API."""
    if runner is run_agent_deck_command and shutil.which("agent-deck") is None:
        return AgentDeckCreateResult(False, error="Agent Deck executable not found")
    try:
        result = runner(create_session_argv(request))
    except subprocess.TimeoutExpired:
        return AgentDeckCreateResult(False, error="Agent Deck session creation timed out")
    except OSError as exc:
        return AgentDeckCreateResult(False, error=f"Agent Deck unavailable: {exc}")
    if result.returncode != 0:
        detail = _redacted_provider_detail(result, request.prompt)
        error = "Agent Deck session creation failed"
        if detail:
            error = f"{error}: {detail}"
        return AgentDeckCreateResult(False, error=error)
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return AgentDeckCreateResult(False, error="Agent Deck returned malformed JSON")
    return _create_result_from_payload(payload, request.prompt)


def snapshot(*, runner: AgentDeckRunner = run_agent_deck_command) -> AgentDeckSnapshot:
    if runner is run_agent_deck_command and shutil.which("agent-deck") is None:
        return AgentDeckSnapshot(False)
    try:
        result = runner(["agent-deck", "list", "--json"])
    except subprocess.TimeoutExpired:
        return AgentDeckSnapshot(True, warning="Agent Deck status timed out")
    except OSError as exc:
        return AgentDeckSnapshot(False, warning=f"Agent Deck unavailable: {exc}")
    if result.returncode != 0:
        return AgentDeckSnapshot(True, warning="Agent Deck status failed")
    try:
        payload = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return AgentDeckSnapshot(True, warning="Agent Deck returned malformed JSON")
    if not isinstance(payload, (list, dict)):
        return AgentDeckSnapshot(True, warning="Agent Deck returned an unexpected response")
    return AgentDeckSnapshot(True, parse_sessions(payload))


def attach_argv(session_id: str) -> list[str]:
    if not session_id.strip():
        raise ValueError("Agent Deck session id cannot be empty")
    return ["agent-deck", "session", "attach", session_id]
