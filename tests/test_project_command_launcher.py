from __future__ import annotations

import shlex
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

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
    title: str,
    command: str,
    *,
    pane_id: str = "%2",
    window_id: str = "@1",
    dead: bool = False,
    pane_index: int | None = None,
    role: str | None = None,
) -> tmux.TmuxPaneRuntime:
    return tmux.TmuxPaneRuntime(
        "demo", "main", title, command, dead, window_id, pane_id, pane_index, role
    )


def test_dev_uses_existing_development_server_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, tuple[str, ...]]] = []
    selected: list[tuple[str, str]] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        launcher.tmux,
        "list_tmux_panes",
        lambda **kwargs: [_runtime("server", "bash", pane_id="%server", role="dev_server")],
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
        lambda **kwargs: [_runtime("server", "node", pane_id="%server", role="dev_server")],
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
                "demo", "th-build", "terminal-home:build", "bash", False, "@task", "%task",
                role="task:build",
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


# --- Pane identity regression tests (PR #3 review) ----------------------------
#
# The target pane must be the one Terminal Home tagged with the role option
# when it created it.  Pane indexes (base 0 or 1, user-configurable) and
# titles (settable by any program) must never select a pane on their own.


def _patch_execution(
    monkeypatch: pytest.MonkeyPatch, panes: list[tmux.TmuxPaneRuntime]
) -> tuple[list[tuple[str, tuple[str, ...]]], list[str]]:
    sent: list[tuple[str, tuple[str, ...]]] = []
    created: list[str] = []
    monkeypatch.setattr(launcher.tmux, "session_exists", lambda *args, **kwargs: True)
    monkeypatch.setattr(launcher.tmux, "list_tmux_panes", lambda **kwargs: panes)
    monkeypatch.setattr(
        launcher.tmux,
        "create_task_window",
        lambda *args, **kwargs: (created.append(args[1]) or ("@task", "%task")),
    )
    monkeypatch.setattr(launcher.tmux, "set_pane_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher.tmux, "set_pane_role", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        launcher.tmux,
        "send_argv_to_pane",
        lambda pane, argv, **kwargs: sent.append((pane, argv)),
    )
    monkeypatch.setattr(launcher.tmux, "select_window_and_pane", lambda *args, **kwargs: None)
    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)
    return sent, created


@pytest.mark.parametrize("base", [0, 1])
def test_unrelated_panes_listed_first_never_receive_the_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: int
) -> None:
    # Spec order: agent, git, dev server, editor -> runtime indexes base..base+3.
    # The old matcher accepted index {i, i+1} for each spec pane, so the git
    # pane (zero-based index 1, one-based index 2) could stand in for others.
    panes = [
        _runtime("claude", "bash", pane_id="%agent", pane_index=base),
        _runtime("server", "bash", pane_id="%git", pane_index=base + 1),  # spoofed title
        _runtime("Development Server", "bash", pane_id="%editor", pane_index=base + 3),
        _runtime("x", "bash", pane_id="%dev", pane_index=base + 2, role="dev_server"),
    ]
    sent, created = _patch_execution(monkeypatch, panes)

    launcher.execute_project_command_request(
        _request(tmp_path, "dev", ("npm", "run", "dev")), runner=_ok
    )

    assert sent == [("%dev", ("npm", "run", "dev"))]
    assert created == []


@pytest.mark.parametrize("base", [0, 1])
def test_untagged_session_uses_a_task_window_not_an_index_or_title_guess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: int
) -> None:
    # A session created before pane roles existed (or by hand): every pane
    # "looks" right by index or title, but none is verified.
    panes = [
        _runtime("server", "bash", pane_id=f"%{i}", pane_index=base + i) for i in range(4)
    ]
    sent, created = _patch_execution(monkeypatch, panes)

    launcher.execute_project_command_request(
        _request(tmp_path, "test", ("pytest",)), runner=_ok
    )

    assert created == ["th-test"]
    assert sent == [("%task", ("pytest",))]


def test_ambiguous_role_uses_a_task_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    panes = [
        _runtime("server", "bash", pane_id="%a", role="dev_server"),
        _runtime("server", "bash", pane_id="%b", role="dev_server"),
    ]
    sent, created = _patch_execution(monkeypatch, panes)

    launcher.execute_project_command_request(
        _request(tmp_path, "dev", ("npm", "run", "dev")), runner=_ok
    )

    assert created == ["th-dev"]
    assert sent == [("%task", ("npm", "run", "dev"))]


def test_role_in_another_session_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmux.TmuxPaneRuntime(
        "other", "main", "server", "bash", False, "@9", "%other", 0, "dev_server"
    )
    sent, created = _patch_execution(monkeypatch, [other])

    launcher.execute_project_command_request(
        _request(tmp_path, "dev", ("npm", "run", "dev")), runner=_ok
    )

    assert created == ["th-dev"] and sent == [("%task", ("npm", "run", "dev"))]


def test_task_window_title_alone_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spoof = tmux.TmuxPaneRuntime(
        "demo", "main", "terminal-home:build", "bash", False, "@1", "%editor", 0
    )
    sent, created = _patch_execution(monkeypatch, [spoof])

    launcher.execute_project_command_request(
        _request(tmp_path, "build", ("make",)), runner=_ok
    )

    assert created == ["th-build"] and sent == [("%task", ("make",))]


def test_list_tmux_panes_reads_role_and_tolerates_older_formats() -> None:
    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        rows = [
            "demo\tmain\tt\tbash\t0\t@1\t%1\t0\tdev_server",
            "demo\tmain\tt\tbash\t0\t@1\t%2\t1\t",
            "demo\tmain\tt\tbash\t0\t@1\t%3\t2",
        ]
        return subprocess.CompletedProcess(argv, 0, "\n".join(rows), "")

    panes = tmux.list_tmux_panes(runner=runner)
    assert [(p.pane_id, p.pane_index, p.role) for p in panes] == [
        ("%1", 0, "dev_server"),
        ("%2", 1, None),
        ("%3", 2, None),
    ]


def test_set_pane_role_is_best_effort() -> None:
    calls: list[list[str]] = []

    def unsupported(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "unknown flag -p")

    assert tmux.set_pane_role("%1", "dev_server", runner=unsupported) is False
    assert calls == [["tmux", "set-option", "-p", "-t", "%1", tmux.PANE_ROLE_OPTION, "dev_server"]]


@pytest.mark.parametrize("base", [0, 1])
def test_real_tmux_command_reaches_only_the_tagged_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: int
) -> None:
    """End to end on a private tmux server with base-index/pane-base-index
    set to *base*: create the workspace, reorder its panes, run a command,
    and prove only the dev-server pane received it."""
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    socket = f"th-cmd-{uuid4().hex[:12]}"
    config = tmp_path / "tmux.conf"
    config.write_text(
        f"set -g base-index {base}\nset -g pane-base-index {base}\n"
        "set -g default-command /bin/sh\n"
    )
    project = tmp_path / "project"
    project.mkdir()
    workspace = WorkspaceSpec(
        project_name="demo",
        project_location=LocalProjectLocation(project),
        session_name=f"th-cmd-{uuid4().hex[:8]}",
        windows=(
            WindowSpec(
                window_name="main",
                panes=(
                    PaneSpec(PaneKind.BLANK_TERMINAL, "Shell"),
                    PaneSpec(PaneKind.GIT, "Git"),
                    PaneSpec(PaneKind.DEV_SERVER, "Development Server"),
                    PaneSpec(PaneKind.TEST_TERMINAL, "Tests"),
                ),
            ),
        ),
    )

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["tmux", "-L", socket, "-f", str(config), *argv[1:]],
            capture_output=True, text=True, timeout=5,
        )

    def pane_rows() -> list[tuple[str, int, str]]:
        out = runner(["tmux", "list-panes", "-t", workspace.session_name, "-F",
                      "#{pane_id}\t#{pane_index}\t#{@terminal_home_role}"])
        assert out.returncode == 0, out.stderr
        return [
            (pid, int(idx), role)
            for pid, idx, role in (line.split("\t") for line in out.stdout.splitlines())
        ]

    def captured(pane_id: str) -> str:
        return runner(["tmux", "capture-pane", "-p", "-t", pane_id]).stdout

    monkeypatch.setattr(launcher, "attach_workspace_session", lambda *args: None)
    try:
        try:
            tmux.create_workspace_session(workspace, {}, runner=runner)
        except tmux.TmuxCommandError as exc:
            if "Operation not permitted" in str(exc):
                pytest.skip("sandbox does not permit a real tmux test server")
            raise
        rows = pane_rows()
        assert [index for _, index, _ in rows] == [base, base + 1, base + 2, base + 3]
        dev_pane = next(pid for pid, _, role in rows if role == "dev_server")
        first_pane = rows[0][0]
        # Reorder: move the dev server pane to the front, so the pane now at
        # the index the dev server was created with is an unrelated one.
        swapped = runner(["tmux", "swap-pane", "-s", dev_pane, "-t", first_pane])
        assert swapped.returncode == 0, swapped.stderr
        rows = pane_rows()
        assert rows[0] == (dev_pane, base, "dev_server")

        marker = f"th-marker-{uuid4().hex[:8]}"
        launcher.execute_project_command_request(
            RunProjectCommandRequest(workspace, "dev", "dev", "dev", ("echo", marker)),
            runner=runner,
        )

        deadline = time.monotonic() + 5
        while marker not in captured(dev_pane) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker in captured(dev_pane)
        others = [pid for pid, _, _ in rows if pid != dev_pane]
        assert all(marker not in captured(pid) for pid in others)
        assert len(pane_rows()) == 4  # no task window was needed
    finally:
        runner(["tmux", "kill-server"])
