"""Settings screen: home screen presentation preferences (artwork, clock,
layout density). Each toggle persists immediately via settings_store --
there's no separate Save step, and no confirmation is needed since these
are simple, freely-reversible presentation preferences, not destructive
project actions.
"""

from __future__ import annotations

from dataclasses import replace

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Static

from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.screens.project_discovery import ProjectDiscoveryScreen
from dashboard.services.settings_store import load_settings, save_settings


class SettingsScreen(Screen[None]):
    """Home screen presentation preferences."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.settings: AppSettings = load_settings()

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Settings", id="screen-title")
                yield Static(
                    "These preferences control the home screen's appearance.",
                    classes="wizard-hint",
                )
                yield Checkbox(
                    "Show ASCII artwork",
                    value=self.settings.artwork_enabled,
                    id="artwork-checkbox",
                )
                yield Checkbox(
                    "Show clock and date",
                    value=self.settings.clock_visible,
                    id="clock-checkbox",
                )
                yield Checkbox(
                    "Compact layout",
                    value=self.settings.layout_mode is LayoutMode.COMPACT,
                    id="compact-checkbox",
                )
                with Horizontal(classes="button-row"):
                    yield Button("Project Discovery...", id="project-discovery-button")
                yield Static(
                    "Theme choices and shortcut remapping are planned for a\n"
                    "future version.\n\n"
                    "Press Escape to go back.",
                    id="placeholder-body",
                )
        yield Footer()

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "artwork-checkbox":
            self.settings = replace(self.settings, artwork_enabled=event.value)
        elif event.checkbox.id == "clock-checkbox":
            self.settings = replace(self.settings, clock_visible=event.value)
        elif event.checkbox.id == "compact-checkbox":
            new_mode = LayoutMode.COMPACT if event.value else LayoutMode.EXPANDED
            self.settings = replace(self.settings, layout_mode=new_mode)
        else:
            return
        save_settings(self.settings)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-discovery-button":
            self.app.push_screen(ProjectDiscoveryScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
