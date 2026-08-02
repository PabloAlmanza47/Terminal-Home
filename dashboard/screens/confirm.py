"""A small reusable Yes/No confirmation dialog for destructive,
metadata-only actions (Reset to Default Workspace, Forget Saved Workspace).

Used with `await self.app.push_screen_wait(ConfirmScreen(...))`, which
suspends the calling handler until the user confirms, cancels, or presses
Escape, then resumes with a plain bool.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmScreen(ModalScreen[bool]):
    """Modal Yes/No prompt. Resolves to True only if Confirm is pressed --
    Escape and Cancel both resolve to False.
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self, message: str, *, confirm_label: str = "Confirm", cancel_label: str = "Cancel"
    ) -> None:
        super().__init__()
        self.message = message
        self.confirm_label = confirm_label
        self.cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-panel", classes="panel"):
            yield Static(self.message, id="confirm-message")
            with Horizontal(classes="button-row"):
                yield Button(self.confirm_label, id="confirm-button", variant="error")
                yield Button(self.cancel_label, id="cancel-button")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-button":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
