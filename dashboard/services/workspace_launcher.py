"""The non-Textual orchestration layer: turns a confirmed LaunchRequest into
a real tmux session and hands the terminal over to it.

This module must only ever run *after* the Textual App has fully exited --
never from a mounted screen, since Textual owns the terminal until then.
dashboard.app.main() and the mutating ``th up`` CLI command are its callers.
"""

from __future__ import annotations

import sys
from typing import TextIO

from dashboard.models import LaunchAction, LaunchRequest, WorkspaceSpec
from dashboard.services import pane_commands, tmux


class LaunchError(Exception):
    """Raised when a LaunchRequest cannot be turned into a running session."""


def build_pane_plans(
    workspace: WorkspaceSpec,
) -> dict[tuple[str, int], pane_commands.PaneLaunchPlan]:
    """Resolve every pane in *workspace* into a PaneLaunchPlan, keyed by
    (window_name, pane_index).
    """
    return {
        (window.window_name, pane_index): pane_commands.plan_for_pane(
            pane, workspace.project_path
        )
        for window in workspace.windows
        for pane_index, pane in enumerate(window.panes)
    }


def _build_create_and_attach(workspace: WorkspaceSpec, stream: TextIO) -> None:
    """Create *workspace* as a brand-new tmux session and hand over the
    terminal. Shared by LaunchAction.CREATE and by LaunchAction.ATTACH's
    fallback when the session it hoped to attach to has disappeared.
    """
    if not workspace.project_path.is_dir():
        raise LaunchError(
            f"Project directory no longer exists: {workspace.project_path}. "
            "Nothing was created; the saved workspace is untouched."
        )

    pane_plans = build_pane_plans(workspace)
    warnings = [plan.warning for plan in pane_plans.values() if plan.warning]

    tmux.create_workspace_session(workspace, pane_plans)

    for warning in warnings:
        print(f"Note: {warning}", file=stream)

    argv = tmux.attach_or_switch_argv(workspace.session_name)
    tmux.exec_attach(argv)


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

    if not tmux.is_tmux_installed():
        raise LaunchError("tmux is not installed -- install tmux to launch a workspace.")

    session_name = request.resolved_session_name

    if request.action is LaunchAction.ATTACH:
        if tmux.session_exists(session_name):
            tmux.exec_attach(tmux.attach_or_switch_argv(session_name))
            return
        if request.workspace is None:
            raise LaunchError(
                f"tmux session '{session_name}' is no longer running, and there is no "
                "saved workspace to recreate it from -- use Configure Workspace instead."
            )
        _build_create_and_attach(request.workspace, stream)
        return

    # LaunchAction.CREATE: request.workspace is guaranteed non-None here
    # (enforced by LaunchRequest.__post_init__).
    assert request.workspace is not None
    if tmux.session_exists(session_name):
        raise LaunchError(
            f"A tmux session named '{session_name}' already exists -- "
            "leaving it untouched. Your project directory and saved workspace "
            "are intact; attach to the existing session manually, or rename it "
            "and run the dashboard again."
        )
    _build_create_and_attach(request.workspace, stream)
