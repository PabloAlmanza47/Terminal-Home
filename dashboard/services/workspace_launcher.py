"""The non-Textual orchestration layer: turns a confirmed LaunchRequest into
a real tmux session and hands the terminal over to it.

This module must only ever run *after* the Textual App has fully exited --
never from a mounted screen, since Textual owns the terminal until then.
dashboard.app.main() and the mutating ``th up`` CLI command are its callers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from dashboard.models import (
    LaunchAction,
    LaunchRequest,
    LocalProjectLocation,
    PaneKind,
    WorkspaceSpec,
)
from dashboard.services import pane_commands, tmux
from dashboard.services.project_commands import DetectedProjectCommands, detect_project_commands
from dashboard.services.ssh import (
    SshInteractiveResult,
    quote_remote_argument,
    run_interactive_ssh,
)


class LaunchError(Exception):
    """Raised when a LaunchRequest cannot be turned into a running session."""


def build_pane_plans(
    workspace: WorkspaceSpec,
) -> dict[tuple[str, int], pane_commands.PaneLaunchPlan]:
    """Resolve every pane in *workspace* into a PaneLaunchPlan, keyed by
    (window_name, pane_index).
    """
    project_aware_kinds = {PaneKind.DEV_SERVER, PaneKind.TEST_TERMINAL}
    needs_detection = any(
        pane.kind in project_aware_kinds
        for window in workspace.windows
        for pane in window.panes
    )
    if isinstance(workspace.project_location, LocalProjectLocation):
        local_project_path = workspace.project_location.path
        project_path: Path | str = local_project_path
        detected_commands = (
            detect_project_commands(local_project_path) if needs_detection else None
        )
    else:
        # Remote inspection and remote project discovery are separate phases.
        # Keep planning read-only and avoid probing a remote path locally.
        project_path = workspace.project_location.remote_path
        detected_commands = (
            DetectedProjectCommands(development=None, test=None) if needs_detection else None
        )
    return {
        (window.window_name, pane_index): pane_commands.plan_for_pane(
            pane, project_path, detected_commands
        )
        for window in workspace.windows
        for pane_index, pane in enumerate(window.panes)
    }


def _build_create_and_attach(
    workspace: WorkspaceSpec,
    stream: TextIO,
    runner: tmux.TmuxCommandRunner,
) -> None:
    """Create *workspace* as a brand-new tmux session and hand over the
    terminal. Shared by LaunchAction.CREATE and by LaunchAction.ATTACH's
    fallback when the session it hoped to attach to has disappeared.
    """
    if (
        isinstance(workspace.project_location, LocalProjectLocation)
        and not workspace.project_path.is_dir()
    ):
        raise LaunchError(
            f"Project directory no longer exists: {workspace.project_path}. "
            "Nothing was created; the saved workspace is untouched."
        )

    pane_plans = build_pane_plans(workspace)
    warnings = [plan.warning for plan in pane_plans.values() if plan.warning]

    if runner is tmux.run_tmux_command:
        tmux.create_workspace_session(workspace, pane_plans)
    else:
        tmux.create_workspace_session(workspace, pane_plans, runner=runner)

    for warning in warnings:
        print(f"Note: {warning}", file=stream)

    if isinstance(workspace.project_location, LocalProjectLocation):
        argv = tmux.attach_or_switch_argv(workspace.session_name)
        tmux.exec_attach(argv)
    else:
        _attach_remote(workspace.session_name, runner)


def _attach_remote(session_name: str, runner: tmux.TmuxCommandRunner) -> None:
    if not isinstance(runner, tmux.SshTmuxCommandRunner):
        raise LaunchError("SSH workspace did not resolve to an SSH tmux runner.")

    remote_command = " ".join(
        quote_remote_argument(argument)
        for argument in ["tmux", "attach-session", "-t", session_name]
    )
    result: SshInteractiveResult = run_interactive_ssh(
        runner.destination,
        remote_command,
        request_tty=True,
    )
    if not result.succeeded:
        detail = result.error or (
            f"interactive SSH exited with status {result.status} "
            f"(return code {result.returncode})"
        )
        raise LaunchError(f"Could not attach to remote tmux session: {detail}")


def _resolve_runner(workspace: WorkspaceSpec) -> tmux.TmuxCommandRunner:
    resolution = tmux.resolve_tmux_runner(workspace)
    if resolution.runner is None:
        message = (
            resolution.error.message
            if resolution.error is not None
            else "Unknown runner error."
        )
        raise LaunchError(message)
    return resolution.runner


def _session_exists(session_name: str, runner: tmux.TmuxCommandRunner) -> bool:
    if runner is tmux.run_tmux_command:
        return tmux.session_exists(session_name)
    return tmux.session_exists(session_name, runner=runner)


def execute_launch_request(request: LaunchRequest, *, out: TextIO | None = None) -> None:
    """Build and start (or attach to) the tmux session for *request*.

    Assumes the project directory, `git init`, and workspace persistence
    have already happened -- the wizard's final step does that while
    Textual is still running, before returning this request. This function
    only ever builds/attaches the tmux session; it always re-checks the
    session's actual current state rather than trusting whatever the
    Textual screen last observed, since a session can appear or disappear
    in the gap between scanning and launching.
    """
    stream = out if out is not None else sys.stdout

    if request.workspace is None:
        runner: tmux.TmuxCommandRunner = tmux.run_tmux_command
        if not tmux.is_tmux_installed():
            raise LaunchError("tmux is not installed -- install tmux to launch a workspace.")
    else:
        runner = _resolve_runner(request.workspace)
        if runner is tmux.run_tmux_command and not tmux.is_tmux_installed():
            raise LaunchError("tmux is not installed -- install tmux to launch a workspace.")

    session_name = request.resolved_session_name

    if request.action is LaunchAction.ATTACH:
        if _session_exists(session_name, runner):
            if request.workspace is None or runner is tmux.run_tmux_command:
                tmux.exec_attach(tmux.attach_or_switch_argv(session_name))
            else:
                _attach_remote(session_name, runner)
            return
        if request.workspace is None:
            raise LaunchError(
                f"tmux session '{session_name}' is no longer running, and there is no "
                "saved workspace to recreate it from -- use Configure Workspace instead."
            )
        _build_create_and_attach(request.workspace, stream, runner)
        return

    # LaunchAction.CREATE: request.workspace is guaranteed non-None here
    # (enforced by LaunchRequest.__post_init__).
    assert request.workspace is not None
    if _session_exists(session_name, runner):
        raise LaunchError(
            f"A tmux session named '{session_name}' already exists -- "
            "leaving it untouched. Your project directory and saved workspace "
            "are intact; attach to the existing session manually, or rename it "
            "and run the dashboard again."
        )
    _build_create_and_attach(request.workspace, stream, runner)
