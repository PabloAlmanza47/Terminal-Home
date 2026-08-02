"""Step 1 of the New Project wizard: project name, folder name, and the
git-init choice.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Checkbox, Footer, Input, Static

from dashboard.screens.new_project.state import WizardState
from dashboard.services import project_creation
from dashboard.services.project_creation import validate_new_project
from dashboard.services.slug import slugify


class NewProjectScreen(Screen[None]):
    """Project display name, folder name, and git-init choice."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, state: WizardState | None = None) -> None:
        super().__init__()
        self.state = state if state is not None else WizardState()

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Create New Project -- Step 1 of 5: Project Info", id="screen-title")
                yield Static("Project name", classes="field-label")
                yield Input(
                    value=self.state.project_name,
                    placeholder="My Cool Project",
                    id="project-name-input",
                )
                yield Static("Folder name", classes="field-label")
                yield Input(
                    value=self.state.folder_name,
                    placeholder="my-cool-project",
                    id="folder-name-input",
                )
                yield Static("", id="destination-preview")
                yield Checkbox(
                    "Initialize an empty Git repository",
                    value=self.state.init_git,
                    id="git-init-checkbox",
                )
                yield Static("", id="wizard-error")
                with Horizontal(classes="button-row"):
                    yield Button("Next", id="next-button", variant="primary")
                    yield Button("Cancel", id="cancel-button")
        yield Footer()

    def on_mount(self) -> None:
        self._update_destination_preview()
        self.query_one("#project-name-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "project-name-input":
            self.state.project_name = event.value
            if not self.state.folder_name_touched:
                slug = slugify(event.value)
                self.state.folder_name = slug
                self.query_one("#folder-name-input", Input).value = slug
        elif event.input.id == "folder-name-input":
            self.state.folder_name_touched = True
            self.state.folder_name = event.value
        self._update_destination_preview()

    def _update_destination_preview(self) -> None:
        folder = self.state.folder_name.strip()
        root = project_creation.DEFAULT_PROJECTS_ROOT
        destination = root / folder if folder else root
        self.query_one("#destination-preview", Static).update(f"Destination: {destination}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "next-button":
            self._go_next()
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def _go_next(self) -> None:
        self.state.project_name = self.query_one("#project-name-input", Input).value
        self.state.folder_name = self.query_one("#folder-name-input", Input).value
        self.state.init_git = self.query_one("#git-init-checkbox", Checkbox).value

        validation = validate_new_project(self.state.project_name, self.state.folder_name)
        if not validation.is_valid:
            self.query_one("#wizard-error", Static).update("\n".join(validation.errors))
            return

        self.query_one("#wizard-error", Static).update("")
        # Imported here to avoid a circular import (step_window_config also
        # imports this module's screen for the "Back" transition).
        from dashboard.screens.new_project.step_window_config import WindowConfigScreen

        self.app.switch_screen(WindowConfigScreen(self.state, back_target="project_info"))

    def action_cancel(self) -> None:
        self.app.pop_screen()
