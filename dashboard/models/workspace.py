"""Textual-independent models describing a tmux project workspace.

A WorkspaceSpec (one tmux session) contains one or more WindowSpecs (tmux
windows), each of which contains one to four ordered PaneSpecs (tmux panes).
These are plain, validated dataclasses -- no Textual imports, no subprocess
calls -- so they can be built, validated, serialized, and unit tested in
isolation from both the wizard UI and the tmux orchestration layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

MAX_PANES_PER_WINDOW = 4


class WorkspaceValidationError(ValueError):
    """Raised when a workspace, window, or pane spec fails validation."""


class PaneKind(str, Enum):
    """The fixed catalogue of pane types offered by the New Project wizard."""

    CODE_EDITOR = "code_editor"
    CLAUDE_CODE = "claude_code"
    GIT = "git"
    FILE_TREE = "file_tree"
    TEST_TERMINAL = "test_terminal"
    DEV_SERVER = "dev_server"
    BLANK_TERMINAL = "blank_terminal"
    CUSTOM_COMMAND = "custom_command"


PANE_KIND_LABELS: dict[PaneKind, str] = {
    PaneKind.CODE_EDITOR: "Code Editor",
    PaneKind.CLAUDE_CODE: "Claude Code",
    PaneKind.GIT: "Git",
    PaneKind.FILE_TREE: "File Tree",
    PaneKind.TEST_TERMINAL: "Test Terminal",
    PaneKind.DEV_SERVER: "Development Server",
    PaneKind.BLANK_TERMINAL: "Blank Terminal",
    PaneKind.CUSTOM_COMMAND: "Custom Command",
}

# Ordering here is the order pane kinds are offered in the wizard's
# selection list; it has no bearing on final pane order, which is
# determined solely by PaneSpec order within a WindowSpec.
PANE_KIND_ORDER: tuple[PaneKind, ...] = tuple(PANE_KIND_LABELS.keys())


@dataclass(frozen=True, slots=True)
class PaneSpec:
    """One pane within a window: a type, a display name, and (for custom
    panes) the literal command that will run when the workspace starts.

    For every kind other than CUSTOM_COMMAND, the actual launch command is
    resolved at launch time by dashboard.services.pane_commands, based on
    which tools are installed -- this spec only records the *strategy*
    (the pane kind), never a stale, wizard-time snapshot of a command.
    """

    kind: PaneKind
    display_name: str
    custom_command: str | None = None

    def __post_init__(self) -> None:
        if not self.display_name.strip():
            raise WorkspaceValidationError("Pane display name cannot be empty.")
        if self.kind is PaneKind.CUSTOM_COMMAND and not (self.custom_command or "").strip():
            raise WorkspaceValidationError("A custom command pane requires a non-empty command.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "display_name": self.display_name,
            "custom_command": self.custom_command,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaneSpec:
        return cls(
            kind=PaneKind(data["kind"]),
            display_name=data["display_name"],
            custom_command=data.get("custom_command"),
        )


@dataclass(frozen=True, slots=True)
class WindowSpec:
    """One tmux window: a name and an ordered, non-empty list of 1-4 panes.

    Pane order is preserved exactly as given -- it determines left-to-right,
    top-to-bottom pane placement when the layout is applied.
    """

    window_name: str
    panes: tuple[PaneSpec, ...]

    def __post_init__(self) -> None:
        if not self.window_name.strip():
            raise WorkspaceValidationError("Window name cannot be empty.")
        if not (1 <= len(self.panes) <= MAX_PANES_PER_WINDOW):
            raise WorkspaceValidationError(
                f"A window must have between 1 and {MAX_PANES_PER_WINDOW} panes "
                f"(got {len(self.panes)})."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "window_name": self.window_name,
            "panes": [pane.to_dict() for pane in self.panes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WindowSpec:
        return cls(
            window_name=data["window_name"],
            panes=tuple(PaneSpec.from_dict(pane) for pane in data["panes"]),
        )


@dataclass(frozen=True, slots=True)
class WorkspaceSpec:
    """One tmux session: a project, a session name, and 1+ ordered windows."""

    project_name: str
    project_path: Path
    session_name: str
    windows: tuple[WindowSpec, ...]

    def __post_init__(self) -> None:
        if not self.project_name.strip():
            raise WorkspaceValidationError("Project name cannot be empty.")
        if not self.session_name.strip():
            raise WorkspaceValidationError("Session name cannot be empty.")
        if not self.project_path.is_absolute():
            raise WorkspaceValidationError(
                f"Project path must be absolute after resolution, got: {self.project_path}"
            )
        if not self.windows:
            raise WorkspaceValidationError("A workspace must contain at least one window.")
        names = [window.window_name for window in self.windows]
        if len(names) != len(set(names)):
            raise WorkspaceValidationError("Window names must be unique within a workspace.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_name": self.project_name,
            "project_path": str(self.project_path),
            "session_name": self.session_name,
            "windows": [window.to_dict() for window in self.windows],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceSpec:
        return cls(
            project_name=data["project_name"],
            project_path=Path(data["project_path"]),
            session_name=data["session_name"],
            windows=tuple(WindowSpec.from_dict(window) for window in data["windows"]),
        )


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    """The structured hand-off from the wizard (Textual) to the tmux
    orchestration layer (plain Python, runs after the Textual app exits).
    """

    workspace: WorkspaceSpec
    init_git: bool
