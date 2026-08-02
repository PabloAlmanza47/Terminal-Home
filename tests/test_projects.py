"""Tests for project discovery logic (dashboard.services.projects)."""

from __future__ import annotations

from pathlib import Path

from dashboard.services.projects import Project, discover_projects


def _make_tree(tmp_path: Path, dirs: list[str], files: list[str] | None = None) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    for name in dirs:
        (root / name).mkdir()
    for name in files or []:
        (root / name).touch()
    return root


def test_discovers_immediate_child_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "beta"])

    projects = discover_projects(root=root, exclude=set())

    assert [p.name for p in projects] == ["alpha", "beta"]
    assert all(isinstance(p, Project) for p in projects)
    assert projects[0].path == root / "alpha"


def test_excludes_terminal_home_by_default(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "terminal-home", "beta"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["alpha", "beta"]


def test_ignores_files_only_lists_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"], files=["notes.txt", "README.md"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["alpha"]


def test_sorted_case_insensitively(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["Zebra", "apple", "Banana"])

    projects = discover_projects(root=root)

    assert [p.name for p in projects] == ["apple", "Banana", "Zebra"]


def test_missing_root_returns_empty_list(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    assert discover_projects(root=missing) == []


def test_root_that_is_a_file_returns_empty_list(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-directory"
    not_a_dir.touch()

    assert discover_projects(root=not_a_dir) == []


def test_custom_exclude_set(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "beta", "gamma"])

    projects = discover_projects(root=root, exclude={"beta", "gamma"})

    assert [p.name for p in projects] == ["alpha"]
