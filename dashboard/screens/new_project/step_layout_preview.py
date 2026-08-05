"""Step 3 of the New Project wizard: a visual preview of the pane layout
that the Step 2 selection/order will produce.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static

from dashboard.models.layout import render_pane_preview
from dashboard.screens.new_project.state import WizardState
from dashboard.widgets import ActionItem, KeyboardActionList


class LayoutPreviewScreen(Screen[None]):
    """Read-only preview of the current window's pane layout."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, state: WizardState, back_target: str | None) -> None:
        super().__init__()
        self.state = state
        self.back_target = back_target

    def compose(self) -> ComposeResult:
        window = self.state.pending_window
        labels = [pane.display_name for pane in window.panes]
        preview_lines = render_pane_preview(labels)

        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static(self.state.step_label(3, "Layout Preview"), id="screen-title")
                yield Static(f"Window: {window.window_name}", id="preview-window-name")
                yield Static(
                    "\n".join(f"{i + 1}. {label}" for i, label in enumerate(labels)),
                    id="preview-pane-list",
                )
                yield Static("\n".join(preview_lines), id="preview-grid", classes="preview-grid")
                yield KeyboardActionList(
                    ActionItem("back", "Back"),
                    ActionItem("continue", "Continue"),
                    ActionItem("cancel", "Cancel"),
                    id="preview-actions",
                )
        yield Footer()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "continue":
            self._continue()
        elif event.action_id == "back":
            self.action_back()
        elif event.action_id == "cancel":
            self.action_cancel()

    def on_mount(self) -> None:
        self.query_one("#preview-actions", KeyboardActionList).focus()

    def _continue(self) -> None:
        self.state.commit_pending_window()

        from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen

        self.app.switch_screen(WindowSummaryScreen(self.state))

    def action_back(self) -> None:
        from dashboard.screens.new_project.step_window_config import WindowConfigScreen

        self.app.switch_screen(WindowConfigScreen(self.state, back_target=self.back_target))

    def action_cancel(self) -> None:
        self.app.pop_screen()
