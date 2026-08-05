from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import OptionList

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceTemplate
from dashboard.screens.new_project.state import WizardState
from dashboard.screens.new_project.step_workspace_start import WorkspaceStartScreen
from dashboard.services.template_store import create_template, default_template_store_path
from dashboard.widgets import KeyboardActionList


class _WizardApp(App[None]):
    pass


def test_saved_template_prepopulates_independent_wizard_drafts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    template = create_template(
        WorkspaceTemplate(
            "00000000-0000-0000-0000-000000000001",
            "Full Stack",
            (
                WindowSpec(
                    "web",
                    (
                        PaneSpec(PaneKind.DEV_SERVER, "Development Server"),
                        PaneSpec(PaneKind.CUSTOM_COMMAND, "Docs", "  mkdocs serve  "),
                    ),
                ),
                WindowSpec("tests", (PaneSpec(PaneKind.TEST_TERMINAL, "Test Terminal"),)),
            ),
        )
    )
    state = WizardState(project_name="Destination", folder_name="destination")

    async def scenario() -> None:
        app = _WizardApp()
        async with app.run_test(size=(100, 50)) as pilot:
            app.push_screen(WorkspaceStartScreen(state))
            await pilot.pause()
            option_list = app.screen.query_one("#workspace-start-list", OptionList)
            option_list.highlighted = 2
            actions = app.screen.query_one("#workspace-start-actions", KeyboardActionList)
            actions.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "WindowSummaryScreen"
            assert [window.window_name for window in state.windows] == ["web", "tests"]
            state.windows[0].window_name = "changed"
            assert template.windows[0].window_name == "web"
            assert state.windows[0].panes[1].custom_command == "  mkdocs serve  "

    def load_name() -> str:
        # Function body is intentionally below the flow assertion so the
        # store read is visibly separate from mutable wizard state.
        from dashboard.services.template_store import load_all_templates

        return load_all_templates(default_template_store_path())[0].name

    asyncio.run(scenario())
    assert load_name() == "Full Stack"


def test_selecting_blank_or_default_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    for index in (0, 1):
        state = WizardState(project_name="Destination", folder_name="destination")

        async def scenario() -> None:
            app = _WizardApp()
            async with app.run_test(size=(100, 50)) as pilot:
                app.push_screen(WorkspaceStartScreen(state))
                await pilot.pause()
                app.screen.query_one("#workspace-start-list", OptionList).highlighted = index
                actions = app.screen.query_one("#workspace-start-actions", KeyboardActionList)
                actions.focus()
                await pilot.press("enter")
                await pilot.pause()

        asyncio.run(scenario())
    assert not default_template_store_path().exists()
