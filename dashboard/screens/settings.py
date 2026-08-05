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
from dashboard.screens.remote_projects import RemoteProjectsScreen
from dashboard.screens.ssh_hosts import SshHostsScreen
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
                    yield Button("SSH Hosts...", id="ssh-hosts-button")
                    yield Button("Remote Projects...", id="remote-projects-button")
                yield Static(
                    "Theme: open the command palette (ctrl+p) and search\n"
                    "\"theme\" -- your choice is applied immediately and\n"
                    "persists across launches.\n\n"
                    "Shortcut remapping is planned for a future version.\n\n"
                    "Press Escape to go back.",
                    id="placeholder-body",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#artwork-checkbox", Checkbox).focus()

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
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.app.notify(
                f"Settings changed for this session, but couldn't be saved: {exc}",
                title="Settings",
                severity="error",
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "project-discovery-button":
            self.app.push_screen(ProjectDiscoveryScreen())
        elif event.button.id == "ssh-hosts-button":
            self.app.push_screen(SshHostsScreen())
        elif event.button.id == "remote-projects-button":
            self.app.push_screen(RemoteProjectsScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
