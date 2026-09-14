from __future__ import annotations

import json
from pathlib import Path

import pytest

import dashboard.services.agent_hub as agent_hub_module
from dashboard.services.agent_deck import (
    AgentDeckSession,
    AgentDeckSnapshot,
    AgentStatus,
    parse_sessions,
)
from dashboard.services.agent_hub import (
    AgentAssociation,
    AgentHubStatus,
    build_agent_hub_snapshot,
    load_agent_hub_snapshot,
    normalize_status,
)
from dashboard.services.projects import Project, ProjectStatus


def _project_status(name: str, path: Path) -> ProjectStatus:
    return ProjectStatus(
        project=Project(name, path),
        canonical_path=path.resolve(),
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name=name,
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )


def _session(
    identifier: str, path: Path, status: AgentStatus = AgentStatus.RUNNING
) -> AgentDeckSession:
    return AgentDeckSession(identifier, identifier, path.resolve(), "codex", status)


def test_agent_session_matches_exact_project_path(tmp_path: Path) -> None:
    project_path = tmp_path / "terminal-home"
    project_path.mkdir()
    status = _project_status("terminal-home", project_path)

    result = build_agent_hub_snapshot(
        AgentDeckSnapshot(True, (_session("agent-1", project_path),)), [status]
    )

    entry = result.entries[0]
    assert entry.association is AgentAssociation.KNOWN_PROJECT
    assert entry.project_name == "terminal-home"
    assert entry.project_path == project_path.resolve()
    assert entry.session_id == "agent-1"


def test_separate_worktrees_match_independently(tmp_path: Path) -> None:
    main_path = tmp_path / "repo"
    worktree_path = tmp_path / "repo-feature"
    main_path.mkdir()
    worktree_path.mkdir()
    statuses = [
        _project_status("repo", main_path),
        _project_status("repo-feature", worktree_path),
    ]

    result = build_agent_hub_snapshot(
        AgentDeckSnapshot(
            True,
            (
                _session("main-agent", main_path),
                _session("feature-agent", worktree_path, AgentStatus.WAITING),
            ),
        ),
        statuses,
    )

    by_id = {entry.session_id: entry for entry in result.entries}
    assert by_id["main-agent"].project_name == "repo"
    assert by_id["feature-agent"].project_name == "repo-feature"
    assert by_id["feature-agent"].association is AgentAssociation.KNOWN_PROJECT


def test_unmatched_existing_path_is_not_assigned_by_name(tmp_path: Path) -> None:
    known_path = tmp_path / "repo"
    unmatched_path = tmp_path / "other" / "repo"
    known_path.mkdir()
    unmatched_path.mkdir(parents=True)

    result = build_agent_hub_snapshot(
        AgentDeckSnapshot(True, (_session("unmatched", unmatched_path),)),
        [_project_status("repo", known_path)],
    )

    entry = result.entries[0]
    assert entry.project_name is None
    assert entry.project_path is None
    assert entry.association is AgentAssociation.UNREGISTERED_PATH


def test_unavailable_agent_deck_is_preserved_without_entries() -> None:
    result = build_agent_hub_snapshot(
        AgentDeckSnapshot(False, warning="Agent Deck unavailable"), []
    )

    assert result.available is False
    assert result.entries == ()
    assert result.warning == "Agent Deck unavailable"


def test_loader_reuses_existing_agent_deck_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = AgentDeckSnapshot(False, warning="not installed")
    monkeypatch.setattr(agent_hub_module, "agent_deck_snapshot", lambda: expected)

    result = load_agent_hub_snapshot([])

    assert result == build_agent_hub_snapshot(expected, [])


def test_empty_session_list_is_available_and_empty() -> None:
    result = build_agent_hub_snapshot(AgentDeckSnapshot(True), [])

    assert result.available is True
    assert result.entries == ()
    assert result.warning is None


def test_malformed_session_data_is_dropped_by_existing_parser() -> None:
    sessions = parse_sessions(
        json.loads(
            json.dumps(
                [
                    {"id": "valid", "path": "/tmp/project", "status": "waiting"},
                    {"path": "/tmp/missing-id", "status": "running"},
                    {"id": "missing-path", "status": "running"},
                    "not a session",
                ]
            )
        )
    )

    assert [session.id for session in sessions] == ["valid"]


@pytest.mark.parametrize(
    ("agent_status", "hub_status"),
    [
        (AgentStatus.RUNNING, AgentHubStatus.WORKING),
        (AgentStatus.WAITING, AgentHubStatus.WAITING),
        (AgentStatus.IDLE, AgentHubStatus.COMPLETED),
        (AgentStatus.STOPPED, AgentHubStatus.UNKNOWN),
        (AgentStatus.ERROR, AgentHubStatus.UNKNOWN),
        (AgentStatus.UNKNOWN, AgentHubStatus.UNKNOWN),
    ],
)
def test_status_normalization(
    agent_status: AgentStatus, hub_status: AgentHubStatus
) -> None:
    assert normalize_status(agent_status) is hub_status
