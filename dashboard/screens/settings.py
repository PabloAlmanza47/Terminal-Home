"""Placeholder screen for dashboard settings, coming in a future version."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static


class SettingsScreen(Screen[None]):
    """Attractive stand-in until settings ship."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Settings", id="screen-title")
                yield Static(
                    "Theme choices, custom project roots, and shortcut\n"
                    "remapping are planned for a future version.\n\n"
                    "Press Escape to go back.",
                    id="placeholder-body",
                )
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()
