"""Resolves each PaneSpec into a concrete PaneLaunchPlan at *launch* time.

Kept as a separate step from the wizard (and from WorkspaceSpec itself) so
tool detection -- is nvim/claude/lazygit/tree on PATH -- always reflects
what's actually installed when the workspace is created, not a stale
snapshot from whenever the wizard ran.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from dashboard.models import PaneKind, PaneSpec
from dashboard.models.settings import CodingAgent
from dashboard.services.project_commands import (
    DetectedProjectCommands,
    detect_project_commands,
)

# find/sort is the fallback file-tree listing when `tree` isn't installed --
# available on every POSIX system this dashboard targets.
_FILE_TREE_FALLBACK_COMMAND = "find . -maxdepth 3 -not -path '*/.git*' | sort"


@dataclass(frozen=True, slots=True)
class PaneLaunchPlan:
    """What happens in a freshly created pane.

    startup_command: sent into the pane's shell via `send-keys` once it's
        created; None leaves the pane at a plain interactive shell prompt.
    pane_title: applied via `select-pane -T` when the pane's terminal
        supports titles; None leaves the default title.
    warning: a nonfatal, user-facing message when a preferred tool was
        unavailable and a fallback was used instead.
    """

    startup_command: str | None
    pane_title: str | None
    warning: str | None = None


def _is_git_repo(project_path: Path | str) -> bool:
    return isinstance(project_path, Path) and (project_path / ".git").exists()


def plan_for_pane(
    pane: PaneSpec,
    project_path: Path | str,
    detected_commands: DetectedProjectCommands | None = None,
    coding_agent: CodingAgent = CodingAgent.CLAUDE_CODE,
    *,
    remote: bool = False,
) -> PaneLaunchPlan:
    """Decide the startup command, pane title, and any warning for *pane*,
    given the project directory it belongs to.
    """
    if pane.kind is PaneKind.CODE_EDITOR:
        if shutil.which("nvim"):
            return PaneLaunchPlan(startup_command="nvim .", pane_title="editor")
        return PaneLaunchPlan(
            startup_command=None,
            pane_title="editor",
            warning="Neovim was not found on PATH -- opened a shell instead.",
        )

    if pane.kind is PaneKind.CLAUDE_CODE:
        command = {
            CodingAgent.NONE: None,
            CodingAgent.CODEX: "codex",
            CodingAgent.CLAUDE_CODE: "claude",
        }[coding_agent]
        if command is None:
            return PaneLaunchPlan(
                startup_command=None,
                pane_title="agent",
                warning="No Coding Agent is selected -- opened a shell instead.",
            )
        if remote or shutil.which(command):
            return PaneLaunchPlan(startup_command=command, pane_title=command)
        return PaneLaunchPlan(
            startup_command=None,
            pane_title=command,
            warning=f"{command} was not found on PATH -- opened a shell instead.",
        )

    if pane.kind is PaneKind.GIT:
        if not _is_git_repo(project_path):
            return PaneLaunchPlan(
                startup_command=None,
                pane_title="git",
                warning="This project is not a git repository yet -- opened a shell instead.",
            )
        if shutil.which("lazygit"):
            return PaneLaunchPlan(startup_command="lazygit", pane_title="git")
        return PaneLaunchPlan(startup_command="git status", pane_title="git")

    if pane.kind is PaneKind.FILE_TREE:
        if shutil.which("tree"):
            return PaneLaunchPlan(startup_command="tree -C .", pane_title="tree")
        return PaneLaunchPlan(startup_command=_FILE_TREE_FALLBACK_COMMAND, pane_title="tree")

    if pane.kind is PaneKind.TEST_TERMINAL:
        if detected_commands is not None:
            commands = detected_commands
        elif isinstance(project_path, Path):
            commands = detect_project_commands(project_path)
        else:
            commands = DetectedProjectCommands(development=None, test=None)
        if commands.test is not None:
            return PaneLaunchPlan(startup_command=commands.test.command, pane_title="tests")
        return PaneLaunchPlan(
            startup_command=None,
            pane_title="tests",
            warning="No supported test command was detected — opened a shell instead.",
        )

    if pane.kind is PaneKind.DEV_SERVER:
        if detected_commands is not None:
            commands = detected_commands
        elif isinstance(project_path, Path):
            commands = detect_project_commands(project_path)
        else:
            commands = DetectedProjectCommands(development=None, test=None)
        if commands.development is not None:
            return PaneLaunchPlan(
                startup_command=commands.development.command, pane_title="server"
            )
        return PaneLaunchPlan(
            startup_command=None,
            pane_title="server",
            warning="No supported development command was detected — opened a shell instead.",
        )

    if pane.kind is PaneKind.BLANK_TERMINAL:
        return PaneLaunchPlan(startup_command=None, pane_title=None)

    if pane.kind is PaneKind.CUSTOM_COMMAND:
        return PaneLaunchPlan(startup_command=pane.custom_command, pane_title=pane.display_name)

    raise ValueError(f"Unhandled pane kind: {pane.kind!r}")
