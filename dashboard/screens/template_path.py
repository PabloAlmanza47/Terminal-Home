"""Reusable file-path input modal for template import and export."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from dashboard.widgets import ActionItem, KeyboardActionList


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
            yield KeyboardActionList(
                ActionItem("submit", self.submit_label),
                ActionItem("cancel", "Cancel"),
                id="template-path-actions",
            )

    def on_mount(self) -> None:
        self.query_one("#template-path-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "submit":
            self.dismiss(self.query_one("#template-path-input", Input).value)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
