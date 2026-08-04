"""Tests for project-discovery configuration persistence
(dashboard.services.projects_config_store).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.models.projects_config import ProjectsConfig
from dashboard.services.load_result import LoadSource
from dashboard.services.projects_config_store import (
    PROJECTS_CONFIG_SCHEMA_VERSION,
    default_projects_config_path,
    load_projects_config,
    load_projects_config_result,
    save_projects_config,
)


def test_atomic_rotation_and_backup_recovery(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    first = ProjectsConfig(roots=(tmp_path / "one",))
    second = ProjectsConfig(roots=(tmp_path / "two",))
    save_projects_config(first, path)
    first_bytes = path.read_bytes()
    save_projects_config(second, path)
    assert Path(f"{path}.bak").read_bytes() == first_bytes
    path.write_text("broken")
    result = load_projects_config_result(path)
    assert result.value == first
    assert result.source is LoadSource.BACKUP
    assert result.warning


def test_missing_primary_does_not_resurrect_backup(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    save_projects_config(ProjectsConfig(roots=(tmp_path / "one",)), path)
    path.rename(Path(f"{path}.bak"))
    result = load_projects_config_result(path)
    assert result.value == ProjectsConfig()
    assert result.source is LoadSource.DEFAULT


def test_future_primary_does_not_fall_back_or_allow_save(tmp_path: Path) -> None:
    path = tmp_path / "projects.json"
    path.write_text(json.dumps({"schema_version": 99, "config": {}}))
    Path(f"{path}.bak").write_text(
        json.dumps({"schema_version": 1, "config": ProjectsConfig().to_dict()})
    )
    before = path.read_bytes()
    result = load_projects_config_result(path)
    assert result.source is LoadSource.DEFAULT
    assert result.unsupported_version
    with pytest.raises(ValueError, match="newer schema"):
        save_projects_config(ProjectsConfig(), path)
    assert path.read_bytes() == before


def test_load_missing_file_returns_defaults(tmp_path: Path) -> None:
    assert load_projects_config(tmp_path / "projects.json") == ProjectsConfig()


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"
    config = ProjectsConfig(
        roots=(tmp_path / "a",), max_depth=2, manual_projects=(tmp_path / "m",)
    )

    save_projects_config(config, config_path=config_path)
    loaded = load_projects_config(config_path=config_path)

    assert loaded == config


def test_save_creates_parent_directories(tmp_path: Path) -> None:
    config_path = tmp_path / "does" / "not" / "exist" / "projects.json"

    save_projects_config(ProjectsConfig(), config_path=config_path)

    assert config_path.exists()


def test_save_writes_current_schema_version_envelope(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"

    save_projects_config(ProjectsConfig(), config_path=config_path)

    on_disk = json.loads(config_path.read_text())
    assert on_disk["schema_version"] == PROJECTS_CONFIG_SCHEMA_VERSION
    assert "config" in on_disk


def test_load_malformed_json_returns_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"
    config_path.write_text("{not valid json")

    assert load_projects_config(config_path=config_path) == ProjectsConfig()


def test_load_json_that_is_not_an_object_returns_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"
    config_path.write_text("[1, 2, 3]")

    assert load_projects_config(config_path=config_path) == ProjectsConfig()


def test_load_envelope_missing_config_key_returns_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"
    config_path.write_text(json.dumps({"schema_version": 1}))

    assert load_projects_config(config_path=config_path) == ProjectsConfig()


@pytest.mark.parametrize(
    "bad_field",
    [
        {"max_depth": "not-a-number"},
        {"max_depth": 0},
        {"max_directories": -1},
        {"roots": "not-a-list"},
        {"excluded_names": 5},
    ],
)
def test_load_malformed_individual_field_returns_defaults(
    tmp_path: Path, bad_field: dict[str, object]
) -> None:
    config_path = tmp_path / "projects.json"
    base = ProjectsConfig().to_dict()
    base.update(bad_field)
    config_path.write_text(json.dumps({"schema_version": 1, "config": base}))

    assert load_projects_config(config_path=config_path) == ProjectsConfig()


def test_load_unsupported_future_schema_version_returns_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": PROJECTS_CONFIG_SCHEMA_VERSION + 1,
                "config": ProjectsConfig(max_depth=7).to_dict(),
            }
        )
    )

    # Not silently reinterpreted as the current version -- a config this
    # build doesn't recognize is treated the same as "nothing saved".
    assert load_projects_config(config_path=config_path) == ProjectsConfig()


def test_load_expands_home_in_saved_roots(tmp_path: Path) -> None:
    config_path = tmp_path / "projects.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "config": {
                    "roots": ["~/somewhere"],
                    "excluded_names": [],
                    "max_depth": 1,
                    "manual_projects": ["~/manual"],
                    "max_directories": 10,
                },
            }
        )
    )

    loaded = load_projects_config(config_path=config_path)

    assert loaded.roots == (Path("~/somewhere").expanduser(),)
    assert loaded.manual_projects == (Path("~/manual").expanduser(),)


def test_default_projects_config_path_uses_xdg_config_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert default_projects_config_path() == tmp_path / "terminal-home" / "projects.json"


def test_default_projects_config_path_falls_back_to_dot_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    expected = tmp_path / ".config" / "terminal-home" / "projects.json"
    assert default_projects_config_path() == expected
