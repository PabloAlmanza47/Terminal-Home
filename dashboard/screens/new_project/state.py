"""Mutable, in-progress wizard state shared by every step of the window/pane
configuration flow -- used both by the New Project wizard (which also
creates the project directory and optionally runs `git init`) and by the
Open Project flow's "Configure Workspace" / "Edit Workspace" actions for an
already-existing project (which never touch the project directory).

These are deliberately *not* part of dashboard.models: they're draft,
UI-flow objects (a window mid-edit, pane kinds not yet finalized) rather
than the validated, persisted WorkspaceSpec/WindowSpec/PaneSpec models.
Each draft's to_*_spec() method performs the one-way conversion once its
step is confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from dashboard.models import PANE_KIND_LABELS, PaneKind, PaneSpec, WindowSpec, WorkspaceSpec


class WizardMode(str, Enum):
    """Which flow is driving the shared window/pane configuration screens.

    NEW_PROJECT: the "Create New Project" wizard -- creates the project
        directory, optionally runs `git init`, then launches.
    EXISTING_CREATE: "Configure Workspace" for a project that has no saved
        WorkspaceSpec yet -- never touches the project directory, saves
        the new spec, and launches, just like NEW_PROJECT's final step.
    EXISTING_EDIT: "Edit Workspace" for a project that already has a saved
        WorkspaceSpec -- never touches the project directory, saves the
        updated spec, and returns to Project Detail *without* launching,
        since a live tmux session (if any) must never be disturbed here.
    """

    NEW_PROJECT = "new_project"
    EXISTING_CREATE = "existing_create"
    EXISTING_EDIT = "existing_edit"


_STEP_PREFIXES: dict[WizardMode, str] = {
    WizardMode.NEW_PROJECT: "Create New Project",
    WizardMode.EXISTING_CREATE: "Configure Workspace",
    WizardMode.EXISTING_EDIT: "Edit Workspace",
}


@dataclass(slots=True)
class PaneDraft:
    """A single pane selection made in Step 2, before conversion to a
    validated PaneSpec.
    """

    kind: PaneKind
    custom_name: str = ""
    custom_command: str = ""

    @property
    def display_name(self) -> str:
        if self.kind is PaneKind.CUSTOM_COMMAND and self.custom_name.strip():
            return self.custom_name.strip()
        return PANE_KIND_LABELS[self.kind]

    def to_pane_spec(self) -> PaneSpec:
        return PaneSpec(
            kind=self.kind,
            display_name=self.display_name,
            custom_command=(
                self.custom_command.strip() if self.kind is PaneKind.CUSTOM_COMMAND else None
            ),
        )


@dataclass(slots=True)
class WindowDraft:
    """One window's worth of in-progress configuration: a name and an
    ordered list of pane drafts, exactly as arranged by the user.
    """

    window_name: str = ""
    panes: list[PaneDraft] = field(default_factory=list)

    def to_window_spec(self) -> WindowSpec:
        return WindowSpec(
            window_name=self.window_name.strip(),
            panes=tuple(pane.to_pane_spec() for pane in self.panes),
        )

    @classmethod
    def from_window_spec(cls, spec: WindowSpec) -> WindowDraft:
        panes: list[PaneDraft] = []
        for pane in spec.panes:
            if pane.kind is PaneKind.CUSTOM_COMMAND:
                panes.append(
                    PaneDraft(
                        kind=pane.kind,
                        custom_name=pane.display_name,
                        custom_command=pane.custom_command or "",
                    )
                )
            else:
                panes.append(PaneDraft(kind=pane.kind))
        return cls(window_name=spec.window_name, panes=panes)

    def clone(self) -> WindowDraft:
        return WindowDraft(
            window_name=self.window_name,
            panes=[PaneDraft(p.kind, p.custom_name, p.custom_command) for p in self.panes],
        )


@dataclass(slots=True)
class WizardState:
    """Everything gathered across every step of the window/pane
    configuration flow.

    A single instance is created when the flow is entered (from Home for
    New Project, or from Project Detail for Configure/Edit Workspace) and
    threaded through every step screen by reference, so switching screens
    forward or backward never loses data.
    """

    mode: WizardMode = WizardMode.NEW_PROJECT
    project_name: str = ""
    folder_name: str = ""
    folder_name_touched: bool = False
    init_git: bool = True
    windows: list[WindowDraft] = field(default_factory=list)
    session_name: str = ""

    # Set only in EXISTING_CREATE/EXISTING_EDIT mode: the already-resolved
    # canonical path of the project being configured. NEW_PROJECT mode
    # resolves its destination from folder_name instead (see
    # dashboard.services.project_creation).
    existing_project_path: Path | None = None

    # True when a currently-running tmux session exists for this project as
    # Edit Workspace was entered -- shown on the final review step as a
    # reminder that the update applies next time the session is recreated,
    # not immediately.
    warn_session_running: bool = False

    # The window currently being added or edited via Steps 2-3, and which
    # index in `windows` it will replace when confirmed (None means it will
    # be appended as a brand-new window).
    pending_window: WindowDraft = field(default_factory=WindowDraft)
    editing_index: int | None = None

    @classmethod
    def for_configuring_existing_project(cls, project_name: str, project_path: Path) -> WizardState:
        """A fresh state for "Configure Workspace" on a project with no
        saved WorkspaceSpec yet -- starts with no windows, entering
        directly into adding the first one.
        """
        state = cls(
            mode=WizardMode.EXISTING_CREATE,
            project_name=project_name,
            existing_project_path=project_path,
            init_git=False,
        )
        state.start_new_window()
        return state

    @classmethod
    def for_editing_workspace(
        cls, workspace: WorkspaceSpec, *, session_running: bool = False
    ) -> WizardState:
        """A state pre-populated from *workspace* for "Edit Workspace" --
        preserves the existing session_name so editing never mints a new
        tmux session identity for the project.
        """
        return cls(
            mode=WizardMode.EXISTING_EDIT,
            project_name=workspace.project_name,
            existing_project_path=workspace.project_path,
            init_git=False,
            session_name=workspace.session_name,
            windows=[WindowDraft.from_window_spec(window) for window in workspace.windows],
            warn_session_running=session_running,
        )

    def step_label(self, new_project_step: int, step_name: str) -> str:
        """The title shown at the top of a shared wizard step screen.

        *new_project_step* is that step's number in the 5-step New Project
        flow (2 for Configure Window, 3 for Layout Preview, 4 for Windows,
        5 for Review) -- Existing Project modes skip Step 1, so they
        renumber to a 4-step flow and shift every step down by one.
        """
        prefix = _STEP_PREFIXES[self.mode]
        if self.mode is WizardMode.NEW_PROJECT:
            return f"{prefix} -- Step {new_project_step} of 5: {step_name}"
        return f"{prefix} -- Step {new_project_step - 1} of 4: {step_name}"

    def start_new_window(self) -> None:
        """Reset the pending-window draft for adding a brand-new window."""
        default_name = "main" if not self.windows else ""
        self.pending_window = WindowDraft(window_name=default_name)
        self.editing_index = None

    def start_editing_window(self, index: int) -> None:
        """Load window *index* into the pending-window draft for editing."""
        self.pending_window = self.windows[index].clone()
        self.editing_index = index

    def commit_pending_window(self) -> None:
        """Save the pending-window draft into `windows`, replacing the
        window being edited or appending a new one.
        """
        if self.editing_index is not None:
            self.windows[self.editing_index] = self.pending_window
        else:
            self.windows.append(self.pending_window)
        self.editing_index = None

    def other_window_names(self) -> set[str]:
        """Window names already in use by windows other than the one
        currently being edited -- used to reject duplicates.
        """
        return {
            window.window_name
            for index, window in enumerate(self.windows)
            if index != self.editing_index
        }
