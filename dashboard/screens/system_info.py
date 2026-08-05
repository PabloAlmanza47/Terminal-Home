"""System Information screen: a read-only snapshot of the host machine."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from dashboard.services.system_info import gather_system_info
from dashboard.widgets import ActionItem, KeyboardActionList


class SystemInfoScreen(Screen[None]):
    """Displays hostname, OS, Python version, shell, tmux version, and disk usage."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        info = gather_system_info()
        disk = info.disk_usage
        disk_line = (
            f"{disk.used_gb} GB used of {disk.total_gb} GB "
            f"({disk.percent_used}% full, {disk.free_gb} GB free)"
            if disk is not None
            else "unavailable"
        )
        rows = [
            ("Hostname", info.hostname),
            ("Operating System", info.operating_system),
            ("Python Version", info.python_version),
            ("Shell", info.shell),
            ("tmux Version", info.tmux_version),
            ("Disk Usage (home)", disk_line),
        ]

        with VerticalScroll(classes="screen-root system-info-scroll"):
            with Vertical(classes="panel system-info-panel"):
                yield Static("System Information", id="screen-title")
                with Vertical(id="info-rows"):
                    for label, value in rows:
                        yield Static(f"{label:<20} {value}", classes="info-row")
                yield KeyboardActionList(ActionItem("back", "Back"), id="system-info-actions")
        yield Footer()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        self.action_go_back()

    def on_mount(self) -> None:
        self.query_one("#system-info-actions", KeyboardActionList).focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()
