from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest
from rich.text import Text
from textual.widgets.option_list import Option

import dashboard.screens.tmux_sessions as tmux_screen_module
from dashboard.models.settings import AppSettings, TableHeaderColor
from dashboard.models.workspace import TmuxSessionAttachRequest
from dashboard.services import tmux
from dashboard.services.cli_colors import style_table_header
from dashboard.services.project_rows import format_project_row
from dashboard.services.settings_store import load_settings, save_settings
from dashboard.services.tmux import TmuxSession
from dashboard.services.workspace_launcher import LaunchError, execute_tmux_session_attach
from dashboard.widgets import KeyboardOptionList


def test_project_rows_fit_wide_and_narrow_limits() -> None:
    for width in (20, 31, 52, 80):
        row = format_project_row(
            "a very long project name", "Saved Workspace", "feature/long", width
        )
        assert len(row) <= width


@pytest.mark.parametrize("width", [20, 31, 40, 61, 80])
def test_tmux_rows_fit_available_width(width: int) -> None:
    row = tmux_screen_module.format_tmux_session_row(
        TmuxSession("a-session-with-a-long-name", 3, "Mon Aug  1 09:00:00 2026", True),
        width,
    )
    assert len(row) <= max(12, width)


def test_resume_screen_returns_immutable_request_after_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tmux_screen_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(
        tmux_screen_module,
        "list_tmux_sessions",
        lambda: [TmuxSession("dev", 2, "created", False)],
    )

    async def scenario():
        from textual.app import App

        class Host(App[object]):
            def on_mount(self) -> None:
                self.push_screen(tmux_screen_module.TmuxSessionsScreen())

        app = Host()
        async with app.run_test(size=(48, 16)) as pilot:
            await pilot.pause()
            session_list = app.screen.query_one("#tmux-list", tmux_screen_module.OptionList)
            session_list.highlighted = 0
            session_list.focus()
            await pilot.press("space")
        return app.return_value

    result = asyncio.run(scenario())
    assert result == TmuxSessionAttachRequest("dev")


def test_shared_option_marker_updates_do_not_accumulate_or_flatten_rich_prompt() -> None:
    option = Option(Text("row", style="bold red"))
    widget = KeyboardOptionList(option)
    widget._update_prompt_markers()
    widget._update_prompt_markers()
    assert option.prompt.plain == "  row"
    assert option.prompt.spans
    assert option.prompt.spans[0].start == 2
    assert option.prompt.spans[0].style == "bold red"


def test_project_row_wide_layout_aligns_status_and_branch() -> None:
    first = format_project_row("one", "Running", "dev", 80)
    second = format_project_row("two", "Not Configured", None, 80)
    assert first.index("[") == second.index("[")
    assert first.rfind("dev") > first.index("]")


def test_settings_header_color_migrates_and_invalid_value_preserves_other_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"artwork_enabled": false, "table_header_color": "bogus"}')
    loaded = load_settings(path)
    assert loaded.artwork_enabled is False
    assert loaded.table_header_color is TableHeaderColor.THEME
    save_settings(AppSettings(table_header_color=TableHeaderColor.MAGENTA), path)
    assert load_settings(path).table_header_color is TableHeaderColor.MAGENTA


@pytest.mark.parametrize("setting", list(TableHeaderColor))
def test_cli_header_color_policy(
    setting: TableHeaderColor, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = io.StringIO()
    monkeypatch.delenv("NO_COLOR", raising=False)
    assert "\033[" not in style_table_header("NAME", setting, stream)

    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    tty = Tty()
    styled = style_table_header("NAME", setting, tty)
    if setting is TableHeaderColor.NONE:
        assert styled == "NAME"
    else:
        assert "\033[" in styled
    monkeypatch.setenv("NO_COLOR", "1")
    assert style_table_header("NAME", setting, tty) == "NAME"


def test_tmux_attach_revalidates_and_uses_existing_argv_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux, "session_exists", lambda name: name == "dev")
    monkeypatch.setattr(tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name])
    monkeypatch.setattr(tmux, "exec_attach", calls.append)
    execute_tmux_session_attach(TmuxSessionAttachRequest("dev"))
    assert calls == [["tmux", "attach", "dev"]]


def test_tmux_attach_reports_disappearance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux, "session_exists", lambda name: False)
    with pytest.raises(LaunchError, match="disappeared"):
        execute_tmux_session_attach(TmuxSessionAttachRequest("gone"))
