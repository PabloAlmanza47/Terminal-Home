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
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from dashboard.models import LaunchAction, LaunchRequest
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.new_project.state import WizardState
from dashboard.screens.new_project.step_window_config import WindowConfigScreen
from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen
from dashboard.services.projects import (
    Project,
    ProjectAction,
    ProjectStatus,
    build_launch_request,
    gather_single_project_status,
    primary_actions,
    secondary_actions,
)
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import (
    WorkspaceStoreVersionError,
    forget_workspace,
    save_workspace,
)

_ACTION_LABELS: dict[ProjectAction, str] = {
    ProjectAction.RESUME: "Resume Session",
    ProjectAction.RECREATE: "Recreate Workspace",
    ProjectAction.OPEN_DEFAULT: "Open Default Workspace",
    ProjectAction.CONFIGURE: "Configure Workspace",
    ProjectAction.EDIT: "Edit Workspace",
    ProjectAction.RESET: "Reset to Default Workspace",
    ProjectAction.FORGET: "Forget Saved Workspace",
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


class ProjectDetailScreen(Screen[None]):
    """Read the project's status, then offer only the actions that are
    safe to take from that status.
    """

    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(self, project: Project) -> None:
        super().__init__()
        self.project = project
        self.status: ProjectStatus = gather_single_project_status(project)

    def compose(self) -> ComposeResult:
        status = self.status

        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static(f"Project: {status.project.name}", id="screen-title")
                yield Static(f"Path:           {status.canonical_path}", id="detail-path")
                yield Static(_git_line(status), id="detail-git")
                yield Static(_workspace_line(status), id="detail-workspace")
                yield Static(_session_line(status), id="detail-session")

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

                if status.saved_workspace is not None:
                    yield Static("Saved windows:", classes="field-label")
                    for window in status.saved_workspace.windows:
                        pane_summary = ", ".join(pane.display_name for pane in window.panes)
                        yield Static(f"  {window.window_name}: {pane_summary}")

                yield Static("", id="detail-error")

                primary = primary_actions(status)
                secondary = secondary_actions(status)

                if primary:
                    with Horizontal(classes="button-row"):
                        for index, action in enumerate(primary):
                            yield Button(
                                _ACTION_LABELS[action],
                                id=_action_id(action),
                                variant="primary" if index == 0 else "default",
                            )
                else:
                    yield Static(
                        "No actions are available for this project right now.",
                        id="detail-no-actions",
                        classes="wizard-hint",
                    )

                if secondary:
                    with Horizontal(classes="button-row"):
                        for action in secondary:
                            yield Button(_ACTION_LABELS[action], id=_action_id(action))

                with Horizontal(classes="button-row"):
                    yield Button("Back to Project List", id="back-to-list-button")
        yield Footer()

    async def on_screen_resume(self) -> None:
        await self._refresh_status()

    async def action_refresh(self) -> None:
        await self._refresh_status()

    def action_go_back(self) -> None:
        self.app.pop_screen()

    async def _refresh_status(self) -> None:
        self.status = gather_single_project_status(self.project)
        await self.recompose()

    def _show_error(self, message: str) -> None:
        self.query_one("#detail-error", Static).update(message)

    @work
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "back-to-list-button":
            self.app.pop_screen()
        elif button_id in (_action_id(ProjectAction.RESUME), _action_id(ProjectAction.RECREATE)):
            self._resume_or_recreate()
        elif button_id == _action_id(ProjectAction.OPEN_DEFAULT):
            self._open_default_workspace()
        elif button_id == _action_id(ProjectAction.CONFIGURE):
            self._configure_workspace()
        elif button_id == _action_id(ProjectAction.EDIT):
            self._edit_workspace()
        elif button_id == _action_id(ProjectAction.RESET):
            await self._reset_to_default()
        elif button_id == _action_id(ProjectAction.FORGET):
            await self._forget_workspace()

    def _resume_or_recreate(self) -> None:
        """Both Resume Session and Recreate Workspace produce the exact
        same ATTACH request -- see build_launch_request.
        """
        self.app.exit(build_launch_request(self.status))

    def _open_default_workspace(self) -> None:
        status = self.status
        # status.expected_session_name is already the deterministic,
        # collision-aware name gather_single_project_status assigned this
        # project -- reusing it (rather than re-deriving one here) keeps
        # the workspace actually created in sync with what was displayed,
        # and this action is only ever offered when that name isn't
        # currently running (see primary_actions), so it's always safe.
        workspace = build_default_workspace(
            status.project.name, status.canonical_path, status.expected_session_name
        )
        try:
            save_workspace(workspace)
        except WorkspaceStoreVersionError as exc:
            self._show_error(str(exc))
            return
        self.app.exit(
            LaunchRequest(workspace=workspace, init_git=False, action=LaunchAction.CREATE)
        )

    def _configure_workspace(self) -> None:
        status = self.status
        state = WizardState.for_configuring_existing_project(
            status.project.name, status.canonical_path, session_name=status.expected_session_name
        )
        self.app.push_screen(WindowConfigScreen(state, back_target=None))

    def _edit_workspace(self) -> None:
        status = self.status
        if status.saved_workspace is None:
            return
        state = WizardState.for_editing_workspace(
            status.saved_workspace, session_running=status.session_running
        )
        self.app.push_screen(WindowSummaryScreen(state))

    async def _reset_to_default(self) -> None:
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
            status.project.name, status.canonical_path, status.saved_workspace.session_name
        )
        try:
            save_workspace(workspace)
        except WorkspaceStoreVersionError as exc:
            # Dismissing the confirm screen above already triggers
            # on_screen_resume's own refresh -- refresh again explicitly
            # and show the error last, so it's never clobbered by that
            # refresh racing with this one.
            await self._refresh_status()
            self._show_error(str(exc))
            return
        await self._refresh_status()

    async def _forget_workspace(self) -> None:
        status = self.status
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                "Forget this project's saved workspace metadata?\n"
                "The project folder, git history, and any tmux session are not affected.",
                confirm_label="Forget",
            )
        )
        if not confirmed:
            return
        try:
            forget_workspace(status.canonical_path)
        except WorkspaceStoreVersionError as exc:
            # See _reset_to_default: refresh explicitly, then show the
            # error last, so it isn't clobbered by on_screen_resume's own
            # refresh (triggered by dismissing the confirm screen above).
            await self._refresh_status()
            self._show_error(str(exc))
            return
        await self._refresh_status()
