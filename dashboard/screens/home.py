"""The landing screen: a header (title, artwork, clock, greeting) plus a
responsive 4-panel dashboard -- Primary Actions, Recent Projects, Active
Sessions, and System Status.

Project/session/system data is refreshed by a background worker (mount and
F5 only, same pattern as ProjectsScreen); the per-second clock timer only
ever touches the clock/greeting text, never triggers a rescan. Nothing
here calls tmux directly -- Recent Projects and Active Sessions both hand
off through the same LaunchRequest/execute_launch_request path used by
ProjectDetailScreen.
"""

from __future__ import annotations

from datetime import datetime

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.art import ASCII_ART
from dashboard.models import LaunchAction, LaunchRequest
from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.screens.new_project import NewProjectScreen
from dashboard.screens.project_detail import ProjectDetailScreen
from dashboard.screens.projects import ProjectsScreen
from dashboard.screens.settings import SettingsScreen
from dashboard.screens.system_info import SystemInfoScreen
from dashboard.screens.tmux_sessions import TmuxSessionsScreen
from dashboard.services import tmux
from dashboard.services.formatting import format_relative_time, greeting_for
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

# A terminal at least this many columns wide gets the 2x2 panel grid;
# narrower terminals get a single stacked column instead.
_WIDE_BREAKPOINT = 100
# Below this many rows, the artwork logo is hidden regardless of the
# artwork_enabled setting -- there isn't room for it and all four panels.
_MIN_HEIGHT_FOR_ART = 28
_MAX_RECENT_PROJECTS = 5

# Primary-menu option/action ids.
CONTINUE_PROJECT = "continue_project"
NEW_PROJECT = "new_project"
RESUME_TMUX = "resume_tmux"
SYSTEM_INFO = "system_info"
SETTINGS = "settings"
EXIT = "exit"

_VIEW_ALL_PROJECTS = "__view_all_projects__"
_CREATE_PROJECT_FROM_EMPTY = "__create_project_from_empty__"

# (digit shown, label, option/action id) -- shared by the menu's OptionList
# and its digit-key shortcuts, so the two can never fall out of sync.
_MENU_ITEMS: list[tuple[str, str, str]] = [
    ("1", "Continue Project", CONTINUE_PROJECT),
    ("2", "Create New Project", NEW_PROJECT),
    ("3", "Resume tmux Session", RESUME_TMUX),
    ("4", "System Information", SYSTEM_INFO),
    ("5", "Settings", SETTINGS),
    ("6", "Exit", EXIT),
]


def _recent_project_label(status: ProjectStatus, display_name: str, *, compact: bool) -> str:
    badge = status_badge(status)
    if compact:
        return f"{display_name}  [{badge}]"
    parts = [f"{display_name}  [{badge}]"]
    if status.git_branch:
        parts.append(f"({status.git_branch})")
    if status.last_modified is not None:
        parts.append(f"· {format_relative_time(status.last_modified)}")
    return "  ".join(parts)


def _session_label(session: TmuxSession, matched: ProjectStatus | None, *, compact: bool) -> str:
    name = matched.project.name if matched is not None else f"{session.name}  (unmatched session)"
    if compact:
        return name
    return f"{name}  ·  {session.windows} window(s)"


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


class HomeScreen(Screen[None]):
    """First screen shown on launch; every other screen is reached from here."""

    BINDINGS = [
        ("q", "quit_app", "Quit"),
        ("f5", "refresh", "Refresh"),
        ("1", "select_menu(0)", "Continue Project"),
        ("2", "select_menu(1)", "New Project"),
        ("3", "select_menu(2)", "Resume tmux"),
        ("4", "select_menu(3)", "System Info"),
        ("5", "select_menu(4)", "Settings"),
        ("6", "select_menu(5)", "Exit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.settings: AppSettings = load_settings()
        self._scanning = False
        self._project_lookup: dict[str, Project] = {}
        self._session_lookup: dict[str, ProjectStatus] = {}
        self._last_statuses: list[ProjectStatus] = []
        self._last_sessions: list[TmuxSession] = []
        self._last_scan_warning: str = ""
        self._wsl_distro: str | None = None

    def compose(self) -> ComposeResult:
        with Container(id="home", classes="screen-root"):
            with Vertical(id="home-shell"):
                with Vertical(id="home-header"):
                    yield Static(ASCII_ART, id="home-logo")
                    yield Static("TERMINAL HOME", id="home-title")
                    yield Static(id="home-subtitle")
                with Container(id="home-dashboard"):
                    with Vertical(id="panel-actions", classes="home-panel"):
                        yield Static("Primary Actions", classes="panel-heading")
                        yield OptionList(
                            *[
                                Option(f"{digit}  {label}", id=option_id)
                                for digit, label, option_id in _MENU_ITEMS
                            ],
                            id="main-menu",
                        )
                    with Vertical(id="panel-recent", classes="home-panel"):
                        yield Static("Recent Projects", classes="panel-heading")
                        yield OptionList(id="recent-projects-list")
                    with Vertical(id="panel-sessions", classes="home-panel"):
                        yield Static("Active Sessions", classes="panel-heading")
                        yield OptionList(id="active-sessions-list")
                    with Vertical(id="panel-status", classes="home-panel"):
                        yield Static("System Status", classes="panel-heading")
                        with VerticalScroll(id="system-status-scroll"):
                            yield Static("Loading...", id="system-status-body")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_settings()
        self._update_clock()
        self.set_interval(1.0, self._update_clock)
        self.query_one("#main-menu", OptionList).focus()
        self._start_scan()

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

        # The artwork logo is the header's biggest line-count cost -- once
        # the terminal is too short to comfortably fit it *and* all four
        # panels below, it's hidden regardless of the artwork_enabled
        # setting (which only controls whether it CAN show, not whether a
        # cramped terminal is forced to).
        show_art = self.settings.artwork_enabled and height >= _MIN_HEIGHT_FOR_ART
        self.query_one("#home-logo", Static).display = show_art
        self.query_one("#home-subtitle", Static).display = self.settings.clock_visible

    def _update_clock(self) -> None:
        now = datetime.now()
        parts = [now.strftime("%a %b %d %Y  ·  %H:%M:%S"), f"{greeting_for(now)}!"]
        if self._wsl_distro:
            parts.append(f"WSL: {self._wsl_distro}")
        self.query_one("#home-subtitle", Static).update("   ·   ".join(parts))

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
        self.query_one("#system-status-body", Static).update("Loading...")
        self.run_worker(self._scan, thread=True, exclusive=True)

    def _scan(self) -> None:
        """Runs in a worker thread: scan_all_projects, tmux session
        listing, and system info gathering all make blocking
        filesystem/subprocess calls.
        """
        scan_result = scan_all_projects()
        sessions = tmux.list_tmux_sessions() if tmux.is_tmux_installed() else []
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
        self.query_one("#system-status-body", Static).update(format_system_status(system_info))

    # --- Panel population ----------------------------------------------------

    def _populate_recent_projects(self, statuses: list[ProjectStatus]) -> None:
        option_list = self.query_one("#recent-projects-list", OptionList)
        option_list.clear_options()
        self._project_lookup = {project_option_id(status): status.project for status in statuses}

        if self._last_scan_warning:
            option_list.add_option(Option(f"Warning: {self._last_scan_warning}", disabled=True))

        if not statuses:
            option_list.add_option(
                Option("No projects yet in the configured project roots", disabled=True)
            )
            option_list.add_option(Option("Create New Project", id=_CREATE_PROJECT_FROM_EMPTY))
            return

        compact = self.settings.layout_mode is LayoutMode.COMPACT
        recent = sorted(statuses, key=lambda s: s.last_modified or datetime.min, reverse=True)
        visible = recent[:_MAX_RECENT_PROJECTS]
        # Disambiguated against only what's actually shown together -- two
        # same-named projects both landing in the visible top N get a path
        # suffix; a uniquely-named project never does, even if some other
        # project sharing its name exists elsewhere but didn't make the cut.
        display_names = disambiguated_display_names(visible)
        for status, display_name in zip(visible, display_names):
            option_list.add_option(
                Option(
                    _recent_project_label(status, display_name, compact=compact),
                    id=project_option_id(status),
                )
            )
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
        if not sessions:
            option_list.add_option(Option("No tmux sessions are running", disabled=True))
            return

        compact = self.settings.layout_mode is LayoutMode.COMPACT
        by_session_name = {status.expected_session_name: status for status in statuses}
        for session in sessions:
            matched = by_session_name.get(session.name)
            if matched is not None:
                self._session_lookup[session.name] = matched
            option_list.add_option(
                Option(_session_label(session, matched, compact=compact), id=session.name)
            )

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

    # --- Global shortcuts ----------------------------------------------------

    def action_select_menu(self, index: int) -> None:
        if 0 <= index < len(_MENU_ITEMS):
            self._handle_menu_selection(_MENU_ITEMS[index][2])

    def action_quit_app(self) -> None:
        self.app.exit()
