"""Textual Pilot tests for the Settings screen (dashboard.screens.settings).

Each toggle persists immediately; these tests isolate XDG_CONFIG_HOME so
nothing ever touches a real settings file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rich.cells import cell_len
from rich.color import ColorTriplet
from textual.app import App, ComposeResult
from textual.color import Color
from textual.screen import Screen
from textual.widgets import RadioButton, Static

from dashboard.models.settings import AppSettings, LayoutMode
from dashboard.screens.settings import SettingsScreen
from dashboard.services.settings_store import default_settings_path, load_settings, save_settings
from dashboard.widgets import CircularSelectionList, KeyboardActionList

_SIZE = (80, 24)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


class _HostApp(App[None]):
    def on_mount(self) -> None:
        self.push_screen(SettingsScreen())


class _StyledHostApp(App[None]):
    CSS_PATH = str(Path(__file__).parents[1] / "dashboard" / "app.tcss")

    def on_mount(self) -> None:
        self.push_screen(SettingsScreen())


def _run(coro):
    return asyncio.run(coro)


def test_defaults_shown_when_nothing_saved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[set[str], bool]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            appearance = screen.query_one("#appearance-settings", CircularSelectionList)
            return set(appearance.selected), appearance.has_focus

    selected, focused = _run(scenario())
    assert selected == {"artwork", "clock"}
    assert focused is True


def test_independent_settings_use_circular_indicators(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[str, str, bool, bool]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            appearance = app.screen.query_one("#appearance-settings", CircularSelectionList)
            await pilot.press("space")
            await pilot.pause()
            return (
                "".join(segment.text for segment in appearance.render_line(0)),
                "".join(segment.text for segment in appearance.render_line(1)),
                "artwork" in appearance.selected,
                "clock" in appearance.selected,
            )

    artwork, clock, artwork_value, clock_value = _run(scenario())
    assert "○" in artwork and "▐" not in artwork and "▌" not in artwork
    assert "●" in clock and "▐" not in clock and "▌" not in clock
    assert artwork_value is False
    assert clock_value is True


@pytest.mark.parametrize("size", [(80, 24), (120, 35)])
def test_settings_indicators_match_native_radio_geometry_and_styling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    """Custom multi-select rows share native radio glyph geometry without rails."""
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[list[tuple[str, object, object]], list[object]]:
        app = _StyledHostApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            appearance = screen.query_one("#appearance-settings", CircularSelectionList)
            coding = screen.query_one("#coding-agent-set")
            header = screen.query_one("#table-header-color-set")
            appearance.focus()
            await pilot.pause()
            borders = [appearance.styles.border]

            native_rows = [
                next(
                    radio
                    for radio in group.query(RadioButton)
                    if radio.value is selected
                )
                for group, selected in ((coding, True), (coding, False), (header, True))
            ]
            appearance_rows = [appearance.render_line(index) for index in range(3)]
            for group in (coding, header):
                group.focus()
                await pilot.pause()
                borders.append(group.styles.border)
                return (
                    [
                        (
                            list(row)[0].text,
                            list(row)[0].style,
                            list(row)[1].style,
                        )
                    for row in appearance_rows
                ],
                [radio.render() for radio in native_rows] + borders,
            )

    appearance_rows, native_and_borders = asyncio.run(scenario())
    native_contents = native_and_borders[:3]
    borders = native_and_borders[3:]
    assert [glyphs for glyphs, _, _ in appearance_rows] == ["●", "●", "○"]
    assert all(cell_len(glyphs) == 1 for glyphs, _, _ in appearance_rows)
    assert all("▐" not in str(content) and "▌" not in str(content) for content in native_contents)
    assert all("▐" not in glyphs and "▌" not in glyphs for glyphs, _, _ in appearance_rows)
    assert all(row[1].color != row[1].bgcolor for row in appearance_rows)
    purple = ColorTriplet(155, 140, 255)
    white = ColorTriplet(255, 255, 255)
    assert all(
        segment_color not in (white, ColorTriplet(0, 255, 255), ColorTriplet(138, 212, 161))
        for _, central, side in appearance_rows
        for segment_color in (central.color.triplet, side.color.triplet)
    )
    assert appearance_rows[0][1].color.triplet == purple
    assert appearance_rows[1][1].color.triplet == purple
    assert all(border.top[1] == Color.parse("#9b8cff") for border in borders)


def test_settings_choice_groups_share_focused_border(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[object, object, object]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            appearance = app.screen.query_one("#appearance-settings")
            coding = app.screen.query_one("#coding-agent-set")
            header = app.screen.query_one("#table-header-color-set")
            appearance.focus()
            await pilot.pause()
            appearance_border = appearance.styles.border
            coding.focus()
            await pilot.pause()
            coding_border = coding.styles.border
            header.focus()
            await pilot.pause()
            return appearance_border, coding_border, header.styles.border

    appearance_border, coding_border, header_border = _run(scenario())
    assert appearance_border == coding_border == header_border


@pytest.mark.parametrize("size", [(120, 35), (100, 30), (80, 24), (60, 18)])
def test_cli_header_group_border_and_keyboard_reachability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, size: tuple[int, int]
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> tuple[int, int, int, int, int, str | None, str | None, str | None]:
        app = _StyledHostApp()
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            screen = app.screen
            scroll = screen.query_one(".settings-scroll")
            group = screen.query_one("#table-header-color-set")
            group.focus()
            await pilot.pause()
            project_management = next(
                widget
                for widget in screen.query(Static)
                if str(widget.render()) == "Project Management"
            )
            group_bottom = group.region.bottom
            viewport_bottom = scroll.region.bottom
            project_management_y = project_management.region.y
            max_scroll_x = scroll.max_scroll_x

            # RadioSet navigation remains keyboard-only and reaches the eighth
            # option without changing the selected value until Space.
            for _ in range(7):
                await pilot.press("down")
            before_space = group.pressed_button.id if group.pressed_button else None
            await pilot.press("space")
            await pilot.pause()
            after_space = group.pressed_button.id if group.pressed_button else None
            await pilot.press("tab")
            await pilot.pause()
            return (
                group.region.height,
                group_bottom,
                viewport_bottom,
                project_management_y,
                max_scroll_x,
                before_space,
                after_space,
                app.focused.id if app.focused else None,
            )

    (
        height,
        group_bottom,
        viewport_bottom,
        project_management_y,
        max_scroll_x,
        before,
        after,
        focused_id,
    ) = asyncio.run(scenario())
    assert height >= 10  # eight rows plus top and bottom border rows
    assert group_bottom <= viewport_bottom
    assert project_management_y >= group_bottom
    assert max_scroll_x == 0
    assert before == "header-color-theme"
    assert after == "header-color-none"
    assert focused_id == "settings-actions"


def test_toggling_artwork_checkbox_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#appearance-settings", CircularSelectionList).toggle("artwork")
            await pilot.pause()

    _run(scenario())

    assert load_settings(default_settings_path()).artwork_enabled is False


def test_toggling_compact_checkbox_persists_layout_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#appearance-settings", CircularSelectionList).toggle("compact")
            await pilot.pause()

    _run(scenario())

    assert load_settings(default_settings_path()).layout_mode is LayoutMode.COMPACT


def test_toggling_clock_checkbox_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#appearance-settings", CircularSelectionList).toggle("clock")
            await pilot.pause()

    _run(scenario())

    assert load_settings(default_settings_path()).clock_visible is False


def test_existing_settings_are_loaded_on_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_settings(
        AppSettings(artwork_enabled=False, layout_mode=LayoutMode.COMPACT, clock_visible=False)
    )

    async def scenario() -> tuple[set[str], bool]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            appearance = screen.query_one("#appearance-settings", CircularSelectionList)
            return set(appearance.selected), appearance.has_focus

    selected, focused = _run(scenario())
    assert selected == {"compact"}
    assert focused is True


def test_malformed_settings_file_falls_back_to_defaults_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    settings_path = default_settings_path()
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json")

    async def scenario() -> bool:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return "artwork" in app.screen.query_one(
                "#appearance-settings", CircularSelectionList
            ).selected

    assert _run(scenario()) is True  # default, no traceback


def test_escape_returns_to_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    class _Host(Screen[None]):
        def compose(self) -> ComposeResult:
            return iter(())

    class _App(App[None]):
        def on_mount(self) -> None:
            self.push_screen(_Host())
            self.push_screen(SettingsScreen())

    async def scenario() -> str:
        app = _App()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "_Host"


def test_project_discovery_action_opens_project_discovery_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            actions = app.screen.query_one("#settings-actions", KeyboardActionList)
            actions.selected_index = 0
            actions.focus()
            await pilot.press("enter")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "ProjectDiscoveryScreen"
