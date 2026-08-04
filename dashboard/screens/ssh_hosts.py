"""Offline management of registered SSH host metadata."""

from __future__ import annotations

from uuid import uuid4

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.models import SshHost, SshModelValidationError
from dashboard.screens.confirm import ConfirmScreen
from dashboard.services.remote_registry import (
    HostStillReferencedError,
    RemoteRegistryError,
    remove_ssh_host,
)
from dashboard.services.ssh_host_store import (
    SshHostStoreError,
    create_ssh_host,
    load_all_ssh_hosts,
    update_ssh_host,
)


class SshHostsScreen(Screen[None]):
    """List and edit SSH destinations without testing connectivity."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.hosts: list[SshHost] = []
        self.selected_id: str | None = None

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static("SSH Hosts", id="screen-title")
                yield Static(
                    "Stored destinations are managed locally; no connection is tested.",
                    classes="wizard-hint",
                )
                yield OptionList(id="host-list")
                yield Input(placeholder="Display name", id="host-name")
                yield Input(
                    placeholder="SSH destination (for example user@host)",
                    id="host-destination",
                )
                with Horizontal(classes="button-row"):
                    yield Button("Add Host", id="add-host-button", variant="primary")
                    yield Button("Edit Selected", id="edit-host-button")
                    yield Button("Remove Selected", id="remove-host-button")
                yield Static("", id="host-error", classes="wizard-hint")
                with Horizontal(classes="button-row"):
                    yield Button("Back", id="back-button")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self.hosts = list(load_all_ssh_hosts())
        options = self.query_one("#host-list", OptionList)
        options.clear_options()
        for host in self.hosts:
            options.add_option(
                Option(f"{host.display_name}  {host.id}  {host.destination}", id=host.id)
            )
        if self.selected_id and any(host.id == self.selected_id for host in self.hosts):
            self._select(self.selected_id)

    def _select(self, host_id: str) -> None:
        self.selected_id = host_id
        host = next((item for item in self.hosts if item.id == host_id), None)
        if host is not None:
            self.query_one("#host-name", Input).value = host.display_name
            self.query_one("#host-destination", Input).value = host.destination

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option and event.option.id:
            self._select(str(event.option.id))

    def _values(self) -> tuple[str, str] | None:
        name = self.query_one("#host-name", Input).value
        destination = self.query_one("#host-destination", Input).value
        if not name.strip() or not destination.strip():
            self.query_one("#host-error", Static).update(
                "Display name and destination are required."
            )
            return None
        return name, destination

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-host-button":
            values = self._values()
            if values is None:
                return
            try:
                host = create_ssh_host(SshHost(str(uuid4()), *values))
            except (SshModelValidationError, SshHostStoreError) as exc:
                self.query_one("#host-error", Static).update(str(exc))
                return
            self.selected_id = host.id
            self.query_one("#host-error", Static).update("Host added.")
            self._refresh()
        elif event.button.id == "edit-host-button":
            self._edit_selected()
        elif event.button.id == "remove-host-button":
            self._confirm_remove()
        elif event.button.id == "back-button":
            self.action_go_back()

    def _edit_selected(self) -> None:
        if self.selected_id is None:
            self.query_one("#host-error", Static).update("Select a host first.")
            return
        values = self._values()
        if values is None:
            return
        try:
            update_ssh_host(self.selected_id, display_name=values[0], destination=values[1])
        except (SshModelValidationError, SshHostStoreError) as exc:
            self.query_one("#host-error", Static).update(str(exc))
            return
        self.query_one("#host-error", Static).update("Host updated.")
        self._refresh()

    @work
    async def _confirm_remove(self) -> None:
        if self.selected_id is None:
            self.query_one("#host-error", Static).update("Select a host first.")
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                "Remove this SSH host? Referenced hosts cannot be removed.",
                confirm_label="Remove",
            )
        )
        if not confirmed:
            return
        try:
            remove_ssh_host(self.selected_id)
        except (HostStillReferencedError, RemoteRegistryError, SshHostStoreError) as exc:
            self.query_one("#host-error", Static).update(str(exc))
            return
        self.selected_id = None
        self.query_one("#host-error", Static).update("Host removed.")
        self._refresh()

    def action_go_back(self) -> None:
        self.app.pop_screen()
