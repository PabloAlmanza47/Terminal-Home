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

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.geometry import Region
from textual.screen import Screen
from textual.widgets import Footer, Input, Static
from textual.widgets.option_list import Option

from dashboard.screens.project_detail import ProjectDetailScreen
from dashboard.services.project_categories import ProjectEntry, group_project_entries
from dashboard.services.project_rows import (
    format_project_row,
    format_remote_project_row,
    project_row_width,
)
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


def _status_label(status: ProjectStatus) -> str:
    if status.session_running:
        tag = "Running"
    elif status.workspace_metadata_error:
        tag = "Saved Workspace (corrupted)"
    elif status.saved_workspace is not None:
        tag = "Saved Workspace"
    else:
        tag = "Not Configured"

    return tag


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
        self._all_entries: list[ProjectEntry] = []
        self._entries_by_id: dict[str, ProjectEntry] = {}
        # These indexes describe the currently rendered option list.  Keeping
        # them separate from ``_entries_by_id`` means disabled category rows
        # remain presentation-only and can never become selectable projects.
        self._category_header_index_by_entry_id: dict[str, int] = {}
        self._preferred_entry_id: str | None = None
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

    def on_resize(self, event: events.Resize) -> None:
        if not self._all_entries or self._scanning:
            return
        selected_id = self._highlighted_entry_id()
        query = self.query_one("#project-filter", Input).value.strip().lower()
        self._populate(self._filtered(query))
        self._restore_highlight(selected_id)

    def _start_scan(self) -> None:
        if self._scanning:
            return
        self._scanning = True
        self._preferred_entry_id = self._highlighted_entry_id()
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
        preferred_id = self._highlighted_entry_id() or self._preferred_entry_id
        option_list.clear_options()
        self._category_header_index_by_entry_id.clear()
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
        display_names_by_id = {
            project_option_id(entry): display_name
            for entry, display_name in zip(local_statuses, display_names)
        }
        content_width = option_list.content_region.width or option_list.size.width
        child_indent = "  "
        row_width = project_row_width(content_width, leading_indent=len(child_indent))
        for category in group_project_entries(entries):
            header_index = option_list.option_count
            option_list.add_option(Option(f"── {category.title} ──", disabled=True))
            for entry in category.entries:
                if isinstance(entry, ProjectStatus):
                    option_id = project_option_id(entry)
                    label = format_project_row(
                        display_names_by_id[option_id],
                        _status_label(entry),
                        entry.git_branch if entry.is_git_repo else None,
                        row_width,
                    )
                else:
                    host = get_ssh_host(entry.location.host_id)
                    host_label = (
                        host.display_name
                        if host is not None
                        else f"{entry.location.host_id} [missing host]"
                    )
                    label = format_remote_project_row(entry, host_label, row_width)
                    option_id = entry.selector
                self._category_header_index_by_entry_id[option_id] = header_index
                option_list.add_option(Option(child_indent + label, id=option_id))
        visible_ids = {
            project_option_id(entry) if isinstance(entry, ProjectStatus) else entry.selector
            for entry in entries
        }
        self._restore_highlight(preferred_id if preferred_id in visible_ids else None)
        first_id = preferred_id if preferred_id in visible_ids else next(
            (
                project_option_id(entry) if isinstance(entry, ProjectStatus) else entry.selector
                for entry in entries
            ),
            None,
        )
        self._preferred_entry_id = first_id
        self._show_details(first_id)
        self._queue_category_context_scroll(first_id)

    def on_input_changed(self, event: Input.Changed) -> None:
        self._populate(self._filtered(event.value.strip().lower()))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        option_id = event.option.id if event.option else None
        self._show_details(option_id)
        if option_id is not None:
            self._queue_category_context_scroll(str(option_id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        option_id = event.option.id if event.option else None
        entry = self._entries_by_id.get(option_id) if option_id else None
        if isinstance(entry, ProjectStatus):
            self.app.push_screen(ProjectDetailScreen(entry.project))
        elif isinstance(entry, RegisteredRemoteProject):
            self.app.push_screen(ProjectDetailScreen(entry))

    def _highlighted_entry_id(self) -> str | None:
        option_list = self.query_one("#project-list", OptionList)
        if option_list.highlighted is None:
            return None
        option = option_list.get_option_at_index(option_list.highlighted)
        return str(option.id) if option.id in self._entries_by_id else None

    def _restore_highlight(self, entry_id: str | None) -> None:
        if not entry_id:
            return
        option_list = self.query_one("#project-list", OptionList)
        for index, option in enumerate(option_list.options):
            if option.id == entry_id:
                option_list.highlighted = index
                return

    def _queue_category_context_scroll(self, option_id: str | None) -> None:
        """Scroll after Textual has completed its normal highlight update.

        ``OptionList`` scrolls the highlighted line into view immediately,
        which can leave a disabled category heading one line above the
        viewport.  Deferring this small, public-API scroll adjustment until
        after refresh lets the option regions and content size settle first.
        """
        if option_id is not None:
            self.call_after_refresh(self._ensure_category_context_visible, option_id)

    def _ensure_category_context_visible(self, option_id: str) -> None:
        """Keep a first project row and its category heading visible together."""
        option_list = self.query_one("#project-list", OptionList)
        header_index = self._category_header_index_by_entry_id.get(option_id)
        if header_index is None or option_list.highlighted is None:
            return

        highlighted_option = option_list.get_option_at_index(option_list.highlighted)
        if highlighted_option.id != option_id:
            return

        # Category rows and project rows are intentionally single-line
        # options.  A two-line region therefore represents the heading plus
        # its first child and lets Textual calculate the smallest scroll
        # adjustment through its supported scroll API.
        if option_list.highlighted != header_index + 1:
            return
        content_width = max(1, option_list.scrollable_content_region.width)
        option_list.scroll_to_region(
            Region(0, header_index, content_width, 2),
            force=True,
            animate=False,
            immediate=True,
        )

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
