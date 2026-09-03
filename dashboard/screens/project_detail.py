"""Project Detail screen: shown after picking a project from Open Project,
before anything is ever launched.

Renders the project's current status (git, saved workspace, running tmux
session) and offers exactly the actions that are safe given that status
(dashboard.services.projects.primary_actions / secondary_actions), then
hands off to the existing structured launch mechanism -- this screen never
calls tmux directly. Resume/Recreate/Open Default all end by exiting the
Textual app with a LaunchRequest; Configure/Edit push the shared wizard
screens (dashboard.screens.new_project) in existing-project mode; Reset
and Forget are metadata-only and never exit the app.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Static

from dashboard.models import (
    AgentDeckAttachRequest,
    LaunchAction,
    LaunchRequest,
    LocalProjectLocation,
    TemplateValidationError,
    template_from_workspace,
)
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.new_project.state import WizardState
from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen
from dashboard.screens.new_project.step_workspace_start import WorkspaceStartScreen
from dashboard.screens.template_name import TemplateNameScreen
from dashboard.services.activity import codex_status, server_status, workspace_status
from dashboard.services.git import GitStatus, load_status
from dashboard.services.pane_layout_store import (
    PaneLayoutStoreError,
    forget_pane_layouts_for_location,
    load_pane_layouts_for_location,
    save_pane_layouts_for_location,
)
from dashboard.services.project_launch import prepare_project_launch_for_selector
from dashboard.services.project_selection import RegisteredRemoteProject
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
    codex = [s for s in status.agent_sessions if s.tool.casefold() == "codex"]
    if not codex:
        return None
    order = {"error": 0, "waiting": 1, "running": 2, "idle": 3, "stopped": 4, "unknown": 5}
    selected = min(codex, key=lambda session: order.get(session.status.value, 5))
    labels = {"error": "Error", "waiting": "Waiting", "running": "Working",
              "idle": "Idle", "stopped": "Stopped", "unknown": "Unknown"}
    count = f" ({len(codex)})" if len(codex) > 1 else ""
    return f"Agent:          Codex{count}  ({labels.get(selected.status.value, 'Unknown')})"


def format_activity_block(status: ProjectStatus) -> str:
    workspace = workspace_status(status)
    server = server_status(status)
    agent, count = codex_status(status)
    agent_label = f"({count}) {agent.label}" if count > 1 else agent.label
    return "\n".join(
        [f"Workspace  {workspace.glyph} {workspace.label}",
         f"Server     {server.glyph} {server.label}",
         f"Codex      {agent.glyph} {agent_label}"]
    )


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


class ProjectDetailScreen(Screen[None]):
    """Read the project's status, then offer only the actions that are
    safe to take from that status.
    """

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("f5", "refresh", "Refresh"),
        ("a", "open_agent", "Open Agent"),
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
        self._git_timer: Timer | None = None
        self._git_rendered: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        status = self.status

        if status is None:
            assert self.remote_project is not None
            project = self.remote_project
            yield from self._compose_remote(project)
            yield Static(
                "↑↓ Navigate   Enter Select   a Agent   Esc Back   F5 Refresh   ? Help   q Quit",
                id="detail-footer",
            )
            return

        with Container(classes="screen-root project-detail-root"):
            with VerticalScroll(classes="panel"):
                yield Static(status.project.name, id="screen-title")
                yield Static(format_activity_block(status), id="detail-activity")
                yield Static("Git", id="detail-git-heading", classes="panel-heading")
                yield Static(format_git_summary(self._git_status), id="detail-git")
                yield Static(format_git_files(self._git_status), id="detail-git-files")
                yield Static(
                    "Workspace", id="detail-workspace-heading", classes="panel-heading"
                )
                yield Static(_workspace_line(status), id="detail-workspace")
                yield Static(_session_line(status), id="detail-session")
                yield Static(
                    f"Path: {status.canonical_path}",
                    id="detail-path",
                    classes="detail-secondary",
                )

                if not status.project_dir_exists:
                    yield Static(
                        "This project's directory no longer exists on disk.",
                        id="detail-missing-dir",
                        classes="wizard-hint",
                    )
                if status.workspace_metadata_error:
                    yield Static(
                        f"Warning: {status.workspace_metadata_error}",
                        id="detail-metadata-error",
                        classes="wizard-hint",
                    )
                if status.workspace_metadata_warning:
                    yield Static(
                        f"Warning: {status.workspace_metadata_warning}",
                        id="detail-metadata-warning",
                        classes="wizard-hint",
                    )

                if status.saved_workspace is not None:
                    yield Static("Saved windows:", classes="field-label")
                    for window in status.saved_workspace.windows:
                        pane_summary = ", ".join(pane.display_name for pane in window.panes)
                        yield Static(f"  {window.window_name}: {pane_summary}")

                yield Static(self._feedback, id="detail-error")

                primary = primary_actions(status)
                secondary = secondary_actions(status)

                if primary:
                    actions = [
                        ActionItem(_action_id(action), _ACTION_LABELS[action])
                        for action in (*primary, *secondary)
                    ]
                    if status.agent_sessions:
                        actions.append(ActionItem("open-agent", "Open Codex Agent"))
                    actions.append(ActionItem("back-to-list", "Back to Project List"))
                    yield Static("Actions", classes="panel-heading")
                    yield KeyboardActionList(*actions, id="project-actions")
                else:
                    yield Static(
                        "No actions are available for this project right now.",
                        id="detail-no-actions",
                        classes="wizard-hint",
                    )

                if not primary:
                    yield KeyboardActionList(
                        *(
                            [ActionItem("open-agent", "Open Codex Agent")]
                            if status.agent_sessions
                            else []
                        ),
                        ActionItem("back-to-list", "Back to Project List"), id="project-actions"
                    )
        yield Static(
            "↑↓ Navigate   Enter Select   a Agent   Esc Back   F5 Refresh   ? Help   q Quit",
            id="detail-footer",
        )

    def on_mount(self) -> None:
        actions = self.query("#project-actions")
        if actions:
            actions.first().focus()
        self.call_after_refresh(self._reset_scroll_position)
        if self.status is not None:
            self._git_timer = self.set_interval(1.75, self._start_git_refresh)
            self._start_git_refresh()

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
                yield KeyboardActionList(
                    ActionItem("action-resume", "Launch Remote Workspace"),
                    ActionItem("back-to-list", "Back to Project List"),
                    id="project-actions",
                )

    async def on_screen_resume(self) -> None:
        if self._git_timer is not None:
            self._git_timer.resume()
        self._start_git_refresh()
        await self._refresh_status()

    def on_screen_suspend(self) -> None:
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
        self.run_worker(self._load_git_status, thread=True)

    def _load_git_status(self) -> None:
        assert self.status is not None
        result = load_status(self.status.canonical_path)
        self.app.call_from_thread(self._on_git_status, result)

    def _on_git_status(self, result: GitStatus) -> None:
        self._git_refreshing = False
        if result.error and self._git_status is not None:
            return
        summary = format_git_summary(result)
        files = format_git_files(result)
        if self._git_rendered == (summary, files):
            self._git_status = result
            return
        self._git_status = result
        self._git_rendered = (summary, files)
        self.query_one("#detail-git", Static).update(summary)
        self.query_one("#detail-git-files", Static).update(files)

    def action_go_back(self) -> None:
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
        actions = self.query_one("#project-actions", KeyboardActionList)
        actions.set_actions(actions.actions, preferred_id)
        actions.focus()

    def _show_error(self, message: str) -> None:
        self._feedback = message
        self.query_one("#detail-error", Static).update(message)

    @work
    async def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        button_id = event.action_id
        if button_id == "back-to-list":
            self.app.pop_screen()
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
            await self._reset_remembered_pane_sizes()
        elif button_id == _action_id(ProjectAction.SAVE_TEMPLATE):
            await self._save_as_template()
        elif button_id == _action_id(ProjectAction.RESET):
            await self._reset_to_default()
        elif button_id == _action_id(ProjectAction.FORGET):
            await self._forget_workspace()

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
        codex = [s for s in self.status.agent_sessions if s.tool.casefold() == "codex"]
        if codex:
            self.app.exit(AgentDeckAttachRequest(codex[0].id))

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

    async def _save_as_template(self) -> None:
        assert self.status is not None
        workspace = self.status.saved_workspace
        if workspace is None:
            return
        name = await self.app.push_screen_wait(TemplateNameScreen("Save Workspace as Template"))
        if name is None:
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
            self._show_error(str(exc))
            return
        self._show_error(f'Saved template "{template.name}".')

    async def _reset_to_default(self) -> None:
        assert self.status is not None
        status = self.status
        if status.saved_workspace is None:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                "Reset the saved workspace to the default layout?\n"
                "A currently running tmux session, if any, is not affected.",
                confirm_label="Reset",
            )
        )
        if not confirmed:
            return
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
            return
        await self._refresh_status()

    async def _reset_remembered_pane_sizes(self) -> None:
        assert self.status is not None
        status = self.status
        if status.saved_workspace is None or not status.remembered_pane_layouts:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                "Reset this project's remembered pane sizes?\n"
                "The saved workspace and any running tmux session are not affected.",
                confirm_label="Reset",
            )
        )
        if not confirmed:
            return
        try:
            forget_pane_layouts_for_location(LocalProjectLocation(status.canonical_path))
        except (OSError, PaneLayoutStoreError) as exc:
            await self._refresh_status()
            self._show_error(str(exc))
            return
        await self._refresh_status()
        self._show_error("Remembered pane sizes reset. They will apply on the next recreation.")

    async def _forget_workspace(self) -> None:
        assert self.status is not None
        status = self.status
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                "Forget this project's saved workspace metadata?\n"
                "Remembered pane sizes will also be removed; project files and any "
                "tmux session are not affected.",
                confirm_label="Forget",
            )
        )
        if not confirmed:
            return
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
            return
        await self._refresh_status()
