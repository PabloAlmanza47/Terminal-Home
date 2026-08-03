"""Tests for workspace persistence (dashboard.services.workspace_store)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services.workspace_store import (
    WORKSPACE_STORE_SCHEMA_VERSION,
    WorkspaceStoreVersionError,
    default_store_path,
    ensure_workspace_store_writable,
    forget_workspace,
    load_all_workspaces,
    load_workspace,
    load_workspace_result,
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
    data["workspaces"]["/some/bad/path"] = {"project_name": "bad"}  # missing required fields
    data["workspaces"]["/another/bad/path"] = "not-a-dict-at-all"
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


# --- load_workspace_result: distinguishing "absent" from "malformed" -----------


def test_load_workspace_result_absent_has_no_error(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    result = load_workspace_result(tmp_path / "nowhere", store_path=store_path)
    assert result.workspace is None
    assert result.error is None


def test_load_workspace_result_returns_saved_workspace(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    project_path.mkdir()
    workspace = _make_workspace(project_path)
    save_workspace(workspace, store_path=store_path)

    result = load_workspace_result(project_path, store_path=store_path)

    assert result.workspace == workspace
    assert result.error is None


def test_load_workspace_result_reports_malformed_entry(tmp_path: Path) -> None:
    import json

    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path.write_text(json.dumps({str(project_path.resolve()): {"project_name": "bad"}}))

    result = load_workspace_result(project_path, store_path=store_path)

    assert result.workspace is None
    assert result.error is not None


def test_load_workspace_result_reports_non_dict_entry(tmp_path: Path) -> None:
    import json

    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path.write_text(json.dumps({str(project_path.resolve()): "not-a-dict"}))

    result = load_workspace_result(project_path, store_path=store_path)

    assert result.workspace is None
    assert result.error is not None


# --- forget_workspace -----------------------------------------------------------


def test_forget_workspace_removes_only_that_entry(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    keep = _make_workspace(tmp_path / "keep", name="keep")
    forget = _make_workspace(tmp_path / "forget", name="forget")
    save_workspace(keep, store_path=store_path)
    save_workspace(forget, store_path=store_path)

    removed = forget_workspace(tmp_path / "forget", store_path=store_path)

    assert removed is True
    assert load_workspace(tmp_path / "forget", store_path=store_path) is None
    assert load_workspace(tmp_path / "keep", store_path=store_path) == keep


def test_forget_workspace_missing_entry_returns_false(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    assert forget_workspace(tmp_path / "nowhere", store_path=store_path) is False


def test_forget_workspace_never_touches_project_directory(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    project_path.mkdir()
    (project_path / "keep-me.txt").write_text("still here")
    save_workspace(_make_workspace(project_path), store_path=store_path)

    forget_workspace(project_path, store_path=store_path)

    assert (project_path / "keep-me.txt").exists()


# --- versioned envelope: reading both formats -----------------------------------


def _write_legacy_store(store_path: Path, entries: dict[str, WorkspaceSpec]) -> None:
    """Write *entries* in the pre-versioning flat-dict format, with no
    envelope at all -- exactly what terminal-home wrote before this slice.
    """
    import json

    store_path.parent.mkdir(parents=True, exist_ok=True)
    flat = {str(path): spec.to_dict() for path, spec in entries.items()}
    store_path.write_text(json.dumps(flat, indent=2))


def _write_versioned_store(
    store_path: Path, entries: dict[str, WorkspaceSpec], *, schema_version: int
) -> None:
    import json

    store_path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "schema_version": schema_version,
        "workspaces": {str(path): spec.to_dict() for path, spec in entries.items()},
    }
    store_path.write_text(json.dumps(envelope, indent=2))


def test_load_all_workspaces_reads_legacy_flat_store(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    workspace = _make_workspace(project_path)
    _write_legacy_store(store_path, {project_path.resolve(): workspace})

    assert load_all_workspaces(store_path=store_path) == {str(project_path.resolve()): workspace}


def test_load_all_workspaces_reads_versioned_envelope(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    workspace = _make_workspace(project_path)
    _write_versioned_store(
        store_path,
        {project_path.resolve(): workspace},
        schema_version=WORKSPACE_STORE_SCHEMA_VERSION,
    )

    assert load_all_workspaces(store_path=store_path) == {str(project_path.resolve()): workspace}


def test_load_all_workspaces_skips_malformed_entries_in_legacy_store(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    good = _make_workspace(tmp_path / "good", name="good")
    _write_legacy_store(store_path, {(tmp_path / "good").resolve(): good})

    import json

    data = json.loads(store_path.read_text())
    data["/some/bad/path"] = {"project_name": "bad"}
    store_path.write_text(json.dumps(data))

    workspaces = load_all_workspaces(store_path=store_path)
    assert len(workspaces) == 1
    assert load_workspace(tmp_path / "good", store_path=store_path) == good


# --- versioned envelope: writing always produces the current version ------------


def test_save_workspace_writes_versioned_envelope(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    workspace = _make_workspace(tmp_path / "demo")

    save_workspace(workspace, store_path=store_path)

    import json

    on_disk = json.loads(store_path.read_text())
    assert on_disk["schema_version"] == WORKSPACE_STORE_SCHEMA_VERSION
    assert set(on_disk["workspaces"]) == {str((tmp_path / "demo").resolve())}


def test_loading_a_legacy_store_does_not_rewrite_it(tmp_path: Path) -> None:
    """Migration must only ever happen as a side effect of a real save or
    forget -- never merely because the store was read.
    """
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    workspace = _make_workspace(project_path)
    _write_legacy_store(store_path, {project_path.resolve(): workspace})
    original_text = store_path.read_text()

    load_all_workspaces(store_path=store_path)
    load_workspace(project_path, store_path=store_path)
    load_workspace_result(project_path, store_path=store_path)

    assert store_path.read_text() == original_text


def test_save_workspace_migrates_legacy_store_preserving_existing_entries(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    first = _make_workspace(tmp_path / "first", name="first")
    second = _make_workspace(tmp_path / "second", name="second")
    _write_legacy_store(
        store_path, {(tmp_path / "first").resolve(): first, (tmp_path / "second").resolve(): second}
    )

    third = _make_workspace(tmp_path / "third", name="third")
    save_workspace(third, store_path=store_path)

    import json

    on_disk = json.loads(store_path.read_text())
    assert on_disk["schema_version"] == WORKSPACE_STORE_SCHEMA_VERSION
    assert set(on_disk["workspaces"]) == {
        str((tmp_path / "first").resolve()),
        str((tmp_path / "second").resolve()),
        str((tmp_path / "third").resolve()),
    }
    assert load_workspace(tmp_path / "first", store_path=store_path) == first
    assert load_workspace(tmp_path / "second", store_path=store_path) == second
    assert load_workspace(tmp_path / "third", store_path=store_path) == third


def test_forget_workspace_migrates_legacy_store_preserving_remaining_entries(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    keep = _make_workspace(tmp_path / "keep", name="keep")
    forget = _make_workspace(tmp_path / "forget", name="forget")
    _write_legacy_store(
        store_path, {(tmp_path / "keep").resolve(): keep, (tmp_path / "forget").resolve(): forget}
    )

    removed = forget_workspace(tmp_path / "forget", store_path=store_path)

    assert removed is True
    import json

    on_disk = json.loads(store_path.read_text())
    assert on_disk["schema_version"] == WORKSPACE_STORE_SCHEMA_VERSION
    assert set(on_disk["workspaces"]) == {str((tmp_path / "keep").resolve())}
    assert load_workspace(tmp_path / "keep", store_path=store_path) == keep
    assert load_workspace(tmp_path / "forget", store_path=store_path) is None


# --- load_workspace_result under both formats -------------------------------------


def test_load_workspace_result_returns_saved_workspace_from_versioned_store(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    workspace = _make_workspace(project_path)
    _write_versioned_store(
        store_path,
        {project_path.resolve(): workspace},
        schema_version=WORKSPACE_STORE_SCHEMA_VERSION,
    )

    result = load_workspace_result(project_path, store_path=store_path)

    assert result.workspace == workspace
    assert result.error is None


def test_load_workspace_result_reports_malformed_entry_in_versioned_store(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    _write_versioned_store(store_path, {}, schema_version=WORKSPACE_STORE_SCHEMA_VERSION)

    import json

    data = json.loads(store_path.read_text())
    data["workspaces"][str(project_path.resolve())] = {"project_name": "bad"}
    store_path.write_text(json.dumps(data))

    result = load_workspace_result(project_path, store_path=store_path)

    assert result.workspace is None
    assert result.error is not None


# --- unsupported (newer) schema versions ------------------------------------------


def test_load_workspace_result_unsupported_schema_version_returns_controlled_error(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    future_version = WORKSPACE_STORE_SCHEMA_VERSION + 1
    _write_versioned_store(store_path, {}, schema_version=future_version)

    result = load_workspace_result(project_path, store_path=store_path)

    assert result.workspace is None
    assert result.error is not None
    assert str(future_version) in result.error
    assert "newer" in result.error.lower()


def test_load_all_workspaces_unsupported_schema_version_is_not_silently_treated_as_current(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    project_path = tmp_path / "demo"
    workspace = _make_workspace(project_path)
    future_version = WORKSPACE_STORE_SCHEMA_VERSION + 1
    _write_versioned_store(
        store_path, {project_path.resolve(): workspace}, schema_version=future_version
    )

    # A perfectly valid-looking entry must NOT come back just because its
    # shape happens to match -- an unsupported version is refused outright,
    # never silently reinterpreted as the current one.
    assert load_all_workspaces(store_path=store_path) == {}
    assert load_workspace(project_path, store_path=store_path) is None


def test_save_workspace_refuses_to_silently_overwrite_unsupported_schema_version(
    tmp_path: Path,
) -> None:
    """Saving must not blindly rewrite (and thereby destroy) a store from a
    newer version of Terminal Home it can't interpret.
    """
    store_path = tmp_path / "workspaces.json"
    future_version = WORKSPACE_STORE_SCHEMA_VERSION + 1
    _write_versioned_store(store_path, {}, schema_version=future_version)
    original_text = store_path.read_text()

    with pytest.raises(WorkspaceStoreVersionError):
        save_workspace(_make_workspace(tmp_path / "demo"), store_path=store_path)

    assert store_path.read_text() == original_text


# --- malformed envelopes fail safely -----------------------------------------------


def test_load_all_workspaces_handles_envelope_missing_workspaces_key(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    store_path.write_text('{"schema_version": 1}')

    assert load_all_workspaces(store_path=store_path) == {}


def test_load_all_workspaces_handles_envelope_with_non_int_schema_version(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    store_path.write_text('{"schema_version": "not-a-number", "workspaces": {}}')

    assert load_all_workspaces(store_path=store_path) == {}


# --- ensure_workspace_store_writable: read-only compatibility preflight ---------


def test_ensure_workspace_store_writable_succeeds_for_missing_file(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"

    ensure_workspace_store_writable(store_path=store_path)  # must not raise

    assert not store_path.exists()  # and must not create one either


def test_ensure_workspace_store_writable_succeeds_for_legacy_store(tmp_path: Path) -> None:
    store_path = tmp_path / "workspaces.json"
    workspace = _make_workspace(tmp_path / "demo")
    _write_legacy_store(store_path, {(tmp_path / "demo").resolve(): workspace})
    original_text = store_path.read_text()

    ensure_workspace_store_writable(store_path=store_path)  # must not raise

    assert store_path.read_text() == original_text  # and must not rewrite/migrate it


def test_ensure_workspace_store_writable_succeeds_for_current_version_store(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    workspace = _make_workspace(tmp_path / "demo")
    _write_versioned_store(
        store_path,
        {(tmp_path / "demo").resolve(): workspace},
        schema_version=WORKSPACE_STORE_SCHEMA_VERSION,
    )
    original_text = store_path.read_text()

    ensure_workspace_store_writable(store_path=store_path)  # must not raise

    assert store_path.read_text() == original_text


def test_ensure_workspace_store_writable_raises_for_unsupported_future_version(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "workspaces.json"
    future_version = WORKSPACE_STORE_SCHEMA_VERSION + 1
    _write_versioned_store(store_path, {}, schema_version=future_version)
    original_text = store_path.read_text()

    with pytest.raises(WorkspaceStoreVersionError) as exc_info:
        ensure_workspace_store_writable(store_path=store_path)

    assert str(future_version) in str(exc_info.value)
    assert "newer" in str(exc_info.value).lower()
    assert store_path.read_text() == original_text  # never writes, even when raising


def test_ensure_workspace_store_writable_uses_default_store_path_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    future_version = WORKSPACE_STORE_SCHEMA_VERSION + 1
    _write_versioned_store(default_store_path(), {}, schema_version=future_version)

    with pytest.raises(WorkspaceStoreVersionError):
        ensure_workspace_store_writable()
