"""Tests for workspace persistence (dashboard.services.workspace_store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services.workspace_store import (
    default_store_path,
    load_all_workspaces,
    load_workspace,
    save_workspace,
)


def _make_workspace(project_path: Path, name: str = "demo") -> WorkspaceSpec:
    return WorkspaceSpec(
        project_name=name,
        project_path=project_path,
        session_name=name,
        windows=(
            WindowSpec(
                window_name="main",
                panes=(PaneSpec(kind=PaneKind.CODE_EDITOR, display_name="Code Editor"),),
            ),
        ),
    )


def test_save_and_load_workspace_round_trips(tmp_path: Path) -> None:
    store_path = tmp_path / "store" / "workspaces.json"
    project_path = tmp_path / "projects" / "demo"
    project_path.mkdir(parents=True)
    workspace = _make_workspace(project_path)

    save_workspace(workspace, store_path=store_path)
    loaded = load_workspace(project_path, store_path=store_path)

    assert loaded == workspace


def test_save_workspace_creates_parent_directories(tmp_path: Path) -> None:
    store_path = tmp_path / "does" / "not" / "exist" / "workspaces.json"
    workspace = _make_workspace(tmp_path / "demo")

    save_workspace(workspace, store_path=store_path)

    assert store_path.exists()


def test_load_workspace_missing_returns_none(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    assert load_workspace(tmp_path / "nowhere", store_path=store_path) is None


def test_save_workspace_keys_by_canonical_project_path(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    project_path.mkdir()
    workspace = _make_workspace(project_path)

    save_workspace(workspace, store_path=store_path)

    # A differently-spelled but equivalent path resolves to the same entry.
    unresolved = tmp_path / "." / "demo"
    assert load_workspace(unresolved, store_path=store_path) == workspace


def test_save_workspace_merges_with_existing_entries(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    first = _make_workspace(tmp_path / "first", name="first")
    second = _make_workspace(tmp_path / "second", name="second")

    save_workspace(first, store_path=store_path)
    save_workspace(second, store_path=store_path)

    all_workspaces = load_all_workspaces(store_path=store_path)
    assert len(all_workspaces) == 2
    assert load_workspace(tmp_path / "first", store_path=store_path) == first
    assert load_workspace(tmp_path / "second", store_path=store_path) == second


def test_load_all_workspaces_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    assert load_all_workspaces(store_path=tmp_path / "missing.json") == {}


def test_load_all_workspaces_handles_invalid_json(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    store_path.write_text("{not valid json")

    assert load_all_workspaces(store_path=store_path) == {}


def test_load_all_workspaces_handles_json_that_is_not_an_object(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    store_path.write_text("[1, 2, 3]")

    assert load_all_workspaces(store_path=store_path) == {}


def test_load_all_workspaces_skips_malformed_entries(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    good = _make_workspace(tmp_path / "good", name="good")
    save_workspace(good, store_path=store_path)

    import json

    data = json.loads(store_path.read_text())
    data["/some/bad/path"] = {"project_name": "bad"}  # missing required fields
    data["/another/bad/path"] = "not-a-dict-at-all"
    store_path.write_text(json.dumps(data))

    workspaces = load_all_workspaces(store_path=store_path)
    assert len(workspaces) == 1
    assert load_workspace(tmp_path / "good", store_path=store_path) == good


def test_default_store_path_uses_xdg_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert default_store_path() == tmp_path / "terminal-home" / "workspaces.json"


def test_default_store_path_falls_back_to_local_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".local" / "share" / "terminal-home" / "workspaces.json"
    assert default_store_path() == expected
