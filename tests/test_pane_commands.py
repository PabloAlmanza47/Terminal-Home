"""Tests for pane launch-plan resolution (dashboard.services.pane_commands).

shutil.which is monkeypatched throughout so these never depend on which
tools actually happen to be installed on the machine running the tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec
from dashboard.services import pane_commands as pane_commands_module
from dashboard.services.pane_commands import plan_for_pane


def _which_only(*available: str):
    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in available else None

    return fake_which


# --- Code Editor ---------------------------------------------------------------


def test_code_editor_uses_nvim_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only("nvim"))
    pane = PaneSpec(kind=PaneKind.CODE_EDITOR, display_name="Code Editor")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command == "nvim ."
    assert plan.warning is None


def test_code_editor_falls_back_to_shell_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only())
    pane = PaneSpec(kind=PaneKind.CODE_EDITOR, display_name="Code Editor")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command is None
    assert plan.warning is not None
    assert "Neovim" in plan.warning


# --- Claude Code -----------------------------------------------------------------


def test_claude_code_uses_claude_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only("claude"))
    pane = PaneSpec(kind=PaneKind.CLAUDE_CODE, display_name="Claude Code")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command == "claude"
    assert plan.warning is None


def test_claude_code_falls_back_to_shell_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only())
    pane = PaneSpec(kind=PaneKind.CLAUDE_CODE, display_name="Claude Code")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command is None
    assert plan.warning is not None


# --- Git -------------------------------------------------------------------------


def test_git_pane_not_a_repo_falls_back_to_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only("lazygit"))
    pane = PaneSpec(kind=PaneKind.GIT, display_name="Git")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command is None
    assert plan.warning is not None
    assert "not a git repository" in plan.warning


def test_git_pane_uses_lazygit_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only("lazygit"))
    pane = PaneSpec(kind=PaneKind.GIT, display_name="Git")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command == "lazygit"
    assert plan.warning is None


def test_git_pane_falls_back_to_git_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only())
    pane = PaneSpec(kind=PaneKind.GIT, display_name="Git")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command == "git status"


# --- File Tree --------------------------------------------------------------------


def test_file_tree_uses_tree_when_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only("tree"))
    pane = PaneSpec(kind=PaneKind.FILE_TREE, display_name="File Tree")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command == "tree -C ."


def test_file_tree_falls_back_when_tree_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pane_commands_module.shutil, "which", _which_only())
    pane = PaneSpec(kind=PaneKind.FILE_TREE, display_name="File Tree")

    plan = plan_for_pane(pane, tmp_path)

    assert plan.startup_command is not None
    assert plan.startup_command != "tree -C ."
    assert plan.warning is None


# --- Test Terminal / Dev Server / Blank Terminal ----------------------------------


def test_test_terminal_is_a_titled_shell(tmp_path: Path) -> None:
    pane = PaneSpec(kind=PaneKind.TEST_TERMINAL, display_name="Test Terminal")
    plan = plan_for_pane(pane, tmp_path)
    assert plan.startup_command is None
    assert plan.pane_title == "tests"


def test_dev_server_is_a_titled_shell(tmp_path: Path) -> None:
    pane = PaneSpec(kind=PaneKind.DEV_SERVER, display_name="Development Server")
    plan = plan_for_pane(pane, tmp_path)
    assert plan.startup_command is None
    assert plan.pane_title == "server"


def test_blank_terminal_has_no_command_or_title(tmp_path: Path) -> None:
    pane = PaneSpec(kind=PaneKind.BLANK_TERMINAL, display_name="Blank Terminal")
    plan = plan_for_pane(pane, tmp_path)
    assert plan.startup_command is None
    assert plan.pane_title is None
    assert plan.warning is None


# --- Custom Command ----------------------------------------------------------------


def test_custom_command_uses_the_given_command_and_name(tmp_path: Path) -> None:
    pane = PaneSpec(
        kind=PaneKind.CUSTOM_COMMAND, display_name="Docs", custom_command="mkdocs serve"
    )
    plan = plan_for_pane(pane, tmp_path)
    assert plan.startup_command == "mkdocs serve"
    assert plan.pane_title == "Docs"
    assert plan.warning is None
