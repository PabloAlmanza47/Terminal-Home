"""Open Project screen: a searchable list of directories under the
configured project roots, each annotated with its live status (git
branch, saved workspace, running tmux session). Selecting a project opens
ProjectDetailScreen, where the actual launch decision is made -- this
screen only discovers and displays.

Scanning is done in a worker thread (scan_all_projects makes blocking
filesystem/subprocess calls) so the UI stays responsive and can show a
loading indicator while a scan is in progress.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Static
from textual.widgets.option_list import Option

from dashboard.screens.project_detail import ProjectDetailScreen
from dashboard.services.project_selection import (
    RegisteredRemoteProject,
    SelectableProject,
    list_selectable_projects,
)
from dashboard.services.projects import (
    Project,
    ProjectScanResult,
    ProjectStatus,
    disambiguated_display_names,
    format_scan_warnings,
    project_option_id,
    scan_all_projects,
)
from dashboard.services.ssh_host_store import get_ssh_host
from dashboard.widgets import KeyboardOptionList as OptionList


def _row_label(display_name: str, status: ProjectStatus) -> str:
    if status.session_running:
        tag = "Running"
    elif status.workspace_metadata_error:
        tag = "Saved Workspace (corrupted)"
    elif status.saved_workspace is not None:
        tag = "Saved Workspace"
    else:
        tag = "Not Configured"

    label = f"{display_name}  [{tag}]"
    if status.is_git_repo and status.git_branch:
        label += f"  ({status.git_branch})"
    return label


def _remote_row_label(project: RegisteredRemoteProject) -> str:
    host_status = "" if get_ssh_host(project.location.host_id) is not None else "  [missing host]"
    return (
        f"{project.name}  [Remote]  {project.location.host_id}"
        f"  {project.location.remote_path}{host_status}"
    )


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


def _remote_details_text(project: RegisteredRemoteProject) -> str:
    return (
        f"Host ID: {project.location.host_id}\n"
        f"Remote path: {project.location.remote_path}\n"
        "Status: registered metadata only (remote status not checked)"
    )


class ProjectsScreen(Screen[None]):
    """Lists and filters project directories; Enter opens project detail."""

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("down", "focus_list", "Browse results"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._all_entries: list[ProjectStatus | RegisteredRemoteProject] = []
        self._entries_by_id: dict[str, ProjectStatus | RegisteredRemoteProject] = {}
        self._scanning = False

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Open Project", id="screen-title")
                yield Static("", id="scan-warning", classes="wizard-hint")
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
        """Runs in a worker thread: scan_all_projects makes blocking
        filesystem/subprocess calls.
        """
        scan_result = scan_all_projects()
        selectable = list_selectable_projects()
        self.app.call_from_thread(self._on_scan_complete, scan_result, selectable)

    def _on_scan_complete(
        self,
        scan_result: ProjectScanResult,
        selectable: tuple[SelectableProject, ...],
    ) -> None:
        self._scanning = False
        statuses_by_location = {status.project.location: status for status in scan_result.statuses}
        self._all_entries = [
            statuses_by_location[project.location]
            if isinstance(project, Project)
            else project
            for project in selectable
            if isinstance(project, RegisteredRemoteProject)
            or project.location in statuses_by_location
        ]
        self._entries_by_id = {
            project_option_id(entry)
            if isinstance(entry, ProjectStatus)
            else entry.selector: entry
            for entry in self._all_entries
        }
        self.query_one("#scan-warning", Static).update(format_scan_warnings(scan_result))
        query = self.query_one("#project-filter", Input).value.strip().lower()
        self._populate(self._filtered(query))

    def _filtered(self, query: str) -> list[ProjectStatus | RegisteredRemoteProject]:
        if not query:
            return list(self._all_entries)
        return [
            entry
            for entry in self._all_entries
            if query in (
                entry.project.name if isinstance(entry, ProjectStatus) else entry.name
            ).lower()
        ]

    def _populate(self, entries: list[ProjectStatus | RegisteredRemoteProject]) -> None:
        option_list = self.query_one("#project-list", OptionList)
        option_list.clear_options()
        path_widget = self.query_one("#project-path", Static)

        if not self._all_entries:
            option_list.add_option(
                Option("No projects found in the configured project roots", disabled=True)
            )
            path_widget.update("")
            return
        if not entries:
            option_list.add_option(Option("No matches", disabled=True))
            path_widget.update("")
            return
        local_statuses = [entry for entry in entries if isinstance(entry, ProjectStatus)]
        display_names = disambiguated_display_names(local_statuses)
        local_index = 0
        for entry in entries:
            if isinstance(entry, ProjectStatus):
                display_name = display_names[local_index]
                local_index += 1
                label = _row_label(display_name, entry)
                option_id = project_option_id(entry)
            else:
                label = _remote_row_label(entry)
                option_id = entry.selector
            option_list.add_option(
                Option(label, id=option_id)
            )
        first = entries[0]
        self._show_details(
            project_option_id(first) if isinstance(first, ProjectStatus) else first.selector
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        self._populate(self._filtered(event.value.strip().lower()))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        self._show_details(event.option.id if event.option else None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id if event.option else None
        entry = self._entries_by_id.get(option_id) if option_id else None
        if isinstance(entry, ProjectStatus):
            self.app.push_screen(ProjectDetailScreen(entry.project))
        elif isinstance(entry, RegisteredRemoteProject):
            self.app.push_screen(ProjectDetailScreen(entry))

    def _show_details(self, option_id: str | None) -> None:
        path_widget = self.query_one("#project-path", Static)
        if not option_id:
            path_widget.update("")
            return
        entry = self._entries_by_id.get(option_id)
        if isinstance(entry, ProjectStatus):
            path_widget.update(_details_text(entry))
        elif isinstance(entry, RegisteredRemoteProject):
            path_widget.update(_remote_details_text(entry))
        else:
            path_widget.update("")

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def action_focus_list(self) -> None:
        """Lets Down move focus from the filter box into the results list."""
        option_list = self.query_one("#project-list", OptionList)
        if option_list.option_count:
            option_list.focus()
