"""Dedicated, popup-sized Terminal Home workspace selector."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Input, Static
from textual.widgets.option_list import Option

from dashboard.models import LaunchRequest
from dashboard.services import tmux
from dashboard.services.project_launch import ProjectLaunchPreparationError, prepare_project_launch
from dashboard.services.projects import build_launch_request, scan_all_projects
from dashboard.services.quick_switch import (
    QuickSwitchEntry,
    build_quick_switch_entries,
    filter_quick_switch_entries,
    format_quick_switch_row,
)
from dashboard.services.workspace_store import WorkspaceStoreVersionError
from dashboard.widgets import KeyboardOptionList as OptionList


class QuickSwitchScreen(Screen[LaunchRequest | None]):
    BINDINGS = [("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[QuickSwitchEntry] = []
        self._lookup: dict[str, QuickSwitchEntry] = {}

    def compose(self) -> ComposeResult:
        with Container(classes="quick-switch-root"):
            with Vertical(classes="quick-switch-panel"):
                yield Static("Switch Workspace", id="quick-switch-title")
                with Horizontal(id="quick-switch-search-bar"):
                    yield Static("›", id="quick-switch-search-prefix")
                    yield Input(placeholder="Search projects…", id="quick-switch-search")
                yield Static("Loading projects…", id="quick-switch-status")
                yield OptionList(id="quick-switch-list")
                yield Static("↑↓ Select   Enter Switch   Esc Close", id="quick-switch-help")

    def on_mount(self) -> None:
        try:
            scan = scan_all_projects()
            current = tmux.current_client_session()
            self.entries = build_quick_switch_entries(scan, current)
            status_widget = self.query_one("#quick-switch-status", Static)
            if scan.warnings:
                status_widget.update(scan.warnings[0])
            else:
                status_widget.display = False
            self._refresh_rows()
            # Adding the first selectable project makes OptionList ensure it
            # is visible, which can scroll past the non-selectable ACTIVE
            # heading. Reset only the initial viewport; the selection stays
            # on the first project and subsequent keyboard scrolling remains
            # unchanged.
            option_list = self.query_one("#quick-switch-list", OptionList)
            self.call_after_refresh(lambda: option_list.scroll_home(animate=False))
            self.query_one("#quick-switch-search", Input).focus()
        except (OSError, ValueError, WorkspaceStoreVersionError) as exc:
            self.query_one("#quick-switch-status", Static).update(f"Could not load projects: {exc}")
            self.query_one("#quick-switch-search", Input).focus()

    def _refresh_rows(self) -> None:
        option_list = self.query_one("#quick-switch-list", OptionList)
        query = self.query_one("#quick-switch-search", Input).value
        visible = filter_quick_switch_entries(self.entries, query)
        self._lookup = {entry.option_id: entry for entry in visible}
        option_list.clear_options()
        active = [entry for entry in visible if entry.is_running]
        recent = [entry for entry in visible if not entry.is_running]
        if active:
            option_list.add_option(Option("ACTIVE", disabled=True))
            self._add_entry_rows(option_list, active)
        if recent:
            option_list.add_option(Option("RECENT", disabled=True))
            self._add_entry_rows(option_list, recent)
        if not visible:
            option_list.add_option(Option("No matching projects", disabled=True))

    @staticmethod
    def _add_entry_rows(option_list: OptionList, entries: list[QuickSwitchEntry]) -> None:
        # Leave room for KeyboardOptionList's focus marker and the row
        # padding. The formatter then uses terminal cell width and truncates
        # the name before the status can collide or wrap.
        width = max(12, (option_list.content_region.width or 64) - 6)
        for entry in entries:
            option_list.add_option(
                Option(format_quick_switch_row(entry, width), id=entry.option_id)
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "quick-switch-search":
            self._refresh_rows()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "quick-switch-search":
            self._select_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._select(str(event.option.id))

    def _select_highlighted(self) -> None:
        options = self.query_one("#quick-switch-list", OptionList)
        if options.highlighted is not None:
            option = options.get_option_at_index(options.highlighted)
            if option.id:
                self._select(str(option.id))

    def _select(self, option_id: str) -> None:
        entry = self._lookup.get(option_id)
        if entry is None:
            return
        try:
            request = (
                build_launch_request(entry.status)
                if entry.is_running
                else prepare_project_launch(entry.status).request
            )
            self.app.exit(request)
        except (
            OSError,
            ValueError,
            ProjectLaunchPreparationError,
            WorkspaceStoreVersionError,
        ) as exc:
            self.query_one("#quick-switch-status", Static).update(f"Cannot open project: {exc}")

    def action_close(self) -> None:
        self.app.exit(None)
