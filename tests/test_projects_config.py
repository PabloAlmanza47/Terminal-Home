"""Tests for the project-discovery configuration model
(dashboard.models.projects_config.ProjectsConfig).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models.projects_config import (
    DEFAULT_EXCLUDED_NAMES,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_DIRECTORIES,
    DEFAULT_ROOT,
    ProjectsConfig,
    ProjectsConfigValidationError,
)


def test_defaults() -> None:
    config = ProjectsConfig()
    assert config.roots == (DEFAULT_ROOT,)
    assert config.excluded_names == DEFAULT_EXCLUDED_NAMES
    assert config.max_depth == DEFAULT_MAX_DEPTH
    assert config.manual_projects == ()
    assert config.max_directories == DEFAULT_MAX_DIRECTORIES


def test_default_excluded_names_cover_common_expensive_directories() -> None:
    for name in (".git", "node_modules", ".venv", "venv", "__pycache__"):
        assert name in DEFAULT_EXCLUDED_NAMES
    assert "terminal-home" not in DEFAULT_EXCLUDED_NAMES


def test_round_trip_to_dict_from_dict(tmp_path: Path) -> None:
    config = ProjectsConfig(
        roots=(tmp_path / "a", tmp_path / "b"),
        excluded_names=frozenset({"node_modules", "dist"}),
        max_depth=3,
        manual_projects=(tmp_path / "manual",),
        max_directories=100,
    )

    restored = ProjectsConfig.from_dict(config.to_dict())

    assert restored == config


def test_from_dict_expands_home_in_roots_and_manual_projects() -> None:
    config = ProjectsConfig.from_dict(
        {
            "roots": ["~/somewhere"],
            "excluded_names": [],
            "max_depth": 1,
            "manual_projects": ["~/manual-project"],
            "max_directories": 10,
        }
    )

    assert config.roots == (Path("~/somewhere").expanduser(),)
    assert config.manual_projects == (Path("~/manual-project").expanduser(),)


def test_to_dict_sorts_excluded_names_deterministically() -> None:
    config = ProjectsConfig(excluded_names=frozenset({"zeta", "alpha", "mid"}))
    assert config.to_dict()["excluded_names"] == ["alpha", "mid", "zeta"]


@pytest.mark.parametrize("max_depth", [0, -1, -100])
def test_invalid_max_depth_raises(max_depth: int) -> None:
    with pytest.raises(ProjectsConfigValidationError):
        ProjectsConfig(max_depth=max_depth)


@pytest.mark.parametrize("max_directories", [0, -1, -100])
def test_invalid_max_directories_raises(max_directories: int) -> None:
    with pytest.raises(ProjectsConfigValidationError):
        ProjectsConfig(max_directories=max_directories)


def test_valid_boundary_values_do_not_raise() -> None:
    ProjectsConfig(max_depth=1, max_directories=1)  # must not raise


@pytest.mark.parametrize(
    "bad_data",
    [
        {
            "roots": "not-a-list",
            "excluded_names": [],
            "max_depth": 1,
            "manual_projects": [],
            "max_directories": 10,
        },
        {
            "roots": [],
            "excluded_names": "not-a-list",
            "max_depth": 1,
            "manual_projects": [],
            "max_directories": 10,
        },
        {
            "roots": [],
            "excluded_names": [],
            "max_depth": 1,
            "manual_projects": "not-a-list",
            "max_directories": 10,
        },
        {
            "roots": [],
            "excluded_names": [],
            "max_depth": "not-a-number",
            "manual_projects": [],
            "max_directories": 10,
        },
    ],
)
def test_from_dict_rejects_wrong_shaped_fields(bad_data: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        ProjectsConfig.from_dict(bad_data)


def test_from_dict_rejects_invalid_depth_via_post_init() -> None:
    with pytest.raises(ProjectsConfigValidationError):
        ProjectsConfig.from_dict(
            {
                "roots": [],
                "excluded_names": [],
                "max_depth": 0,
                "manual_projects": [],
                "max_directories": 10,
            }
        )
