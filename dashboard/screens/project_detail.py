"""Project Detail screen: shown after picking a project from Open Project.

Renders the project's current status (git, saved workspace, running tmux
session) and offers exactly the actions that are safe given that status,
including explicit requests for supported project commands.
(dashboard.services.projects.primary_actions / secondary_actions), then
hands off to the existing structured launch mechanism -- this screen never
calls tmux directly. Resume/Recreate/Open Default all end by exiting the
Textual app with a LaunchRequest; Configure/Edit push the shared wizard
screens (dashboard.screens.new_project) in existing-project mode; Reset
and Forget are metadata-only and never exit the app.
"""

from __future__ import annotations

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Static
from textual.widgets.option_list import Option
from textual.worker import Worker

from dashboard.models import (
    AgentDeckAttachRequest,
    LaunchAction,
    LaunchRequest,
    LocalProjectLocation,
    RunProjectCommandRequest,
    TemplateValidationError,
    template_from_workspace,
)
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.diff_view import DiffScreen
from dashboard.screens.new_project.state import WizardState
from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen
from dashboard.screens.new_project.step_workspace_start import WorkspaceStartScreen
from dashboard.screens.template_name import TemplateNameScreen
from dashboard.services.activity import (
    agent_display_name,
    agent_status,
    effective_agent_session,
    server_status,
)
from dashboard.services.git import GitDiffResult, GitFileChange, GitStatus, load_diff, load_status
from dashboard.services.pane_layout_store import (
    PaneLayoutStoreError,
    forget_pane_layouts_for_location,
    load_pane_layouts_for_location,
    save_pane_layouts_for_location,
)
from dashboard.services.project_commands import (
    DetectedProjectCommands,
    detect_project_commands,
)
from dashboard.services.project_intelligence import ProjectIntelligence, inspect_project
from dashboard.services.project_launch import prepare_project_launch_for_selector
from dashboard.services.project_selection import RegisteredRemoteProject
from dashboard.services.project_tools import ProjectTools, detect_project_tools
from dashboard.services.projects import (
    Project,
    ProjectAction,
    ProjectStatus,
    build_launch_request,
    gather_single_project_status,
    primary_actions,
    secondary_actions,
)
from dashboard.services.ssh_host_store import get_ssh_host
from dashboard.services.template_store import (
    DuplicateTemplateNameError,
    TemplateStoreError,
    TemplateStoreVersionError,
    create_template,
)
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import (
    WorkspaceStoreVersionError,
    forget_workspace,
    load_workspace_result_for_location,
    save_workspace,
)
from dashboard.widgets import ActionItem, KeyboardActionList
from dashboard.widgets import KeyboardOptionList as OptionList

_ACTION_LABELS: dict[ProjectAction, str] = {
    ProjectAction.RESUME: "Resume Session",
    ProjectAction.RECREATE: "Recreate Workspace",
    ProjectAction.OPEN_DEFAULT: "Open Default Workspace",
    ProjectAction.CONFIGURE: "Configure Workspace",
    ProjectAction.EDIT: "Edit Workspace",
    ProjectAction.SAVE_TEMPLATE: "Save as Template",
    ProjectAction.RESET: "Reset to Default Workspace",
    ProjectAction.FORGET: "Forget Saved Workspace",
    ProjectAction.RESET_PANE_SIZES: "Reset Remembered Pane Sizes",
}


@dataclass(frozen=True, slots=True)
class ProjectDetailAction:
    """Small screen-level action descriptor, ready for future project tools."""

    id: str
    label: str
    enabled: bool = True
    description: str = ""

    def to_item(self) -> ActionItem:
        label = f"{self.label}  —  {self.description}" if self.description else self.label
        return ActionItem(self.id, label, disabled=not self.enabled)


class GitFileList(OptionList):
    """Selectable Git files with a local diff shortcut."""

    async def handle_key(self, event) -> bool:
        if event.key == "d":
            screen = self.screen
            if isinstance(screen, ProjectDetailScreen):
                screen.action_open_diff()
                event.stop()
                return True
        if event.key == "down" and self.highlighted == self.option_count - 1:
            screen = self.screen
            if isinstance(screen, ProjectDetailScreen):
                screen.query_one("#project-actions", KeyboardActionList).focus()
                event.stop()
                return True
        return await super().handle_key(event)


class ProjectCommandList(KeyboardActionList):
    """Keyboard-selectable detected commands with stable command ids."""

    def on_key(self, event) -> None:
        if event.key == "down" and self.selected_index == len(self.actions) - 1:
            self.screen.query_one("#project-actions", KeyboardActionList).focus()
            event.stop()
            return
        super().on_key(event)


class ProjectActionList(KeyboardActionList):
    """Project actions that can move back to the changed-file list."""

    def on_key(self, event) -> None:
        if event.key == "up" and self.selected_index == 0:
            screen = self.screen
            if isinstance(screen, ProjectDetailScreen) and screen.remote_project is None:
                files = screen.query_one("#detail-git-files-list", OptionList)
                if files.display and files.option_count:
                    files.focus()
                    event.stop()
                    return
                commands = screen.query_one("#detail-commands", ProjectCommandList)
                if commands.selected_index is not None:
                    commands.focus()
                    event.stop()
                    return
        super().on_key(event)


def _action_id(action: ProjectAction) -> str:
    return f"action-{action.value}"


def _git_line(status: ProjectStatus) -> str:
    if not status.is_git_repo:
        return "Git:            not a git repository"
    if status.git_branch:
        return f"Git branch:     {status.git_branch}"
    return "Git:            repository (branch unknown)"


def _workspace_line(status: ProjectStatus) -> str:
    if status.workspace_metadata_error:
        return "Saved workspace: corrupted -- see warning below"
    if status.saved_workspace is not None:
        return "Saved workspace: yes"
    return "Saved workspace: not configured"


def _session_line(status: ProjectStatus) -> str:
    if not status.tmux_available:
        return f"tmux session:   {status.expected_session_name}  (tmux is not installed)"
    state = "running" if status.session_running else "not running"
    return f"tmux session:   {status.expected_session_name}  ({state})"


def _agent_line(status: ProjectStatus) -> str | None:
    selected, count = effective_agent_session(status.agent_sessions)
    if selected is None:
        return None
    labels = {"error": "Error", "waiting": "Waiting", "running": "Working",
              "idle": "Idle", "stopped": "Stopped", "unknown": "Unknown"}
    count_label = f" ({count})" if count > 1 else ""
    status_label = labels.get(selected.status.value, "Unknown")
    return f"Agent:          {agent_display_name(selected.tool)}{count_label}  ({status_label})"


def format_activity_block(status: ProjectStatus) -> str:
    server = server_status(status)
    agent, count, selected = agent_status(status)
    lines = [f"Server  {server.glyph} {server.label}"]
    if selected is not None:
        agent_label = f"({count}) {agent.label}" if count > 1 else agent.label
        lines.append(f"Agent   {agent_display_name(selected.tool)} {agent.glyph} {agent_label}")
    return "\n".join(lines)


def format_git_summary(status: GitStatus | None) -> str:
    if status is None:
        return "Loading Git status..."
    if status.error and status.is_repo is None:
        return f"Git status unavailable: {status.error}"
    if status.is_repo is False:
        return "Not a Git repository"
    branch = "(detached HEAD)" if status.detached else (status.branch or "(unknown)")
    if status.clean:
        return f"{branch} · Clean"
    change_word = "change" if len(status.changes) == 1 else "changes"
    return (
        f"{branch} · {len(status.changes)} {change_word}\n"
        f"Staged {status.staged_count}   Modified {status.modified_count}   "
        f"Untracked {status.untracked_count}"
    )


def format_git_files(status: GitStatus | None) -> str:
    if status is None or not status.changes:
        return ""
    lines = ["Changed Files"]
    for change in status.changes:
        path = change.path
        if change.old_path is not None:
            path = f"{change.old_path} → {change.path}"
        lines.append(f"  {change.indicator:<2} {path}")
    return "\n".join(lines)


def format_workspace_summary(status: ProjectStatus) -> str:
    if not status.tmux_available:
        return "Unavailable · tmux is not installed"
    if not status.session_running:
        if status.saved_workspace is not None:
            return "○ Stopped · ready to open"
        return "— Not configured"
    details: list[str] = ["● Running"]
    if status.running_window_count is not None:
        details.append(f"{status.running_window_count} window")
        if status.running_window_count != 1:
            details[-1] += "s"
    if status.running_pane_count is not None:
        details.append(f"{status.running_pane_count} pane")
        if status.running_pane_count != 1:
            details[-1] += "s"
    if status.session_attached is not None:
        details.append("attached" if status.session_attached else "detached")
    return " · ".join(details)


def format_project_overview(
    status: ProjectStatus, intelligence: ProjectIntelligence | None
) -> str:
    lines = [f"{status.project.name}", str(status.canonical_path)]
    if intelligence is not None:
        kind = ", ".join(framework for framework in intelligence.frameworks)
        if not kind:
            kind = ", ".join(ecosystem.value for ecosystem in intelligence.ecosystems)
        if kind:
            lines.append(kind)
    return "\n".join(lines)


def format_project_commands(commands: DetectedProjectCommands | None) -> str:
    if commands is None:
        return "Detecting supported commands..."
    rows: list[str] = []
    if commands.development is not None:
        rows.append(f"  Dev server    {commands.development.command}")
    if commands.test is not None:
        rows.append(f"  Tests         {commands.test.command}")
    if commands.build is not None:
        rows.append(f"  Build         {commands.build.command}")
    if commands.lint is not None:
        rows.append(f"  Lint          {commands.lint.command}")
    return "\n".join(rows) if rows else "  No supported project commands detected"


def project_command_items(
    commands: DetectedProjectCommands | None,
    *,
    can_execute: bool,
) -> list[ActionItem]:
    if commands is None:
        return [ActionItem("commands-loading", "Detecting supported commands...", disabled=True)]
    detected = [commands.development, commands.test, commands.build, commands.lint]
    items: list[ActionItem] = []
    labels = {"dev": "Dev server", "test": "Tests", "build": "Build", "lint": "Lint"}
    for command in detected:
        if command is None:
            continue
        executable = command.executable and can_execute
        description = command.command
        if not command.executable:
            description += "  (informational only)"
        elif not can_execute:
            description += "  (configure a workspace to run)"
        items.append(
            ActionItem(
                f"command:{command.id}",
                f"{labels[command.category.value]:<14} {description}",
                disabled=not executable,
            )
        )
    return items or [
        ActionItem("commands-empty", "No supported project commands detected", disabled=True)
    ]


def format_project_tools(tools: ProjectTools) -> str:
    rows = []
    if tools.lazygit:
        rows.append("  Lazygit       available in a running workspace (prefix + g)")
    if tools.editor_in_workspace:
        rows.append("  Editor        configured in workspace")
    if tools.terminal_in_workspace:
        rows.append("  Terminal      configured in workspace")
    return "\n".join(rows) if rows else "  No additional project tools available"


class ProjectDetailScreen(Screen[None]):
    """Read the project's status, then offer only the actions that are
    safe to take from that status.
    """

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("f5", "refresh", "Refresh"),
        ("a", "open_agent", "Open Agent"),
        ("d", "open_diff", "Diff"),
    ]

    def __init__(self, project: Project | RegisteredRemoteProject) -> None:
        super().__init__()
        self.project = project
        self.status: ProjectStatus | None = (
            gather_single_project_status(project) if isinstance(project, Project) else None
        )
        remote_project = project if isinstance(project, RegisteredRemoteProject) else None
        self.remote_project = remote_project
        self.remote_workspace = (
            load_workspace_result_for_location(remote_project.location).workspace
            if remote_project is not None
            else None
        )
        self.remote_host = (
            get_ssh_host(remote_project.location.host_id) if remote_project is not None else None
        )
        self._feedback = ""
        self._git_status: GitStatus | None = None
        self._git_refreshing = False
        self._git_refresh_generation = 0
        self._git_refresh_active = False
        self._modal_action_active = False
        self._last_action_worker: Worker[None] | None = None
        self._git_timer: Timer | None = None
        self._git_rendered: tuple[str, str] | None = None
        self._diff_opening = False
        self._selected_diff_change: GitFileChange | None = None
        self._restore_file_focus = False
        self._restore_file_path: str | None = None
        self._back_requested = False
        self._project_intelligence: ProjectIntelligence | None = None
        self._project_commands: DetectedProjectCommands | None = None
        self._project_tools = ProjectTools(False, False, False)

    def compose(self) -> ComposeResult:
        status = self.status

        if status is None:
            assert self.remote_project is not None
            project = self.remote_project
            yield from self._compose_remote(project)
            yield Static(
                "↑↓ Navigate   d Diff   a Agent   Esc Back   F5 Refresh   ? Help   q Quit",
                id="detail-footer",
            )
            return

        with Container(classes="screen-root project-detail-root"):
            with VerticalScroll(classes="panel"):
                yield Static(status.project.name, id="screen-title")
                yield Static(
                    format_project_overview(status, self._project_intelligence),
                    id="detail-overview",
                )
                with Horizontal(id="detail-status-grid"):
                    with Vertical(classes="detail-section"):
                        yield Static("Workspace", classes="panel-heading")
                        yield Static(
                            format_workspace_summary(status), id="detail-workspace-summary"
                        )
                        yield Static(format_activity_block(status), id="detail-activity")
                        if status.workspace_metadata_error:
                            yield Static(
                                f"Metadata warning: {status.workspace_metadata_error}",
                                id="detail-metadata-error",
                                classes="wizard-hint",
                            )
                    with Vertical(classes="detail-section"):
                        yield Static("Git", classes="panel-heading")
                        yield Static(format_git_summary(self._git_status), id="detail-git")
                        yield Static(
                            "Changed Files",
                            id="detail-git-files",
                            classes="panel-heading detail-git-files-hidden",
                        )
                        yield GitFileList(
                            Option("No changes", disabled=True),
                            id="detail-git-files-list",
                            classes="detail-git-files-hidden",
                            reset_on_blur=False,
                        )

                with Horizontal(id="detail-command-tool-grid"):
                    with Vertical(classes="detail-section"):
                        yield Static("Commands", classes="panel-heading")
                        yield ProjectCommandList(
                            *project_command_items(
                                self._project_commands,
                                can_execute=status.saved_workspace is not None,
                            ),
                            id="detail-commands",
                        )
                    with Vertical(classes="detail-section"):
                        yield Static("Tools", classes="panel-heading")
                        yield Static(
                            format_project_tools(self._project_tools), id="detail-tools"
                        )

                if status.saved_workspace is not None:
                    yield Static("Workspace Layout", classes="panel-heading")
                    for window in status.saved_workspace.windows:
                        pane_summary = " · ".join(pane.display_name for pane in window.panes)
                        yield Static(
                            f"{window.window_name:<8} {pane_summary}",
                            classes="detail-layout-row",
                        )

                if not status.project_dir_exists:
                    yield Static(
                        "This project's directory no longer exists on disk.",
                        id="detail-missing-dir",
                        classes="wizard-hint",
                    )
                if status.workspace_metadata_warning:
                    yield Static(
                        f"Warning: {status.workspace_metadata_warning}",
                        id="detail-metadata-warning",
                        classes="wizard-hint",
                    )

                yield Static(self._feedback, id="detail-error")

                primary = primary_actions(status)
                secondary = secondary_actions(status)
                primary_items = self._action_items(tuple(primary))
                if status.agent_sessions:
                    primary_items.append(
                        ProjectDetailAction("open-agent", "Open Agent Deck Session").to_item()
                    )
                if not primary_items:
                    primary_items.append(
                        ProjectDetailAction(
                            "no-primary-action", "No workspace action available", enabled=False
                        ).to_item()
                    )
                if secondary:
                    primary_items.append(ActionItem("more-heading", "More", disabled=True))
                    primary_items.extend(
                        ProjectDetailAction(
                            item.id,
                            f"More · {item.label}",
                            enabled=item.enabled,
                            description=item.description,
                        ).to_item()
                        for item in self._action_descriptors(tuple(secondary))
                    )
                primary_items.append(ActionItem("back-to-list", "Back to Project List"))
                yield Static("Next", classes="panel-heading")
                yield ProjectActionList(*primary_items, id="project-actions")
        yield Static(
            "↑↓ Navigate   Enter Select   d Diff   a Agent   Esc Back   "
            "F5 Refresh   ? Help   q Quit",
            id="detail-footer",
        )

    def on_mount(self) -> None:
        self._update_detail_width(self.size.width)
        self._git_refresh_active = True
        if self.remote_project is not None:
            remote_actions = self.query(KeyboardActionList).filter("#project-actions")
            if remote_actions:
                remote_actions.first().focus()
            return
        # Keep the changed-file region out of the initial layout. Clean
        # repositories settle on the Git summary without showing a loading
        # placeholder or an empty diagnostic section.
        heading = self.query_one("#detail-git-files", Static)
        files = self.query_one("#detail-git-files-list", OptionList)
        heading.display = False
        files.display = False
        # A worker result may arrive between Screen construction and the
        # dynamic widgets finishing their mount. In that case the status
        # snapshot is already current, but the first callback could not paint
        # the file rows. Reconcile it now that the mounted instance exists.
        if self._git_status is not None:
            self._update_git_files(self._git_status)
        actions = self.query(KeyboardActionList).filter("#project-actions")
        if actions:
            actions.first().focus()
        self.call_after_refresh(self._reset_scroll_position)
        if self.status is not None:
            self._start_project_inspection()
            self._project_tools = self._detect_tools()
            self.query_one("#detail-tools", Static).update(
                format_project_tools(self._project_tools)
            )
            self._start_command_detection()
            self._git_timer = self.set_interval(1.75, self._start_git_refresh)
            self._start_git_refresh()

    def on_resize(self, event) -> None:
        self._update_detail_width(event.size.width)

    def _update_detail_width(self, width: int) -> None:
        roots = self.query(".project-detail-root")
        if roots:
            roots.first(Container).set_class(width < 90, "detail-narrow")

    def _action_items(self, actions: tuple[ProjectAction, ...]) -> list[ActionItem]:
        return [descriptor.to_item() for descriptor in self._action_descriptors(actions)]

    def _action_descriptors(self, actions: tuple[ProjectAction, ...]) -> list[ProjectDetailAction]:
        return [
            ProjectDetailAction(_action_id(action), _ACTION_LABELS[action])
            for action in actions
        ]

    def _detect_tools(self) -> ProjectTools:
        assert self.status is not None
        panes = tuple(
            pane.kind.value
            for window in (
                self.status.saved_workspace.windows if self.status.saved_workspace else ()
            )
            for pane in window.panes
        )
        return detect_project_tools(
            self.status.canonical_path,
            has_editor_pane="code_editor" in panes,
            has_terminal_pane=bool(panes),
        )

    def _start_command_detection(self) -> None:
        if self.status is None or not self.status.project_dir_exists:
            return
        self.run_worker(
            lambda: detect_project_commands(self.status.canonical_path), thread=True
        )

    def _start_project_inspection(self) -> None:
        if self.status is None or not self.status.project_dir_exists:
            return
        self.run_worker(lambda: inspect_project(self.status.canonical_path), thread=True)

    def on_worker_state_changed(self, event) -> None:
        """Paint one-shot project inspection results without rebuilding focus."""
        result = getattr(event.worker, "result", None)
        if isinstance(result, DetectedProjectCommands):
            self._project_commands = result
            widget = self.query("#detail-commands")
            if widget:
                widget.first(ProjectCommandList).set_actions(
                    project_command_items(
                        result,
                        can_execute=self.status is not None
                        and self.status.saved_workspace is not None,
                    )
                )
        elif isinstance(result, ProjectIntelligence):
            self._project_intelligence = result
            widget = self.query("#detail-overview")
            if widget and self.status is not None:
                widget.first(Static).update(format_project_overview(self.status, result))

    def on_descendant_focus(self, event) -> None:
        if event.widget.id == "detail-git-files-list":
            self.query_one("#detail-footer", Static).update(
                "↑↓ Navigate   d Diff   a Agent   Esc Back   F5 Refresh   ? Help   q Quit"
            )
        elif event.widget.id == "project-actions":
            self.query_one("#detail-footer", Static).update(
                "↑↓ Navigate   Enter Select   d Diff   a Agent   Esc Back   "
                "F5 Refresh   ? Help   q Quit"
            )

    def on_key(self, event) -> None:
        files = self.query_one("#detail-git-files-list", OptionList)
        if event.key == "d" and self.app.focused is files:
            self.action_open_diff()
            event.stop()
            return
        if event.key == "up" and self.app.focused is self.query_one(
            "#project-actions", KeyboardActionList
        ):
            if files.display and files.option_count:
                files.focus()
                event.stop()
                return
        if event.key == "down" and self.app.focused is self.query_one(
            "#detail-git-files-list", OptionList
        ):
            files = self.query_one("#detail-git-files-list", OptionList)
            if files.highlighted == files.option_count - 1:
                self.query_one("#project-actions", KeyboardActionList).focus()
                event.stop()
                return

    def _reset_scroll_position(self) -> None:
        panel = self.query(".project-detail-root > .panel")
        if panel:
            panel.first().scroll_home(animate=False)

    def _compose_remote(self, project: RegisteredRemoteProject) -> ComposeResult:
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static(f"Project: {project.name}", id="screen-title")
                yield Static(f"Host ID:       {project.location.host_id}", id="detail-host")
                yield Static(f"Remote path:   {project.location.remote_path}", id="detail-path")
                if self.remote_host is None:
                    yield Static(
                        "Missing SSH host registration.",
                        id="detail-missing-host",
                        classes="wizard-hint",
                    )
                else:
                    yield Static(
                        f"SSH destination: {self.remote_host.destination}",
                        id="detail-destination",
                    )
                saved = "yes" if self.remote_workspace is not None else "not configured"
                yield Static(f"Saved workspace: {saved}", id="detail-workspace")
                yield Static(
                    "Remote status: registered metadata only (not checked)",
                    id="detail-remote-status",
                )
                yield Static(self._feedback, id="detail-error")
                yield Static("Actions", classes="panel-heading")
                yield ProjectActionList(
                    ActionItem("action-resume", "Launch Remote Workspace"),
                    ActionItem("back-to-list", "Back to Project List"),
                    id="project-actions",
                )

    async def on_screen_resume(self) -> None:
        self._git_refresh_active = True
        restore_files = self._restore_file_focus
        restore_path = self._restore_file_path
        self._restore_file_focus = False
        self._restore_file_path = None
        if self._git_timer is not None:
            self._git_timer.resume()
        if self._modal_action_active or (
            self._last_action_worker is not None and self._last_action_worker.is_running
        ):
            return
        self._start_git_refresh()
        await self._refresh_status()
        if restore_files:
            self._restore_changed_file_focus(restore_path)

    def on_screen_suspend(self) -> None:
        self._git_refresh_active = False
        self._git_refresh_generation += 1
        # Pausing the timer does not stop a worker which has already started.
        # Let the next resume start a fresh refresh even if that worker's
        # callback is still queued for delivery.
        self._git_refreshing = False
        if self._git_timer is not None:
            self._git_timer.pause()

    async def action_refresh(self) -> None:
        self._start_git_refresh()
        await self._refresh_status()

    def _start_git_refresh(self) -> None:
        if self.status is None or self._git_refreshing:
            return
        self._git_refreshing = True
        # The screen also uses workers for actions such as saving templates;
        # this refresh is independently guarded by _git_refreshing and must
        # not cancel those action workers.
        generation = self._git_refresh_generation
        self.run_worker(lambda: self._load_git_status(generation), thread=True)

    def _load_git_status(self, generation: int) -> None:
        assert self.status is not None
        result = load_status(self.status.canonical_path)
        self.app.call_from_thread(self._on_git_status, result, generation)

    def _on_git_status(self, result: GitStatus, generation: int | None = None) -> None:
        if generation is not None and generation != self._git_refresh_generation:
            return
        self._git_refreshing = False
        if not self._git_refresh_active or not self.is_attached:
            return
        if result.error and self._git_status is not None:
            return

        previous_path = self._selected_git_file_path()
        summary = format_git_summary(result)
        files = format_git_files(result)
        if self._git_rendered == (summary, files):
            self._git_status = result
            mounted_files = self.query("#detail-git-files-list")
            if mounted_files:
                self._update_git_files(result, previous_path)
            return
        self._git_status = result
        self._git_rendered = (summary, files)
        git_widgets = self.query("#detail-git")
        if not git_widgets:
            return
        git_widgets.first(Static).update(summary)
        self._update_git_files(result, previous_path)

    def on_unmount(self) -> None:
        self._git_refresh_active = False
        self._git_refresh_generation += 1

    def _selected_git_file_path(self) -> str | None:
        file_widgets = self.query("#detail-git-files-list")
        if not file_widgets:
            # A worker callback can arrive during the small mount window
            # before compose() has attached the dynamic Git file list.
            return None
        files = file_widgets.first(OptionList)
        if files.highlighted is None or self._git_status is None:
            return None
        if files.highlighted >= len(self._git_status.changes):
            return None
        return self._git_status.changes[files.highlighted].path

    def _update_git_files(self, status: GitStatus, previous_path: str | None = None) -> None:
        file_widgets = self.query("#detail-git-files-list")
        heading_widgets = self.query("#detail-git-files")
        if not file_widgets or not heading_widgets:
            return
        files = file_widgets.first(OptionList)
        heading = heading_widgets.first(Static)
        if previous_path is None:
            previous_path = self._selected_git_file_path()
        was_focused = self.app.focused is files
        files.clear_options()
        hidden = not status.changes
        heading.set_class(hidden, "detail-git-files-hidden")
        files.set_class(hidden, "detail-git-files-hidden")
        if not status.changes:
            heading.display = False
            files.display = False
            if was_focused:
                self.query_one("#project-actions", KeyboardActionList).focus()
            return
        heading.display = True
        files.display = True
        for index, change in enumerate(status.changes):
            path = (
                f"{change.old_path} -> {change.path}"
                if change.old_path is not None
                else change.path
            )
            files.add_option(Option(f"{change.indicator:<2} {path}", id=str(index)))
        # OptionList options are rendered internally rather than as child
        # widgets, so content-sized layout cannot always infer their height
        # after they are added to a mounted widget. Set the row height
        # explicitly, capped so the surrounding detail panel can scroll for
        # large repositories.
        files.styles.height = min(len(status.changes), 12)
        # Options are virtual rows inside OptionList. Force both the virtual
        # row map and the mounted widget layout to be refreshed after the
        # dynamic update so the rows are painted in the current frame.
        files.refresh(layout=True)
        selected = next(
            (
                index
                for index, change in enumerate(status.changes)
                if change.path == previous_path
            ),
            0,
        )
        files.highlighted = selected
        if was_focused:
            files.focus()

    def action_open_diff(self) -> None:
        files = self.query_one("#detail-git-files-list", OptionList)
        if (
            self._diff_opening
            or self.app.focused is not files
            or files.highlighted is None
            or self._git_status is None
        ):
            self.app.notify("Select a changed file first.", severity="warning")
            return
        if files.highlighted >= len(self._git_status.changes):
            self.app.notify("Select a changed file first.", severity="warning")
            return
        self._selected_diff_change = self._git_status.changes[files.highlighted]
        self._restore_file_focus = True
        self._restore_file_path = self._selected_diff_change.path
        self._diff_opening = True
        self.run_worker(self._load_selected_diff, thread=True)

    def _load_selected_diff(self) -> None:
        assert self.status is not None
        assert self._selected_diff_change is not None
        result = load_diff(self.status.canonical_path, self._selected_diff_change)
        self.app.call_from_thread(self._on_diff_loaded, result)

    def _on_diff_loaded(self, result: GitDiffResult) -> None:
        self._diff_opening = False
        self.app.push_screen(DiffScreen(result))

    def _restore_changed_file_focus(self, path: str | None) -> None:
        files = self.query_one("#detail-git-files-list", OptionList)
        if self._git_status is None or not self._git_status.changes:
            files.display = False
            self.query_one("#project-actions", KeyboardActionList).focus()
            return
        self._update_git_files(self._git_status, path)
        files.focus()

    def action_go_back(self) -> None:
        if self._back_requested:
            return
        self._back_requested = True
        self.app.pop_screen()

    async def _refresh_status(self) -> None:
        preferred_id: str | None = None
        try:
            preferred_id = self.query_one("#project-actions", KeyboardActionList).selected_action_id
        except Exception:
            pass
        if self.remote_project is not None:
            self.remote_workspace = load_workspace_result_for_location(
                self.remote_project.location
            ).workspace
            await self.recompose()
        else:
            assert isinstance(self.project, Project)
            self.status = gather_single_project_status(self.project)
            await self.recompose()
        if not self._git_refresh_active or not self.is_attached:
            return
        actions = self.query_one("#project-actions", KeyboardActionList)
        actions.set_actions(actions.actions, preferred_id)
        actions.focus()

    def _show_error(self, message: str) -> None:
        self._feedback = message
        self.query_one("#detail-error", Static).update(message)

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        event.stop()
        button_id = event.action_id
        if event.action_list.id == "detail-commands":
            command_id = button_id.removeprefix("command:")
            self._run_detected_command(command_id)
            return
        if button_id == "back-to-list":
            self.action_go_back()
        elif button_id in (_action_id(ProjectAction.RESUME), _action_id(ProjectAction.RECREATE)):
            self._resume_or_recreate()
        elif button_id == "open-agent":
            self._open_agent()
        elif button_id == _action_id(ProjectAction.OPEN_DEFAULT):
            self._open_default_workspace()
        elif button_id == _action_id(ProjectAction.CONFIGURE):
            self._configure_workspace()
        elif button_id == _action_id(ProjectAction.EDIT):
            self._edit_workspace()
        elif button_id == _action_id(ProjectAction.RESET_PANE_SIZES):
            self._last_action_worker = self.run_worker(self._reset_remembered_pane_sizes)
        elif button_id == _action_id(ProjectAction.SAVE_TEMPLATE):
            self._save_as_template()
        elif button_id == _action_id(ProjectAction.RESET):
            self._last_action_worker = self.run_worker(self._reset_to_default)
        elif button_id == _action_id(ProjectAction.FORGET):
            self._last_action_worker = self.run_worker(self._forget_workspace)

    def _run_detected_command(self, command_id: str) -> None:
        if self.remote_project is not None or self.status is None:
            self._show_error("Remote project commands are informational only.")
            return
        if self.status.saved_workspace is None:
            self._show_error("Configure a workspace before running project commands.")
            return
        commands = self._project_commands
        if commands is None:
            self._show_error("Project commands are still being detected.")
            return
        command = next(
            (
                candidate
                for candidate in (
                    commands.development,
                    commands.test,
                    commands.build,
                    commands.lint,
                )
                if candidate is not None and candidate.id == command_id
            ),
            None,
        )
        if command is None or not command.executable or command.argv is None:
            self._show_error("That detected command is informational only.")
            return
        self.app.exit(
            RunProjectCommandRequest(
                workspace=self.status.saved_workspace,
                command_id=command.id or command_id,
                label=command.command,
                category=command.category.value,
                argv=command.argv,
            )
        )

    def _resume_or_recreate(self) -> None:
        """Both Resume Session and Recreate Workspace produce the exact
        same ATTACH request -- see build_launch_request.
        """
        if self.remote_project is not None:
            self._launch_remote()
            return
        assert self.status is not None
        self.app.exit(build_launch_request(self.status))

    def _open_agent(self) -> None:
        if self.status is None:
            return
        selected, _ = effective_agent_session(self.status.agent_sessions)
        if selected is not None:
            self.app.exit(AgentDeckAttachRequest(selected.id))

    def _launch_remote(self) -> None:
        assert self.remote_project is not None
        try:
            resolved = prepare_project_launch_for_selector(self.remote_project.selector)
        except OSError as exc:
            self._show_error(str(exc))
            return
        if resolved.prepared is None:
            self._show_error(resolved.error or "Remote workspace could not be prepared.")
            return
        self.app.exit(resolved.prepared.request)

    def _open_default_workspace(self) -> None:
        assert self.status is not None
        status = self.status
        # status.expected_session_name is already the deterministic,
        # collision-aware name gather_single_project_status assigned this
        # project -- reusing it (rather than re-deriving one here) keeps
        # the workspace actually created in sync with what was displayed,
        # and this action is only ever offered when that name isn't
        # currently running (see primary_actions), so it's always safe.
        workspace = build_default_workspace(
            status.project.name,
            LocalProjectLocation(status.canonical_path),
            status.expected_session_name,
        )
        try:
            save_workspace(workspace)
        except (OSError, WorkspaceStoreVersionError) as exc:
            self._show_error(str(exc))
            return
        self.app.exit(
            LaunchRequest(workspace=workspace, init_git=False, action=LaunchAction.CREATE)
        )

    def _configure_workspace(self) -> None:
        assert self.status is not None
        status = self.status
        state = WizardState.for_configuring_existing_project(
            status.project.name, status.canonical_path, session_name=status.expected_session_name
        )
        self.app.push_screen(WorkspaceStartScreen(state))

    def _edit_workspace(self) -> None:
        assert self.status is not None
        status = self.status
        if status.saved_workspace is None:
            return
        state = WizardState.for_editing_workspace(
            status.saved_workspace, session_running=status.session_running
        )
        self.app.push_screen(WindowSummaryScreen(state))

    def _save_as_template(self) -> None:
        assert self.status is not None
        workspace = self.status.saved_workspace
        if workspace is None:
            return

        def save_template(name: str | None) -> None:
            self.app.call_after_refresh(lambda: self._save_template_with_name(name))

        self.app.push_screen(
            TemplateNameScreen("Save Workspace as Template"),
            save_template,
        )

    def _save_template_with_name(self, name: str | None) -> None:
        assert self.status is not None
        workspace = self.status.saved_workspace
        if workspace is None or name is None:
            return
        try:
            template = template_from_workspace(workspace, name)
            create_template(template)
        except (
            DuplicateTemplateNameError,
            TemplateStoreError,
            TemplateStoreVersionError,
            TemplateValidationError,
            OSError,
        ) as exc:
            self.call_after_refresh(lambda error=str(exc): self._show_error(error))
            return
        self.call_after_refresh(
            lambda: self._show_error(f'Saved template "{template.name}".')
        )

    async def _reset_to_default(self) -> None:
        assert self.status is not None
        status = self.status
        if status.saved_workspace is None:
            return
        self._modal_action_active = True
        try:
            confirmed = await self.app.push_screen_wait(
                ConfirmScreen(
                    "Reset the saved workspace to the default layout?\n"
                    "A currently running tmux session, if any, is not affected.",
                    confirm_label="Reset",
                )
            )
        finally:
            self._modal_action_active = False
        if not confirmed:
            return
        self._modal_action_active = True
        workspace = build_default_workspace(
            status.project.name,
            LocalProjectLocation(status.canonical_path),
            status.saved_workspace.session_name,
        )
        location = LocalProjectLocation(status.canonical_path)
        previous_layouts = load_pane_layouts_for_location(location)
        layouts_cleared = False
        try:
            layouts_cleared = forget_pane_layouts_for_location(location)
            save_workspace(workspace)
        except (OSError, WorkspaceStoreVersionError, PaneLayoutStoreError) as exc:
            if layouts_cleared and previous_layouts:
                try:
                    save_pane_layouts_for_location(location, previous_layouts)
                except Exception:
                    pass
            # Dismissing the confirm screen above already triggers
            # on_screen_resume's own refresh -- refresh again explicitly
            # and show the error last, so it's never clobbered by that
            # refresh racing with this one.
            await self._refresh_status()
            self._show_error(str(exc))
            self._modal_action_active = False
            return
        await self._refresh_status()
        self._modal_action_active = False

    async def _reset_remembered_pane_sizes(self) -> None:
        assert self.status is not None
        status = self.status
        if status.saved_workspace is None or not status.remembered_pane_layouts:
            return
        self._modal_action_active = True
        try:
            confirmed = await self.app.push_screen_wait(
                ConfirmScreen(
                    "Reset this project's remembered pane sizes?\n"
                    "The saved workspace and any running tmux session are not affected.",
                    confirm_label="Reset",
                )
            )
        finally:
            self._modal_action_active = False
        if not confirmed:
            return
        self._modal_action_active = True
        try:
            forget_pane_layouts_for_location(LocalProjectLocation(status.canonical_path))
        except (OSError, PaneLayoutStoreError) as exc:
            await self._refresh_status()
            self._show_error(str(exc))
            self._modal_action_active = False
            return
        await self._refresh_status()
        self._show_error("Remembered pane sizes reset. They will apply on the next recreation.")
        self._modal_action_active = False

    async def _forget_workspace(self) -> None:
        assert self.status is not None
        status = self.status
        self._modal_action_active = True
        try:
            confirmed = await self.app.push_screen_wait(
                ConfirmScreen(
                    "Forget this project's saved workspace metadata?\n"
                    "Remembered pane sizes will also be removed; project files and any "
                    "tmux session are not affected.",
                    confirm_label="Forget",
                )
            )
        finally:
            self._modal_action_active = False
        if not confirmed:
            return
        self._modal_action_active = True
        location = LocalProjectLocation(status.canonical_path)
        previous_layouts = load_pane_layouts_for_location(location)
        layouts_cleared = False
        try:
            layouts_cleared = forget_pane_layouts_for_location(location)
            forget_workspace(status.canonical_path)
        except (OSError, WorkspaceStoreVersionError, PaneLayoutStoreError) as exc:
            if layouts_cleared and previous_layouts:
                try:
                    save_pane_layouts_for_location(location, previous_layouts)
                except Exception:
                    pass
            # See _reset_to_default: refresh explicitly, then show the
            # error last, so it isn't clobbered by on_screen_resume's own
            # refresh (triggered by dismissing the confirm screen above).
            await self._refresh_status()
            self._show_error(str(exc))
            self._modal_action_active = False
            return
        await self._refresh_status()
        self._modal_action_active = False
