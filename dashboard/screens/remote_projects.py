"""Offline management of registered remote-project metadata."""

from __future__ import annotations

from uuid import uuid4

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Static
from textual.widgets.option_list import Option

from dashboard.models import RemoteProjectRegistration, SshModelValidationError
from dashboard.screens.confirm import ConfirmScreen
from dashboard.services.remote_project_store import RemoteProjectStoreError
from dashboard.services.remote_registry import (
    RemoteRegistryError,
    inspect_remote_registry_integrity,
    register_remote_project,
    remove_registered_remote_project,
    update_registered_remote_project,
)
from dashboard.widgets import KeyboardOptionList as OptionList


class RemoteProjectsScreen(Screen[None]):
    """List and edit registrations without inspecting remote machines."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.projects: list[RemoteProjectRegistration] = []
        self.orphaned_ids: set[str] = set()
        self.selected_id: str | None = None

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static("Registered Remote Projects", id="screen-title")
                yield Static(
                    "Registrations are local metadata; no SSH connection is made.",
                    classes="wizard-hint",
                )
                yield OptionList(id="remote-project-list")
                yield Input(placeholder="Project name", id="remote-name")
                yield Input(placeholder="SSH host ID", id="remote-host-id")
                yield Input(placeholder="Absolute remote path", id="remote-path")
                with Horizontal(classes="button-row"):
                    yield Button("Add Project", id="add-remote-button", variant="primary")
                    yield Button("Edit Selected", id="edit-remote-button")
                    yield Button("Remove Selected", id="remove-remote-button")
                yield Static("", id="remote-error", classes="wizard-hint")
                with Horizontal(classes="button-row"):
                    yield Button("Back", id="back-button")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#remote-project-list", OptionList).focus()

    def _refresh(self) -> None:
        result = inspect_remote_registry_integrity()
        self.projects = list(result.projects)
        self.orphaned_ids = set(result.orphaned_project_ids)
        options = self.query_one("#remote-project-list", OptionList)
        options.clear_options()
        for project in self.projects:
            marker = "  [missing host]" if project.id in self.orphaned_ids else ""
            options.add_option(
                Option(
                    f"{project.name}  {project.host_id}  {project.remote_path}{marker}",
                    id=project.id,
                )
            )
        if self.selected_id and any(project.id == self.selected_id for project in self.projects):
            self._select(self.selected_id)

    def _select(self, project_id: str) -> None:
        self.selected_id = project_id
        project = next((item for item in self.projects if item.id == project_id), None)
        if project is not None:
            self.query_one("#remote-name", Input).value = project.name
            self.query_one("#remote-host-id", Input).value = project.host_id
            self.query_one("#remote-path", Input).value = project.remote_path

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option and event.option.id:
            self._select(str(event.option.id))

    def _values(self) -> tuple[str, str, str] | None:
        name = self.query_one("#remote-name", Input).value
        host_id = self.query_one("#remote-host-id", Input).value
        remote_path = self.query_one("#remote-path", Input).value
        values = (name, host_id, remote_path)
        if not all(value.strip() for value in values):
            self.query_one("#remote-error", Static).update(
                "Name, host ID, and remote path are required."
            )
            return None
        return values

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-remote-button":
            values = self._values()
            if values is None:
                return
            try:
                project = register_remote_project(
                    RemoteProjectRegistration(str(uuid4()), values[1], values[0], values[2])
                )
            except (SshModelValidationError, RemoteRegistryError, RemoteProjectStoreError) as exc:
                self.query_one("#remote-error", Static).update(str(exc))
                return
            self.selected_id = project.id
            self.query_one("#remote-error", Static).update("Remote project added.")
            self._refresh()
        elif event.button.id == "edit-remote-button":
            self._edit_selected()
        elif event.button.id == "remove-remote-button":
            self._confirm_remove()
        elif event.button.id == "back-button":
            self.action_go_back()

    def _edit_selected(self) -> None:
        if self.selected_id is None:
            self.query_one("#remote-error", Static).update("Select a remote project first.")
            return
        values = self._values()
        if values is None:
            return
        try:
            update_registered_remote_project(
                self.selected_id,
                name=values[0],
                host_id=values[1],
                remote_path=values[2],
            )
        except (SshModelValidationError, RemoteRegistryError, RemoteProjectStoreError) as exc:
            self.query_one("#remote-error", Static).update(str(exc))
            return
        self.query_one("#remote-error", Static).update("Remote project updated.")
        self._refresh()

    @work
    async def _confirm_remove(self) -> None:
        if self.selected_id is None:
            self.query_one("#remote-error", Static).update("Select a remote project first.")
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen("Remove this registered remote project?", confirm_label="Remove")
        )
        if not confirmed:
            return
        try:
            remove_registered_remote_project(self.selected_id)
        except (RemoteRegistryError, RemoteProjectStoreError) as exc:
            self.query_one("#remote-error", Static).update(str(exc))
            return
        self.selected_id = None
        self.query_one("#remote-error", Static).update("Remote project removed.")
        self._refresh()

    def action_go_back(self) -> None:
        self.app.pop_screen()
