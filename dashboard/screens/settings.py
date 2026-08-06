"""Settings screen: home screen presentation preferences (artwork, clock,
layout density). Each toggle persists immediately via settings_store --
there's no separate Save step, and no confirmation is needed since these
are simple, freely-reversible presentation preferences, not destructive
project actions.
"""

from __future__ import annotations

from dataclasses import replace

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, RadioButton, RadioSet, SelectionList, Static
from textual.widgets.selection_list import Selection

from dashboard.models.settings import AppSettings, CodingAgent, LayoutMode, TableHeaderColor
from dashboard.screens.project_discovery import ProjectDiscoveryScreen
from dashboard.screens.remote_projects import RemoteProjectsScreen
from dashboard.screens.ssh_hosts import SshHostsScreen
from dashboard.services.settings_store import load_settings, save_settings
from dashboard.widgets import ActionItem, CircularSelectionList, KeyboardActionList


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
                yield Static("Appearance", classes="panel-heading")
                yield CircularSelectionList(
                    Selection(
                        "Show Terminal Home artwork",
                        "artwork",
                        self.settings.artwork_enabled,
                    ),
                    Selection("Show clock and date", "clock", self.settings.clock_visible),
                    Selection(
                        "Compact layout",
                        "compact",
                        self.settings.layout_mode is LayoutMode.COMPACT,
                    ),
                    id="appearance-settings",
                    classes="settings-choice-group",
                )
                yield Static("Coding Agent", classes="panel-heading")
                with RadioSet(id="coding-agent-set", classes="settings-choice-group"):
                    yield RadioButton(
                        "None",
                        id="agent-none",
                        value=self.settings.coding_agent is CodingAgent.NONE,
                    )
                    yield RadioButton(
                        "Codex",
                        id="agent-codex",
                        value=self.settings.coding_agent is CodingAgent.CODEX,
                    )
                    yield RadioButton(
                        "Claude Code",
                        id="agent-claude",
                        value=self.settings.coding_agent is CodingAgent.CLAUDE_CODE,
                    )
                yield Static("CLI table header color", classes="panel-heading")
                with RadioSet(id="table-header-color-set", classes="settings-choice-group"):
                    for color, label in (
                        (TableHeaderColor.THEME, "Theme accent (default)"),
                        (TableHeaderColor.BLUE, "Blue"),
                        (TableHeaderColor.CYAN, "Cyan"),
                        (TableHeaderColor.GREEN, "Green"),
                        (TableHeaderColor.MAGENTA, "Magenta"),
                        (TableHeaderColor.YELLOW, "Yellow"),
                        (TableHeaderColor.WHITE, "White"),
                        (TableHeaderColor.NONE, "No color"),
                    ):
                        yield RadioButton(
                            label,
                            id=f"header-color-{color.value}",
                            value=self.settings.table_header_color is color,
                        )
                yield Static("Project Management", classes="panel-heading")
                yield KeyboardActionList(
                    ActionItem("project-discovery", "Project Discovery..."),
                    ActionItem("ssh-hosts", "SSH Hosts..."),
                    ActionItem("remote-projects", "Remote Projects..."),
                    id="settings-actions",
                )
                yield Static(
                    "Theme: open the command palette (ctrl+p) and search\n"
                    '"theme" -- your choice is applied immediately and\n'
                    "persists across launches.\n\n"
                    "CLI table headings use this color only on a TTY; NO_COLOR, pipes, and\n"
                    "the No color option always produce plain text.\n\n"
                    "Remote access actions do not probe or install anything on remote hosts.\n\n"
                    "Coding agents are never installed or authenticated by Terminal Home.\n\n"
                    "Press Escape to go back.",
                    id="placeholder-body",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#appearance-settings", CircularSelectionList).focus()

    def on_selection_list_selection_toggled(self, event: SelectionList.SelectionToggled) -> None:
        if event.selection_list.id != "appearance-settings":
            return
        selected = set(event.selection_list.selected)
        self.settings = replace(
            self.settings,
            artwork_enabled="artwork" in selected,
            clock_visible="clock" in selected,
            layout_mode=(LayoutMode.COMPACT if "compact" in selected else LayoutMode.EXPANDED),
        )
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.app.notify(
                f"Settings changed for this session, but couldn't be saved: {exc}",
                title="Settings",
                severity="error",
            )

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        selected_id = event.pressed.id
        if selected_id and selected_id.startswith("header-color-"):
            try:
                selected_color = TableHeaderColor(selected_id.removeprefix("header-color-"))
            except ValueError:
                return
            self.settings = replace(self.settings, table_header_color=selected_color)
            self._save_settings("Table header color")
            return
        selected = {
            "agent-none": CodingAgent.NONE,
            "agent-codex": CodingAgent.CODEX,
            "agent-claude": CodingAgent.CLAUDE_CODE,
        }.get(selected_id or "")
        if selected is None:
            return
        self.settings = replace(self.settings, coding_agent=selected)
        self._save_settings("Coding Agent")

    def _save_settings(self, label: str) -> None:
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.app.notify(
                f"{label} changed for this session, but couldn't be saved: {exc}",
                title="Settings",
                severity="error",
            )

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "project-discovery":
            self.app.push_screen(ProjectDiscoveryScreen())
        elif event.action_id == "ssh-hosts":
            self.app.push_screen(SshHostsScreen())
        elif event.action_id == "remote-projects":
            self.app.push_screen(RemoteProjectsScreen())

    def action_go_back(self) -> None:
        self.app.pop_screen()
