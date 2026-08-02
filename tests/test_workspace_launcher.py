"""Tests for the non-Textual orchestration layer
(dashboard.services.workspace_launcher). tmux is fully mocked -- no real
tmux session is ever created, and exec_attach is monkeypatched so nothing
ever replaces the test process.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from dashboard.models import LaunchRequest, PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services import workspace_launcher as launcher_module
from dashboard.services.pane_commands import PaneLaunchPlan
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request


def _request(tmp_path: Path, *panes: PaneSpec) -> LaunchRequest:
    workspace = WorkspaceSpec(
        project_name="demo",
        project_path=tmp_path,
        session_name="demo",
        windows=(WindowSpec(window_name="main", panes=panes or (_pane(),)),),
    )
    return LaunchRequest(workspace=workspace, init_git=True)


def _pane(kind: PaneKind = PaneKind.BLANK_TERMINAL, name: str | None = None) -> PaneSpec:
    return PaneSpec(kind=kind, display_name=name or kind.value)


def test_refuses_to_touch_an_existing_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    monkeypatch.setattr(launcher_module.tmux, "exec_attach", lambda argv: exec_calls.append(argv))

    execute_launch_request(_request(tmp_path))

    assert len(build_calls) == 1
    assert exec_calls == [["tmux", "attach-session", "-t", "demo"]]


def test_reports_pane_warnings_before_attaching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher_module.tmux, "session_exists", lambda name: False)
    monkeypatch.setattr(
        launcher_module.tmux, "create_workspace_session", lambda workspace, pane_plans: None
    )
    monkeypatch.setattr(launcher_module.tmux, "attach_or_switch_argv", lambda name: ["tmux"])
    monkeypatch.setattr(launcher_module.tmux, "exec_attach", lambda argv: None)
    monkeypatch.setattr(
        launcher_module.pane_commands,
        "plan_for_pane",
        lambda pane, path: PaneLaunchPlan(
            startup_command=None, pane_title=None, warning="Neovim was not found"
        ),
    )

    out = io.StringIO()
    execute_launch_request(_request(tmp_path), out=out)

    assert "Neovim was not found" in out.getvalue()


def test_build_pane_plans_keys_by_window_and_pane_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = WorkspaceSpec(
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
