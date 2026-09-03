from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from dashboard.services.agent_deck import (
    AgentStatus,
    normalize_project_path,
    parse_sessions,
    snapshot,
)


def _result(payload: object, code: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["agent-deck"], code, json.dumps(payload), "")


def test_missing_executable(monkeypatch: pytest.MonkeyPatch) -> None:
    import dashboard.services.agent_deck as module
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert snapshot().available is False


def test_valid_json_and_multiple_sessions_for_one_project(tmp_path: Path) -> None:
    path = str(tmp_path / "project")
    result = snapshot(runner=lambda _: _result([
        {"id": "one", "title": "One", "path": path, "tool": "codex", "status": "running"},
        {"id": "two", "title": "Two", "path": path, "tool": "codex", "status": "waiting"},
    ]))
    assert result.available is True
    assert len(result.sessions) == 2
    assert result.sessions[1].status is AgentStatus.WAITING


def test_malformed_json_timeout_and_nonzero_are_safe() -> None:
    malformed = snapshot(runner=lambda _: subprocess.CompletedProcess([], 0, "{", ""))
    assert malformed.sessions == () and malformed.warning
    timeout = snapshot(runner=lambda _: (_ for _ in ()).throw(subprocess.TimeoutExpired([], 2)))
    assert timeout.warning
    failed = snapshot(runner=lambda _: subprocess.CompletedProcess([], 7, "", "failed"))
    assert failed.warning


def test_malformed_individual_session_is_skipped_and_paths_normalize(tmp_path: Path) -> None:
    path = tmp_path / "project"
    sessions = parse_sessions([
        {"id": "ok", "path": str(path), "tool": "codex", "status": "idle"},
        {"path": str(path), "tool": "codex", "status": "running"},
        "bad",
    ])
    assert len(sessions) == 1
    assert sessions[0].path == normalize_project_path(path)
    assert sessions[0].status is AgentStatus.IDLE


def test_unknown_status_has_internal_fallback() -> None:
    session = parse_sessions([{"id": "x", "path": "/tmp/x", "status": "future-state"}])[0]
    assert session.status is AgentStatus.UNKNOWN
