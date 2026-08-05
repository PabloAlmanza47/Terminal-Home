"""The lightweight keyboard reference overlay."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class HelpScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self, screen_bindings: list[tuple[str, str]]) -> None:
        super().__init__()
        self.screen_bindings = screen_bindings

    def compose(self) -> ComposeResult:
        lines = [
            "GLOBAL SHORTCUTS",
            "  ?  Keyboard help     q  Quit",
            "  /  Focus search      n  New Project",
            "  p  Projects          s  Settings",
            "",
            "NAVIGATION",
            "  Tab / Shift+Tab  Move focus",
            "  Arrow keys       Move in lists and choices",
            "  Enter / Space    Activate the focused item",
            "  Esc              Back or close",
        ]
        if self.screen_bindings:
            lines.extend(["", "THIS SCREEN"])
            lines.extend(f"  {key:<16}{label}" for key, label in self.screen_bindings)
        lines.extend(["", "Press Esc or choose Close to dismiss this help."])
        with Vertical(id="help-panel", classes="panel"):
            yield Static("Keyboard Shortcuts", id="help-title")
            yield Static("\n".join(lines), id="help-body", markup=False)
            yield Button("Close", id="help-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#help-close", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()
