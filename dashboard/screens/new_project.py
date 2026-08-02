"""Placeholder screen for project scaffolding, coming in a future version."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static


class NewProjectScreen(Screen[None]):
    """Attractive stand-in until project scaffolding ships."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Create New Project", id="screen-title")
                yield Static(
                    "Project scaffolding is on the roadmap for a future version --\n"
                    "templates, git init, and a first commit, all from one prompt.\n\n"
                    "Press Escape to go back.",
                    id="placeholder-body",
                )
        yield Footer()

    def action_go_back(self) -> None:
        self.app.pop_screen()
