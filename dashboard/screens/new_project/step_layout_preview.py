"""Step 3 of the New Project wizard: a visual preview of the pane layout
that the Step 2 selection/order will produce.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from dashboard.models.layout import render_pane_preview
from dashboard.screens.new_project.state import WizardState


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
                with Horizontal(classes="button-row"):
                    yield Button("Back", id="back-button")
                    yield Button("Continue", id="continue-button", variant="primary")
                    yield Button("Cancel", id="cancel-button")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-button":
            self._continue()
        elif event.button.id == "back-button":
            self.action_back()
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def on_mount(self) -> None:
        self.query_one("#continue-button", Button).focus()

    def _continue(self) -> None:
        self.state.commit_pending_window()

        from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen

        self.app.switch_screen(WindowSummaryScreen(self.state))

    def action_back(self) -> None:
        from dashboard.screens.new_project.step_window_config import WindowConfigScreen

        self.app.switch_screen(WindowConfigScreen(self.state, back_target=self.back_target))

    def action_cancel(self) -> None:
        self.app.pop_screen()
