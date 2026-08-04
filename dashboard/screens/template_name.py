"""Reusable keyboard-first template-name input modal."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from dashboard.models import MAX_TEMPLATE_NAME_LENGTH


class TemplateNameScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, *, value: str = "", submit_label: str = "Save") -> None:
        super().__init__()
        self.dialog_title = title
        self.value = value
        self.submit_label = submit_label

    def compose(self) -> ComposeResult:
        with Vertical(id="template-name-panel", classes="panel"):
            yield Static(self.dialog_title, id="template-name-title")
            yield Input(
                value=self.value,
                placeholder="Full Stack",
                max_length=MAX_TEMPLATE_NAME_LENGTH,
                id="template-name-input",
            )
            with Horizontal(classes="button-row"):
                yield Button(self.submit_label, id="template-name-submit", variant="primary")
                yield Button("Cancel", id="template-name-cancel")

    def on_mount(self) -> None:
        self.query_one("#template-name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "template-name-submit":
            self.dismiss(self.query_one("#template-name-input", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
