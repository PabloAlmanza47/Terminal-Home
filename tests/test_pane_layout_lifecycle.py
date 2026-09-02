"""Focused tests for automatic remembered-layout checkpoints."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dashboard.models import (
    LaunchAction,
    LaunchRequest,
    LocalProjectLocation,
    PaneKind,
    PaneSpec,
    SshProjectLocation,
    WindowSpec,
    WorkspaceSpec,
)
from dashboard.services import workspace_launcher as launcher
from dashboard.services.pane_layout_store import PaneLayout, load_pane_layouts_for_location
from dashboard.services.ssh import SshInteractiveResult


class _Runner:
    def __init__(self, outputs: list[str], exists: list[bool] | None = None) -> None:
        self.outputs = list(outputs)
        self.exists = list(exists or [True, True])
        self.commands: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(argv)
        if argv[1] == "has-session":
            return subprocess.CompletedProcess(argv, 0 if self.exists.pop(0) else 1)
        if argv[1] == "list-windows":
            return subprocess.CompletedProcess(argv, 0, self.outputs.pop(0), "")
        return subprocess.CompletedProcess(argv, 0)


def _workspace(tmp_path: Path, *, remote: bool = False) -> WorkspaceSpec:
    location = (
        SshProjectLocation("c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", "/srv/demo")
        if remote
        else LocalProjectLocation(tmp_path)
    )
    return WorkspaceSpec(
        project_name="demo",
        project_location=location,
        session_name="demo",
        windows=(
            WindowSpec(
                "main",
                (
                    PaneSpec(PaneKind.BLANK_TERMINAL, "one"),
                    PaneSpec(PaneKind.BLANK_TERMINAL, "two"),
                ),
            ),
        ),
    )


def _request(workspace: WorkspaceSpec) -> LaunchRequest:
    return LaunchRequest(workspace=workspace, init_git=False, action=LaunchAction.ATTACH)


def test_local_attach_checkpoints_before_and_after_detach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    workspace = _workspace(tmp_path)
    runner = _Runner(["main\t2\tbefore\n", "main\t2\tafter\n"])
    resolution = launcher.tmux.TmuxRunnerResolution("resolved", runner)
    monkeypatch.setattr(launcher.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(
        launcher.tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name]
    )
    monkeypatch.setattr(
        launcher.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )

    launcher.execute_launch_request(_request(workspace))

    assert load_pane_layouts_for_location(workspace.project_location)["main"] == PaneLayout(
        "main", 2, "after"
    )
    assert [command[1] for command in runner.commands] == [
        "has-session", "list-windows", "has-session", "list-windows"
    ]


def test_local_post_detach_disappearance_keeps_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    workspace = _workspace(tmp_path)
    runner = _Runner(["main\t2\tbefore\n"], exists=[True, False])
    resolution = launcher.tmux.TmuxRunnerResolution("resolved", runner)
    monkeypatch.setattr(launcher.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(
        launcher.tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name]
    )
    monkeypatch.setattr(
        launcher.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )

    launcher.execute_launch_request(_request(workspace))

    assert (
        load_pane_layouts_for_location(workspace.project_location)["main"].tmux_layout
        == "before"
    )


def test_switch_client_only_checkpoints_before_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    captured: list[str] = []
    monkeypatch.setattr(
        launcher,
        "remember_live_workspace_layout",
        lambda ws, runner: captured.append("capture"),
    )
    monkeypatch.setattr(
        launcher.tmux,
        "resolve_tmux_runner",
        lambda workspace: launcher.tmux.TmuxRunnerResolution("resolved", _Runner([])),
    )
    monkeypatch.setattr(
        launcher.tmux,
        "attach_or_switch_argv",
        lambda name: ["tmux", "switch-client", "-t", name],
    )
    switched: list[list[str]] = []
    monkeypatch.setattr(launcher.tmux, "exec_attach", switched.append)

    launcher.execute_launch_request(_request(workspace))

    assert captured == ["capture"]
    assert switched == [["tmux", "switch-client", "-t", "demo"]]


def test_capture_or_save_failure_does_not_block_local_attach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path)
    runner = _Runner([])
    resolution = launcher.tmux.TmuxRunnerResolution("resolved", runner)
    monkeypatch.setattr(launcher.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(
        launcher.tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name]
    )
    monkeypatch.setattr(
        launcher.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )
    monkeypatch.setattr(
        launcher.tmux,
        "capture_tmux_window_layouts",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("capture failed")),
    )

    launcher.execute_launch_request(_request(workspace))


def test_unmanaged_attach_does_not_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher.tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda name: True)
    monkeypatch.setattr(
        launcher.tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name]
    )
    monkeypatch.setattr(launcher.tmux, "exec_attach", lambda argv: None)
    monkeypatch.setattr(
        launcher,
        "remember_live_workspace_layout",
        lambda *args: pytest.fail("unmanaged attach captured a layout"),
    )

    launcher.execute_launch_request(
        LaunchRequest(
            workspace=None, init_git=False, action=LaunchAction.ATTACH, session_name="orphan"
        )
    )


def test_remote_attach_checkpoints_before_and_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(Path("/tmp/remote-project"), remote=True)
    runner = launcher.tmux.SshTmuxCommandRunner("deploy@example.test")
    resolution = launcher.tmux.TmuxRunnerResolution("resolved", runner)
    monkeypatch.setattr(launcher.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    captured: list[object] = []
    monkeypatch.setattr(
        launcher,
        "remember_live_workspace_layout",
        lambda ws, actual: captured.append(actual),
    )
    monkeypatch.setattr(
        launcher,
        "run_interactive_ssh",
        lambda *args, **kwargs: SshInteractiveResult("success", 0),
    )

    launcher.execute_launch_request(_request(workspace))

    assert captured == [runner, runner]
