"""Focused offline tests for SSH host and remote-project management screens."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input, OptionList, Static

from dashboard.models import RemoteProjectRegistration, SshHost
from dashboard.screens.remote_projects import RemoteProjectsScreen
from dashboard.screens.ssh_hosts import SshHostsScreen
from dashboard.services.remote_project_store import create_remote_project
from dashboard.services.ssh_host_store import create_ssh_host, load_all_ssh_hosts
from dashboard.widgets import KeyboardActionList

_HOST_ID = "d84aeefb-7c29-4c63-b39c-766d559df977"
_PROJECT_ID = "c27c7b67-8e3f-4ebc-8dce-d66d559df977"
_MISSING_HOST_ID = "e95bfffc-8d3e-4d74-c4ad-877e66ef2aa8"


class _HostApp(App[None]):
    def on_mount(self) -> None:
        self.push_screen(SshHostsScreen())


class _RemoteApp(App[None]):
    def on_mount(self) -> None:
        self.push_screen(RemoteProjectsScreen())


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


def _run(coro):
    return asyncio.run(coro)


async def _activate(pilot, list_id: str, action_id: str) -> None:
    actions = pilot.app.screen.query_one(list_id, KeyboardActionList)
    actions.selected_index = next(
        i for i, item in enumerate(actions.actions) if item.id == action_id
    )
    actions.focus()
    await pilot.press("enter")
    await pilot.pause()


def test_ssh_host_screen_adds_and_confirms_unreferenced_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=(100, 30)) as pilot:
            app.screen.query_one("#host-name", Input).value = "build"
            app.screen.query_one("#host-destination", Input).value = "builder"
            await _activate(pilot, "#host-actions", "add")
            await pilot.pause()
            assert len(load_all_ssh_hosts()) == 1
            await _activate(pilot, "#host-actions", "remove")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()

    _run(scenario())
    assert load_all_ssh_hosts() == ()


def test_host_screen_explains_referenced_host_and_remote_screen_labels_orphan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    create_ssh_host(SshHost(_HOST_ID, "build", "builder"))
    create_remote_project(RemoteProjectRegistration(_PROJECT_ID, _HOST_ID, "api", "/srv/api"))

    async def host_scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=(100, 30)) as pilot:
            app.screen.query_one("#host-list", OptionList).highlighted = 0
            await _activate(pilot, "#host-actions", "remove")
            await pilot.pause()
            await pilot.press("down", "enter")
            await pilot.pause()
            return str(app.screen.query_one("#host-error", Static).render())

    assert "referenced" in _run(host_scenario())
    assert load_all_ssh_hosts()

    # Orphaned records remain visible and editable without the host store entry.
    create_remote_project(
        RemoteProjectRegistration(_MISSING_HOST_ID, _MISSING_HOST_ID, "orphan", "/srv/orphan")
    )

    async def remote_scenario() -> str:
        app = _RemoteApp()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            options = app.screen.query_one("#remote-project-list", OptionList)
            return "\n".join(
                str(options.get_option_at_index(index).prompt)
                for index in range(options.option_count)
            )

    assert "missing host" in _run(remote_scenario())
