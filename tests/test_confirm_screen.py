"""Textual Pilot tests for the reusable Yes/No confirmation dialog
(dashboard.screens.confirm.ConfirmScreen).
"""

from __future__ import annotations

import asyncio

from textual import work
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Button

from dashboard.screens.confirm import ConfirmScreen

_SIZE = (80, 24)


class _HostScreen(Screen[None]):
    """Minimal host screen with a button that opens a ConfirmScreen and
    records its result.
    """

    def __init__(self) -> None:
        super().__init__()
        self.result: bool | None = "unset"  # type: ignore[assignment]

    def compose(self) -> ComposeResult:
        yield Button("Open", id="open-button")

    @work
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        self.result = await self.app.push_screen_wait(ConfirmScreen("Are you sure?"))


class _HostApp(App[None]):
    def on_mount(self) -> None:
        self.host = _HostScreen()
        self.push_screen(self.host)


def _run(coro):
    return asyncio.run(coro)


def test_confirm_button_resolves_true() -> None:
    async def scenario() -> bool | None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#open-button")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            await pilot.press("down", "enter")
            await pilot.pause()
            await app.workers.wait_for_complete()

            return app.host.result

    assert _run(scenario()) is True


def test_cancel_button_resolves_false() -> None:
    async def scenario() -> bool | None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#open-button")
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()
            await app.workers.wait_for_complete()

            return app.host.result

    assert _run(scenario()) is False


def test_escape_resolves_false() -> None:
    async def scenario() -> bool | None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#open-button")
            await pilot.pause()

            await pilot.press("escape")
            await pilot.pause()
            await app.workers.wait_for_complete()

            return app.host.result

    assert _run(scenario()) is False
