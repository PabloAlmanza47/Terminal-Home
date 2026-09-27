from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from dashboard.models import (
    LocalProjectLocation,
    PaneKind,
    PaneSpec,
    RunProjectCommandRequest,
    WindowSpec,
    WorkspaceSpec,
)
from dashboard.services import project_command_launcher as launcher
from dashboard.services import tmux


def _workspace(path: Path) -> WorkspaceSpec:
    return WorkspaceSpec(
        project_name="demo",
        project_location=LocalProjectLocation(path),
        session_name="demo",
        windows=(
            WindowSpec(
                window_name="main",
                panes=(
                    PaneSpec(PaneKind.CODING_AGENT, "Claude Code"),
                    PaneSpec(PaneKind.GIT, "Git"),
                    PaneSpec(PaneKind.DEV_SERVER, "Development Server"),
                    PaneSpec(PaneKind.CODE_EDITOR, "Code Editor"),
                ),
            ),
            WindowSpec(
                window_name="tree",
                panes=(PaneSpec(PaneKind.FILE_TREE, "File Tree"),),
            ),
        ),
    )


def _request(path: Path, category: str, argv: tuple[str, ...]) -> RunProjectCommandRequest:
    return RunProjectCommandRequest(_workspace(path), category, category, category, argv)


def _runtime(
    title: str, command: str, *, pane_id: str = "%2", window_id: str = "@1", dead: bool = False
) -> tmux.TmuxPaneRuntime:
    return tmux.TmuxPaneRuntime("demo", "main", title, command, dead, window_id, pane_id)


def test_dev_uses_existing_development_server_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, tuple[str, ...]]] = []
    selected: list[tuple[str, str]] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        launcher.tmux,
        "list_tmux_panes",
        lambda **kwargs: [_runtime("server", "bash", pane_id="%server")],
    )
    monkeypatch.setattr(
        launcher.tmux,
        "send_argv_to_pane",
        lambda pane, argv, **kwargs: sent.append((pane, argv)),
    )
    monkeypatch.setattr(
        launcher.tmux,
        "select_window_and_pane",
        lambda window, pane, **kwargs: selected.append((window, pane)),
    )
    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)

    launcher.execute_project_command_request(
        _request(tmp_path, "dev", ("npm", "run", "dev")), runner=lambda argv: _ok(argv)
    )

    assert sent == [("%server", ("npm", "run", "dev"))]
    assert selected == [("@1", "%server")]


def test_task_command_never_overwrites_development_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, tuple[str, ...]]] = []
    created: list[str] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        launcher.tmux,
        "list_tmux_panes",
        lambda **kwargs: [_runtime("server", "node", pane_id="%server")],
    )
    monkeypatch.setattr(
        launcher.tmux,
        "create_task_window",
        lambda *args, **kwargs: (created.append(args[1]) or ("@task", "%task")),
    )
    monkeypatch.setattr(launcher.tmux, "set_pane_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        launcher.tmux,
        "send_argv_to_pane",
        lambda pane, argv, **kwargs: sent.append((pane, argv)),
    )
    monkeypatch.setattr(launcher.tmux, "select_window_and_pane", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)

    launcher.execute_project_command_request(
        _request(tmp_path, "build", ("npm", "run", "build")), runner=lambda argv: _ok(argv)
    )

    assert created == ["th-build"]
    assert sent == [("%task", ("npm", "run", "build"))]


def test_running_target_is_focused_without_starting_duplicate_dev_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, tuple[str, ...]]] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        launcher.tmux,
        "list_tmux_panes",
        lambda **kwargs: [_runtime("server", "node", pane_id="%server")],
    )
    monkeypatch.setattr(
        launcher.tmux,
        "send_argv_to_pane",
        lambda pane, argv, **kwargs: sent.append((pane, argv)),
    )
    monkeypatch.setattr(launcher.tmux, "select_window_and_pane", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)

    launcher.execute_project_command_request(
        _request(tmp_path, "dev", ("npm", "run", "dev")), runner=lambda argv: _ok(argv)
    )

    assert sent == []


def test_completed_task_pane_is_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, tuple[str, ...]]] = []
    created: list[str] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        launcher.tmux,
        "list_tmux_panes",
        lambda **kwargs: [
            tmux.TmuxPaneRuntime(
                "demo", "th-build", "terminal-home:build", "bash", False, "@task", "%task"
            )
        ],
    )
    monkeypatch.setattr(
        launcher.tmux,
        "create_task_window",
        lambda *args, **kwargs: (created.append(args[1]) or ("@new", "%new")),
    )
    monkeypatch.setattr(
        launcher.tmux,
        "send_argv_to_pane",
        lambda pane, argv, **kwargs: sent.append((pane, argv)),
    )
    monkeypatch.setattr(launcher.tmux, "select_window_and_pane", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)

    launcher.execute_project_command_request(
        _request(tmp_path, "build", ("npm", "run", "build")), runner=lambda argv: _ok(argv)
    )

    assert created == []
    assert sent == [("%task", ("npm", "run", "build"))]


def test_stopped_workspace_is_created_before_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_workspace: list[WorkspaceSpec] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        launcher,
        "create_workspace_session_without_attach",
        lambda workspace, **kwargs: created_workspace.append(workspace),
    )
    monkeypatch.setattr(launcher.tmux, "list_tmux_panes", lambda **kwargs: [])
    monkeypatch.setattr(
        launcher.tmux,
        "create_task_window",
        lambda *args, **kwargs: ("@task", "%task"),
    )
    monkeypatch.setattr(launcher.tmux, "set_pane_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher.tmux, "send_argv_to_pane", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher.tmux, "select_window_and_pane", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)

    launcher.execute_project_command_request(
        _request(tmp_path, "test", ("pytest",)), runner=lambda argv: _ok(argv)
    )

    assert [workspace.session_name for workspace in created_workspace] == ["demo"]


def test_send_argv_quotes_arguments_with_spaces() -> None:
    calls: list[list[str]] = []

    tmux.send_argv_to_pane(
        "%1",
        ("python", "-c", "print('hello world')"),
        runner=lambda argv: calls.append(argv) or _ok(argv),
    )

    assert calls == [["tmux", "send-keys", "-t", "%1", shlex.join((
        "python", "-c", "print('hello world')"
    )), "Enter"]]


def test_missing_directory_fails_before_tmux(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(launcher.ProjectCommandExecutionError, match="no longer exists"):
        launcher.execute_project_command_request(
            _request(missing, "build", ("npm", "run", "build")), runner=lambda argv: _ok(argv)
        )


def test_tmux_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(launcher.tmux, "list_tmux_panes", lambda **kwargs: [])
    monkeypatch.setattr(
        launcher.tmux,
        "create_task_window",
        lambda *args, **kwargs: (_ for _ in ()).throw(tmux.TmuxCommandError("create failed")),
    )

    with pytest.raises(launcher.ProjectCommandExecutionError, match="create failed"):
        launcher.execute_project_command_request(
            _request(tmp_path, "lint", ("npm", "run", "lint")), runner=lambda argv: _ok(argv)
        )


def test_tmux_unavailable_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.tmux, "is_tmux_installed", lambda: False)

    with pytest.raises(launcher.ProjectCommandExecutionError, match="tmux is not installed"):
        launcher.execute_project_command_request(
            _request(tmp_path, "test", ("pytest",))
        )


def _ok(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, "", "")
