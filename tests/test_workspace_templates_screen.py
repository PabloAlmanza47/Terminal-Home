from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Input, OptionList, Static

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceTemplate
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.template_import_review import ImportTemplateReviewScreen
from dashboard.screens.template_name import TemplateNameScreen
from dashboard.screens.template_path import TemplatePathScreen
from dashboard.screens.workspace_templates import WorkspaceTemplatesScreen
from dashboard.services.template_portability import serialize_portable_template
from dashboard.services.template_store import create_template, delete_template, load_all_templates

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


def test_empty_state_keeps_import_available_and_export_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[bool, bool, str]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            import_disabled = app.screen.query_one("#import-template-button", Button).disabled
            export_disabled = app.screen.query_one("#export-template-button", Button).disabled
            await pilot.click("#import-template-button")
            await pilot.pause()
            modal = type(app.screen).__name__
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            return import_disabled, export_disabled, modal

    assert asyncio.run(scenario()) == (False, True, "TemplatePathScreen")


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


def test_import_review_cancel_and_confirm_with_stable_new_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    source = tmp_path / "portable.th-template.json"
    source.write_text(
        serialize_portable_template(_template("00000000-0000-0000-0000-000000000001", "Imported"))
    )

    async def scenario(confirm: bool) -> tuple[int, str, str]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#import-template-button")
            await pilot.pause()
            assert isinstance(app.screen, TemplatePathScreen)
            app.screen.query_one("#template-path-input", Input).value = str(source)
            await pilot.click("#template-path-submit")
            await pilot.pause()
            assert isinstance(app.screen, ImportTemplateReviewScreen)
            review = str(app.screen.query_one("#template-import-review-body", Static).render())
            await pilot.click("#template-import-confirm" if confirm else "#template-import-cancel")
            await app.workers.wait_for_complete()
            await pilot.pause()
            templates = load_all_templates()
            option_list = app.screen.query_one("#template-list", OptionList)
            selected_id = (
                option_list.get_option_at_index(option_list.highlighted or 0).id
                if templates
                else ""
            )
            return len(templates), review, selected_id or ""

    count, review, selected_id = asyncio.run(scenario(False))
    assert count == 0
    assert "code" in review and "code_editor" in review
    assert "Docs" in review and "mkdocs serve" in review

    count, review, selected_id = asyncio.run(scenario(True))
    assert count == 1
    assert selected_id == load_all_templates()[0].id
    assert selected_id != "00000000-0000-0000-0000-000000000001"


def test_duplicate_import_requires_explicit_new_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    existing = create_template(_template("00000000-0000-0000-0000-000000000001", "Full Stack"))
    source = tmp_path / "portable.json"
    source.write_text(serialize_portable_template(existing))

    async def scenario() -> None:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#import-template-button")
            await pilot.pause()
            app.screen.query_one("#template-path-input", Input).value = str(source)
            await pilot.click("#template-path-submit")
            await pilot.pause()
            assert isinstance(app.screen, TemplateNameScreen)
            app.screen.query_one("#template-name-input", Input).value = "   "
            await pilot.click("#template-name-submit")
            await pilot.pause()
            assert isinstance(app.screen, TemplateNameScreen)
            app.screen.query_one("#template-name-input", Input).value = "Full Stack Copy"
            await pilot.click("#template-name-submit")
            await pilot.pause()
            assert isinstance(app.screen, ImportTemplateReviewScreen)
            await pilot.click("#template-import-confirm")
            await app.workers.wait_for_complete()

    asyncio.run(scenario())
    templates = load_all_templates()
    assert [item.name for item in templates] == ["Full Stack", "Full Stack Copy"]
    assert templates[0].id == existing.id
    assert templates[0].windows == templates[1].windows


def test_changed_import_source_after_review_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    source = tmp_path / "portable.json"
    source.write_text(
        serialize_portable_template(_template("00000000-0000-0000-0000-000000000001", "Reviewed"))
    )

    async def scenario() -> str:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#import-template-button")
            await pilot.pause()
            app.screen.query_one("#template-path-input", Input).value = str(source)
            await pilot.click("#template-path-submit")
            await pilot.pause()
            source.write_text(
                serialize_portable_template(
                    _template("00000000-0000-0000-0000-000000000002", "Changed")
                )
            )
            await pilot.click("#template-import-confirm")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return str(app.screen.query_one("#template-error", Static).render())

    assert "changed after review" in asyncio.run(scenario())
    assert load_all_templates() == ()


def test_import_parse_error_and_missing_export_selection_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    template = create_template(_template("00000000-0000-0000-0000-000000000001", "Local"))
    invalid = tmp_path / "invalid.json"
    invalid.write_text("not json")

    async def scenario() -> tuple[str, str]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#import-template-button")
            await pilot.pause()
            app.screen.query_one("#template-path-input", Input).value = str(invalid)
            await pilot.click("#template-path-submit")
            await app.workers.wait_for_complete()
            parse_error = str(app.screen.query_one("#template-error", Static).render())

            await pilot.click("#export-template-button")
            await pilot.pause()
            assert delete_template(template.id)
            app.screen.query_one("#template-path-input", Input).value = str(
                tmp_path / "never-written.json"
            )
            await pilot.click("#template-path-submit")
            await app.workers.wait_for_complete()
            missing_error = str(app.screen.query_one("#template-error", Static).render())
            return parse_error, missing_error

    parse_error, missing_error = asyncio.run(scenario())
    assert "not valid JSON" in parse_error
    assert missing_error == "Template no longer exists."
    assert not (tmp_path / "never-written.json").exists()


def test_export_cancel_success_and_confirmed_overwrite_keep_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    template = create_template(_template("00000000-0000-0000-0000-000000000001", "Full Stack"))
    target = tmp_path / "shared.th-template.json"

    async def scenario() -> tuple[str, str, str]:
        app = _TemplatesApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.click("#export-template-button")
            await pilot.pause()
            assert isinstance(app.screen, TemplatePathScreen)
            default_name = app.screen.query_one("#template-path-input", Input).value
            await pilot.press("escape")
            await app.workers.wait_for_complete()
            assert not target.exists()

            await pilot.click("#export-template-button")
            await pilot.pause()
            app.screen.query_one("#template-path-input", Input).value = str(target)
            await pilot.click("#template-path-submit")
            await app.workers.wait_for_complete()
            first_status = str(app.screen.query_one("#template-error", Static).render())

            target.write_text("old bytes")
            await pilot.click("#export-template-button")
            await pilot.pause()
            app.screen.query_one("#template-path-input", Input).value = str(target)
            await pilot.click("#template-path-submit")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.click("#confirm-button")
            await app.workers.wait_for_complete()
            option_list = app.screen.query_one("#template-list", OptionList)
            selected_id = option_list.get_option_at_index(option_list.highlighted or 0).id or ""
            return default_name, first_status, selected_id

    default_name, first_status, selected_id = asyncio.run(scenario())
    assert default_name == "full-stack.th-template.json"
    assert str(target.resolve()) in first_status
    assert selected_id == template.id
    assert json.loads(target.read_text())["format"] == "terminal-home-workspace-template"
    assert target.with_name(f"{target.name}.bak").read_text() == "old bytes"
