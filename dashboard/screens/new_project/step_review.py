"""Step 5 of the New Project wizard: final review and confirmation.

Nothing touches the filesystem, git, or tmux until "Create and Open" is
pressed here. On confirmation this creates the project directory, runs
`git init` if requested, and persists the workspace spec -- all safe to do
while Textual is still mounted -- then exits the app with a LaunchRequest
so the (non-Textual) tmux orchestration layer can take over the terminal.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static

from dashboard.models import LaunchRequest, WorkspaceSpec, WorkspaceValidationError
from dashboard.models.layout import render_pane_preview
from dashboard.screens.new_project.state import WizardState
from dashboard.services.project_creation import (
    ProjectCreationError,
    create_project_directory,
    init_git_repo,
    resolve_destination,
    validate_new_project,
)
from dashboard.services.workspace_store import save_workspace


class ReviewScreen(Screen[None]):
    """Final summary of the whole workspace before anything is created."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        folder = self.state.folder_name.strip()
        try:
            destination = resolve_destination(folder) if folder else None
        except ProjectCreationError:
            destination = None

        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static("Create New Project -- Step 5 of 5: Review", id="screen-title")
                yield Static(f"Project name:  {self.state.project_name}")
                yield Static(f"Destination:   {destination if destination else folder}")
                yield Static(f"Git init:      {'yes' if self.state.init_git else 'no'}")
                yield Static(f"tmux session:  {self.state.session_name}")
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
                    yield Button("Create and Open", id="create-button", variant="primary")
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

        created_dir = False
        try:
            create_project_directory(destination)
            created_dir = True
            if self.state.init_git:
                init_git_repo(destination)
            save_workspace(workspace)
        except ProjectCreationError as exc:
            detail = (
                f" (the directory at {destination} was already created)" if created_dir else ""
            )
            self._show_error(f"{exc}{detail}")
            return

        self.app.exit(LaunchRequest(workspace=workspace, init_git=self.state.init_git))

    def _show_error(self, message: str) -> None:
        self.query_one("#wizard-error", Static).update(message)

    def action_cancel(self) -> None:
        self.app.pop_screen()
