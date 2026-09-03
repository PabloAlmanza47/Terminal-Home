"""Tests for the CLI dispatcher (dashboard.cli): the TUI/subcommand split,
argument parsing, and the read-only `list`/`plan`/`doctor` commands
end-to-end. Every test isolates XDG_CONFIG_HOME/XDG_DATA_HOME; none ever
starts a real tmux server or touches the user's real configuration.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

import dashboard.app as app_module
import dashboard.cli as cli_module
import dashboard.services.project_creation as project_creation_module
import dashboard.services.project_launch as project_launch_module
import dashboard.services.terminal as terminal_module
import dashboard.services.tmux as tmux_module
import dashboard.services.workspace_launcher as workspace_launcher_module
import dashboard.services.workspace_store as workspace_store_module
from dashboard.models import (
    LocalProjectLocation,
    RemoteProjectRegistration,
    SshHost,
    SshProjectLocation,
)
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services.agent_deck import AgentDeckSession, AgentStatus
from dashboard.services.git import GitFileChange, GitStatus
from dashboard.services.project_selection import ProjectSelectionResult
from dashboard.services.projects import Project, ProjectStatus
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.remote_project_store import create_remote_project
from dashboard.services.ssh_host_store import create_ssh_host
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_launcher import LaunchError


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


def _make_project(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    return path


def _cli_status(project: Project, sessions: tuple[AgentDeckSession, ...] = ()) -> ProjectStatus:
    return ProjectStatus(
        project=project,
        canonical_path=project.path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=build_default_workspace(
            project.name, LocalProjectLocation(project.path.resolve()), project.name
        ),
        workspace_metadata_error=None,
        expected_session_name=project.name,
        tmux_available=True,
        session_running=True,
        last_modified=None,
        agent_sessions=sessions,
        server_status="running",
    )


def _configure_roots(*roots: Path) -> None:
    save_projects_config(ProjectsConfig(roots=roots))


def _assume_no_tmux_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])


# --- Dispatcher / entrypoint behavior ---------------------------------------


def test_no_arguments_calls_the_tui_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(1))

    exit_code = cli_module.run([])

    assert calls == [1]
    assert exit_code == 0


@pytest.mark.parametrize("argv", [["list"], ["doctor"]])
def test_read_only_commands_never_call_the_tui(
    argv: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_no_tmux_sessions(monkeypatch)
    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(1))

    cli_module.run(argv)

    assert calls == []


def test_read_only_commands_never_clear_terminal_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_no_tmux_sessions(monkeypatch)
    calls: list[object] = []

    def record_cleanup() -> None:
        calls.append(1)

    monkeypatch.setattr(terminal_module, "clear_terminal_display", record_cleanup)

    cli_module.run(["list"])
    cli_module.run(["doctor"])

    assert calls == []


def test_doctor_remote_flag_is_forwarded(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    calls = []
    monkeypatch.setattr(
        cli_module,
        "run_diagnostics",
        lambda *, remote: calls.append(remote) or (),
    )

    assert cli_module.run(["doctor", "--remote"]) == 0
    assert calls == [True]
    assert "Terminal Home doctor" in capsys.readouterr().out


@pytest.mark.parametrize("command", ["plan", "up"])
def test_project_commands_never_call_the_tui(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    _isolate(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(1))

    cli_module.run([command, "nonexistent"])

    assert calls == []


def test_help_does_not_open_textual(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(1))

    with pytest.raises(SystemExit) as excinfo:
        cli_module.run(["--help"])

    assert excinfo.value.code == 0
    assert calls == []


@pytest.mark.parametrize(
    "argv", [["list", "--help"], ["plan", "--help"], ["up", "--help"], ["doctor", "--help"]]
)
def test_subcommand_help_does_not_open_textual(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(app_module, "main", lambda: calls.append(1))

    with pytest.raises(SystemExit) as excinfo:
        cli_module.run(argv)

    assert excinfo.value.code == 0
    assert calls == []


def test_unknown_command_exits_with_code_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_module.run(["bogus"])

    assert excinfo.value.code == 2


@pytest.mark.parametrize("argv", [["up"], ["up", "demo", "--force"]])
def test_up_syntax_errors_keep_argparse_exit_code_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_module.run(argv)
    assert excinfo.value.code == 2


def test_main_only_raises_system_exit_on_nonzero_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_module, "run", lambda argv=None: 0)
    cli_module.main()  # must not raise

    monkeypatch.setattr(cli_module, "run", lambda argv=None: 1)
    with pytest.raises(SystemExit) as excinfo:
        cli_module.main()
    assert excinfo.value.code == 1


# --- `th list` ---------------------------------------------------------------


def test_list_shows_running_saved_and_default_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "projects"
    running = _make_project(root, "running-proj")
    saved = _make_project(root, "saved-proj")
    _make_project(root, "default-proj")
    _configure_roots(root)
    workspace_store_module.save_workspace(
        build_default_workspace("saved-proj", LocalProjectLocation(saved), "saved-proj")
    )
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(
        tmux_module,
        "list_tmux_sessions",
        lambda: [
            tmux_module.TmuxSession(name="running-proj", windows=1, created="", attached=False)
        ],
    )

    exit_code = cli_module.run(["list"])
    out = capsys.readouterr().out

    assert exit_code == 0
    lines = {line.split()[0]: line for line in out.splitlines() if line.strip()}
    assert "running" in lines["running-proj"]
    assert "saved" in lines["saved-proj"]
    assert "default" in lines["default-proj"]
    assert str(running.resolve()) in lines["running-proj"]


def test_list_distinguishes_duplicate_names_by_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    school = _make_project(tmp_path / "school", "example")
    work = _make_project(tmp_path / "work", "example")
    _configure_roots(tmp_path / "school", tmp_path / "work")
    _assume_no_tmux_sessions(monkeypatch)

    cli_module.run(["list"])
    out = capsys.readouterr().out

    assert str(school.resolve()) in out
    assert str(work.resolve()) in out


def test_list_empty_discovery_prints_a_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    _assume_no_tmux_sessions(monkeypatch)

    exit_code = cli_module.run(["list"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "No projects discovered" in out


def test_list_warns_on_missing_root_without_discarding_found_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    ok_root = tmp_path / "ok"
    _make_project(ok_root, "alpha")
    missing_root = tmp_path / "does-not-exist"
    _configure_roots(ok_root, missing_root)
    _assume_no_tmux_sessions(monkeypatch)

    exit_code = cli_module.run(["list"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "alpha" in captured.out
    assert str(missing_root) in captured.err


def test_list_order_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "projects"
    for name in ("zebra", "apple", "banana"):
        _make_project(root, name)
    _configure_roots(root)
    _assume_no_tmux_sessions(monkeypatch)

    cli_module.run(["list"])
    first = capsys.readouterr().out
    cli_module.run(["list"])
    second = capsys.readouterr().out

    assert first == second
    names_in_order = [line.split()[0] for line in first.splitlines()[1:] if line.strip()]
    assert names_in_order == ["apple", "banana", "zebra"]


def test_host_cli_crud_and_referenced_removal_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    project_id = "c27c7b67-8e3f-4ebc-8dce-d66d559df977"

    assert cli_module.run(
        ["host", "add", "--id", host_id, "--name", "build", "--destination", "builder"]
    ) == 0
    assert cli_module.run(
        ["host", "edit", host_id, "--name", "build-prod", "--destination", "prod-builder"]
    ) == 0
    assert cli_module.run(["host", "list"]) == 0
    assert "build-prod" in capsys.readouterr().out

    assert cli_module.run(
        [
            "remote",
            "add",
            "--id",
            project_id,
            "--name",
            "api",
            "--host-id",
            host_id,
            "--remote-path",
            "/srv/Project With Spaces",
        ]
    ) == 0
    assert cli_module.run(["host", "remove", host_id]) == 1
    assert "still referenced" in capsys.readouterr().err

    assert cli_module.run(
        [
            "remote",
            "edit",
            project_id,
            "--name",
            "api-renamed",
            "--remote-path",
            "/srv/api-renamed",
        ]
    ) == 0
    assert cli_module.run(["remote", "list"]) == 0
    assert "api-renamed" in capsys.readouterr().out
    assert cli_module.run(["remote", "remove", project_id]) == 0
    assert cli_module.run(["host", "remove", host_id]) == 0


def test_orphaned_remote_registration_can_be_listed_and_edited_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    missing_host = "e95bfffc-8d3e-4d74-c4ad-877e66ef2aa8"
    project_id = "f38d8c78-9f4a-5e85-d5be-988f77f3bb19"
    create_remote_project(
        RemoteProjectRegistration(project_id, missing_host, "orphan", "/srv/orphan")
    )

    assert cli_module.run(["remote", "list"]) == 0
    assert "missing host" in capsys.readouterr().out
    assert cli_module.run(
        ["remote", "edit", project_id, "--name", "orphan-renamed", "--remote-path", "/srv/new"]
    ) == 0
    assert "orphan-renamed" in capsys.readouterr().out


def test_list_shows_registered_remote_projects_without_ssh_or_tmux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    _assume_no_tmux_sessions(monkeypatch)
    monkeypatch.setattr(
        "dashboard.services.ssh.run_ssh_command",
        lambda *args, **kwargs: pytest.fail("list must not make an SSH call"),
    )

    known_host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    missing_host_id = "e95bfffc-8d3e-4d74-c4ad-877e66ef2aa8"
    create_ssh_host(SshHost(known_host_id, "build host", "builder"))
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            known_host_id,
            "remote-api",
            "/srv/Project With Spaces",
        )
    )
    create_remote_project(
        RemoteProjectRegistration(
            "f38d8c78-9f4a-5e85-d5be-988f77f3bb19",
            missing_host_id,
            "orphan-api",
            "/srv/orphan path",
        )
    )

    assert cli_module.run(["list"]) == 0
    out = capsys.readouterr().out

    assert "REMOTE PROJECTS" in out
    assert "remote-api" in out
    assert "ssh:d84aeefb-7c29-4c63-b39c-766d559df977:/srv/Project With Spaces" in out
    assert "/srv/Project With Spaces" in out
    assert "orphan-api" in out
    assert "ssh:e95bfffc-8d3e-4d74-c4ad-877e66ef2aa8:/srv/orphan path" in out
    assert "orphaned (missing host)" in out


# --- `th plan` -----------------------------------------------------------------


def test_plan_missing_selector_exits_1_with_stderr_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    _configure_roots(tmp_path / "empty")
    (tmp_path / "empty").mkdir()

    exit_code = cli_module.run(["plan", "nonexistent"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "error:" in captured.err
    assert captured.out == ""


def test_plan_ambiguous_selector_exits_1_with_stderr_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    _make_project(tmp_path / "school", "example")
    _make_project(tmp_path / "work", "example")
    _configure_roots(tmp_path / "school", tmp_path / "work")

    exit_code = cli_module.run(["plan", "example"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "multiple projects match" in captured.err
    assert captured.out == ""


def test_plan_running_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    project = _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    # gather_single_project_status (used by `th plan`) checks one session at
    # a time via session_exists, unlike scan_all_projects (used by `th
    # list`), which batches the same fact through list_tmux_sessions.
    monkeypatch.setattr(tmux_module, "session_exists", lambda name, **k: name == "demo")

    exit_code = cli_module.run(["plan", "demo"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Action: attach to existing session" in out
    assert "Source: running tmux session" in out
    assert str(project.resolve()) in out


def test_plan_saved_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    project = _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    workspace_store_module.save_workspace(
        build_default_workspace("demo", LocalProjectLocation(project), "demo")
    )
    _assume_no_tmux_sessions(monkeypatch)

    exit_code = cli_module.run(["plan", "demo"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Action: create saved workspace" in out
    assert "Source: saved workspace" in out
    assert "Window 1: code" in out


def test_plan_default_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    _assume_no_tmux_sessions(monkeypatch)

    exit_code = cli_module.run(["plan", "demo"])
    out = capsys.readouterr().out

    assert exit_code == 0
    assert "Action: create default workspace" in out
    assert "Source: generated default" in out


def test_plan_remote_registration_creates_in_memory_default_without_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    create_ssh_host(SshHost(host_id, "build host", "builder"))
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            host_id,
            "remote-api",
            "/srv/Project With Spaces",
        )
    )
    runner_calls: list[list[str]] = []
    monkeypatch.setattr(
        project_launch_module,
        "resolve_tmux_runner",
        lambda workspace: tmux_module.TmuxRunnerResolution(
            status="resolved", runner=lambda argv: runner_calls.append(argv)
        ),
    )
    monkeypatch.setattr(project_launch_module, "session_exists", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        project_launch_module,
        "save_workspace",
        lambda *args, **kwargs: pytest.fail("th plan must not save a workspace"),
    )

    assert cli_module.run(["plan", "ssh:" + host_id + ":/srv/Project With Spaces"]) == 0
    out = capsys.readouterr().out

    assert "Project: remote-api" in out
    assert "Location: ssh:" + host_id + ":/srv/Project With Spaces" in out
    assert "Session: remote-api" in out
    assert "Action: create default workspace" in out
    assert runner_calls == []


def test_plan_running_remote_session_attaches_and_unique_name_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    create_ssh_host(SshHost(host_id, "build host", "builder"))
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", host_id, "remote-api", "/srv/api"
        )
    )
    calls = []
    monkeypatch.setattr(
        project_launch_module,
        "resolve_tmux_runner",
        lambda workspace: tmux_module.TmuxRunnerResolution(
            status="resolved", runner=lambda argv: calls.append(argv)
        ),
    )
    monkeypatch.setattr(project_launch_module, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        project_launch_module,
        "run_interactive_ssh",
        lambda *args, **kwargs: pytest.fail("th plan must not attach interactively"),
        raising=False,
    )

    assert cli_module.run(["plan", "remote-api"]) == 0
    out = capsys.readouterr().out
    assert "Action: attach to existing session" in out
    assert "Session: remote-api" in out
    assert calls == []


def test_plan_saved_remote_workspace_recreates_and_missing_host_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    create_ssh_host(SshHost(host_id, "build host", "builder"))
    location = SshProjectLocation(host_id, "/srv/api")
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", host_id, "remote-api", "/srv/api"
        )
    )
    workspace_store_module.save_workspace(
        build_default_workspace("remote-api", location, "remote-api")
    )
    monkeypatch.setattr(
        project_launch_module,
        "resolve_tmux_runner",
        lambda workspace: tmux_module.TmuxRunnerResolution(
            status="resolved", runner=lambda argv: pytest.fail("fake runner unused")
        ),
    )
    monkeypatch.setattr(project_launch_module, "session_exists", lambda *args, **kwargs: False)

    assert cli_module.run(["plan", "remote-api"]) == 0
    assert "Action: recreate saved workspace" in capsys.readouterr().out

    missing_host = "e95bfffc-8d3e-4d74-c4ad-877e66ef2aa8"
    create_remote_project(
        RemoteProjectRegistration(
            "f38d8c78-9f4a-5e85-d5be-988f77f3bb19",
            missing_host,
            "orphan-api",
            "/srv/orphan",
        )
    )
    monkeypatch.setattr(
        project_launch_module, "resolve_tmux_runner", tmux_module.resolve_tmux_runner
    )
    monkeypatch.setattr(
        project_launch_module,
        "session_exists",
        lambda *args, **kwargs: pytest.fail("missing host must fail before tmux"),
    )
    assert cli_module.run(["plan", "orphan-api"]) == 1
    assert "SSH host " + missing_host + " is not registered." in capsys.readouterr().err


# --- `th up` -----------------------------------------------------------------


def test_up_default_saves_before_calling_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    project = _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    requests = []
    monkeypatch.setattr(cli_module, "execute_launch_request", requests.append)

    assert cli_module.run(["up", "demo"]) == 0

    assert len(requests) == 1
    assert requests[0].action.value == "create"
    assert workspace_store_module.load_workspace(project) == requests[0].workspace
    assert "Creating default workspace 'demo'" in capsys.readouterr().out


def _register_remote_cli_project(
    host_id: str, name: str = "remote-api", remote_path: str = "/srv/api"
) -> None:
    create_ssh_host(SshHost(host_id, "build host", "builder"))
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            host_id,
            name,
            remote_path,
        )
    )


def test_up_new_remote_project_saves_location_and_uses_attach_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    _register_remote_cli_project(host_id, remote_path="/srv/Project With Spaces")
    monkeypatch.setattr(
        project_launch_module,
        "resolve_tmux_runner",
        lambda workspace: tmux_module.TmuxRunnerResolution(
            status="resolved", runner=lambda argv: subprocess.CompletedProcess(argv, 0)
        ),
    )
    monkeypatch.setattr(project_launch_module, "session_exists", lambda *a, **k: False)
    requests = []
    monkeypatch.setattr(cli_module, "execute_launch_request", requests.append)

    assert cli_module.run(["up", "remote-api"]) == 0
    request = requests[0]
    assert request.action.value == "create"
    assert isinstance(request.workspace.project_location, SshProjectLocation)
    assert request.workspace.project_location.remote_path == "/srv/Project With Spaces"
    assert isinstance(request.workspace.project_location.remote_path, str)
    assert workspace_store_module.load_workspace_for_location(
        request.workspace.project_location
    ) == request.workspace
    assert "Creating default workspace 'remote-api'" in capsys.readouterr().out


def test_up_running_remote_session_attaches_without_recreation_or_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    _register_remote_cli_project(host_id)
    monkeypatch.setattr(
        project_launch_module,
        "resolve_tmux_runner",
        lambda workspace: tmux_module.TmuxRunnerResolution(
            status="resolved", runner=lambda argv: subprocess.CompletedProcess(argv, 0)
        ),
    )
    monkeypatch.setattr(project_launch_module, "session_exists", lambda *a, **k: True)
    monkeypatch.setattr(
        project_launch_module,
        "save_workspace",
        lambda *a, **k: pytest.fail("running remote session must not save"),
    )
    requests = []
    monkeypatch.setattr(cli_module, "execute_launch_request", requests.append)

    assert cli_module.run(["up", "ssh:" + host_id + ":/srv/api"]) == 0
    assert requests[0].action.value == "attach"
    assert not workspace_store_module.default_store_path().exists()
    assert "Attaching to tmux session 'remote-api'" in capsys.readouterr().out


def test_up_saved_remote_workspace_recreates_and_missing_host_does_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    _configure_roots(empty_root)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    _register_remote_cli_project(host_id)
    saved = build_default_workspace(
        "remote-api", SshProjectLocation(host_id, "/srv/api"), "remote-api"
    )
    workspace_store_module.save_workspace(saved)
    monkeypatch.setattr(
        project_launch_module,
        "resolve_tmux_runner",
        lambda workspace: tmux_module.TmuxRunnerResolution(
            status="resolved", runner=lambda argv: subprocess.CompletedProcess(argv, 0)
        ),
    )
    monkeypatch.setattr(project_launch_module, "session_exists", lambda *a, **k: False)
    requests = []
    monkeypatch.setattr(cli_module, "execute_launch_request", requests.append)

    assert cli_module.run(["up", "remote-api"]) == 0
    assert requests[0].workspace == saved
    assert workspace_store_module.load_workspace_for_location(saved.project_location) == saved
    capsys.readouterr()

    missing_host = "e95bfffc-8d3e-4d74-c4ad-877e66ef2aa8"
    create_remote_project(
        RemoteProjectRegistration(
            "f38d8c78-9f4a-5e85-d5be-988f77f3bb19",
            missing_host,
            "orphan-api",
            "/srv/orphan",
        )
    )
    monkeypatch.setattr(
        project_launch_module, "resolve_tmux_runner", tmux_module.resolve_tmux_runner
    )
    monkeypatch.setattr(
        cli_module,
        "execute_launch_request",
        lambda request: pytest.fail("missing host must not launch"),
    )
    assert cli_module.run(["up", "orphan-api"]) == 1
    assert "SSH host " + missing_host + " is not registered." in capsys.readouterr().err


def test_up_running_unsaved_attaches_without_saving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: True)
    requests = []
    monkeypatch.setattr(cli_module, "execute_launch_request", requests.append)

    assert cli_module.run(["up", "demo"]) == 0
    assert requests[0].workspace is None
    assert requests[0].action.value == "attach"
    assert not workspace_store_module.default_store_path().exists()
    assert "Attaching to tmux session 'demo'" in capsys.readouterr().out


def test_up_exact_ad_hoc_path_saves_workspace_but_not_project_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    outside = _make_project(tmp_path / "outside", "adhoc")
    empty = tmp_path / "configured"
    empty.mkdir()
    _configure_roots(empty)
    config_path = tmp_path / "xdg-config" / "terminal-home" / "projects.json"
    config_before = config_path.read_bytes()
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(cli_module, "execute_launch_request", lambda request: None)

    assert cli_module.run(["up", str(outside)]) == 0
    assert workspace_store_module.load_workspace(outside) is not None
    assert config_path.read_bytes() == config_before


def test_up_save_failure_never_calls_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(
        "dashboard.services.project_launch.save_workspace",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    calls = []
    monkeypatch.setattr(cli_module, "execute_launch_request", lambda request: calls.append(1))

    assert cli_module.run(["up", "demo"]) == 1
    assert calls == []
    assert "error: disk full" in capsys.readouterr().err


def test_up_launch_failure_is_controlled_and_keeps_saved_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    project = _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(
        cli_module,
        "execute_launch_request",
        lambda request: (_ for _ in ()).throw(OSError("exec failed")),
    )

    assert cli_module.run(["up", "demo"]) == 1
    assert workspace_store_module.load_workspace(project) is not None
    assert "error: exec failed" in capsys.readouterr().err


@pytest.mark.parametrize(
    "failure",
    [
        LaunchError("launch failed"),
        tmux_module.TmuxCommandError("tmux failed"),
        subprocess.TimeoutExpired(["tmux"], 3),
    ],
)
def test_up_expected_launch_failures_are_controlled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: BaseException,
) -> None:
    _isolate(monkeypatch, tmp_path)
    _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(
        cli_module,
        "execute_launch_request",
        lambda request: (_ for _ in ()).throw(failure),
    )
    assert cli_module.run(["up", "demo"]) == 1
    assert "error:" in capsys.readouterr().err


def test_up_launches_backup_recovered_workspace_without_rewriting_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    project = _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    workspace_store_module.save_workspace(
        build_default_workspace("demo", LocalProjectLocation(project), "demo")
    )
    store = workspace_store_module.default_store_path()
    store.rename(Path(f"{store}.bak"))
    store.write_text("broken")
    before = (store.read_bytes(), Path(f"{store}.bak").read_bytes())
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    requests = []
    monkeypatch.setattr(cli_module, "execute_launch_request", requests.append)

    assert cli_module.run(["up", "demo"]) == 0
    captured = capsys.readouterr()
    assert requests[0].action.value == "attach"
    assert "Recovered workspace data" in captured.err
    assert (store.read_bytes(), Path(f"{store}.bak").read_bytes()) == before


def test_plan_and_up_block_corrupt_stopped_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    store = workspace_store_module.default_store_path()
    store.parent.mkdir(parents=True)
    store.write_text("broken")
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    calls = []
    monkeypatch.setattr(cli_module, "execute_launch_request", lambda request: calls.append(1))

    assert cli_module.run(["plan", "demo"]) == 1
    plan_output = capsys.readouterr().out
    assert "Action: blocked" in plan_output
    assert "invalid saved workspace metadata" in plan_output
    assert cli_module.run(["up", "demo"]) == 1
    assert "error:" in capsys.readouterr().err
    assert calls == []
    assert store.read_text() == "broken"


# --- No-mutation guarantee ---------------------------------------------------


def test_read_only_commands_never_mutate_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    project = _make_project(tmp_path / "projects", "demo")
    _configure_roots(tmp_path / "projects")
    _assume_no_tmux_sessions(monkeypatch)

    spies: dict[str, list[object]] = {
        "execute_launch_request": [],
        "create_workspace_session": [],
        "exec_attach": [],
        "save_workspace": [],
        "create_project_directory": [],
    }
    monkeypatch.setattr(
        workspace_launcher_module,
        "execute_launch_request",
        lambda *a, **k: spies["execute_launch_request"].append(1),
    )
    monkeypatch.setattr(
        tmux_module,
        "create_workspace_session",
        lambda *a, **k: spies["create_workspace_session"].append(1),
    )
    monkeypatch.setattr(tmux_module, "exec_attach", lambda *a, **k: spies["exec_attach"].append(1))
    monkeypatch.setattr(
        workspace_store_module,
        "save_workspace",
        lambda *a, **k: spies["save_workspace"].append(1),
    )
    monkeypatch.setattr(
        project_launch_module,
        "save_workspace",
        lambda *a, **k: spies["save_workspace"].append(1),
    )
    monkeypatch.setattr(
        project_creation_module,
        "create_project_directory",
        lambda *a, **k: spies["create_project_directory"].append(1),
    )

    assert cli_module.run(["list"]) == 0
    assert cli_module.run(["plan", "demo"]) == 0
    cli_module.run(["doctor"])

    assert all(calls == [] for calls in spies.values()), spies
    assert not (tmp_path / "xdg-data" / "terminal-home" / "workspaces.json").exists()
    assert list(project.iterdir()) == []


def test_status_command_human_and_json_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project("demo", tmp_path / "demo")
    session = AgentDeckSession(
        "session-1", "Demo agent", project.path, "codex", AgentStatus.RUNNING
    )
    status = _cli_status(project, (session,))
    git = GitStatus(
        True,
        "main",
        False,
        changes=(GitFileChange("file.py", ".", "M"),),
        modified_count=1,
    )
    monkeypatch.setattr(
        cli_module,
        "resolve_project_selector",
        lambda _: ProjectSelectionResult(project=project),
    )
    monkeypatch.setattr(cli_module, "gather_single_project_status", lambda _: status)
    monkeypatch.setattr(cli_module, "load_status", lambda _: git)

    assert cli_module.run(["status", "."]) == 0
    output = capsys.readouterr().out
    assert "demo" in output
    assert "Workspace  Running" in output
    assert "Codex      Working" in output
    assert "Modified  1" in output
    assert "Changed Files" in output
    assert ".M file.py" in output

    assert cli_module.run(["status", ".", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project"]["name"] == "demo"
    assert payload["agent"] == {"count": 1, "status": "working"}
    assert payload["git"]["clean"] is False
    assert payload["git"]["files"][0]["worktree_status"] == "M"


def test_agent_command_attaches_one_session_and_reports_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project("demo", tmp_path / "demo")
    status = _cli_status(project)
    monkeypatch.setattr(
        cli_module,
        "resolve_project_selector",
        lambda _: ProjectSelectionResult(project=project),
    )
    monkeypatch.setattr(cli_module, "gather_single_project_status", lambda _: status)

    assert cli_module.run(["agent", "."]) == 1
    assert "No Agent Deck session found" in capsys.readouterr().err

    session = AgentDeckSession(
        "session-1", "Demo agent", project.path, "codex", AgentStatus.WAITING
    )
    monkeypatch.setattr(
        cli_module,
        "gather_single_project_status",
        lambda _: _cli_status(project, (session,)),
    )
    attached: list[str] = []
    monkeypatch.setattr(cli_module, "execute_agent_deck_attach", attached.append)

    assert cli_module.run(["agent", "demo"]) == 0
    assert attached == ["session-1"]


def test_status_watch_refreshes_in_place_and_exits_cleanly_on_ctrl_c(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Project("demo", tmp_path / "demo")
    status = _cli_status(project)
    git = GitStatus(True, "main", False)
    monkeypatch.setattr(
        cli_module,
        "resolve_project_selector",
        lambda _: ProjectSelectionResult(project=project),
    )
    calls = 0
    dirty = GitStatus(
        True,
        "main",
        False,
        changes=(GitFileChange("changed.py", ".", "M"),),
        modified_count=1,
    )

    def collect(_project, _previous=None):
        nonlocal calls
        calls += 1
        return status, git if calls == 1 else dirty

    monkeypatch.setattr(cli_module, "_collect_status", collect)
    sleeps = 0

    def sleep(_interval: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli_module.time, "sleep", sleep)
    output = io.StringIO()
    output.isatty = lambda: True  # type: ignore[method-assign]
    monkeypatch.setattr(cli_module.sys, "stdout", output)

    assert cli_module.run(["status", ".", "--watch"]) == 0
    assert calls == 2
    assert "Refreshing every 1s" in output.getvalue()
    assert "\x1b[2J\x1b[H" in output.getvalue()


def test_status_watch_rejects_non_tty_and_json_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project("demo", tmp_path / "demo")
    monkeypatch.setattr(
        cli_module,
        "resolve_project_selector",
        lambda _: ProjectSelectionResult(project=project),
    )
    assert cli_module.run(["status", ".", "--watch"]) == 2
    assert "interactive stdout" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        cli_module.run(["status", ".", "--json", "--watch"])
