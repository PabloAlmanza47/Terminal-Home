"""Ephemeral Terminal Home windows for viewing Agent Deck sessions."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from dashboard.services import tmux

AGENT_VIEW_OPTION: Final = "@terminal_home_agent_view"
AGENT_VIEW_SESSION_OPTION: Final = "@terminal_home_agent_session_id"
_VIEW_LIST_FORMAT: Final = (
    "#{session_name}\t#{window_id}\t#{window_name}\t"
    f"#{{{AGENT_VIEW_OPTION}}}\t#{{{AGENT_VIEW_SESSION_OPTION}}}"
)


@dataclass(frozen=True, slots=True)
class AgentViewRequest:
    """Inputs for one non-persistent Agent Deck view window."""

    session_id: str
    workspace_session: str
    window_name: str = "Agent View"


@dataclass(frozen=True, slots=True)
class AgentViewRouteRequest:
    """A provider-backed Agent Hub selection awaiting post-TUI routing."""

    session_id: str
    associated_workspace_session: str | None = None


@dataclass(frozen=True, slots=True)
class AgentViewResult:
    """The stable tmux IDs returned for an opened view, or a safe error."""

    success: bool
    session_id: str
    workspace_session: str
    window_id: str | None = None
    pane_id: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AgentViewRuntime:
    """A currently marked Terminal Home Agent View window."""

    session_id: str
    tmux_session: str
    window_id: str
    window_name: str


@dataclass(frozen=True, slots=True)
class AgentViewRouteResult:
    """Outcome of routing an Agent Hub selection through Terminal Home."""

    routed: bool
    view: AgentViewRuntime | None = None
    reason: str | None = None


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


def _set_view_markers(
    window_id: str,
    session_id: str,
    runner: tmux.TmuxCommandRunner,
) -> None:
    for option, value in (
        (AGENT_VIEW_OPTION, "1"),
        (AGENT_VIEW_SESSION_OPTION, session_id),
    ):
        result = runner(
            ["tmux", "set-window-option", "-t", window_id, option, value]
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            suffix = f": {detail[:500]}" if detail else ""
            raise tmux.TmuxCommandError(f"Could not mark Agent View window{suffix}")


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
    try:
        _set_view_markers(window_id, request.session_id, runner)
    except (OSError, subprocess.TimeoutExpired, tmux.TmuxCommandError) as exc:
        return _failure(request, str(exc))
    return AgentViewResult(
        True,
        session_id=request.session_id,
        workspace_session=request.workspace_session,
        window_id=window_id,
        pane_id=pane_id,
    )


def discover_agent_views(
    *,
    runner: tmux.TmuxCommandRunner = tmux.run_tmux_command,
) -> dict[str, tuple[AgentViewRuntime, ...]]:
    """Discover marked Agent Views with one read-only all-windows query."""
    try:
        result = runner(["tmux", "list-windows", "-a", "-F", _VIEW_LIST_FORMAT])
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    discovered: dict[str, list[AgentViewRuntime]] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        tmux_session, window_id, window_name, marked, session_id = fields
        if marked != "1" or not all((tmux_session, window_id, window_name, session_id)):
            continue
        discovered.setdefault(session_id, []).append(
            AgentViewRuntime(session_id, tmux_session, window_id, window_name)
        )
    return {
        session_id: tuple(sorted(views, key=lambda view: (view.tmux_session, view.window_id)))
        for session_id, views in discovered.items()
    }


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


def _switch_client(session_name: str, runner: tmux.TmuxCommandRunner) -> bool:
    try:
        result = runner(["tmux", "switch-client", "-t", session_name])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def route_agent_view(
    request: AgentViewRouteRequest,
    *,
    runner: tmux.TmuxCommandRunner = tmux.run_tmux_command,
    view_creator: Callable[[AgentViewRequest], AgentViewResult] | None = None,
) -> AgentViewRouteResult:
    """Route one Agent Hub selection to an existing or new Agent View.

    This function is intentionally runtime-only. Saved workspace definitions
    are read to identify managed sessions, but no placement information is
    written and no provider-owned resource is changed.
    """
    if not request.session_id.strip():
        return AgentViewRouteResult(False, reason="Agent Deck session ID cannot be empty")
    views = discover_agent_views(runner=runner).get(request.session_id, ())
    try:
        from dashboard.services.workspace_store import load_all_workspaces

        workspaces = load_all_workspaces()
    except (OSError, ValueError):
        workspaces = {}

    try:
        current_session = tmux.current_client_session(runner=runner)
    except Exception:
        current_session = None
    managed_sessions = {workspace.session_name for workspace in workspaces.values()}
    current_is_managed = current_session in managed_sessions

    if views:
        if current_is_managed:
            current_views = tuple(view for view in views if view.tmux_session == current_session)
            if current_views:
                selected = current_views[0]
                if focus_agent_view(selected.window_id, runner=runner):
                    return AgentViewRouteResult(True, selected)
        for selected in views:
            if selected.tmux_session != current_session:
                if not _switch_client(selected.tmux_session, runner):
                    continue
            if focus_agent_view(selected.window_id, runner=runner):
                return AgentViewRouteResult(True, selected)
        return AgentViewRouteResult(False, reason="Existing Agent View could not be focused")

    candidates: list[str] = []
    if current_is_managed and current_session is not None:
        candidates.append(current_session)
    associated = request.associated_workspace_session
    if associated and associated in managed_sessions and associated not in candidates:
        candidates.append(associated)
    for workspace_session in candidates:
        if not tmux.session_exists(workspace_session, runner=runner):
            continue
        creator = view_creator or (
            lambda view_request: open_agent_view(view_request, runner=runner)
        )
        created = creator(
            AgentViewRequest(request.session_id, workspace_session),
        )
        if not created.success or created.window_id is None:
            return AgentViewRouteResult(False, reason=created.error or "Agent View creation failed")
        if focus_agent_view(created.window_id, runner=runner):
            return AgentViewRouteResult(
                True,
                AgentViewRuntime(
                    request.session_id,
                    workspace_session,
                    created.window_id,
                    "Agent View",
                ),
            )
        return AgentViewRouteResult(False, reason="New Agent View could not be focused")
    return AgentViewRouteResult(False, reason="No running Terminal Home workspace is available")
