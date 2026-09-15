"""Ephemeral Terminal Home windows for viewing Agent Deck sessions."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from dashboard.services import tmux


@dataclass(frozen=True, slots=True)
class AgentViewRequest:
    """Inputs for one non-persistent Agent Deck view window."""

    session_id: str
    workspace_session: str
    window_name: str = "Agent View"


@dataclass(frozen=True, slots=True)
class AgentViewResult:
    """The stable tmux IDs returned for an opened view, or a safe error."""

    success: bool
    session_id: str
    workspace_session: str
    window_id: str | None = None
    pane_id: str | None = None
    error: str | None = None


AgentDeckAvailable = Callable[[], bool]


def _agent_deck_available() -> bool:
    return shutil.which("agent-deck") is not None


def agent_view_argv(request: AgentViewRequest) -> list[str]:
    """Build the direct-command tmux window argv.

    The attach command is one shell-command argument because that is the
    tmux ``new-window`` interface. ``shlex.join`` quotes values for tmux's
    command shell; Terminal Home itself never invokes a shell.
    """
    return [
        "tmux",
        "new-window",
        "-t",
        request.workspace_session,
        "-n",
        request.window_name,
        "-P",
        "-F",
        "#{window_id} #{pane_id}",
        shlex.join(["agent-deck", "session", "attach", request.session_id]),
    ]


def _failure(request: AgentViewRequest, message: str) -> AgentViewResult:
    return AgentViewResult(
        False,
        session_id=request.session_id,
        workspace_session=request.workspace_session,
        error=message,
    )


def _parse_window_result(result: subprocess.CompletedProcess[str]) -> tuple[str, str] | None:
    fields = result.stdout.strip().split()
    if len(fields) != 2 or not all(fields):
        return None
    return fields[0], fields[1]


def open_agent_view(
    request: AgentViewRequest,
    *,
    runner: tmux.TmuxCommandRunner = tmux.run_tmux_command,
    tmux_available: Callable[[], bool] = tmux.is_tmux_installed,
    agent_deck_available: AgentDeckAvailable = _agent_deck_available,
) -> AgentViewResult:
    """Create one directly-attached, Terminal Home-owned view window."""
    if not request.session_id.strip():
        return _failure(request, "Agent Deck session ID cannot be empty")
    if not request.workspace_session.strip():
        return _failure(request, "Terminal Home workspace session cannot be empty")
    if not request.window_name.strip():
        return _failure(request, "Agent view window name cannot be empty")
    if not tmux_available():
        return _failure(request, "tmux is not installed")
    if not agent_deck_available():
        return _failure(request, "Agent Deck is not installed")

    try:
        workspace_check = runner(
            ["tmux", "has-session", "-t", request.workspace_session]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _failure(request, f"Could not inspect Terminal Home workspace: {exc}")
    if workspace_check.returncode != 0:
        return _failure(
            request,
            f"Terminal Home workspace session is unavailable: {request.workspace_session}",
        )

    argv = agent_view_argv(request)
    try:
        result = runner(argv)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _failure(request, f"Could not create Agent View window: {exc}")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail[:500]}" if detail else ""
        return _failure(request, f"Could not create Agent View window{suffix}")
    ids = _parse_window_result(result)
    if ids is None:
        return _failure(request, "tmux did not report the Agent View window ID")
    window_id, pane_id = ids
    return AgentViewResult(
        True,
        session_id=request.session_id,
        workspace_session=request.workspace_session,
        window_id=window_id,
        pane_id=pane_id,
    )


def agent_view_exists(
    window_id: str,
    *,
    runner: tmux.TmuxCommandRunner = tmux.run_tmux_command,
) -> bool:
    """Check a known view window by stable tmux window ID."""
    if not window_id.strip():
        return False
    try:
        result = runner(["tmux", "list-windows", "-t", window_id, "-F", "#{window_id}"])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and window_id in result.stdout.split()


def focus_agent_view(
    window_id: str,
    *,
    runner: tmux.TmuxCommandRunner = tmux.run_tmux_command,
) -> bool:
    """Focus a still-existing known view without guessing its identity."""
    if not agent_view_exists(window_id, runner=runner):
        return False
    try:
        result = runner(["tmux", "select-window", "-t", window_id])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
