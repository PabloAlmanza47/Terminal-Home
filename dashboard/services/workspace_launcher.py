"""The non-Textual orchestration layer: turns a confirmed LaunchRequest into
a real tmux session and hands the terminal over to it.

This module must only ever run *after* the Textual App has fully exited --
never from a mounted screen, since Textual owns the terminal until then.
dashboard.app.main() is the only caller.
"""

from __future__ import annotations

import sys
from typing import TextIO

from dashboard.models import LaunchRequest, WorkspaceSpec
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


def execute_launch_request(request: LaunchRequest, *, out: TextIO | None = None) -> None:
    """Build and start the tmux session for *request*, then attach or
    switch to it.

    Assumes the project directory, `git init`, and workspace persistence
    have already happened -- the wizard's final step does that while
    Textual is still running, before returning this request. This function
    only builds the tmux session and hands over the terminal.
    """
    stream = out if out is not None else sys.stdout
    workspace = request.workspace

    if tmux.session_exists(workspace.session_name):
        raise LaunchError(
            f"A tmux session named '{workspace.session_name}' already exists -- "
            "leaving it untouched. Your project directory and saved workspace "
            "are intact; attach to the existing session manually, or rename it "
            "and run the dashboard again."
        )

    pane_plans = build_pane_plans(workspace)
    warnings = [plan.warning for plan in pane_plans.values() if plan.warning]

    tmux.create_workspace_session(workspace, pane_plans)

    for warning in warnings:
        print(f"Note: {warning}", file=stream)

    argv = tmux.attach_or_switch_argv(workspace.session_name)
    tmux.exec_attach(argv)
