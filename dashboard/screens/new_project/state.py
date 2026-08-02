"""Mutable, in-progress wizard state shared by every New Project step.

These are deliberately *not* part of dashboard.models: they're draft,
UI-flow objects (a window mid-edit, pane kinds not yet finalized) rather
than the validated, persisted WorkspaceSpec/WindowSpec/PaneSpec models.
Each draft's to_*_spec() method performs the one-way conversion once its
step is confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dashboard.models import PANE_KIND_LABELS, PaneKind, PaneSpec, WindowSpec


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
    """Everything gathered across every step of the New Project wizard.

    A single instance is created when the wizard is entered from Home and
    threaded through every step screen by reference, so switching screens
    forward or backward never loses data.
    """

    project_name: str = ""
    folder_name: str = ""
    folder_name_touched: bool = False
    init_git: bool = True
    windows: list[WindowDraft] = field(default_factory=list)
    session_name: str = ""

    # The window currently being added or edited via Steps 2-3, and which
    # index in `windows` it will replace when confirmed (None means it will
    # be appended as a brand-new window).
    pending_window: WindowDraft = field(default_factory=WindowDraft)
    editing_index: int | None = None

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
