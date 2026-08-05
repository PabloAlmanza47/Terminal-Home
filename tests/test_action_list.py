from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult

from dashboard.widgets import ActionItem, KeyboardActionList


class _ActionApp(App[str | None]):
    def compose(self) -> ComposeResult:
        yield KeyboardActionList(
            ActionItem("cancel", "Cancel"),
            ActionItem("forget", "Forget", dangerous=True),
            id="actions",
        )


def test_action_list_starts_safe_and_activates_with_arrows() -> None:
    async def scenario() -> tuple[str | None, int | None, str | None]:
        app = _ActionApp()
        async with app.run_test(size=(60, 12)) as pilot:
            actions = app.query_one("#actions", KeyboardActionList)
            await pilot.press("down")
            selected = actions.selected_index
            await pilot.press("space")
            await pilot.pause()
            return actions.selected_action_id, selected, app.screen.query_one("#actions").id

    selected_id, selected_index, action_id = asyncio.run(scenario())
    assert selected_id == "forget"
    assert selected_index == 1
    assert action_id == "actions"
