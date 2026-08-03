"""Final review and confirmation step of the window/pane configuration flow.

Shared by three flows (dashboard.screens.new_project.state.WizardMode):
NEW_PROJECT creates the project directory, optionally runs `git init`, and
launches. EXISTING_CREATE ("Configure Workspace") and EXISTING_EDIT ("Edit
Workspace") never touch the project directory or git; EXISTING_CREATE
saves and launches like NEW_PROJECT, while EXISTING_EDIT only saves and
returns to Project Detail, since a live tmux session (if any) must never
be disturbed by editing its saved layout.

Nothing touches the filesystem, git, or tmux until this step's primary
button is pressed.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from dashboard.models import LaunchAction, LaunchRequest, WorkspaceSpec, WorkspaceValidationError
from dashboard.models.layout import render_pane_preview
from dashboard.screens.new_project.state import WizardMode, WizardState
from dashboard.services.project_creation import (
    ProjectCreationError,
    create_project_directory,
    init_git_repo,
    resolve_destination,
    validate_new_project,
)
from dashboard.services.workspace_store import (
    WorkspaceStoreVersionError,
    ensure_workspace_store_writable,
    save_workspace,
)


class ReviewScreen(Screen[None]):
    """Final summary of the whole workspace before anything is created."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        mode = self.state.mode
        is_existing = mode is not WizardMode.NEW_PROJECT

        if is_existing:
            destination_text = str(self.state.existing_project_path)
        else:
            folder = self.state.folder_name.strip()
            try:
                destination = resolve_destination(folder) if folder else None
            except ProjectCreationError:
                destination = None
            destination_text = str(destination) if destination else folder

        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static(self.state.step_label(5, "Review"), id="screen-title")
                yield Static(f"Project name:  {self.state.project_name}")
                yield Static(f"Destination:   {destination_text}")
                if not is_existing:
                    yield Static(f"Git init:      {'yes' if self.state.init_git else 'no'}")
                yield Static(f"tmux session:  {self.state.session_name}")
                if mode is WizardMode.EXISTING_EDIT and self.state.warn_session_running:
                    yield Static(
                        "Note: a tmux session for this project is currently running.\n"
                        "This updated layout applies the next time the session is\n"
                        "recreated -- the live session is left untouched.",
                        id="running-session-warning",
                        classes="wizard-hint",
                    )
                for window in self.state.windows:
                    labels = [pane.display_name for pane in window.panes]
                    yield Static(f"\nWindow: {window.window_name}", classes="field-label")
                    yield Static(
                        "\n".join(f"  {i + 1}. {label}" for i, label in enumerate(labels))
                    )
                    yield Static(
                        "\n".join(render_pane_preview(labels, width=32, height=8)),
                        classes="preview-grid",
                    )
                yield Static("", id="wizard-error")
                with Horizontal(classes="button-row"):
                    primary_label = (
                        "Save Workspace" if mode is WizardMode.EXISTING_EDIT else "Create and Open"
                    )
                    yield Button(primary_label, id="create-button", variant="primary")
                    yield Button("Go Back", id="back-button")
                    yield Button("Cancel", id="cancel-button")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "create-button":
            self._create()
        elif event.button.id == "back-button":
            from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen

            self.app.switch_screen(WindowSummaryScreen(self.state))
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def _create(self) -> None:
        if self.state.mode is WizardMode.NEW_PROJECT:
            self._create_new_project()
        else:
            self._save_existing_project()

    def _create_new_project(self) -> None:
        validation = validate_new_project(self.state.project_name, self.state.folder_name)
        if not validation.is_valid:
            self._show_error("\n".join(validation.errors))
            return

        try:
            destination = resolve_destination(self.state.folder_name)
        except ProjectCreationError as exc:
            self._show_error(str(exc))
            return

        try:
            workspace = WorkspaceSpec(
                project_name=self.state.project_name.strip(),
                project_path=destination,
                session_name=self.state.session_name,
                windows=tuple(window.to_window_spec() for window in self.state.windows),
            )
        except WorkspaceValidationError as exc:
            self._show_error(str(exc))
            return

        try:
            ensure_workspace_store_writable()
        except WorkspaceStoreVersionError as exc:
            self._show_error(str(exc))
            return

        created_dir = False
        try:
            create_project_directory(destination)
            created_dir = True
            if self.state.init_git:
                init_git_repo(destination)
            save_workspace(workspace)
        except (ProjectCreationError, WorkspaceStoreVersionError) as exc:
            detail = (
                f" (the directory at {destination} was already created)" if created_dir else ""
            )
            self._show_error(f"{exc}{detail}")
            return

        self.app.exit(LaunchRequest(workspace=workspace, init_git=self.state.init_git))

    def _save_existing_project(self) -> None:
        """EXISTING_CREATE ("Configure Workspace") and EXISTING_EDIT ("Edit
        Workspace") both just persist the WorkspaceSpec for an
        already-existing project directory -- never creating, renaming,
        or deleting anything, and never running `git init`.
        """
        assert self.state.existing_project_path is not None

        try:
            workspace = WorkspaceSpec(
                project_name=self.state.project_name.strip(),
                project_path=self.state.existing_project_path,
                session_name=self.state.session_name,
                windows=tuple(window.to_window_spec() for window in self.state.windows),
            )
        except WorkspaceValidationError as exc:
            self._show_error(str(exc))
            return

        try:
            save_workspace(workspace)
        except WorkspaceStoreVersionError as exc:
            self._show_error(str(exc))
            return

        if self.state.mode is WizardMode.EXISTING_EDIT:
            # Never launches -- a currently running session (if any) for
            # this project is left completely untouched.
            self.app.pop_screen()
            return

        self.app.exit(
            LaunchRequest(workspace=workspace, init_git=False, action=LaunchAction.CREATE)
        )

    def _show_error(self, message: str) -> None:
        self.query_one("#wizard-error", Static).update(message)

    def action_cancel(self) -> None:
        self.app.pop_screen()
