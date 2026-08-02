"""Tests for tmux workspace construction (dashboard.services.tmux): command
building, session creation, and attach/switch decisions. Command
*construction* is tested with no subprocess involved at all; command
*execution* is tested against a fake runner so no real tmux server is ever
created or attached to.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services import pane_commands
from dashboard.services import tmux as tmux_module
from dashboard.services.tmux import (
    TmuxCommandError,
    attach_or_switch_argv,
    build_workspace_commands,
    create_workspace_session,
    generate_session_name,
    sanitize_session_name,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _workspace(project_path: Path, *windows: WindowSpec) -> WorkspaceSpec:
    return WorkspaceSpec(
        project_name="demo",
        project_path=project_path,
        session_name="demo",
        windows=windows or (WindowSpec(window_name="main", panes=(_pane(),)),),
    )


def _pane(kind: PaneKind = PaneKind.BLANK_TERMINAL, name: str | None = None) -> PaneSpec:
    return PaneSpec(kind=kind, display_name=name or kind.value)


def _plan(startup_command: str | None = None, pane_title: str | None = None):
    return pane_commands.PaneLaunchPlan(startup_command=startup_command, pane_title=pane_title)


# --- sanitize_session_name / generate_session_name -----------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My Project", "My-Project"),
        ("weird!!chars??", "weird-chars"),
        ("already-safe_123", "already-safe_123"),
        ("   ", "workspace"),
    ],
)
def test_sanitize_session_name(raw: str, expected: str) -> None:
    assert sanitize_session_name(raw) == expected


def test_generate_session_name_no_collision() -> None:
    assert generate_session_name("My Project", existing=set()) == "My-Project"


def test_generate_session_name_appends_suffix_on_collision() -> None:
    name = generate_session_name("My Project", existing={"My-Project", "My-Project-2"})
    assert name == "My-Project-3"


# --- build_workspace_commands: pane counts / layouts ----------------------------


def test_build_commands_one_pane_has_no_layout(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, WindowSpec(window_name="main", panes=(_pane(),)))
    commands = build_workspace_commands(workspace, {("main", 0): _plan()})

    assert commands[0] == [
        "tmux", "new-session", "-d", "-s", "demo", "-n", "main", "-c", str(tmp_path),
    ]
    assert not any(cmd[1] == "split-window" for cmd in commands)
    assert not any(cmd[1] == "select-layout" for cmd in commands)


def test_build_commands_two_panes_even_horizontal(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane()))
    )
    pane_plans = {("main", 0): _plan(), ("main", 1): _plan()}
    commands = build_workspace_commands(workspace, pane_plans)

    split_commands = [c for c in commands if c[1] == "split-window"]
    layout_commands = [c for c in commands if c[1] == "select-layout"]
    assert len(split_commands) == 1
    assert layout_commands == [["tmux", "select-layout", "-t", "demo:main", "even-horizontal"]]


def test_build_commands_three_panes_main_vertical(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane(), _pane()))
    )
    pane_plans = {("main", i): _plan() for i in range(3)}
    commands = build_workspace_commands(workspace, pane_plans)

    split_commands = [c for c in commands if c[1] == "split-window"]
    layout_commands = [c for c in commands if c[1] == "select-layout"]
    assert len(split_commands) == 2
    assert layout_commands == [["tmux", "select-layout", "-t", "demo:main", "main-vertical"]]


def test_build_commands_four_panes_tiled(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane(), _pane(), _pane()))
    )
    pane_plans = {("main", i): _plan() for i in range(4)}
    commands = build_workspace_commands(workspace, pane_plans)

    split_commands = [c for c in commands if c[1] == "split-window"]
    layout_commands = [c for c in commands if c[1] == "select-layout"]
    assert len(split_commands) == 3
    assert layout_commands == [["tmux", "select-layout", "-t", "demo:main", "tiled"]]


def test_build_commands_preserves_pane_order_for_titles_and_startup(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        WindowSpec(
            window_name="main",
            panes=(
                _pane(PaneKind.CODE_EDITOR, "Code Editor"),
                _pane(PaneKind.GIT, "Git"),
            ),
        ),
    )
    pane_plans = {
        ("main", 0): _plan(startup_command="nvim .", pane_title="editor"),
        ("main", 1): _plan(startup_command="lazygit", pane_title="git"),
    }
    commands = build_workspace_commands(workspace, pane_plans)

    title_commands = [c for c in commands if c[1] == "select-pane"]
    send_keys_commands = [c for c in commands if c[1] == "send-keys"]

    assert title_commands == [
        ["tmux", "select-pane", "-t", "demo:main.0", "-T", "editor"],
        ["tmux", "select-pane", "-t", "demo:main.1", "-T", "git"],
        ["tmux", "select-pane", "-t", "demo:main.0"],
    ]
    assert send_keys_commands == [
        ["tmux", "send-keys", "-t", "demo:main.0", "nvim .", "Enter"],
        ["tmux", "send-keys", "-t", "demo:main.1", "lazygit", "Enter"],
    ]


def test_build_commands_respects_pane_base_index(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane()))
    )
    pane_plans = {
        ("main", 0): _plan(pane_title="left"),
        ("main", 1): _plan(pane_title="right"),
    }
    commands = build_workspace_commands(workspace, pane_plans, pane_base_index=1)

    title_commands = [c for c in commands if c[1] == "select-pane" and "-T" in c]
    assert title_commands == [
        ["tmux", "select-pane", "-t", "demo:main.1", "-T", "left"],
        ["tmux", "select-pane", "-t", "demo:main.2", "-T", "right"],
    ]


def test_build_commands_multiple_windows(tmp_path: Path) -> None:
    workspace = _workspace(
        tmp_path,
        WindowSpec(window_name="main", panes=(_pane(),)),
        WindowSpec(window_name="tests", panes=(_pane(), _pane())),
    )
    pane_plans = {
        ("main", 0): _plan(),
        ("tests", 0): _plan(),
        ("tests", 1): _plan(),
    }
    commands = build_workspace_commands(workspace, pane_plans)

    new_window_commands = [c for c in commands if c[1] == "new-window"]
    assert new_window_commands == [
        ["tmux", "new-window", "-t", "demo", "-n", "tests", "-c", str(tmp_path)]
    ]
    # First window selected/focused before attach, not the second one.
    assert commands[-2] == ["tmux", "select-window", "-t", "demo:main"]
    assert commands[-1] == ["tmux", "select-pane", "-t", "demo:main.0"]


# --- create_workspace_session: execution + cleanup on failure -------------------


def test_create_workspace_session_refuses_existing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: True)
    workspace = _workspace(tmp_path)

    calls: list[Any] = []
    with pytest.raises(TmuxCommandError, match="already exists"):
        create_workspace_session(workspace, {("main", 0): _plan()}, runner=calls.append)

    assert calls == []  # never touched a pre-existing session


def test_create_workspace_session_runs_every_command_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(tmux_module, "get_pane_base_index", lambda: 0)
    workspace = _workspace(tmp_path)

    executed: list[list[str]] = []

    def fake_runner(argv: list[str]) -> _FakeCompletedProcess:
        executed.append(argv)
        return _FakeCompletedProcess(returncode=0)

    create_workspace_session(workspace, {("main", 0): _plan()}, runner=fake_runner)

    expected = build_workspace_commands(workspace, {("main", 0): _plan()}, pane_base_index=0)
    assert executed == expected


def test_create_workspace_session_cleans_up_only_the_session_it_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_exists_calls: list[bool] = [False, True]  # False before create, True after failure

    def fake_session_exists(name: str) -> bool:
        return session_exists_calls.pop(0)

    monkeypatch.setattr(tmux_module, "session_exists", fake_session_exists)
    monkeypatch.setattr(tmux_module, "get_pane_base_index", lambda: 0)
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane()))
    )

    executed: list[list[str]] = []

    def failing_runner(argv: list[str]) -> _FakeCompletedProcess:
        executed.append(argv)
        if argv[1] == "select-layout":
            return _FakeCompletedProcess(returncode=1, stderr="boom")
        return _FakeCompletedProcess(returncode=0)

    with pytest.raises(TmuxCommandError, match="boom"):
        create_workspace_session(
            workspace, {("main", 0): _plan(), ("main", 1): _plan()}, runner=failing_runner
        )

    assert executed[-1] == ["tmux", "kill-session", "-t", "demo"]


def test_create_workspace_session_does_not_kill_when_new_session_itself_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(tmux_module, "get_pane_base_index", lambda: 0)
    workspace = _workspace(tmp_path)

    executed: list[list[str]] = []

    def failing_runner(argv: list[str]) -> _FakeCompletedProcess:
        executed.append(argv)
        return _FakeCompletedProcess(returncode=1, stderr="no server")

    with pytest.raises(TmuxCommandError):
        create_workspace_session(workspace, {("main", 0): _plan()}, runner=failing_runner)

    assert len(executed) == 1  # only the failed new-session call; no kill-session issued


# --- attach_or_switch_argv -------------------------------------------------------


def test_attach_outside_tmux() -> None:
    assert attach_or_switch_argv("demo", inside_tmux=False) == [
        "tmux", "attach-session", "-t", "demo",
    ]


def test_switch_client_inside_tmux() -> None:
    assert attach_or_switch_argv("demo", inside_tmux=True) == [
        "tmux", "switch-client", "-t", "demo"
    ]
