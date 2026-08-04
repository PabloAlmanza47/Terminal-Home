"""Reusable file-path input modal for template import and export."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


class TemplatePathScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, title: str, *, value: str = "", submit_label: str) -> None:
        super().__init__()
        self.dialog_title = title
        self.value = value
        self.submit_label = submit_label

    def compose(self) -> ComposeResult:
        with Vertical(id="template-path-panel", classes="panel"):
            yield Static(self.dialog_title, id="template-path-title")
            yield Input(
                value=self.value,
                placeholder="path/to/template.json",
                id="template-path-input",
            )
            with Horizontal(classes="button-row"):
                yield Button(self.submit_label, id="template-path-submit", variant="primary")
                yield Button("Cancel", id="template-path-cancel")

    def on_mount(self) -> None:
        self.query_one("#template-path-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "template-path-submit":
            self.dismiss(self.query_one("#template-path-input", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
