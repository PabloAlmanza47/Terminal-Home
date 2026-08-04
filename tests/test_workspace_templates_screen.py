from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Input, OptionList, Static

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceTemplate
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.template_name import TemplateNameScreen
from dashboard.screens.workspace_templates import WorkspaceTemplatesScreen
from dashboard.services.template_store import create_template, load_all_templates

_SIZE = (100, 40)


class _TemplatesApp(App[None]):
    def on_mount(self) -> None:
        self.push_screen(WorkspaceTemplatesScreen())


def _template(identity: str, name: str, *, window_name: str = "code") -> WorkspaceTemplate:
    return WorkspaceTemplate(
        identity,
        name,
        (
            WindowSpec(
                window_name,
                (
                    PaneSpec(PaneKind.CODE_EDITOR, "Code Editor"),
                    PaneSpec(PaneKind.CUSTOM_COMMAND, "Docs", "mkdocs serve"),
                ),
            ),
        ),
    )


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))


def test_empty_state_and_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[int, str]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            option_list = app.screen.query_one("#template-list", OptionList)
            summary = str(app.screen.query_one("#template-summary", Static).render())
            await pilot.press("escape")
            await pilot.pause()
            return option_list.option_count, summary

    count, summary = asyncio.run(scenario())
    assert count == 1
    assert "Save a configured project" in summary


def test_listing_summary_and_case_only_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    template = create_template(_template("00000000-0000-0000-0000-000000000001", "Full Stack"))

    async def scenario() -> tuple[str, str, str]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            option_list = app.screen.query_one("#template-list", OptionList)
            option_id = option_list.get_option_at_index(0).id
            summary = str(app.screen.query_one("#template-summary", Static).render())
            await pilot.click("#rename-template-button")
            await pilot.pause()
            assert isinstance(app.screen, TemplateNameScreen)
            name_input = app.screen.query_one("#template-name-input", Input)
            assert name_input.value == "Full Stack"
            name_input.value = "FULL STACK"
            await pilot.click("#template-name-submit")
            await app.workers.wait_for_complete()
            await pilot.pause()
            label = str(
                app.screen.query_one("#template-list", OptionList).get_option_at_index(0).prompt
            )
            return option_id or "", summary, label

    option_id, summary, label = asyncio.run(scenario())
    assert option_id == template.id
    assert "1 window(s) · 2 pane(s)" in summary
    assert "code: Code Editor, Docs" in summary
    assert label == "FULL STACK"
    assert load_all_templates()[0].id == template.id


def test_duplicate_rename_reports_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    create_template(_template("00000000-0000-0000-0000-000000000001", "Alpha"))
    create_template(_template("00000000-0000-0000-0000-000000000002", "Beta"))

    async def scenario() -> str:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#rename-template-button")
            await pilot.pause()
            app.screen.query_one("#template-name-input", Input).value = "beta"
            await pilot.click("#template-name-submit")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return str(app.screen.query_one("#template-error", Static).render())

    assert "already exists" in asyncio.run(scenario())
    assert [item.name for item in load_all_templates()] == ["Alpha", "Beta"]


def test_delete_cancel_then_confirm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    create_template(_template("00000000-0000-0000-0000-000000000001", "Keep or Delete"))

    async def scenario() -> tuple[int, int]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#delete-template-button")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#cancel-button")
            await app.workers.wait_for_complete()
            before = len(load_all_templates())
            await pilot.click("#delete-template-button")
            await pilot.pause()
            await pilot.click("#confirm-button")
            await app.workers.wait_for_complete()
            after = len(load_all_templates())
            return before, after

    assert asyncio.run(scenario()) == (1, 0)
