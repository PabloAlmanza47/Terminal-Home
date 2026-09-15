"""Read-only Agent Deck session browser."""

from __future__ import annotations

from rich.cells import cell_len
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Static
from textual.widgets.option_list import Option

from dashboard.screens.new_agent import AgentProjectScreen, AgentWizardState
from dashboard.services.activity import agent_display_name
from dashboard.services.agent_hub import (
    AgentAssociation,
    AgentHubEntry,
    AgentHubSnapshot,
    AgentHubStatus,
    load_agent_hub_snapshot,
)
from dashboard.services.agent_view import AgentViewRouteRequest
from dashboard.services.projects import ProjectStatus, scan_all_projects
from dashboard.widgets import KeyboardOptionList as OptionList


def _fit(value: str, width: int) -> str:
    if cell_len(value) <= width:
        return value
    if width <= 1:
        return "…"
    if width == 2:
        return f"{value[0]}…"
    left_width = max(1, (width - 1) // 3)
    right_width = width - left_width - 1
    return f"{value[:left_width]}…{value[-right_width:]}"


def _agent_label(entry: AgentHubEntry, width: int, *, compact: bool = False) -> str:
    """Render a complete but compact row for the dedicated agent screen."""
    # KeyboardOptionList reserves two cells for its selection marker; the
    # detail lines also carry a two-cell indent.
    line_width = max(1, width - 4)
    title = _fit(entry.title, line_width)
    if entry.association is AgentAssociation.KNOWN_PROJECT:
        context = entry.project_name or "Registered project"
    elif entry.association is AgentAssociation.MISSING_PATH:
        context = "Missing path"
    else:
        context = "Unregistered"
    if compact:
        return _fit(f"{_status_glyph(entry)} {entry.title}  {entry.status.value}", max(1, width))
    return "\n".join(
        (
            f"{_status_glyph(entry)} {title}",
            f"  {_fit(f'{agent_display_name(entry.tool)} · {entry.status.value}', line_width)}",
            f"  {_fit(context, line_width)}",
            f"  {_fit(str(entry.path), line_width)}",
        )
    )


def _status_glyph(entry: AgentHubEntry) -> str:
    return {
        AgentHubStatus.WORKING: "●",
        AgentHubStatus.WAITING: "◐",
        AgentHubStatus.COMPLETED: "✓",
        AgentHubStatus.UNKNOWN: "?",
    }[entry.status]


def _matches(entry: AgentHubEntry, query: str) -> bool:
    needle = query.strip().casefold()
    if not needle:
        return True
    fields = (
        entry.title,
        entry.tool,
        entry.project_name or "",
        str(entry.path),
        entry.status.value,
    )
    return any(needle in field.casefold() for field in fields)


class AgentsScreen(Screen[None]):
    """Browse the current Agent Deck snapshot without owning agent state."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        Binding("n", "new_agent", "New Agent", priority=True),
        ("r", "refresh", "Refresh"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        snapshot: AgentHubSnapshot,
        project_statuses: tuple[ProjectStatus, ...] = (),
        attention_mode: bool = False,
    ) -> None:
        super().__init__()
        self._snapshot = snapshot
        self._project_statuses = project_statuses
        self._entries: tuple[AgentHubEntry, ...] = ()
        self._entry_lookup: dict[str, AgentHubEntry] = {}
        self._preferred_session_id: str | None = None
        self._scanning = False
        self._attention_mode = attention_mode

    def compose(self) -> ComposeResult:
        root_classes = "screen-root agents-screen-root"
        panel_classes = "panel agents-panel"
        if self._attention_mode:
            root_classes += " agents-attention-root"
            panel_classes = "agents-panel agents-attention-panel"
        with Container(classes=root_classes):
            with Vertical(classes=panel_classes):
                yield Static(
                    "Active Agents" if self._attention_mode else "Agents", id="screen-title"
                )
                yield Static("", id="agents-warning", classes="wizard-hint")
                yield Input(placeholder="Search agents...", id="agent-filter")
                yield OptionList(id="agent-list")
        yield Footer()

    def on_mount(self) -> None:
        self._populate(self._snapshot)
        # Keep the list as the primary keyboard target so ``n`` reliably
        # opens New Agent; the app-level ``/`` binding focuses search.
        self.query_one("#agent-list", OptionList).focus()
        if self._attention_mode:
            self.action_refresh()

    def on_resize(self, event: events.Resize) -> None:
        if self._snapshot.available and self._entries and not self._scanning:
            selected_id = self._selected_id()
            self._populate(self._snapshot, selected_id=selected_id)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_new_agent(self) -> None:
        self.app.push_screen(
            AgentProjectScreen(
                AgentWizardState(),
                tuple(self._project_statuses),
            )
        )

    def action_refresh(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.query_one("#agent-list", OptionList).clear_options()
        self.query_one("#agent-list", OptionList).add_option(Option("Refreshing...", disabled=True))
        self.run_worker(self._refresh, thread=True, exclusive=True)

    def _refresh(self) -> None:
        try:
            if self._attention_mode:
                scan = scan_all_projects()
                snapshot = load_agent_hub_snapshot(
                    scan.statuses,
                    agent_snapshot=scan.agent_snapshot,
                )
            else:
                snapshot = load_agent_hub_snapshot(self._project_statuses)
        except Exception as exc:
            snapshot = AgentHubSnapshot(False, warning=f"Refresh failed: {exc}")
        self.app.call_from_thread(self._on_refresh_complete, snapshot)

    def _on_refresh_complete(self, snapshot: AgentHubSnapshot) -> None:
        self._scanning = False
        self._snapshot = snapshot
        self._populate(snapshot, selected_id=self._preferred_session_id)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "agent-filter":
            self._populate(self._snapshot, selected_id=self._selected_id())

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id is None:
            return
        entry = self._entry_lookup.get(str(option_id))
        if entry is not None:
            self.app.exit(
                AgentViewRouteRequest(entry.session_id, entry.workspace_session_name)
            )

    def _selected_id(self) -> str | None:
        option_list = self.query_one("#agent-list", OptionList)
        if option_list.highlighted is None:
            return None
        option = option_list.get_option_at_index(option_list.highlighted)
        return str(option.id) if option.id is not None else None

    def _populate(self, snapshot: AgentHubSnapshot, selected_id: str | None = None) -> None:
        panel = self.query_one(".agents-panel")
        option_list = self.query_one("#agent-list", OptionList)
        query = self.query_one("#agent-filter", Input).value
        entries = tuple(entry for entry in snapshot.entries if _matches(entry, query))
        self._entries = entries
        self._entry_lookup = {entry.session_id: entry for entry in entries}
        option_list.clear_options()

        warning = f"Warning: {snapshot.warning}" if snapshot.warning else ""
        self.query_one("#agents-warning", Static).update(warning)
        if not snapshot.available:
            option_list.add_option(Option("Agent Deck is unavailable", disabled=True))
            return
        if not snapshot.entries:
            option_list.add_option(Option("No Agent Deck sessions found", disabled=True))
            return
        if not entries:
            option_list.add_option(Option(f'No agents match "{query.strip()}"', disabled=True))
            return

        content_width = (
            option_list.content_region.width
            or option_list.size.width
            or panel.region.width - 8
            or self.size.width - 12
        )
        for entry in entries:
            option_list.add_option(
                Option(
                    _agent_label(
                        entry, max(1, content_width), compact=self._attention_mode
                    ),
                    id=entry.session_id,
                )
            )
        preferred_id = selected_id if selected_id in self._entry_lookup else entries[0].session_id
        self._preferred_session_id = preferred_id
        self._restore_selection(preferred_id)

    def _restore_selection(self, selected_id: str | None) -> None:
        option_list = self.query_one("#agent-list", OptionList)
        target_id = selected_id if selected_id in self._entry_lookup else None
        if target_id is None and option_list.option_count:
            target_id = str(option_list.get_option_at_index(0).id)
        for index, option in enumerate(option_list.options):
            if option.id == target_id:
                option_list.highlighted = index
                return
