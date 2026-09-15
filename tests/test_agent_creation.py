from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from dashboard.services.agent_creation import (
    AgentCreationMode,
    AgentCreationRequest,
    create_agent,
    validate_agent_creation_request,
)


def _status_result(dirty: bool = False) -> subprocess.CompletedProcess[str]:
    output = "# branch.head main\0"
    if dirty:
        output += "? changed.txt\0"
    return subprocess.CompletedProcess(["git"], 0, output, "")


def _inspection_output(source: Path, state: Mapping[str, object]) -> str:
    output = f"worktree {source}\nHEAD source\nbranch refs/heads/main\n"
    if state.get("created") and not state.get("removed"):
        output += (
            "\nworktree " + str(state["target_path"]) + "\nHEAD new\nbranch refs/heads/feature\n"
        )
    return output


def _request(
    source: Path,
    mode: AgentCreationMode,
    *,
    branch_name: str | None = None,
    worktree_path: Path | None = None,
) -> AgentCreationRequest:
    return AgentCreationRequest(
        source,
        mode,
        "Fix: special [task]",
        "codex",
        "Keep /tmp/a b",
        branch_name,
        worktree_path,
    )


def test_current_checkout_creates_one_agent_without_git_mutation(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def status(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _status_result()

    def inspect(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _inspection_output(tmp_path, {}), "")

    def agent(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"success": True, "id": "agent-1"}), ""
        )

    result = create_agent(
        _request(tmp_path, AgentCreationMode.CURRENT_CHECKOUT),
        status_runner=status,
        inspection_runner=inspect,
        agent_runner=agent,
    )

    assert result.success is True
    assert result.session_id == "agent-1"
    assert result.resolved_path == tmp_path.resolve()
    assert not any(
        "worktree" in call for call in calls if call and call[0] == "git" and "add" in call
    )
    assert calls[-1][0:4] == ["agent-deck", "launch", str(tmp_path.resolve()), "--title"]


def test_dirty_checkout_is_rejected_before_agent_deck(tmp_path: Path) -> None:
    agent_called = False

    def agent(_: list[str]) -> subprocess.CompletedProcess[str]:
        nonlocal agent_called
        agent_called = True
        return subprocess.CompletedProcess([], 0, "{}", "")

    result = create_agent(
        _request(tmp_path, AgentCreationMode.CURRENT_CHECKOUT),
        status_runner=lambda _: _status_result(True),
        inspection_runner=lambda _: subprocess.CompletedProcess([], 0, "", ""),
        agent_runner=agent,
    )

    assert result.success is False
    assert result.error == "Agent creation requires a clean working tree"
    assert agent_called is False


def test_dirty_source_is_allowed_for_new_worktree_and_uses_head(tmp_path: Path) -> None:
    target = tmp_path / "agent-worktree"
    state: dict[str, bool | Path] = {
        "created": False,
        "removed": False,
        "target_path": target.resolve(),
    }
    calls: list[list[str]] = []

    def inspect(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _inspection_output(tmp_path, state), "")

    def mutate(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "add" in argv:
            state["created"] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def status(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _status_result(dirty=True)

    result = create_agent(
        _request(
            tmp_path,
            AgentCreationMode.NEW_WORKTREE,
            branch_name="feature",
            worktree_path=target,
        ),
        status_runner=status,
        inspection_runner=inspect,
        mutation_runner=mutate,
        agent_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, json.dumps({"success": True, "id": "dirty-new"}), ""
        ),
    )

    assert result.success is True
    assert result.warning and "will not be included" in result.warning
    add_call = next(call for call in calls if "add" in call)
    assert add_call[-1] == "HEAD"
    assert not any(command in add_call for command in ("stash", "commit", "reset"))


def test_new_worktree_passes_exact_canonical_path_and_never_deletes_branch(tmp_path: Path) -> None:
    target = tmp_path / "agent worktree"
    state: dict[str, bool | Path] = {
        "created": False,
        "removed": False,
        "target_path": target.resolve(),
    }
    calls: list[list[str]] = []

    def status(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _status_result()

    def inspect(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, _inspection_output(tmp_path, state), "")

    def mutate(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "worktree" in argv and "add" in argv:
            state["created"] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def agent(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"success": True, "id": "new-1"}), ""
        )

    result = create_agent(
        _request(
            tmp_path,
            AgentCreationMode.NEW_WORKTREE,
            branch_name="feature",
            worktree_path=target,
        ),
        status_runner=status,
        inspection_runner=inspect,
        mutation_runner=mutate,
        agent_runner=agent,
    )

    assert result.success is True
    assert result.session_id == "new-1"
    assert result.worktree_path == target.resolve()
    assert calls[-1][2] == str(target.resolve())
    assert not any("branch" in call and "delete" in call for call in calls)


def test_existing_target_is_rejected_without_mutation(tmp_path: Path) -> None:
    target = tmp_path / "already-there"
    target.mkdir()
    result = create_agent(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, _inspection_output(tmp_path, {}), ""
        ),
        mutation_runner=lambda argv: subprocess.CompletedProcess(argv, 0, "", ""),
    )
    assert result.success is False
    assert "already exists" in (result.error or "")


def test_agent_deck_failure_attempts_only_safe_cleanup(tmp_path: Path) -> None:
    target = tmp_path / "agent-worktree"
    state: dict[str, bool | Path] = {
        "created": False,
        "removed": False,
        "target_path": target.resolve(),
    }
    mutations: list[list[str]] = []

    def mutate(argv: list[str]) -> subprocess.CompletedProcess[str]:
        mutations.append(argv)
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "add" in argv:
            state["created"] = True
            target.mkdir()
        if "remove" in argv:
            state["removed"] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    def inspect(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, _inspection_output(tmp_path, state), "")

    result = create_agent(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=inspect,
        mutation_runner=mutate,
        agent_runner=lambda argv: subprocess.CompletedProcess(argv, 1, "", "failed"),
    )

    assert result.success is False
    assert result.cleanup_attempted is True
    assert result.cleanup_succeeded is True
    assert any("remove" in call for call in mutations)
    assert not any("branch" in call and "delete" in call for call in mutations)


def test_missing_managed_parents_are_created_but_target_is_left_to_git(
    tmp_path: Path, monkeypatch: object
) -> None:
    import dashboard.services.agent_creation as creation

    managed_root = tmp_path / "managed"
    target = managed_root / "project" / "task"
    monkeypatch.setattr(creation, "default_agent_worktree_root", lambda: managed_root)
    state: dict[str, bool | Path] = {"created": False, "removed": False, "target_path": target}

    def mutate(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "add" in argv:
            assert not target.exists()
            state["created"] = True
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = create_agent(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, _inspection_output(tmp_path, state), ""
        ),
        mutation_runner=mutate,
        agent_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, json.dumps({"success": True, "id": "managed-1"}), ""
        ),
    )

    assert result.success is True
    assert state["created"] is True
    assert target.parent.is_dir()


def test_missing_external_parent_is_rejected(tmp_path: Path, monkeypatch: object) -> None:
    import dashboard.services.agent_creation as creation

    monkeypatch.setattr(creation, "default_agent_worktree_root", lambda: tmp_path / "managed")
    target = tmp_path / "external" / "missing" / "task"
    result = validate_agent_creation_request(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, _inspection_output(tmp_path, {}), ""
        ),
        mutation_runner=lambda argv: subprocess.CompletedProcess(
            argv, 1 if "show-ref" in argv else 0, "", ""
        ),
    )
    assert result.success is False
    assert "parent directory does not exist" in (result.error or "")


def test_managed_path_escape_is_rejected(tmp_path: Path, monkeypatch: object) -> None:
    import dashboard.services.agent_creation as creation

    managed_root = tmp_path / "managed"
    monkeypatch.setattr(creation, "default_agent_worktree_root", lambda: managed_root)
    target = managed_root / ".." / "outside" / "task"
    result = validate_agent_creation_request(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, _inspection_output(tmp_path, {}), ""
        ),
        mutation_runner=lambda argv: subprocess.CompletedProcess(
            argv, 1 if "show-ref" in argv else 0, "", ""
        ),
    )
    assert result.success is False
    assert "parent directory does not exist" in (result.error or "")


def test_managed_parent_cleanup_preserves_preexisting_directories_on_git_failure(
    tmp_path: Path, monkeypatch: object
) -> None:
    import dashboard.services.agent_creation as creation

    managed_root = tmp_path / "managed"
    existing = managed_root / "project"
    existing.mkdir(parents=True)
    target = existing / "task" / "worktree"
    monkeypatch.setattr(creation, "default_agent_worktree_root", lambda: managed_root)

    def mutate(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "add" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "cannot add")
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = create_agent(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, _inspection_output(tmp_path, {}), ""
        ),
        mutation_runner=mutate,
    )

    assert result.success is False
    assert existing.is_dir()
    assert not target.exists()


def test_failed_worktree_add_removes_only_new_empty_managed_parents(
    tmp_path: Path, monkeypatch: object
) -> None:
    import dashboard.services.agent_creation as creation

    managed_root = tmp_path / "managed"
    target = managed_root / "project" / "task" / "worktree"
    monkeypatch.setattr(creation, "default_agent_worktree_root", lambda: managed_root)

    def mutate(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if "show-ref" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if "add" in argv:
            assert target.parent.is_dir()
            return subprocess.CompletedProcess(argv, 1, "", "cannot add")
        return subprocess.CompletedProcess(argv, 0, "", "")

    result = create_agent(
        _request(
            tmp_path, AgentCreationMode.NEW_WORKTREE, branch_name="feature", worktree_path=target
        ),
        status_runner=lambda _: _status_result(),
        inspection_runner=lambda argv: subprocess.CompletedProcess(
            argv, 0, _inspection_output(tmp_path, {}), ""
        ),
        mutation_runner=mutate,
    )

    assert result.success is False
    assert not managed_root.exists()
