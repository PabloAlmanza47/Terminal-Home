"""Tests for the project selector (dashboard.services.project_selection),
the one place a CLI selector string (a name or a path) is turned into
exactly one discovered Project.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.models import RemoteProjectRegistration, SshProjectLocation
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import ssh as ssh_module
from dashboard.services.project_selection import (
    RegisteredRemoteProject,
    list_selectable_projects,
    resolve_project_selector,
)
from dashboard.services.remote_project_store import create_remote_project


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


def test_terminal_home_is_a_normal_discovered_project_and_selector(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "roots", ["terminal-home", "node_modules"])
    config = ProjectsConfig(roots=(root,))

    selectable = list_selectable_projects(config)
    assert [project.name for project in selectable] == ["terminal-home"]
    result = resolve_project_selector("terminal-home", config=config)
    assert result.ok and result.project is not None
    assert result.project.path == root / "terminal-home"


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


def test_resolves_dot_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "alpha"
    project.mkdir()
    monkeypatch.chdir(project)
    result = resolve_project_selector(".", config=ProjectsConfig(roots=()))
    assert result.ok
    assert result.project is not None
    assert result.project.path == project.resolve()


def test_resolves_tilde_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "home" / "alpha"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = resolve_project_selector("~/alpha", config=ProjectsConfig(roots=()))
    assert result.ok
    assert result.project is not None
    assert result.project.path == project.resolve()


def test_explicit_path_outside_configured_roots_is_ad_hoc(tmp_path: Path) -> None:
    configured = _make_root(tmp_path, "configured", [])
    outside = _make_root(tmp_path, "outside", ["demo"]) / "demo"
    result = resolve_project_selector(
        str(outside), config=ProjectsConfig(roots=(configured,))
    )
    assert result.ok
    assert result.project is not None
    assert result.project.path == outside.resolve()


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


def _remote_project(
    project_id: str, host_id: str, name: str, path: str
) -> RemoteProjectRegistration:
    return RemoteProjectRegistration(project_id, host_id, name, path)


def test_registered_remote_projects_are_in_combined_selection_data(tmp_path: Path) -> None:
    store = tmp_path / "remote-projects.json"
    registration = _remote_project(
        "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
        "d84aeefb-7c29-4c63-b39c-766d559df977",
        "remote-api",
        "/srv/Project With Spaces",
    )
    create_remote_project(registration, store)

    projects = list_selectable_projects(ProjectsConfig(roots=()), remote_store_path=store)

    assert len(projects) == 1
    project = projects[0]
    assert isinstance(project, RegisteredRemoteProject)
    assert project.location == SshProjectLocation(registration.host_id, registration.remote_path)
    assert isinstance(project.location.remote_path, str)
    assert project.selector == (
        "ssh:d84aeefb-7c29-4c63-b39c-766d559df977:/srv/Project With Spaces"
    )


def test_local_and_remote_same_name_are_ambiguous(tmp_path: Path) -> None:
    root = _make_root(tmp_path, "roots", ["demo"])
    store = tmp_path / "remote-projects.json"
    create_remote_project(
        _remote_project(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            "d84aeefb-7c29-4c63-b39c-766d559df977",
            "demo",
            "/srv/demo",
        ),
        store,
    )

    result = resolve_project_selector(
        "demo", config=ProjectsConfig(roots=(root,)), remote_store_path=store
    )

    assert not result.ok
    assert len(result.candidates) == 2
    assert result.error is not None
    assert "ssh:" in result.error


def test_duplicate_remote_names_are_distinguishable_by_selector(tmp_path: Path) -> None:
    store = tmp_path / "remote-projects.json"
    first = _remote_project(
        "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
        "d84aeefb-7c29-4c63-b39c-766d559df977",
        "demo",
        "/srv/one",
    )
    second = _remote_project(
        "6cd81f5d-9fe4-4c32-b17f-f88e5db754f4",
        "760525f1-fdc9-49a7-99fa-2ff90f324bd9",
        "demo",
        "/srv/two",
    )
    create_remote_project(first, store)
    create_remote_project(second, store)

    ambiguous = resolve_project_selector(
        "demo", config=ProjectsConfig(roots=()), remote_store_path=store
    )
    selected = resolve_project_selector(
        "ssh:760525f1-fdc9-49a7-99fa-2ff90f324bd9:/srv/two",
        config=ProjectsConfig(roots=()),
        remote_store_path=store,
    )

    assert not ambiguous.ok
    assert len(ambiguous.candidates) == 2
    assert selected.ok
    assert isinstance(selected.project, RegisteredRemoteProject)
    assert selected.project.location.remote_path == "/srv/two"


def test_missing_host_registration_remains_visible_and_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "remote-projects.json"
    registration = _remote_project(
        "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
        "d84aeefb-7c29-4c63-b39c-766d559df977",
        "orphan",
        "/srv/orphan",
    )
    create_remote_project(registration, store)
    monkeypatch.setattr(
        ssh_module,
        "run_ssh_command",
        lambda *args, **kwargs: pytest.fail("selection must not connect over SSH"),
    )

    result = resolve_project_selector(
        "orphan", config=ProjectsConfig(roots=()), remote_store_path=store
    )

    assert result.ok
    assert isinstance(result.project, RegisteredRemoteProject)
    assert result.project.location.host_id == registration.host_id
    assert result.project.location.remote_path == registration.remote_path
