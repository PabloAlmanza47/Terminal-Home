"""Open Project screen: a searchable list of directories under ~/projects."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.services.projects import Project, discover_projects


class ProjectsScreen(Screen[None]):
    """Lists and filters project directories; shows the selected project's path."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("down", "focus_list", "Browse results"),
    ]

    def __init__(self) -> None:
        super().__init__()
        # Scanned once on screen creation; filtering below is purely in-memory.
        self._projects: list[Project] = discover_projects()

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Open Project", id="screen-title")
                yield Input(placeholder="Type to filter projects...", id="project-filter")
                yield OptionList(id="project-list")
                yield Static("", id="project-path")
        yield Footer()

    def on_mount(self) -> None:
        self._populate(self._projects)
        self.query_one("#project-filter", Input).focus()

    def _populate(self, projects: list[Project]) -> None:
        option_list = self.query_one("#project-list", OptionList)
        option_list.clear_options()
        path_widget = self.query_one("#project-path", Static)

        if not self._projects:
            option_list.add_option(Option("No projects found in ~/projects", disabled=True))
            path_widget.update("")
            return
        if not projects:
            option_list.add_option(Option("No matches", disabled=True))
            path_widget.update("")
            return
        for project in projects:
            option_list.add_option(Option(project.name, id=project.name))

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        filtered = (
            [p for p in self._projects if query in p.name.lower()] if query else self._projects
        )
        self._populate(filtered)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_path(event.option.id if event.option else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._show_path(event.option.id if event.option else None)

    def _show_path(self, name: str | None) -> None:
        path_widget = self.query_one("#project-path", Static)
        if not name:
            path_widget.update("")
            return
        match = next((p for p in self._projects if p.name == name), None)
        path_widget.update(f"Path: {match.path}" if match else "")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_focus_list(self) -> None:
        """Lets Down move focus from the filter box into the results list."""
        option_list = self.query_one("#project-list", OptionList)
        if option_list.option_count:
            option_list.focus()
