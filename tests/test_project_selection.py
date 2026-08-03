"""Tests for the project selector (dashboard.services.project_selection),
the one place a CLI selector string (a name or a path) is turned into
exactly one discovered Project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models.projects_config import ProjectsConfig
from dashboard.services.project_selection import resolve_project_selector


def _make_root(tmp_path: Path, name: str, dirs: list[str]) -> Path:
    root = tmp_path / name
    root.mkdir()
    for dir_name in dirs:
        (root / dir_name).mkdir()
    return root


def test_resolves_unique_name(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "roots", ["alpha", "beta"])
    config = ProjectsConfig(roots=(root,))

    result = resolve_project_selector("alpha", config=config)

    assert result.ok
    assert result.project is not None
    assert result.project.name == "alpha"
    assert result.project.path.resolve() == (root / "alpha").resolve()


def test_resolves_unique_name_case_insensitively(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "roots", ["Alpha"])
    config = ProjectsConfig(roots=(root,))

    result = resolve_project_selector("alpha", config=config)

    assert result.ok
    assert result.project is not None
    assert result.project.name == "Alpha"


def test_resolves_absolute_path(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "roots", ["alpha"])
    config = ProjectsConfig(roots=(root,))

    result = resolve_project_selector(str((root / "alpha").resolve()), config=config)

    assert result.ok
    assert result.project is not None
    assert result.project.name == "alpha"


def test_resolves_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_root(tmp_path, "roots", ["alpha"])
    config = ProjectsConfig(roots=(root,))
    monkeypatch.chdir(root)

    result = resolve_project_selector("./alpha", config=config)

    assert result.ok
    assert result.project is not None
    assert result.project.name == "alpha"


def test_missing_project_reports_a_concise_error(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "roots", ["alpha"])
    config = ProjectsConfig(roots=(root,))

    result = resolve_project_selector("nonexistent", config=config)

    assert not result.ok
    assert result.error is not None
    assert "nonexistent" in result.error


def test_missing_path_reports_a_concise_error(tmp_path: Path) -> None:
    config = ProjectsConfig(roots=(tmp_path / "roots",))

    result = resolve_project_selector(str(tmp_path / "does-not-exist"), config=config)

    assert not result.ok
    assert result.error is not None


def test_duplicate_name_is_ambiguous_and_never_picked_arbitrarily(tmp_path: Path) -> None:
    school = _make_root(tmp_path, "school", ["example"])
    work = _make_root(tmp_path, "work", ["example"])
    config = ProjectsConfig(roots=(school, work))

    result = resolve_project_selector("example", config=config)

    assert not result.ok
    assert result.project is None
    assert result.error is not None
    assert 'multiple projects match "example"' in result.error
    assert len(result.candidates) == 2
    paths = {str(p.path.resolve()) for p in result.candidates}
    assert paths == {str((school / "example").resolve()), str((work / "example").resolve())}


def test_exact_path_resolves_a_duplicate_name_safely(tmp_path: Path) -> None:
    school = _make_root(tmp_path, "school", ["example"])
    work = _make_root(tmp_path, "work", ["example"])
    config = ProjectsConfig(roots=(school, work))

    result = resolve_project_selector(str((work / "example").resolve()), config=config)

    assert result.ok
    assert result.project is not None
    assert result.project.path.resolve() == (work / "example").resolve()


def test_duplicate_ambiguity_is_deterministic_regardless_of_root_order(tmp_path: Path) -> None:
    school = _make_root(tmp_path, "school", ["example"])
    work = _make_root(tmp_path, "work", ["example"])

    forward = resolve_project_selector(
        "example", config=ProjectsConfig(roots=(school, work))
    )
    reversed_order = resolve_project_selector(
        "example", config=ProjectsConfig(roots=(work, school))
    )

    assert not forward.ok
    assert not reversed_order.ok
    assert forward.error is not None
    assert reversed_order.error is not None


def test_empty_selector_is_a_concise_error(tmp_path: Path) -> None:
    config = ProjectsConfig(roots=(tmp_path,))

    result = resolve_project_selector("   ", config=config)

    assert not result.ok
    assert result.error is not None
