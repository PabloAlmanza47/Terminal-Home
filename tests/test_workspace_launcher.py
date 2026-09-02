"""Tests for the non-Textual orchestration layer
(dashboard.services.workspace_launcher). tmux is fully mocked -- no real
tmux session is ever created, and exec_attach is monkeypatched so nothing
ever replaces the test process.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from dashboard.models import (
    LaunchAction,
    LaunchRequest,
    PaneKind,
    PaneSpec,
    SshProjectLocation,
    WindowSpec,
    WorkspaceSpec,
)
from dashboard.services import workspace_launcher as launcher_module
from dashboard.services.pane_commands import PaneLaunchPlan
from dashboard.services.project_commands import (
    CommandSource,
    DetectedCommand,
    DetectedProjectCommands,
)
from dashboard.services.ssh import SshInteractiveResult
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request


class _FakeRemoteRunner(launcher_module.tmux.SshTmuxCommandRunner):
    def __init__(self) -> None:
        self.destination = "deploy@example.test"
        self.calls: list[list[str]] = []
        self.sessions: set[str] = set()

    def __call__(self, argv: list[str]):
        self.calls.append(argv)
        command = argv[1]
        if command == "has-session":
            target = argv[argv.index("-t") + 1]
            return _FakeCompletedProcess(returncode=0 if target in self.sessions else 1)
        if command == "new-session":
            self.sessions.add(argv[argv.index("-s") + 1])
            return _FakeCompletedProcess(stdout="@remote-window %remote-pane")
        if command == "new-window":
            return _FakeCompletedProcess(stdout="@remote-window-2 %remote-pane-2")
        if command == "split-window":
            return _FakeCompletedProcess(stdout="%remote-pane-2")
        return _FakeCompletedProcess()


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _request(
    tmp_path: Path, *panes: PaneSpec, action: LaunchAction = LaunchAction.CREATE
) -> LaunchRequest:
    workspace = WorkspaceSpec.for_local_project(
        project_name="demo",
        project_path=tmp_path,
        session_name="demo",
        windows=(WindowSpec(window_name="main", panes=panes or (_pane(),)),),
    )
    return LaunchRequest(workspace=workspace, init_git=True, action=action)


def _remote_request(action: LaunchAction = LaunchAction.CREATE) -> LaunchRequest:
    workspace = WorkspaceSpec(
        project_name="remote-demo",
        project_location=SshProjectLocation(
            host_id="c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            remote_path="/srv/Project With Spaces/$HOME",
        ),
        session_name="remote-demo",
        windows=(WindowSpec(window_name="main", panes=(_pane(),)),),
    )
    return LaunchRequest(workspace=workspace, init_git=False, action=action)


def _pane(kind: PaneKind = PaneKind.BLANK_TERMINAL, name: str | None = None) -> PaneSpec:
    return PaneSpec(kind=kind, display_name=name or kind.value)


def _assume_tmux_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher_module.tmux, "is_tmux_installed", lambda: True)


def test_refuses_to_touch_an_existing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: True)
    created = []
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda *a, **k: created.append(1)
    )

    with pytest.raises(LaunchError, match="already exists"):
        execute_launch_request(_request(tmp_path))

    assert created == []


def test_creates_session_and_attaches_when_outside_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    build_calls = []
    monkeypatch.setattr(
        launcher_module.tmux,
        "create_workspace_session",
        lambda workspace, pane_plans: build_calls.append((workspace, pane_plans)),
    )
    monkeypatch.setattr(
        launcher_module.tmux,
        "attach_or_switch_argv",
        lambda name: ["tmux", "attach-session", "-t", name],
    )
    exec_calls = []
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: exec_calls.append(argv) or subprocess.CompletedProcess(argv, 0),
    )

    execute_launch_request(_request(tmp_path))

    assert len(build_calls) == 1
    assert exec_calls == [["tmux", "attach-session", "-t", "demo"]]


@pytest.mark.parametrize("action", [LaunchAction.CREATE, LaunchAction.ATTACH])
def test_remote_create_and_recreate_use_one_runner_and_remote_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    action: LaunchAction,
) -> None:
    request = _remote_request(action)
    fake_runner = _FakeRemoteRunner()
    resolution = launcher_module.tmux.TmuxRunnerResolution(
        status="resolved", runner=fake_runner
    )
    monkeypatch.setattr(launcher_module.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(
        launcher_module.tmux,
        "exec_attach", lambda argv: pytest.fail("remote launch used local attach"),
    )
    monkeypatch.setattr(
        launcher_module.tmux,
        "is_tmux_installed",
        lambda: pytest.fail("remote launch must not require local tmux"),
    )
    interactive_calls: list[tuple[str, str, bool]] = []

    def fake_interactive(
        destination: str, command: str, *, request_tty: bool
    ) -> SshInteractiveResult:
        interactive_calls.append((destination, command, request_tty))
        return SshInteractiveResult(status="success", returncode=0)

    monkeypatch.setattr(launcher_module, "run_interactive_ssh", fake_interactive)

    execute_launch_request(request)

    expected_commands = [
        "has-session",
        "has-session",
        "new-session",
        "select-window",
        "select-pane",
        "list-windows",
        "has-session",
        "list-windows",
    ]
    assert [argv[1] for argv in fake_runner.calls] == expected_commands
    new_session = next(argv for argv in fake_runner.calls if argv[1] == "new-session")
    assert new_session[new_session.index("-c") + 1] == "/srv/Project With Spaces/$HOME"
    assert isinstance(request.workspace.project_location.remote_path, str)
    assert interactive_calls == [
        ("deploy@example.test", "tmux attach-session -t remote-demo", True)
    ]


def test_running_remote_session_attaches_without_recreation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _remote_request(LaunchAction.ATTACH)
    fake_runner = _FakeRemoteRunner()
    fake_runner.sessions.add("remote-demo")
    resolution = launcher_module.tmux.TmuxRunnerResolution(
        status="resolved", runner=fake_runner
    )
    monkeypatch.setattr(launcher_module.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    created: list[object] = []
    monkeypatch.setattr(
        launcher_module.tmux,
        "create_workspace_session",
        lambda *args, **kwargs: created.append(args),
    )
    interactive_calls: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        launcher_module,
        "run_interactive_ssh",
        lambda destination, command, *, request_tty: interactive_calls.append(
            (destination, command, request_tty)
        )
        or SshInteractiveResult(status="success", returncode=0),
    )

    execute_launch_request(request)

    assert created == []
    assert interactive_calls == [
        ("deploy@example.test", "tmux attach-session -t remote-demo", True)
    ]


def test_failed_remote_creation_does_not_attach(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _remote_request()
    fake_runner = _FakeRemoteRunner()
    resolution = launcher_module.tmux.TmuxRunnerResolution(
        status="resolved", runner=fake_runner
    )
    monkeypatch.setattr(launcher_module.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(
        launcher_module.tmux,
        "create_workspace_session",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            launcher_module.tmux.TmuxCommandError("remote create failed")
        ),
    )
    monkeypatch.setattr(
        launcher_module,
        "run_interactive_ssh",
        lambda *args, **kwargs: pytest.fail("failed creation must not attach"),
    )

    with pytest.raises(launcher_module.tmux.TmuxCommandError, match="remote create failed"):
        execute_launch_request(request)


@pytest.mark.parametrize(
    ("status", "returncode"),
    [
        ("command-failure", 23),
        ("connection-failure", 255),
        ("authentication-failure", 255),
        ("missing-ssh", None),
    ],
)
def test_remote_interactive_failure_is_reported(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    returncode: int | None,
) -> None:
    request = _remote_request(LaunchAction.ATTACH)
    fake_runner = _FakeRemoteRunner()
    fake_runner.sessions.add("remote-demo")
    resolution = launcher_module.tmux.TmuxRunnerResolution(
        status="resolved", runner=fake_runner
    )
    monkeypatch.setattr(launcher_module.tmux, "resolve_tmux_runner", lambda workspace: resolution)
    monkeypatch.setattr(
        launcher_module,
        "run_interactive_ssh",
        lambda *args, **kwargs: SshInteractiveResult(
            status=status, returncode=returncode, error=f"{status} detail"
        ),
    )

    with pytest.raises(LaunchError, match=status):
        execute_launch_request(request)


def test_remote_missing_host_fails_before_tmux_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _remote_request()
    calls: list[list[str]] = []
    error = launcher_module.tmux.TmuxRunnerResolutionError(
        status="missing-host",
        host_id=request.workspace.project_location.host_id,
        message="SSH host is not registered.",
    )
    monkeypatch.setattr(
        launcher_module.tmux,
        "resolve_tmux_runner",
        lambda workspace: launcher_module.tmux.TmuxRunnerResolution(
            status="missing-host", error=error
        ),
    )
    monkeypatch.setattr(
        launcher_module.tmux,
        "session_exists",
        lambda *args, **kwargs: calls.append(list(args)) or False,
    )
    monkeypatch.setattr(
        launcher_module.tmux,
        "create_workspace_session",
        lambda *args, **kwargs: calls.append(list(args)),
    )

    with pytest.raises(LaunchError, match="not registered"):
        execute_launch_request(request)

    assert calls == []


def test_reports_pane_warnings_before_attaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda workspace, pane_plans: None
    )
    monkeypatch.setattr(launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux"])
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )
    monkeypatch.setattr(
        launcher_module.pane_commands,
        "plan_for_pane",
        lambda pane, path, detected=None: PaneLaunchPlan(
            startup_command=None, pane_title=None, warning="Neovim was not found"
        ),
    )

    out = io.StringIO()
    execute_launch_request(_request(tmp_path), out=out)

    assert "Neovim was not found" in out.getvalue()


def test_raises_friendly_error_when_tmux_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher_module.tmux, "is_tmux_installed", lambda: False)

    with pytest.raises(LaunchError, match="tmux is not installed"):
        execute_launch_request(_request(tmp_path))


def test_create_refuses_when_project_directory_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    created = []
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda *a, **k: created.append(1)
    )

    missing = tmp_path / "gone"
    with pytest.raises(LaunchError, match="no longer exists"):
        execute_launch_request(_request(missing))

    assert created == []


# --- LaunchAction.ATTACH: Resume Session / Recreate Workspace ----------------


def test_attach_attaches_directly_when_session_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: True)
    created = []
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda *a, **k: created.append(1)
    )
    monkeypatch.setattr(
        launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name]
    )
    exec_calls = []
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: exec_calls.append(argv) or subprocess.CompletedProcess(argv, 0),
    )

    execute_launch_request(_request(tmp_path, action=LaunchAction.ATTACH))

    assert created == []  # never recreates a session that's already running
    assert exec_calls == [["tmux", "attach", "demo"]]


def test_attach_recreates_when_session_has_disappeared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    build_calls = []
    monkeypatch.setattr(
        launcher_module.tmux,
        "create_workspace_session",
        lambda workspace, pane_plans: build_calls.append(workspace),
    )
    monkeypatch.setattr(launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux"])
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )

    execute_launch_request(_request(tmp_path, action=LaunchAction.ATTACH))

    assert len(build_calls) == 1  # safely recreated from the saved workspace


def test_attach_without_workspace_fails_cleanly_when_session_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    created = []
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda *a, **k: created.append(1)
    )

    request = LaunchRequest(
        workspace=None, init_git=False, action=LaunchAction.ATTACH, session_name="orphan"
    )
    with pytest.raises(LaunchError, match="no longer running"):
        execute_launch_request(request)

    assert created == []


def test_attach_without_workspace_attaches_when_session_is_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: True)
    monkeypatch.setattr(
        launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux", "attach", name]
    )
    exec_calls = []
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: exec_calls.append(argv) or subprocess.CompletedProcess(argv, 0),
    )

    request = LaunchRequest(
        workspace=None, init_git=False, action=LaunchAction.ATTACH, session_name="orphan"
    )
    execute_launch_request(request)

    assert exec_calls == [["tmux", "attach", "orphan"]]


def test_build_pane_plans_keys_by_window_and_pane_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceSpec.for_local_project(
        project_name="demo",
        project_path=tmp_path,
        session_name="demo",
        windows=(
            WindowSpec(
                window_name="main",
                panes=(_pane(PaneKind.CODE_EDITOR), _pane(PaneKind.GIT)),
            ),
        ),
    )

    pane_plans = launcher_module.build_pane_plans(workspace)

    assert set(pane_plans.keys()) == {("main", 0), ("main", 1)}


def test_project_commands_are_detected_once_per_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    detected = DetectedProjectCommands(
        development=DetectedCommand("npm run dev", CommandSource.NODE_DEV),
        test=DetectedCommand("npm test", CommandSource.NODE_TEST),
    )
    monkeypatch.setattr(
        launcher_module,
        "detect_project_commands",
        lambda path: calls.append(path) or detected,
    )
    request = _request(
        tmp_path,
        _pane(PaneKind.DEV_SERVER),
        _pane(PaneKind.TEST_TERMINAL),
    )
    assert request.workspace is not None

    plans = launcher_module.build_pane_plans(request.workspace)

    assert calls == [tmp_path]
    assert plans[("main", 0)].startup_command == "npm run dev"
    assert plans[("main", 1)].startup_command == "npm test"


def test_command_detection_is_repeated_for_each_pane_planning_pass(tmp_path: Path) -> None:
    request = _request(tmp_path, _pane(PaneKind.DEV_SERVER))
    assert request.workspace is not None
    (tmp_path / "package.json").write_text('{"scripts": {"dev": "vite"}}')
    first = launcher_module.build_pane_plans(request.workspace)
    (tmp_path / "package.json").write_text('{"scripts": {"start": "node app"}}')
    second = launcher_module.build_pane_plans(request.workspace)
    assert first[("main", 0)].startup_command == "npm run dev"
    assert second[("main", 0)].startup_command == "npm start"


def test_running_attach_never_detects_project_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: True)
    monkeypatch.setattr(launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux"])
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )
    monkeypatch.setattr(
        launcher_module,
        "detect_project_commands",
        lambda path: pytest.fail("running attach performed command detection"),
    )

    execute_launch_request(
        _request(tmp_path, _pane(PaneKind.DEV_SERVER), action=LaunchAction.ATTACH)
    )


def test_project_command_fallback_warning_is_nonfatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _assume_tmux_installed(monkeypatch)
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda workspace, plans: None
    )
    monkeypatch.setattr(launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux"])
    monkeypatch.setattr(
        launcher_module.tmux,
        "run_interactive_tmux",
        lambda argv: subprocess.CompletedProcess(argv, 0),
    )
    out = io.StringIO()

    execute_launch_request(_request(tmp_path, _pane(PaneKind.DEV_SERVER)), out=out)

    assert "No supported development command was detected" in out.getvalue()
