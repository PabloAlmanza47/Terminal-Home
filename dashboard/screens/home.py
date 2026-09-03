"""The landing screen: a compact title and status header plus a
responsive three-section dashboard -- Primary Actions, Recent Projects, and
Active Sessions.

Project/session/system data is refreshed by a background worker (mount and
F5 only, same pattern as ProjectsScreen); the per-second clock timer only
ever touches the clock/greeting text, never triggers a rescan. Nothing
here calls tmux directly -- Recent Projects and Active Sessions both hand
off through the same LaunchRequest/execute_launch_request path used by
ProjectDetailScreen.
"""

from __future__ import annotations

from datetime import datetime

from rich.cells import cell_len
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Input, Static
from textual.widgets.option_list import Option

from dashboard.art import artwork_for_size
from dashboard.models import AgentDeckAttachRequest, LaunchAction, LaunchRequest
from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.screens.new_project import NewProjectScreen
from dashboard.screens.project_detail import ProjectDetailScreen
from dashboard.screens.projects import ProjectsScreen
from dashboard.screens.settings import SettingsScreen
from dashboard.screens.system_info import SystemInfoScreen
from dashboard.screens.tmux_sessions import TmuxSessionsScreen
from dashboard.screens.workspace_templates import WorkspaceTemplatesScreen
from dashboard.services import tmux
from dashboard.services.activity import codex_status, server_status, workspace_status
from dashboard.services.formatting import greeting_for
from dashboard.services.project_rows import (
    ActivityProjectRow,
    format_activity_table,
    format_activity_table_header,
)
from dashboard.services.projects import (
    Project,
    ProjectScanResult,
    ProjectStatus,
    build_launch_request,
    disambiguated_display_names,
    format_scan_warnings,
    project_option_id,
    scan_all_projects,
    status_badge,
)
from dashboard.services.settings_store import load_settings
from dashboard.services.system_info import SystemInfo, gather_system_info
from dashboard.services.tmux import TmuxSession
from dashboard.widgets import ActionItem, KeyboardActionList
from dashboard.widgets import KeyboardOptionList as OptionList

# A terminal at least this many columns wide gets the 2x2 panel grid;
# narrower terminals get a single stacked column instead.
_WIDE_BREAKPOINT = 100
# Keep the Home lists bounded so ordinary content remains content-sized.
_MAX_RECENT_PROJECTS = 5
_MAX_ACTIVE_SESSIONS = 4

# Primary-menu option/action ids.
CONTINUE_PROJECT = "continue_project"
NEW_PROJECT = "new_project"
RESUME_TMUX = "resume_tmux"
SYSTEM_INFO = "system_info"
SETTINGS = "settings"
WORKSPACE_TEMPLATES = "workspace_templates"
EXIT = "exit"

_VIEW_ALL_PROJECTS = "__view_all_projects__"
_VIEW_ALL_SESSIONS = "__view_all_sessions__"
_CREATE_PROJECT_FROM_EMPTY = "__create_project_from_empty__"

# (digit shown, label, option/action id) -- shared by the menu's OptionList
# and its digit-key shortcuts, so the two can never fall out of sync.
_MENU_ITEMS: list[tuple[str, str, str]] = [
    ("1", "Continue Project", CONTINUE_PROJECT),
    ("2", "Create New Project", NEW_PROJECT),
    ("3", "Resume tmux Session", RESUME_TMUX),
    ("4", "System Information", SYSTEM_INFO),
    ("5", "Settings", SETTINGS),
    ("6", "Workspace Templates", WORKSPACE_TEMPLATES),
    ("7", "Exit", EXIT),
]


def _activity_card(
    status: ProjectStatus, display_name: str | None = None, width: int = 100
) -> str:
    workspace = workspace_status(status)
    server = server_status(status)
    agent, agent_count = codex_status(status)
    name = display_name or status.project.name
    name = _fit_activity_name(name, width)
    if status.workspace_metadata_error:
        name = f"{name}  [{status_badge(status)}]"
    count = f"({agent_count}) " if agent_count > 1 else ""
    codex_text = f"Codex {agent.glyph} {count}{agent.label}"
    first_status_line = (
        f"  Workspace {workspace.glyph} {workspace.label}"
        f"  Server {server.glyph} {server.label}"
    )
    status_lines = [first_status_line, f"  {codex_text}"] if width < 78 else [
        f"  {first_status_line[2:]}  {codex_text}"
    ]
    return "\n".join([name, *status_lines])


def _activity_value(value) -> str:
    return f"{value.glyph} {value.label}"


def _fit_activity_name(value: str, width: int) -> str:
    if cell_len(value) <= width:
        return value
    if width <= 1:
        return "…"
    left_width = max(1, (width - 1) // 3)
    left = value[:left_width]
    right = value[-(width - left_width - 1):]
    return f"{left}…{right}"


def _activity_display_name(status: ProjectStatus, display_name: str) -> str:
    if status.workspace_metadata_error:
        return f"{display_name}  [{status_badge(status)}]"
    return display_name


def _codex_activity_value(status: ProjectStatus) -> str:
    value, count = codex_status(status)
    prefix = f"({count}) " if count > 1 else ""
    return f"{value.glyph} {prefix}{value.label}"


def _session_label(session: TmuxSession, matched: ProjectStatus | None, *, compact: bool) -> str:
    name = matched.project.name if matched is not None else f"{session.name}  (unmatched session)"
    if compact:
        return name
    return f"{name}  ·  {session.windows} window(s)"


def _terminal_home_sessions(
    sessions: list[TmuxSession], statuses: list[ProjectStatus]
) -> list[TmuxSession]:
    """Remove only tmux sessions explicitly owned by Agent Deck metadata."""
    agent_tmux_names = {
        agent.tmux_session
        for status in statuses
        for agent in status.agent_sessions
        if agent.tmux_session
    }
    return [session for session in sessions if session.name not in agent_tmux_names]


def format_system_status(info: SystemInfo) -> str:
    """Render a SystemInfo as the compact multi-line body of the System
    Status panel. Pure and Textual-independent so it's directly testable.
    """
    system_line = f"WSL ({info.wsl_distro})" if info.wsl_distro else info.operating_system
    lines = [f"Host      {info.hostname}", f"System    {system_line}", f"Shell     {info.shell}"]

    if info.disk_usage is not None:
        d = info.disk_usage
        lines.append(f"Disk      {d.used_gb:.1f}/{d.total_gb:.1f} GB ({d.percent_used:.0f}%)")
    else:
        lines.append("Disk      unavailable")

    if info.memory_usage is not None:
        m = info.memory_usage
        lines.append(f"Memory    {m.used_gb:.1f}/{m.total_gb:.1f} GB ({m.percent_used:.0f}%)")
    else:
        lines.append("Memory    unavailable")

    lines.append(f"tmux      {info.tmux_version}")
    return "\n".join(lines)


def format_system_summary(info: SystemInfo, project_count: int, session_count: int) -> str:
    """Render the compact, non-detailed home header summary."""
    system = f"WSL {info.wsl_distro}" if info.wsl_distro else info.operating_system
    tmux_version = info.tmux_version or "tmux unavailable"
    session_word = "session" if session_count == 1 else "sessions"
    return (
        f"{project_count} projects • {session_count} active {session_word} • "
        f"{system} • {tmux_version}"
    )


def filter_project_statuses(
    statuses: list[ProjectStatus], query: str
) -> list[ProjectStatus]:
    """Return in-memory project matches with predictable name-first ranking."""
    needle = query.strip().casefold()
    if not needle:
        return list(statuses)

    matches: list[tuple[int, int, ProjectStatus]] = []
    for index, status in enumerate(statuses):
        name = status.project.name.casefold()
        path = str(status.canonical_path).casefold()
        if name == needle:
            rank = 0
        elif name.startswith(needle):
            rank = 1
        elif needle in name:
            rank = 2
        elif needle in path:
            rank = 3
        else:
            continue
        matches.append((rank, index, status))
    return [status for _, _, status in sorted(matches, key=lambda item: (item[0], item[1]))]


class HomeScreen(Screen[None]):
    """First screen shown on launch; every other screen is reached from here."""

    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("f5", "refresh", "Refresh"),
        ("a", "open_agent", "Open Agent"),
        ("/", "focus_project_search", "Search projects"),
        ("escape", "close_project_search", "Close Search"),
        ("1", "select_menu(0)", "Continue Project"),
        ("2", "select_menu(1)", "New Project"),
        ("3", "select_menu(2)", "Resume tmux"),
        ("4", "select_menu(3)", "System Info"),
        ("5", "select_menu(4)", "Settings"),
        ("6", "select_menu(5)", "Workspace Templates"),
        ("7", "select_menu(6)", "Exit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings: AppSettings = load_settings()
        self._scanning = False
        self._project_lookup: dict[str, Project] = {}
        self._session_lookup: dict[str, ProjectStatus] = {}
        self._agent_lookup: dict[str, ProjectStatus] = {}
        self._last_statuses: list[ProjectStatus] = []
        self._last_sessions: list[TmuxSession] = []
        self._last_scan_warning: str = ""
        self._wsl_distro: str | None = None
        self._active_section = "actions"
        self._system_summary = "Loading system summary..."
        self._initial_focus_pending = True
        self._project_search_active = False
        self._preferred_project_id: str | None = None

    def compose(self) -> ComposeResult:
        with Container(id="home", classes="screen-root"):
            with Vertical(id="home-shell"):
                with Vertical(id="home-header"):
                    yield Static(id="home-logo")
                    yield Static("Loading system summary...", id="home-meta")
                with Container(id="home-dashboard"):
                    with Vertical(id="panel-recent", classes="home-panel"):
                        yield Static(
                            "▸ Projects", id="heading-recent", classes="panel-heading"
                        )
                        yield Input(
                            placeholder="Search: type a project name...",
                            id="project-search",
                        )
                        yield Static("", id="recent-project-header", classes="table-header")
                        yield OptionList(
                            id="recent-projects-list",
                            classes="-textual-compact home-list",
                            reset_on_blur=True,
                        )
                    with Vertical(id="panel-actions", classes="home-panel"):
                        yield Static(
                            "  Actions", id="heading-actions", classes="panel-heading"
                        )
                        yield KeyboardActionList(
                            *[
                                ActionItem(option_id, f"{digit}  {label}")
                                for digit, label, option_id in _MENU_ITEMS
                            ],
                            id="main-menu",
                            reset_on_blur=True,
                        )
                    with Vertical(id="panel-sessions", classes="home-panel"):
                        yield Static(
                            "  Active Sessions", id="heading-sessions", classes="panel-heading"
                        )
                        yield OptionList(
                            id="active-sessions-list",
                            classes="-textual-compact home-list",
                            reset_on_blur=True,
                        )
        yield Static(
            "↑↓ Navigate   Enter Select   ←→ Sections   F5 Refresh   ? Help   q Quit",
            id="keyboard-footer",
        )

    def on_mount(self) -> None:
        self._apply_settings()
        self._update_clock()
        self.set_interval(1.0, self._update_clock)
        self.query_one("#recent-projects-list", OptionList).focus()
        self._update_footer("recent")
        self._start_scan()
        recovery_warning = getattr(self.app, "_settings_recovery_warning", None)
        if recovery_warning:
            self.app.notify(
                recovery_warning,
                title="Settings recovery",
                severity="warning",
            )

    def on_screen_resume(self) -> None:
        # Returning from Settings (or anywhere else) should reflect any
        # preference change immediately, without a full rescan.
        self._apply_settings()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout(event.size.width, event.size.height)

    # --- Settings / responsive layout ---------------------------------------

    def _apply_settings(self) -> None:
        self.settings = load_settings()
        self._apply_layout(self.size.width, self.size.height)

        compact = self.settings.layout_mode is LayoutMode.COMPACT
        self.query_one("#home-dashboard").set_class(compact, "compact")

        self._populate_recent_projects(self._last_statuses)
        self._populate_active_sessions(self._last_statuses, self._last_sessions)

    def _apply_layout(self, width: int, height: int) -> None:
        dashboard = self.query_one("#home-dashboard")
        wide = width >= _WIDE_BREAKPOINT
        dashboard.set_class(wide, "layout-wide")
        dashboard.set_class(not wide, "layout-narrow")

        # Preserve the primary workflow on short terminals. Secondary
        # dashboard panels remain available through their dedicated screens;
        # collapsing them here prevents the action list from being squeezed
        # to zero rows.
        compact_screen = height <= 24
        self.query_one("#panel-recent").display = True
        for panel_id in ("#panel-actions", "#panel-sessions"):
            self.query_one(panel_id).display = not compact_screen

        logo = self.query_one("#home-logo", Static)
        artwork = artwork_for_size(width, height, self.settings.artwork_enabled)
        logo.update(artwork or "")
        logo.display = artwork is not None
        self.query_one("#home-meta", Static).display = True

    def _update_clock(self) -> None:
        now = datetime.now()
        clock = " • ".join((now.strftime("%a %b %d"), now.strftime("%H:%M"), greeting_for(now)))
        text = (
            f"{clock}   •   {self._system_summary}"
            if self.settings.clock_visible
            else self._system_summary
        )
        self.query_one("#home-meta", Static).update(text)

    def _set_active_section(self, section: str) -> None:
        labels = {
            "actions": ("heading-actions", "panel-actions", "Primary Actions"),
            "recent": ("heading-recent", "panel-recent", "Recent Projects"),
            "sessions": ("heading-sessions", "panel-sessions", "Active Sessions"),
        }
        self._active_section = section
        for name, (heading_id, panel_id, label) in labels.items():
            self.query_one(f"#{heading_id}", Static).update(
                f"{'▸' if name == section else ' '} {label}"
            )
            self.query_one(f"#{panel_id}").set_class(name == section, "active-section")
        self._update_footer(section)

    def _update_footer(self, section: str) -> None:
        if self._project_search_active:
            self.query_one("#keyboard-footer", Static).update(
                "↑↓ Navigate   Enter Open   Esc Close Search"
            )
            return
        hints = {
            "actions": "↑↓ Navigate   Enter Select   ←→ Sections   F5 Refresh   ? Help   q Quit",
            "recent": (
                "↑↓ Navigate   Enter Details   a Agent   ←→ Sections   "
                "F5 Refresh   ? Help   q Quit"
            ),
            "sessions": "↑↓ Navigate   Enter Resume   ←→ Sections   F5 Refresh   ? Help   q Quit",
        }
        self.query_one("#keyboard-footer", Static).update(hints[section])

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        section_by_id = {
            "main-menu": "actions",
            "recent-projects-list": "recent",
            "active-sessions-list": "sessions",
        }
        section = section_by_id.get(event.widget.id or "")
        if section is not None:
            self._set_active_section(section)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "project-search":
            self._populate_recent_projects(self._last_statuses)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "project-search":
            return
        recent = self.query_one("#recent-projects-list", OptionList)
        if recent.highlighted is not None:
            self._handle_recent_project_selection(
                recent.get_option_at_index(recent.highlighted).id
            )

    def action_focus_project_search(self) -> None:
        if self._project_search_active:
            return
        self._preferred_project_id = self._selected_recent_project_id()
        self._project_search_active = True
        search = self.query_one("#project-search", Input)
        search.display = True
        search.value = ""
        self._set_active_section("recent")
        search.focus()

    def action_close_project_search(self) -> None:
        if self._project_search_active:
            self._close_project_search()

    def _close_project_search(self) -> None:
        search = self.query_one("#project-search", Input)
        self._project_search_active = False
        search.value = ""
        search.display = False
        self._populate_recent_projects(self._last_statuses)
        recent = self.query_one("#recent-projects-list", OptionList)
        recent.focus()
        self._set_active_section("recent")

    def _selected_recent_project_id(self) -> str | None:
        recent = self.query_one("#recent-projects-list", OptionList)
        if recent.highlighted is None:
            return None
        option = recent.get_option_at_index(recent.highlighted)
        return option.id if option.id in self._project_lookup else None

    def _move_search_selection(self, direction: int) -> None:
        recent = self.query_one("#recent-projects-list", OptionList)
        selectable = [
            index
            for index in range(recent.option_count)
            if not recent.get_option_at_index(index).disabled
        ]
        if not selectable:
            return
        current = recent.highlighted
        if current not in selectable:
            target = selectable[0 if direction > 0 else -1]
        else:
            position = selectable.index(current)
            target = selectable[(position + direction) % len(selectable)]
        recent.highlighted = target
        recent.focus()

    def on_key(self, event: events.Key) -> None:
        if self._project_search_active and isinstance(self.focused, Input):
            if event.key == "escape":
                self._close_project_search()
                event.stop()
                return
            if event.key in {"up", "down"}:
                self._move_search_selection(1 if event.key == "down" else -1)
                event.stop()
                return
            # Input owns horizontal cursor movement while search is active.
            return
        if event.key not in {"left", "right"}:
            return
        order = ["actions", "recent", "sessions"]
        index = order.index(getattr(self, "_active_section", "actions"))
        next_index = (index + (1 if event.key == "right" else -1)) % len(order)
        section = order[next_index]
        target = self.query_one(
            {
                "actions": "#main-menu",
                "recent": "#recent-projects-list",
                "sessions": "#active-sessions-list",
            }[section],
            KeyboardActionList if section == "actions" else OptionList,
        )
        if target.display:
            self._set_active_section(section)
            target.focus()
            event.stop()

    # --- Scanning (mount + F5 only -- never on the clock tick) -------------

    def action_refresh(self) -> None:
        self._start_scan()

    def _start_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        recent_list = self.query_one("#recent-projects-list", OptionList)
        sessions_list = self.query_one("#active-sessions-list", OptionList)
        recent_list.clear_options()
        recent_list.add_option(Option("Loading...", disabled=True))
        sessions_list.clear_options()
        sessions_list.add_option(Option("Loading...", disabled=True))
        self._system_summary = "Loading system summary..."
        self._update_clock()
        self.run_worker(self._scan, thread=True, exclusive=True)

    def _scan(self) -> None:
        """Runs in a worker thread: scan_all_projects, tmux session
        listing, and system info gathering all make blocking
        filesystem/subprocess calls.
        """
        scan_result = scan_all_projects()
        sessions = list(scan_result.tmux_sessions)
        system_info = gather_system_info()
        self.app.call_from_thread(self._on_scan_complete, scan_result, sessions, system_info)

    def _on_scan_complete(
        self,
        scan_result: ProjectScanResult,
        sessions: list[TmuxSession],
        system_info: SystemInfo,
    ) -> None:
        self._scanning = False
        self._last_statuses = list(scan_result.statuses)
        self._last_sessions = sessions
        self._last_scan_warning = format_scan_warnings(scan_result)
        self._wsl_distro = system_info.wsl_distro
        self._populate_recent_projects(self._last_statuses)
        self._populate_active_sessions(self._last_statuses, sessions)
        if self._initial_focus_pending:
            self._initial_focus_pending = False
            self._focus_initial_section()
        self._system_summary = format_system_summary(
            system_info,
            len(scan_result.statuses),
            len(_terminal_home_sessions(sessions, self._last_statuses)),
        )
        self._update_clock()

    def _focus_initial_section(self) -> None:
        """Focus the first useful project, or fall back to Primary Actions."""
        recent = self.query_one("#recent-projects-list", OptionList)
        if self._last_statuses and self.app.focused is recent:
            for index in range(recent.option_count):
                option = recent.get_option_at_index(index)
                if option.id in self._project_lookup:
                    recent.highlighted = index
                    recent.focus()
                    return
        if not self._last_statuses and self.app.focused is recent:
            menu = self.query_one("#main-menu", KeyboardActionList)
            menu.focus()
            self._set_active_section("actions")

    # --- Panel population ----------------------------------------------------

    def _populate_recent_projects(self, statuses: list[ProjectStatus]) -> None:
        option_list = self.query_one("#recent-projects-list", OptionList)
        header = self.query_one("#recent-project-header", Static)
        selected_id = self._selected_recent_project_id() or self._preferred_project_id
        option_list.clear_options()
        header.update("")

        if self._last_scan_warning:
            option_list.add_option(Option(f"Warning: {self._last_scan_warning}", disabled=True))

        ordered = sorted(statuses, key=lambda s: s.last_modified or datetime.min, reverse=True)
        query = (
            self.query_one("#project-search", Input).value if self._project_search_active else ""
        )
        matches = filter_project_statuses(ordered, query)
        visible = matches if self._project_search_active else matches[:_MAX_RECENT_PROJECTS]
        if not visible:
            if self._project_search_active:
                option_list.add_option(
                    Option(f'No projects match "{query.strip()}"', disabled=True)
                )
            elif not statuses:
                option_list.add_option(
                    Option("No projects yet in the configured project roots", disabled=True)
                )
                option_list.add_option(Option("Create New Project", id=_CREATE_PROJECT_FROM_EMPTY))
            return

        self._project_lookup = {project_option_id(status): status.project for status in visible}
        self._agent_lookup = {
            project_option_id(status): status
            for status in visible
            if status.agent_sessions
        }
        # Disambiguated against only what's actually shown together -- two
        # same-named projects both landing in the visible top N get a path
        # suffix; a uniquely-named project never does, even if some other
        # project sharing its name exists elsewhere but didn't make the cut.
        display_names = disambiguated_display_names(visible)
        content_width = option_list.content_region.width or option_list.size.width
        table_width = max(1, content_width - 2)
        activity_rows = [
            ActivityProjectRow(
                _activity_display_name(status, display_name),
                _activity_value(workspace_status(status)),
                _activity_value(server_status(status)),
                _codex_activity_value(status),
            )
            for status, display_name in zip(visible, display_names)
        ]
        if table_width >= 78:
            header.update(format_activity_table_header(table_width, activity_rows))
            labels = format_activity_table(activity_rows, table_width)
        else:
            labels = [
                _activity_card(status, display_name, table_width)
                for status, display_name in zip(visible, display_names)
            ]
        for status, label in zip(visible, labels):
            option_list.add_option(
                Option(
                    label,
                    id=project_option_id(status),
                )
            )
        preferred_index = next(
            (
                index
                for index in range(option_list.option_count)
                if option_list.get_option_at_index(index).id == selected_id
            ),
            next(
                (
                    index
                    for index in range(option_list.option_count)
                    if not option_list.get_option_at_index(index).disabled
                ),
                None,
            ),
        )
        option_list.highlighted = preferred_index
        option_list.add_option(Option("View All Projects", id=_VIEW_ALL_PROJECTS))

    def _populate_active_sessions(
        self, statuses: list[ProjectStatus], sessions: list[TmuxSession]
    ) -> None:
        option_list = self.query_one("#active-sessions-list", OptionList)
        option_list.clear_options()
        self._session_lookup = {}

        if not tmux.is_tmux_installed():
            option_list.add_option(Option("tmux is not installed", disabled=True))
            return
        sessions = _terminal_home_sessions(sessions, statuses)
        if not sessions:
            option_list.add_option(Option("No tmux sessions are running", disabled=True))
            return

        compact = self.settings.layout_mode is LayoutMode.COMPACT
        by_session_name = {status.expected_session_name: status for status in statuses}
        for session in sessions[:_MAX_ACTIVE_SESSIONS]:
            matched = by_session_name.get(session.name)
            if matched is not None:
                self._session_lookup[session.name] = matched
            option_list.add_option(
                Option(_session_label(session, matched, compact=compact), id=session.name)
            )
        if len(sessions) > _MAX_ACTIVE_SESSIONS:
            option_list.add_option(Option("View All Sessions", id=_VIEW_ALL_SESSIONS))

    # --- Selection handling ----------------------------------------------------

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        list_id = event.option_list.id
        option_id = event.option.id
        if list_id == "main-menu":
            self._handle_menu_selection(option_id)
        elif list_id == "recent-projects-list":
            self._handle_recent_project_selection(option_id)
        elif list_id == "active-sessions-list":
            self._handle_session_selection(option_id)

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_list.id == "main-menu":
            self._handle_menu_selection(event.action_id)

    def _handle_menu_selection(self, option_id: str | None) -> None:
        if option_id == CONTINUE_PROJECT:
            self.app.push_screen(ProjectsScreen())
        elif option_id == NEW_PROJECT:
            self.app.push_screen(NewProjectScreen())
        elif option_id == RESUME_TMUX:
            self.app.push_screen(TmuxSessionsScreen())
        elif option_id == SYSTEM_INFO:
            self.app.push_screen(SystemInfoScreen())
        elif option_id == SETTINGS:
            self.app.push_screen(SettingsScreen())
        elif option_id == WORKSPACE_TEMPLATES:
            self.app.push_screen(WorkspaceTemplatesScreen())
        elif option_id == EXIT:
            self.app.exit()

    def _handle_recent_project_selection(self, option_id: str | None) -> None:
        if option_id is None:
            return
        if option_id == _VIEW_ALL_PROJECTS:
            self.app.push_screen(ProjectsScreen())
            return
        if option_id == _CREATE_PROJECT_FROM_EMPTY:
            self.app.push_screen(NewProjectScreen())
            return
        project = self._project_lookup.get(option_id)
        if project is not None:
            self.app.push_screen(ProjectDetailScreen(project))

    def _handle_session_selection(self, option_id: str | None) -> None:
        if option_id is None:
            return
        if option_id == _VIEW_ALL_SESSIONS:
            self.app.push_screen(TmuxSessionsScreen())
            return
        matched = self._session_lookup.get(option_id)
        if matched is not None:
            self.app.exit(build_launch_request(matched))
        else:
            # No ProjectStatus at all for this session (it isn't tied to any
            # known project) -- build_launch_request needs a ProjectStatus,
            # so this orphan case is constructed directly from the session
            # name tmux itself reported.
            self.app.exit(
                LaunchRequest(
                    workspace=None,
                    init_git=False,
                    action=LaunchAction.ATTACH,
                    session_name=option_id,
                )
            )

    def action_open_agent(self) -> None:
        option_list = self.query_one("#recent-projects-list", OptionList)
        option = (
            option_list.get_option_at_index(option_list.highlighted)
            if option_list.highlighted is not None
            else None
        )
        if option is None:
            self.app.notify("Select a project first.", severity="warning")
            return
        status = self._agent_lookup.get(option.id or "")
        if status is None:
            self.app.notify("No matching Codex Agent Deck session.", severity="warning")
            return
        codex = [session for session in status.agent_sessions if session.tool.casefold() == "codex"]
        if not codex:
            self.app.notify("No matching Codex Agent Deck session.", severity="warning")
            return
        self.app.exit(AgentDeckAttachRequest(codex[0].id))

    # --- Global shortcuts ----------------------------------------------------

    def action_select_menu(self, index: int) -> None:
        if 0 <= index < len(_MENU_ITEMS):
            self._handle_menu_selection(_MENU_ITEMS[index][2])

    def action_quit_app(self) -> None:
        self.app.exit()
