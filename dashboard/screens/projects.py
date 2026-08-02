"""Open Project screen: a searchable list of directories under ~/projects,
each annotated with its live status (git branch, saved workspace, running
tmux session). Selecting a project opens ProjectDetailScreen, where the
actual launch decision is made -- this screen only discovers and displays.

Scanning is done in a worker thread (discover_projects + gather_project_status
both make blocking filesystem/subprocess calls) so the UI stays responsive
and can show a loading indicator while a scan is in progress.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.screens.project_detail import ProjectDetailScreen
from dashboard.services import tmux
from dashboard.services.projects import (
    Project,
    ProjectStatus,
    discover_projects,
    gather_project_status,
)


def _row_label(project: Project, status: ProjectStatus | None) -> str:
    if status is None:
        return project.name

    if status.session_running:
        tag = "Running"
    elif status.workspace_metadata_error:
        tag = "Saved Workspace (corrupted)"
    elif status.saved_workspace is not None:
        tag = "Saved Workspace"
    else:
        tag = "Not Configured"

    label = f"{project.name}  [{tag}]"
    if status.is_git_repo and status.git_branch:
        label += f"  ({status.git_branch})"
    return label


def _details_text(status: ProjectStatus) -> str:
    lines = [f"Path: {status.canonical_path}"]
    if not status.project_dir_exists:
        lines.append("Warning: this directory no longer exists.")
    if status.is_git_repo:
        lines.append(f"Git branch: {status.git_branch or '(unknown)'}")
    else:
        lines.append("Git: not a git repository")
    if status.last_modified is not None:
        lines.append(f"Last modified: {status.last_modified:%Y-%m-%d %H:%M}")
    if not status.tmux_available:
        lines.append("tmux is not installed.")
    return "\n".join(lines)


class ProjectsScreen(Screen[None]):
    """Lists and filters project directories; Enter opens project detail."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("down", "focus_list", "Browse results"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._projects: list[Project] = []
        self._statuses: dict[str, ProjectStatus] = {}
        self._scanning = False

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Open Project", id="screen-title")
                yield Input(placeholder="Type to filter projects...", id="project-filter")
                yield OptionList(id="project-list")
                yield Static("", id="project-path")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#project-filter", Input).focus()
        self._start_scan()

    def action_refresh(self) -> None:
        self._start_scan()

    def _start_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.query_one("#project-list", OptionList).clear_options()
        self.query_one("#project-path", Static).update("Scanning projects...")
        self.run_worker(self._scan, thread=True, exclusive=True)

    def _scan(self) -> None:
        """Runs in a worker thread: discover_projects and gather_project_status
        both make blocking filesystem/subprocess calls.
        """
        projects = discover_projects()
        running_sessions = {session.name for session in tmux.list_tmux_sessions()}
        statuses = {
            project.name: gather_project_status(project, running_sessions=running_sessions)
            for project in projects
        }
        self.app.call_from_thread(self._on_scan_complete, projects, statuses)

    def _on_scan_complete(
        self, projects: list[Project], statuses: dict[str, ProjectStatus]
    ) -> None:
        self._scanning = False
        self._projects = projects
        self._statuses = statuses
        query = self.query_one("#project-filter", Input).value.strip().lower()
        filtered = (
            [p for p in self._projects if query in p.name.lower()] if query else self._projects
        )
        self._populate(filtered)

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
            status = self._statuses.get(project.name)
            option_list.add_option(Option(_row_label(project, status), id=project.name))
        self._show_details(projects[0].name)

    def on_input_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        filtered = (
            [p for p in self._projects if query in p.name.lower()] if query else self._projects
        )
        self._populate(filtered)

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_details(event.option.id if event.option else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        name = event.option.id if event.option else None
        project = next((p for p in self._projects if p.name == name), None)
        if project is not None:
            self.app.push_screen(ProjectDetailScreen(project))

    def _show_details(self, name: str | None) -> None:
        path_widget = self.query_one("#project-path", Static)
        if not name:
            path_widget.update("")
            return
        status = self._statuses.get(name)
        path_widget.update(_details_text(status) if status is not None else "")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_focus_list(self) -> None:
        """Lets Down move focus from the filter box into the results list."""
        option_list = self.query_one("#project-list", OptionList)
        if option_list.option_count:
            option_list.focus()
