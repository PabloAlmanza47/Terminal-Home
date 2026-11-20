"""Execute explicitly selected project commands in managed tmux workspaces."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from dashboard.models import LocalProjectLocation, RunProjectCommandRequest
from dashboard.services import tmux
from dashboard.services.workspace_launcher import (
    LaunchError,
    attach_workspace_session,
    create_workspace_session_without_attach,
)


class ProjectCommandExecutionError(Exception):
    """A user-facing failure while preparing or running a project command."""


@dataclass(frozen=True, slots=True)
class CommandTarget:
    window_id: str
    pane_id: str
    running: bool


_SHELL_COMMANDS = frozenset({"bash", "dash", "fish", "ksh", "sh", "tcsh", "zsh"})


def _is_running(pane: tmux.TmuxPaneRuntime) -> bool:
    command = pane.current_command.rsplit("/", 1)[-1].casefold()
    return not pane.dead and bool(command) and command not in _SHELL_COMMANDS


# Project command categories that may run in a workspace's own pane, and the
# pane role (tmux.PANE_ROLE_OPTION) that pane must carry.
_CATEGORY_ROLES = {"dev": "dev_server", "test": "test_terminal"}


def _task_title(category: str) -> str:
    return f"terminal-home:{category}"


def _task_role(category: str) -> str:
    return f"task:{category}"


def _only_pane_with_role(
    workspace, role: str, panes: list[tmux.TmuxPaneRuntime]
) -> CommandTarget | None:
    """The single pane of *workspace*'s session tagged *role*, else None.

    Identity is the role option Terminal Home set on the pane by its id when
    creating it -- never a pane index (whose base is user-configurable) or a
    title (which the program in the pane can change). Zero matches (an
    older or hand-built session) or several (ambiguous) both return None, so
    the caller falls back to a dedicated task window rather than risk typing
    into the wrong pane.
    """
    matches = [
        pane
        for pane in panes
        if pane.session_name == workspace.session_name
        and pane.role == role
        and pane.window_id is not None
        and pane.pane_id is not None
    ]
    if len(matches) != 1:
        return None
    pane = matches[0]
    assert pane.window_id is not None and pane.pane_id is not None
    return CommandTarget(pane.window_id, pane.pane_id, _is_running(pane))


def _find_target(
    workspace,
    category: str,
    panes: list[tmux.TmuxPaneRuntime],
) -> CommandTarget | None:
    role = _CATEGORY_ROLES.get(category)
    if role is not None:
        target = _only_pane_with_role(workspace, role, panes)
        if target is not None:
            return target
    return _only_pane_with_role(workspace, _task_role(category), panes)


def _create_task_target(
    workspace,
    category: str,
    runner: tmux.TmuxCommandRunner,
) -> CommandTarget:
    project_dir = str(workspace.project_path)
    window_id, pane_id = tmux.create_task_window(
        workspace.session_name,
        f"th-{category}",
        project_dir,
        runner=runner,
    )
    tmux.set_pane_title(pane_id, _task_title(category), runner=runner)
    tmux.set_pane_role(pane_id, _task_role(category), runner=runner)
    return CommandTarget(window_id, pane_id, False)


def _resolve_runner(request: RunProjectCommandRequest) -> tmux.TmuxCommandRunner:
    if not isinstance(request.workspace.project_location, LocalProjectLocation):
        raise ProjectCommandExecutionError(
            "Project command execution is currently available only for local projects."
        )
    resolution = tmux.resolve_tmux_runner(request.workspace)
    if resolution.runner is None:
        message = (
            resolution.error.message
            if resolution.error is not None
            else "tmux is unavailable."
        )
        raise ProjectCommandExecutionError(message)
    if resolution.runner is tmux.run_tmux_command and not tmux.is_tmux_installed():
        raise ProjectCommandExecutionError(
            "tmux is not installed -- cannot run the project command."
        )
    return resolution.runner


def execute_project_command_request(
    request: RunProjectCommandRequest,
    *,
    out: TextIO | None = None,
    runner: tmux.TmuxCommandRunner | None = None,
) -> None:
    """Ensure the project workspace, run/focus the selected command, and attach."""
    stream = out if out is not None else sys.stdout
    if not isinstance(request.workspace.project_location, LocalProjectLocation):
        raise ProjectCommandExecutionError(
            "Remote project commands remain informational until remote execution is supported."
        )
    project_path: Path = request.workspace.project_path
    if not project_path.is_dir():
        raise ProjectCommandExecutionError(f"Project directory no longer exists: {project_path}")
    if not request.argv:
        raise ProjectCommandExecutionError("This detected command is informational only.")

    command_runner = runner or _resolve_runner(request)
    try:
        if not tmux.session_exists(request.workspace.session_name, runner=command_runner):
            create_workspace_session_without_attach(
                request.workspace, runner=command_runner, stream=stream
            )
        panes = tmux.list_tmux_panes(runner=command_runner)
        target = _find_target(request.workspace, request.category, panes)
        if target is None:
            target = _create_task_target(request.workspace, request.category, command_runner)
        if not target.running:
            tmux.send_argv_to_pane(target.pane_id, request.argv, runner=command_runner)
        tmux.select_window_and_pane(
            target.window_id, target.pane_id, runner=command_runner
        )
        attach_workspace_session(request.workspace, command_runner)
    except (LaunchError, tmux.TmuxCommandError, OSError, subprocess.SubprocessError) as exc:
        raise ProjectCommandExecutionError(
            f"Could not run {request.label}: {exc}"
        ) from exc
