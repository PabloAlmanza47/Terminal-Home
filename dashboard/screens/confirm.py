"""A small reusable Yes/No confirmation dialog for destructive,
metadata-only actions (Reset to Default Workspace, Forget Saved Workspace).

Used with `await self.app.push_screen_wait(ConfirmScreen(...))`, which
suspends the calling handler until the user confirms, cancels, or presses
Escape, then resumes with a plain bool.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from dashboard.widgets import ActionItem, KeyboardActionList


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
            yield KeyboardActionList(
                ActionItem("cancel", self.cancel_label),
                ActionItem("confirm", self.confirm_label, dangerous=True),
                id="confirm-actions",
            )

    def on_mount(self) -> None:
        # Cancellation is the safe default for every destructive confirmation.
        self.query_one("#confirm-actions", KeyboardActionList).focus()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
