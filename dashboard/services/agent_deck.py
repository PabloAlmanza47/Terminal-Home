"""Optional, best-effort integration with the Agent Deck CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

_TIMEOUT = 2.0


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


AgentDeckRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]


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
    if not isinstance(identifier, str) or not identifier.strip() or not isinstance(path, str):
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
