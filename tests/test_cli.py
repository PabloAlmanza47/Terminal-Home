"""Tests for the CLI dispatcher (dashboard.cli): the TUI/subcommand split,
argument parsing, and the read-only `list`/`plan`/`doctor` commands
end-to-end. Every test isolates XDG_CONFIG_HOME/XDG_DATA_HOME; none ever
starts a real tmux server or touches the user's real configuration.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import dashboard.app as app_module
import dashboard.cli as cli_module
import dashboard.services.project_creation as project_creation_module
import dashboard.services.project_launch as project_launch_module
import dashboard.services.tmux as tmux_module
import dashboard.services.workspace_launcher as workspace_launcher_module
import dashboard.services.workspace_store as workspace_store_module
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_launcher import LaunchError


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))


def _make_project(root: Path, name: str) -> Path:
    path = root / name
    path.mkdir(parents=True)
    return path


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
        build_default_workspace("saved-proj", saved, "saved-proj")
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
    workspace_store_module.save_workspace(build_default_workspace("demo", project, "demo"))
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
    workspace_store_module.save_workspace(build_default_workspace("demo", project, "demo"))
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
    monkeypatch.setattr(
        tmux_module, "exec_attach", lambda *a, **k: spies["exec_attach"].append(1)
    )
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
