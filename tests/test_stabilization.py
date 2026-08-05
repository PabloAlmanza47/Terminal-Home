from __future__ import annotations

import asyncio
import io
import re
from pathlib import Path

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets.option_list import Option

import dashboard.cli as cli_module
import dashboard.screens.tmux_sessions as tmux_screen_module
from dashboard.models.project_location import SshProjectLocation
from dashboard.models.settings import AppSettings, TableHeaderColor
from dashboard.models.ssh import RemoteProjectRegistration
from dashboard.models.workspace import TmuxSessionAttachRequest
from dashboard.services import tmux
from dashboard.services.cli_colors import style_table_header
from dashboard.services.project_rows import format_project_row
from dashboard.services.project_selection import RegisteredRemoteProject
from dashboard.services.projects import Project, ProjectStatus
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


def test_dynamic_option_markers_survive_full_mounted_lifecycle() -> None:
    class Host(App[None]):
        def compose(self) -> ComposeResult:
            yield KeyboardOptionList(id="rows", reset_on_blur=True)

        def on_mount(self) -> None:
            self.query_one("#rows", KeyboardOptionList).focus()

    async def scenario() -> list[tuple[str, int, str | None]]:
        app = Host()
        async with app.run_test(size=(60, 12)) as pilot:
            rows = app.query_one("#rows", KeyboardOptionList)
            for name in ("alpha", "  intentional", "gamma"):
                rows.add_option(Option(Text(name, style="bold red"), id=name))
                await pilot.pause()
            for _ in range(3):
                for index in range(rows.option_count):
                    rows.highlighted = index
                    await pilot.pause()
                for index in reversed(range(rows.option_count)):
                    rows.highlighted = index
                    await pilot.pause()
            app.set_focus(None)
            await pilot.pause()
            rows.focus()
            await pilot.pause()
            rows.clear_options()
            rows.add_option(Option(Text("  intentional", style="bold red"), id="reset"))
            rows.add_option(Option(Text("final", style="bold red"), id="final"))
            await pilot.pause()
            rows._update_prompt_markers()
            await pilot.pause()
            return [
                (option.prompt.plain, len(option.prompt.plain), option.id)
                for option in rows.options
            ]

    final = asyncio.run(scenario())
    assert [item[0][:2] for item in final] == ["› ", "  "]
    assert [item[1] for item in final] == [len("›   intentional"), len("  final")]
    assert [item[2] for item in final] == ["reset", "final"]


def test_project_row_wide_layout_aligns_status_and_branch() -> None:
    first = format_project_row("one", "Running", "dev", 80)
    second = format_project_row("two", "Not Configured", None, 80)
    assert first.index("[") == second.index("[")
    assert first.rfind("dev") > first.index("]")


@pytest.mark.parametrize("status", ["Running", "Saved Workspace", "Not Configured"])
def test_project_row_status_tokens_are_complete_and_unpadded(status: str) -> None:
    row = format_project_row("project", status, "main", 56)
    assert f"[{status}]" in row
    assert not re.search(r" +\]", row)
    assert row.count("[") == row.count("]") == 1
    assert len(row) == 56


def test_cli_colors_complete_local_and_remote_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    class Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    local_status = ProjectStatus(
        project=Project("demo", tmp_path),
        canonical_path=tmp_path,
        project_dir_exists=True,
        is_git_repo=False,
        git_branch=None,
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="demo",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )
    registration = RemoteProjectRegistration(
        "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
        "d84aeefb-7c29-4c63-b39c-766d559df977",
        "api",
        "/srv/api",
    )
    remote = RegisteredRemoteProject(
        "api", SshProjectLocation(registration.host_id, registration.remote_path), registration
    )
    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: AppSettings(table_header_color=TableHeaderColor.BLUE),
    )
    local_stream = Tty()
    remote_stream = Tty()
    monkeypatch.setattr(cli_module.sys, "stdout", local_stream)
    cli_module._print_project_table([local_status])
    monkeypatch.setattr(cli_module.sys, "stdout", remote_stream)
    monkeypatch.setattr(cli_module, "load_all_ssh_hosts", lambda: [])
    cli_module._print_remote_project_table([remote])

    local_output = local_stream.getvalue()
    remote_output = remote_stream.getvalue()
    assert "\033[34mNAME" in local_output
    assert local_output.splitlines()[0].endswith("PATH\033[0m")
    assert "\033[34mNAME" in remote_output
    assert remote_output.splitlines()[0].endswith("STATUS\033[0m")
    assert "\033[34m" not in local_output.splitlines()[1]
    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: AppSettings(table_header_color=TableHeaderColor.NONE),
    )
    plain_stream = Tty()
    monkeypatch.setattr(cli_module.sys, "stdout", plain_stream)
    cli_module._print_project_table([local_status])
    assert re.sub(r"\033\[[0-9;]*m", "", local_output) == plain_stream.getvalue()


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
