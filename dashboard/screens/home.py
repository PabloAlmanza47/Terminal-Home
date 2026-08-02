"""The landing screen: title, art, clock, welcome message, and the main menu."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.art import ASCII_ART, to_wide_text
from dashboard.screens.new_project import NewProjectScreen
from dashboard.screens.projects import ProjectsScreen
from dashboard.screens.settings import SettingsScreen
from dashboard.screens.system_info import SystemInfoScreen
from dashboard.screens.tmux_sessions import TmuxSessionsScreen

# Option ids for the main menu, used to dispatch on selection below.
OPEN_PROJECT = "open_project"
RESUME_TMUX = "resume_tmux"
NEW_PROJECT = "new_project"
SYSTEM_INFO = "system_info"
SETTINGS = "settings"
EXIT = "exit"


class HomeScreen(Screen[None]):
    """First screen shown on launch; every other screen is reached from here."""

    BINDINGS = [("q", "quit_app", "Quit")]

    def compose(self) -> ComposeResult:
        with Container(id="home", classes="screen-root"):
            with Vertical(id="home-panel", classes="panel"):
                yield Static(to_wide_text("PABLO'S WORKSPACE"), id="title")
                yield Static(ASCII_ART, id="art")
                yield Static(id="clock")
                yield Static("Welcome back, Pablo. Where do we start today?", id="welcome")
                yield OptionList(
                    Option("Open Project", id=OPEN_PROJECT),
                    Option("Resume tmux Session", id=RESUME_TMUX),
                    Option("Create New Project", id=NEW_PROJECT),
                    Option("System Information", id=SYSTEM_INFO),
                    Option("Settings", id=SETTINGS),
                    Option("Exit", id=EXIT),
                    id="main-menu",
                )
        yield Footer()

    def on_mount(self) -> None:
        self._update_clock()
        self.set_interval(1.0, self._update_clock)
        self.query_one("#main-menu", OptionList).focus()

    def _update_clock(self) -> None:
        now = datetime.now().strftime("%A, %B %d %Y  |  %H:%M:%S")
        self.query_one("#clock", Static).update(now)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id
        if option_id == OPEN_PROJECT:
            self.app.push_screen(ProjectsScreen())
        elif option_id == RESUME_TMUX:
            self.app.push_screen(TmuxSessionsScreen())
        elif option_id == NEW_PROJECT:
            self.app.push_screen(NewProjectScreen())
        elif option_id == SYSTEM_INFO:
            self.app.push_screen(SystemInfoScreen())
        elif option_id == SETTINGS:
            self.app.push_screen(SettingsScreen())
        elif option_id == EXIT:
            self.app.exit()

    def action_quit_app(self) -> None:
        self.app.exit()
