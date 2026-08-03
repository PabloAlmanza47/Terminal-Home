"""Textual Pilot tests for the Project Discovery screen
(dashboard.screens.project_discovery.ProjectDiscoveryScreen).

Each edit persists immediately via projects_config_store; these tests
isolate XDG_CONFIG_HOME so nothing ever touches a real configuration file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.screen import Screen
from textual.widgets import Input, OptionList, Static

from dashboard.models.projects_config import ProjectsConfig
from dashboard.screens.project_discovery import ProjectDiscoveryScreen
from dashboard.services.projects_config_store import load_projects_config, save_projects_config

_SIZE = (100, 100)


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))


class _HostApp(App[None]):
    def on_mount(self) -> None:
        self.push_screen(ProjectDiscoveryScreen())


def _run(coro):
    return asyncio.run(coro)


def _option_ids(option_list: OptionList) -> list[str | None]:
    return [option_list.get_option_at_index(i).id for i in range(option_list.option_count)]


# --- Viewing configured state ---------------------------------------------------


def test_shows_default_root_when_nothing_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    async def scenario() -> list[str | None]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            return _option_ids(app.screen.query_one("#roots-list", OptionList))

    ids = _run(scenario())
    assert ids == [str(ProjectsConfig().roots[0])]


def test_shows_configured_roots_and_excluded_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    save_projects_config(
        ProjectsConfig(roots=(root_a, root_b), excluded_names=frozenset({"dist", "build"}))
    )

    async def scenario() -> tuple[list[str | None], list[str | None]]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            roots = _option_ids(app.screen.query_one("#roots-list", OptionList))
            excluded = _option_ids(app.screen.query_one("#excluded-list", OptionList))
        return roots, excluded

    roots, excluded = _run(scenario())
    assert roots == [str(root_a), str(root_b)]
    assert excluded == ["build", "dist"]


# --- Root management ---------------------------------------------------------------


def test_add_root_persists_and_refreshes_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(roots=()))
    new_root = tmp_path / "work"

    async def scenario() -> list[str | None]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#root-input", Input).value = str(new_root)
            await pilot.click("#add-root-button")
            await pilot.pause()
            return _option_ids(app.screen.query_one("#roots-list", OptionList))

    ids = _run(scenario())
    assert ids == [str(new_root)]
    assert load_projects_config().roots == (new_root,)


def test_add_root_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    save_projects_config(ProjectsConfig(roots=()))

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#root-input", Input).value = "~/work"
            await pilot.click("#add-root-button")
            await pilot.pause()

    _run(scenario())
    assert load_projects_config().roots == (Path("~/work").expanduser(),)


def test_add_duplicate_root_shows_error_and_does_not_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "work"
    save_projects_config(ProjectsConfig(roots=(root,)))

    async def scenario() -> tuple[str, list[str | None]]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#root-input", Input).value = str(root)
            await pilot.click("#add-root-button")
            await pilot.pause()
            error = str(app.screen.query_one("#wizard-error", Static).render())
            ids = _option_ids(app.screen.query_one("#roots-list", OptionList))
        return error, ids

    error, ids = _run(scenario())
    assert "already" in error.lower()
    assert ids == [str(root)]


def test_add_blank_root_shows_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(roots=()))

    async def scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#root-input", Input).value = "   "
            await pilot.click("#add-root-button")
            await pilot.pause()
            return str(app.screen.query_one("#wizard-error", Static).render())

    assert _run(scenario()) != ""


def test_remove_selected_root_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    save_projects_config(ProjectsConfig(roots=(root_a, root_b)))

    async def scenario() -> list[str | None]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            option_list = app.screen.query_one("#roots-list", OptionList)
            option_list.highlighted = 0
            await pilot.click("#remove-root-button")
            await pilot.pause()
            return _option_ids(option_list)

    ids = _run(scenario())
    assert ids == [str(root_b)]
    assert load_projects_config().roots == (root_b,)


def test_remove_root_with_nothing_selected_shows_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(roots=()))

    async def scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#remove-root-button")
            await pilot.pause()
            return str(app.screen.query_one("#wizard-error", Static).render())

    assert _run(scenario()) != ""


# --- Max depth management -----------------------------------------------------------


def test_apply_depth_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(max_depth=1))

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#depth-input", Input).value = "3"
            await pilot.click("#apply-depth-button")
            await pilot.pause()

    _run(scenario())
    assert load_projects_config().max_depth == 3


def test_apply_invalid_depth_shows_error_and_does_not_change_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(max_depth=2))

    async def scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#depth-input", Input).value = "0"
            await pilot.click("#apply-depth-button")
            await pilot.pause()
            return str(app.screen.query_one("#wizard-error", Static).render())

    error = _run(scenario())
    assert error != ""
    assert load_projects_config().max_depth == 2


def test_apply_non_numeric_depth_shows_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(max_depth=2))

    async def scenario() -> str:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#depth-input", Input).value = "abc"
            await pilot.click("#apply-depth-button")
            await pilot.pause()
            return str(app.screen.query_one("#wizard-error", Static).render())

    error = _run(scenario())
    assert error != ""
    assert load_projects_config().max_depth == 2


# --- Excluded names -----------------------------------------------------------------


def test_add_and_remove_excluded_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(excluded_names=frozenset()))

    async def scenario() -> tuple[list[str | None], list[str | None]]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#excluded-input", Input).value = "dist"
            await pilot.click("#add-excluded-button")
            await pilot.pause()
            after_add = _option_ids(app.screen.query_one("#excluded-list", OptionList))

            app.screen.query_one("#excluded-list", OptionList).highlighted = 0
            await pilot.click("#remove-excluded-button")
            await pilot.pause()
            after_remove = _option_ids(app.screen.query_one("#excluded-list", OptionList))
        return after_add, after_remove

    after_add, after_remove = _run(scenario())
    assert after_add == ["dist"]
    assert after_remove != ["dist"]
    assert "dist" not in load_projects_config().excluded_names


# --- Manual projects -----------------------------------------------------------------


def test_add_and_remove_manual_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(manual_projects=()))
    manual_path = tmp_path / "elsewhere" / "side-project"

    async def scenario() -> tuple[list[str | None], list[str | None]]:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#manual-input", Input).value = str(manual_path)
            await pilot.click("#add-manual-button")
            await pilot.pause()
            after_add = _option_ids(app.screen.query_one("#manual-list", OptionList))

            app.screen.query_one("#manual-list", OptionList).highlighted = 0
            await pilot.click("#remove-manual-button")
            await pilot.pause()
            after_remove = _option_ids(app.screen.query_one("#manual-list", OptionList))
        return after_add, after_remove

    after_add, after_remove = _run(scenario())
    assert after_add == [str(manual_path)]
    assert str(manual_path) not in after_remove  # placeholder row only, entry is gone
    assert load_projects_config().manual_projects == ()


def test_add_manual_project_does_not_create_the_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    save_projects_config(ProjectsConfig(manual_projects=()))
    manual_path = tmp_path / "not-created-yet"

    async def scenario() -> None:
        app = _HostApp()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.screen.query_one("#manual-input", Input).value = str(manual_path)
            await pilot.click("#add-manual-button")
            await pilot.pause()

    _run(scenario())
    assert load_projects_config().manual_projects == (manual_path,)
    assert not manual_path.exists()


# --- Navigation -----------------------------------------------------------------------


def test_escape_returns_to_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)

    class _Host(Screen[None]):
        def compose(self) -> ComposeResult:
            return iter(())

    class _App(App[None]):
        def on_mount(self) -> None:
            self.push_screen(_Host())
            self.push_screen(ProjectDiscoveryScreen())

    async def scenario() -> str:
        app = _App()
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            return type(app.screen).__name__

    assert _run(scenario()) == "_Host"
