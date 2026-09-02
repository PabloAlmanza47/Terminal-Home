"""Tests for per-project remembered tmux pane layouts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.models import LocalProjectLocation, SshProjectLocation
from dashboard.services.atomic_file import backup_path_for
from dashboard.services.pane_layout_store import (
    PaneLayout,
    PaneLayoutStoreVersionError,
    forget_pane_layouts_for_location,
    has_saved_pane_layouts,
    load_pane_layouts_for_location,
    pane_layout_storage_key,
    save_pane_layouts_for_location,
)


def _layouts(*names: str) -> dict[str, PaneLayout]:
    return {name: PaneLayout(name, 2, f"custom-{name}") for name in names}


def test_local_round_trip_and_canonical_key(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    project = tmp_path / "project"
    save_pane_layouts_for_location(LocalProjectLocation(project), _layouts("main"), path)

    assert load_pane_layouts_for_location(
        LocalProjectLocation(tmp_path / "." / "project"), path
    ) == _layouts("main")
    assert pane_layout_storage_key(LocalProjectLocation(project)).startswith("loc-")


def test_ssh_round_trip_and_multiple_windows_are_independent(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    first = SshProjectLocation("c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", "/srv/api")
    second = SshProjectLocation("d84aeefb-7c29-4ebc-63b9-766d559df977", "/srv/api")
    save_pane_layouts_for_location(first, _layouts("main", "tests"), path)
    save_pane_layouts_for_location(second, _layouts("main"), path)

    assert load_pane_layouts_for_location(first, path) == _layouts("main", "tests")
    assert load_pane_layouts_for_location(second, path) == _layouts("main")


def test_update_and_forget_project_layouts(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    location = LocalProjectLocation(tmp_path / "project")
    save_pane_layouts_for_location(location, _layouts("main", "tests"), path)
    save_pane_layouts_for_location(location, _layouts("main"), path)
    assert load_pane_layouts_for_location(location, path) == _layouts("main")
    assert forget_pane_layouts_for_location(location, path)
    assert not load_pane_layouts_for_location(location, path)
    assert not forget_pane_layouts_for_location(location, path)


def test_invalid_records_are_skipped_without_mutating_reads(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    path.write_text(json.dumps({"schema_version": 1, "projects": {
        "bad": {"project_location": {}, "layouts": {}},
    }}))
    before = path.read_bytes()
    assert load_pane_layouts_for_location(LocalProjectLocation(tmp_path / "project"), path) == {}
    assert path.read_bytes() == before


def test_corrupt_primary_recovers_from_backup(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    location = LocalProjectLocation(tmp_path / "project")
    save_pane_layouts_for_location(location, _layouts("main"), path)
    backup_path_for(path).write_bytes(path.read_bytes())
    path.write_text("broken")
    assert load_pane_layouts_for_location(location, path) == _layouts("main")


def test_future_schema_is_preserved_and_rejected_on_write(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    original = b'{"schema_version": 99, "projects": {}}'
    path.write_bytes(original)
    with pytest.raises(PaneLayoutStoreVersionError):
        save_pane_layouts_for_location(
            LocalProjectLocation(tmp_path / "project"), _layouts("main"), path
        )
    assert path.read_bytes() == original


def test_has_saved_layouts_is_read_only_and_location_aware(tmp_path: Path) -> None:
    path = tmp_path / "pane-layouts.json"
    local = LocalProjectLocation(tmp_path / "local")
    remote = SshProjectLocation("c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", "/srv/local")
    save_pane_layouts_for_location(local, _layouts("main"), path)
    before = path.read_bytes()

    assert has_saved_pane_layouts(local, path)
    assert not has_saved_pane_layouts(remote, path)
    assert path.read_bytes() == before
