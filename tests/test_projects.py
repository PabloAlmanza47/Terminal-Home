"""Tests for project discovery and status logic (dashboard.services.projects)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from dashboard.models import (
    LaunchAction,
    LaunchRequest,
    PaneKind,
    PaneSpec,
    WindowSpec,
    WorkspaceSpec,
)
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import projects as projects_module
from dashboard.services.projects import (
    Project,
    ProjectAction,
    ProjectStatus,
    build_launch_request,
    disambiguated_display_names,
    discover_projects,
    format_scan_warnings,
    gather_project_status,
    gather_single_project_status,
    primary_actions,
    project_option_id,
    scan_all_projects,
    secondary_actions,
    status_badge,
)
from dashboard.services.projects_config_store import save_projects_config
from dashboard.services.workspace_store import save_workspace


def _make_tree(tmp_path: Path, dirs: list[str], files: list[str] | None = None) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    for name in dirs:
        (root / name).mkdir()
    for name in files or []:
        (root / name).touch()
    return root


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not permitted in this environment")


def test_discovers_immediate_child_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "beta"])

    result = discover_projects(ProjectsConfig(roots=(root,), excluded_names=frozenset()))

    assert [p.name for p in result.projects] == ["alpha", "beta"]
    assert all(isinstance(p, Project) for p in result.projects)
    assert result.projects[0].path == root / "alpha"
    assert result.truncated is False
    assert result.warnings == ()


def test_excludes_terminal_home_by_default(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "terminal-home", "beta"])

    result = discover_projects(ProjectsConfig(roots=(root,)))

    assert [p.name for p in result.projects] == ["alpha", "beta"]


def test_ignores_files_only_lists_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"], files=["notes.txt", "README.md"])

    result = discover_projects(ProjectsConfig(roots=(root,)))

    assert [p.name for p in result.projects] == ["alpha"]


def test_sorted_case_insensitively(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["Zebra", "apple", "Banana"])

    result = discover_projects(ProjectsConfig(roots=(root,)))

    assert [p.name for p in result.projects] == ["apple", "Banana", "Zebra"]


def test_missing_root_returns_empty_list_with_warning(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    result = discover_projects(ProjectsConfig(roots=(missing,)))

    assert result.projects == ()
    assert result.truncated is False
    assert len(result.warnings) == 1
    assert str(missing) in result.warnings[0]


def test_root_that_is_a_file_returns_empty_list(tmp_path: Path) -> None:
    not_a_dir = tmp_path / "not-a-directory"
    not_a_dir.touch()

    result = discover_projects(ProjectsConfig(roots=(not_a_dir,)))

    assert result.projects == ()
    assert len(result.warnings) == 1


def test_custom_exclude_set(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "beta", "gamma"])

    result = discover_projects(
        ProjectsConfig(roots=(root,), excluded_names=frozenset({"beta", "gamma"}))
    )

    assert [p.name for p in result.projects] == ["alpha"]


def test_excludes_hidden_directories(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", ".git", ".config", "beta"])

    result = discover_projects(ProjectsConfig(roots=(root,)))

    assert [p.name for p in result.projects] == ["alpha", "beta"]


# --- multiple roots --------------------------------------------------------------


def test_multiple_roots_are_all_scanned(tmp_path: Path) -> None:
    root_a = tmp_path / "a" / "projects"
    root_a.mkdir(parents=True)
    (root_a / "alpha").mkdir()
    root_b = tmp_path / "b" / "projects"
    root_b.mkdir(parents=True)
    (root_b / "beta").mkdir()

    result = discover_projects(ProjectsConfig(roots=(root_a, root_b)))

    assert sorted(p.name for p in result.projects) == ["alpha", "beta"]


def test_one_unreadable_root_does_not_block_another(tmp_path: Path) -> None:
    missing = tmp_path / "missing-root"
    real_root = _make_tree(tmp_path, dirs=["alpha"])

    result = discover_projects(ProjectsConfig(roots=(missing, real_root)))

    assert [p.name for p in result.projects] == ["alpha"]
    assert len(result.warnings) == 1
    assert str(missing) in result.warnings[0]


# --- depth-limited discovery ------------------------------------------------------


def test_max_depth_one_only_lists_immediate_children(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    (root / "alpha" / "child").mkdir()

    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=1))

    assert [p.name for p in result.projects] == ["alpha"]


def test_max_depth_two_lists_children_and_grandchildren(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    (root / "alpha" / "child").mkdir()

    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=2))

    assert sorted(p.name for p in result.projects) == ["alpha", "child"]


def test_root_itself_is_never_returned_as_a_project(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])

    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=2))

    assert root not in {p.path for p in result.projects}
    assert root.name not in {p.name for p in result.projects}


# --- exclusions --------------------------------------------------------------------


def test_excluded_directory_is_not_returned_or_traversed(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "node_modules"])
    (root / "node_modules" / "should-not-appear").mkdir()

    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=3))

    names = {p.name for p in result.projects}
    assert names == {"alpha"}
    assert "node_modules" not in names
    assert "should-not-appear" not in names


# --- manual projects ---------------------------------------------------------------


def test_manual_project_outside_all_roots_is_included(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    manual_dir = tmp_path / "elsewhere" / "manual-project"
    manual_dir.mkdir(parents=True)

    result = discover_projects(ProjectsConfig(roots=(root,), manual_projects=(manual_dir,)))

    assert sorted(p.name for p in result.projects) == ["alpha", "manual-project"]


def test_missing_manual_project_is_ignored_safely(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    missing_manual = tmp_path / "does-not-exist"

    result = discover_projects(ProjectsConfig(roots=(root,), manual_projects=(missing_manual,)))

    assert [p.name for p in result.projects] == ["alpha"]


def test_manual_project_that_is_a_file_is_ignored_safely(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    manual_file = tmp_path / "not-a-directory"
    manual_file.touch()

    result = discover_projects(ProjectsConfig(roots=(root,), manual_projects=(manual_file,)))

    assert [p.name for p in result.projects] == ["alpha"]


# --- canonical-path deduplication --------------------------------------------------


def test_duplicate_project_across_two_roots_appears_once(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])

    result = discover_projects(ProjectsConfig(roots=(root, root)))

    assert [p.name for p in result.projects] == ["alpha"]


def test_duplicate_across_scanned_and_manual_prefers_the_scanned_path(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    # A different-looking (but canonically identical) path to the same
    # scanned directory -- reachable without needing real symlinks.
    manual_duplicate = root / "." / "alpha"

    result = discover_projects(ProjectsConfig(roots=(root,), manual_projects=(manual_duplicate,)))

    assert len(result.projects) == 1
    # The scanned entry's clean path wins over the manual registration's.
    assert result.projects[0].path == root / "alpha"


def test_symlink_and_its_real_path_deduplicate_to_one_project(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["real-project"])
    _symlink_or_skip(root / "link-project", root / "real-project")

    result = discover_projects(ProjectsConfig(roots=(root,)))

    # Both "real-project" and "link-project" resolve to the same canonical
    # directory, so only one project is returned for it.
    assert len(result.projects) == 1


def test_manually_registered_symlink_is_included_once(tmp_path: Path) -> None:
    real_dir = tmp_path / "real-project"
    real_dir.mkdir()
    link = tmp_path / "link-to-real"
    _symlink_or_skip(link, real_dir)

    result = discover_projects(ProjectsConfig(roots=(), manual_projects=(link,)))

    assert len(result.projects) == 1
    assert result.projects[0].path == link


# --- symlink safety ------------------------------------------------------------------


def test_symlink_and_real_target_deduplicate_with_first_encountered_name(
    tmp_path: Path,
) -> None:
    """Both aliases of the same real directory resolve to one canonical
    path, so exactly one project is returned for it -- and the documented
    first-encountered-path-wins rule (entries within a root are visited
    alphabetically) decides which of the two supplies the display
    name/path: "link-project" sorts before "real-project".
    """
    root = _make_tree(tmp_path, dirs=["real-project"])
    real_project = root / "real-project"
    _symlink_or_skip(root / "link-project", real_project)

    result = discover_projects(ProjectsConfig(roots=(root,)))

    assert len(result.projects) == 1
    project = result.projects[0]
    assert project.name == "link-project"
    assert project.path.resolve() == real_project.resolve()


def test_symlinked_directory_is_not_recursively_traversed(tmp_path: Path) -> None:
    """A symlinked directory is listed as a project, but discovery never
    walks *through* it -- proven with a nested child that is otherwise
    completely unreachable from any configured root, so there is no
    canonical-path deduplication to muddy the result: if the symlink were
    followed, the child would be the *only* way it could appear at all.
    """
    root = tmp_path / "root"
    root.mkdir()
    outside_target = tmp_path / "outside-target"
    outside_target.mkdir()
    (outside_target / "should-not-be-discovered").mkdir()
    _symlink_or_skip(root / "linked-project", outside_target)

    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=3))

    names = {p.name for p in result.projects}
    assert "linked-project" in names
    assert "should-not-be-discovered" not in names


def test_self_referential_symlink_completes_and_is_not_returned_as_a_second_project(
    tmp_path: Path,
) -> None:
    """A symlink pointing back at its own parent must not cause infinite
    recursion (a large max_depth would recurse forever if symlinks were
    followed -- completing at all, rather than hanging, is what this test
    exercises), and since it resolves to the same canonical directory
    already returned for "loop", it must not be returned a second time
    under its alias "self".
    """
    root = tmp_path / "projects"
    root.mkdir()
    loop_dir = root / "loop"
    loop_dir.mkdir()
    _symlink_or_skip(loop_dir / "self", loop_dir)

    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=10))

    assert [p.name for p in result.projects] == ["loop"]
    assert result.truncated is False


def test_returned_canonical_paths_are_always_unique(tmp_path: Path) -> None:
    """General invariant, regardless of how a duplicate arose (two roots
    listing the same directory, or a symlink alias of it): the final
    result never contains two projects resolving to the same canonical
    path.
    """
    root = _make_tree(tmp_path, dirs=["real-project"])
    _symlink_or_skip(root / "link-project", root / "real-project")

    result = discover_projects(ProjectsConfig(roots=(root, root)))

    canonical_paths = [p.path.resolve() for p in result.projects]
    assert len(canonical_paths) == len(set(canonical_paths))


# --- scan limits ---------------------------------------------------------------------


def test_directory_count_limit_truncates_and_reports_it(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["a", "b", "c", "d", "e"])

    result = discover_projects(ProjectsConfig(roots=(root,), max_directories=3))

    assert len(result.projects) == 3
    assert result.truncated is True
    # The first three, in the same deterministic (alphabetical) order a
    # non-truncated scan would have visited them in.
    assert [p.name for p in result.projects] == ["a", "b", "c"]


def test_directory_count_limit_not_hit_reports_no_truncation(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["a", "b"])

    result = discover_projects(ProjectsConfig(roots=(root,), max_directories=100))

    assert result.truncated is False


def test_excluded_names_do_not_consume_the_directory_budget(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha", "node_modules"])
    (root / "node_modules" / "one").mkdir()
    (root / "node_modules" / "two").mkdir()

    # A budget of 1 is only enough for "alpha" -- node_modules and its
    # contents must cost nothing, or this would truncate before "alpha".
    result = discover_projects(ProjectsConfig(roots=(root,), max_depth=2, max_directories=1))

    assert [p.name for p in result.projects] == ["alpha"]
    assert result.truncated is False


def test_format_scan_warnings_empty_when_nothing_to_report(tmp_path: Path) -> None:
    root = _make_tree(tmp_path, dirs=["alpha"])
    result = discover_projects(ProjectsConfig(roots=(root,)))

    assert format_scan_warnings(result) == ""


def test_format_scan_warnings_includes_truncation_and_root_warnings(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    root = _make_tree(tmp_path, dirs=["a", "b"])

    result = discover_projects(ProjectsConfig(roots=(missing, root), max_directories=1))

    text = format_scan_warnings(result)
    assert str(missing) in text
    assert "limit" in text.lower()


# --- project_option_id / disambiguated_display_names ------------------------------


def _status_at(name: str, path: Path) -> ProjectStatus:
    return ProjectStatus(
        project=Project(name=name, path=path),
        canonical_path=path,
        project_dir_exists=True,
        is_git_repo=False,
        git_branch=None,
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name=name,
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )


def test_project_option_id_is_derived_from_canonical_path(tmp_path: Path) -> None:
    status = _status_at("demo", tmp_path / "demo")
    assert project_option_id(status) == str(tmp_path / "demo")


def test_project_option_id_differs_for_same_name_different_paths(tmp_path: Path) -> None:
    """The core of this fix: two projects sharing a basename must never
    collapse to the same identifier."""
    school = _status_at("example", tmp_path / "school" / "example")
    work = _status_at("example", tmp_path / "work" / "example")

    assert project_option_id(school) != project_option_id(work)


def test_disambiguated_display_names_untouched_when_names_are_unique() -> None:
    statuses = [_status_at("alpha", Path("/tmp/alpha")), _status_at("beta", Path("/tmp/beta"))]

    assert disambiguated_display_names(statuses) == ["alpha", "beta"]


def test_disambiguated_display_names_adds_path_suffix_for_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    school = _status_at("example", tmp_path / "school" / "example")
    work = _status_at("example", tmp_path / "work" / "example")

    names = disambiguated_display_names([school, work])

    assert names[0] == "example — ~/school/example"
    assert names[1] == "example — ~/work/example"
    assert names[0] != names[1]


def test_disambiguated_display_names_only_touches_the_duplicated_names(tmp_path: Path) -> None:
    """Preserves current behavior for uniquely named projects: a project
    whose name isn't shared by anything else in the same batch keeps its
    plain name, even when scanned alongside projects that do collide.
    """
    school = _status_at("example", tmp_path / "school" / "example")
    work = _status_at("example", tmp_path / "work" / "example")
    unique = _status_at("solo", tmp_path / "solo")

    names = disambiguated_display_names([school, work, unique])

    assert names[2] == "solo"


# --- gather_project_status ----------------------------------------------------


def _workspace(project_path: Path, session_name: str = "demo") -> WorkspaceSpec:
    return WorkspaceSpec.for_local_project(
        project_name="demo",
        project_path=project_path,
        session_name=session_name,
        windows=(
            WindowSpec(
                window_name="main",
                panes=(PaneSpec(kind=PaneKind.CODE_EDITOR, display_name="Code Editor"),),
            ),
        ),
    )


def test_status_when_session_running_and_workspace_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    workspace = _workspace(project_path, session_name="demo-session")
    save_workspace(workspace, store_path=store_path)
    project = Project(name="demo", path=project_path)

    status = gather_project_status(
        project, store_path=store_path, running_sessions={"demo-session"}
    )

    assert status.saved_workspace == workspace
    assert status.session_running is True
    assert status.expected_session_name == "demo-session"
    assert status.workspace_metadata_error is None


def test_status_when_workspace_saved_but_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    workspace = _workspace(project_path, session_name="demo-session")
    save_workspace(workspace, store_path=store_path)
    project = Project(name="demo", path=project_path)

    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace == workspace
    assert status.session_running is False


def test_status_when_nothing_saved_and_not_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    project = Project(name="demo", path=project_path)

    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace is None
    assert status.session_running is False
    assert status.workspace_metadata_error is None


def test_status_running_session_with_no_saved_workspace_uses_deterministic_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An orphaned running session (e.g. right after Forget Saved Workspace)
    is still detected -- matched only by the exact deterministic slug, never
    a fuzzy/similar name.
    """
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "My Demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    project = Project(name="My Demo", path=project_path)

    status = gather_project_status(project, store_path=store_path, running_sessions={"My-Demo"})

    assert status.saved_workspace is None
    assert status.expected_session_name == "My-Demo"
    assert status.session_running is True


def test_status_isolates_legacy_key_payload_path_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    # Legacy outer identity points at the project, while its payload points
    # elsewhere. Migration isolates this rather than silently moving it.
    stale = _workspace(tmp_path / "old-location", session_name="demo-session")
    import json

    store_path.write_text(
        json.dumps(
            {
                str(project_path.resolve()): {
                    "project_name": stale.project_name,
                    "project_path": str(stale.project_path),
                    "session_name": stale.session_name,
                    "windows": [window.to_dict() for window in stale.windows],
                }
            }
        )
    )

    project = Project(name="demo", path=project_path)
    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace is None
    assert status.workspace_metadata_error is not None


def test_status_reports_malformed_metadata_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    import json

    store_path.write_text(json.dumps({str(project_path.resolve()): {"project_name": "bad"}}))

    project = Project(name="demo", path=project_path)
    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.saved_workspace is None
    assert status.workspace_metadata_error is not None


def test_status_when_project_directory_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    store_path = tmp_path / "workspaces.json"
    project = Project(name="demo", path=tmp_path / "does-not-exist")

    status = gather_project_status(project, store_path=store_path, running_sessions=set())

    assert status.project_dir_exists is False
    assert status.git_branch is None
    assert status.is_git_repo is False


def test_status_when_tmux_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: False)
    project_path = tmp_path / "demo"
    project_path.mkdir()
    store_path = tmp_path / "workspaces.json"
    project = Project(name="demo", path=project_path)

    status = gather_project_status(project, store_path=store_path)

    assert status.tmux_available is False
    assert status.session_running is False


# --- session-name collision resolution ---------------------------------------------


def test_unsaved_unique_name_keeps_plain_sanitized_session_name(tmp_path: Path) -> None:
    """Legacy behavior for the common case: a uniquely named project never
    gets an unnecessary suffix."""
    project_path = tmp_path / "shpe-connect"
    project_path.mkdir()
    project = Project(name="shpe-connect", path=project_path)

    status = gather_project_status(project, store_path=tmp_path / "workspaces.json")

    assert status.expected_session_name == "shpe-connect"


def test_two_unsaved_same_named_projects_get_distinct_session_names(tmp_path: Path) -> None:
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    store_path = tmp_path / "workspaces.json"
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    result = scan_all_projects(config=config, store_path=store_path)

    names = {status.canonical_path: status.expected_session_name for status in result.statuses}
    school_name = names[school_path.resolve()]
    work_name = names[work_path.resolve()]

    assert school_name != work_name
    assert school_name.startswith("example-")
    assert work_name.startswith("example-")


def test_colliding_session_names_are_stable_across_repeated_scans(tmp_path: Path) -> None:
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    store_path = tmp_path / "workspaces.json"
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    first = scan_all_projects(config=config, store_path=store_path)
    second = scan_all_projects(config=config, store_path=store_path)

    first_names = {s.canonical_path: s.expected_session_name for s in first.statuses}
    second_names = {s.canonical_path: s.expected_session_name for s in second.statuses}
    assert first_names == second_names


def test_colliding_session_names_do_not_depend_on_root_order(tmp_path: Path) -> None:
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    store_path = tmp_path / "workspaces.json"

    forward = scan_all_projects(
        config=ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work")),
        store_path=store_path,
    )
    reversed_order = scan_all_projects(
        config=ProjectsConfig(roots=(tmp_path / "work", tmp_path / "school")),
        store_path=store_path,
    )

    forward_names = {s.canonical_path: s.expected_session_name for s in forward.statuses}
    reversed_names = {s.canonical_path: s.expected_session_name for s in reversed_order.statuses}
    assert forward_names == reversed_names


def test_colliding_session_name_suffix_uses_no_randomized_hash(tmp_path: Path) -> None:
    """The suffix must come from a stable algorithm (SHA-256), never
    Python's per-process-randomized hash() builtin."""
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    result = scan_all_projects(config=config, store_path=tmp_path / "workspaces.json")
    names = {s.canonical_path: s.expected_session_name for s in result.statuses}
    school_suffix = names[school_path.resolve()].removeprefix("example-")

    expected_suffix = hashlib.sha256(str(school_path.resolve()).encode("utf-8")).hexdigest()[:8]
    assert school_suffix == expected_suffix
    # hash() is randomized per-process (PYTHONHASHSEED) -- a suffix derived
    # from it would not even be reproducible within a single assertion.
    assert school_suffix != format(hash(str(school_path.resolve())) & 0xFFFFFFFF, "x")[:8]


def test_saved_workspace_session_name_overrides_collision_naming(tmp_path: Path) -> None:
    """A project with a saved workspace always keeps its persisted name,
    even if it would otherwise collide with a same-named sibling."""
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    store_path = tmp_path / "workspaces.json"
    saved = _workspace(school_path.resolve(), session_name="my-custom-name")
    save_workspace(saved, store_path=store_path)
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    result = scan_all_projects(config=config, store_path=store_path)

    by_path = {s.canonical_path: s for s in result.statuses}
    assert by_path[school_path.resolve()].expected_session_name == "my-custom-name"
    # The other, still-unsaved project is unaffected and still gets its
    # own collision-safe name.
    assert by_path[work_path.resolve()].expected_session_name.startswith("example-")


def test_one_running_colliding_session_marks_only_its_own_project_running(
    tmp_path: Path,
) -> None:
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    store_path = tmp_path / "workspaces.json"
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    # Discover first (without a running-session set) to learn each
    # project's own collision-safe expected name -- exactly what Home's
    # scan_all_projects computes before checking tmux.
    baseline = scan_all_projects(config=config, store_path=store_path)
    by_path = {s.canonical_path: s for s in baseline.statuses}
    school_expected = by_path[school_path.resolve()].expected_session_name
    work_expected = by_path[work_path.resolve()].expected_session_name

    running = {school_expected}  # only the school project's session is "running"
    school_status = gather_project_status(
        Project(name="example", path=school_path),
        store_path=store_path,
        running_sessions=running,
        base_session_name_counts={"example": 2},
    )
    work_status = gather_project_status(
        Project(name="example", path=work_path),
        store_path=store_path,
        running_sessions=running,
        base_session_name_counts={"example": 2},
    )

    assert school_status.expected_session_name == school_expected
    assert work_status.expected_session_name == work_expected
    assert school_status.session_running is True
    assert work_status.session_running is False


def test_default_workspace_creation_helper_yields_distinct_session_names(
    tmp_path: Path,
) -> None:
    """Simulates "Open Default Workspace" for each of two same-named
    projects: both must be told to use distinct session names.
    """
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    school_status = gather_single_project_status(
        Project(name="example", path=school_path), config=config
    )
    work_status = gather_single_project_status(
        Project(name="example", path=work_path), config=config
    )

    assert school_status.expected_session_name != work_status.expected_session_name


# --- gather_single_project_status ---------------------------------------------------


def test_gather_single_project_status_agrees_with_a_full_scan(tmp_path: Path) -> None:
    """Project Detail's standalone refresh (gather_single_project_status)
    must never disagree with what scan_all_projects assigned the same
    project -- it must not "forget" the collision-aware name.
    """
    school_path = tmp_path / "school" / "example"
    school_path.mkdir(parents=True)
    work_path = tmp_path / "work" / "example"
    work_path.mkdir(parents=True)
    store_path = tmp_path / "workspaces.json"
    config = ProjectsConfig(roots=(tmp_path / "school", tmp_path / "work"))

    scan_result = scan_all_projects(config=config, store_path=store_path)
    by_path = {s.canonical_path: s for s in scan_result.statuses}

    standalone = gather_single_project_status(
        Project(name="example", path=school_path), store_path=store_path, config=config
    )

    assert standalone.expected_session_name == by_path[school_path.resolve()].expected_session_name


def test_gather_single_project_status_unique_name_unaffected(tmp_path: Path) -> None:
    project_path = tmp_path / "solo"
    project_path.mkdir()
    config = ProjectsConfig(roots=(tmp_path,))

    status = gather_single_project_status(Project(name="solo", path=project_path), config=config)

    assert status.expected_session_name == "solo"


# --- primary_actions / secondary_actions ---------------------------------------


def _status(
    *,
    session_running: bool = False,
    project_dir_exists: bool = True,
    workspace_metadata_error: str | None = None,
    saved_workspace: WorkspaceSpec | None = None,
) -> ProjectStatus:
    return ProjectStatus(
        project=Project(name="demo", path=Path("/tmp/demo")),
        canonical_path=Path("/tmp/demo"),
        project_dir_exists=project_dir_exists,
        is_git_repo=False,
        git_branch=None,
        saved_workspace=saved_workspace,
        workspace_metadata_error=workspace_metadata_error,
        expected_session_name="demo",
        tmux_available=True,
        session_running=session_running,
        last_modified=None,
    )


def test_primary_action_running_session_offers_resume() -> None:
    assert primary_actions(_status(session_running=True)) == [ProjectAction.RESUME]


def test_primary_action_saved_workspace_not_running_offers_recreate(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    assert primary_actions(_status(saved_workspace=workspace)) == [ProjectAction.RECREATE]


def test_primary_action_nothing_saved_offers_default_and_configure() -> None:
    assert primary_actions(_status()) == [ProjectAction.OPEN_DEFAULT, ProjectAction.CONFIGURE]


def test_primary_action_corrupt_metadata_offers_forget_and_configure() -> None:
    actions = primary_actions(_status(workspace_metadata_error="bad data"))
    assert actions == [ProjectAction.FORGET, ProjectAction.CONFIGURE]


def test_primary_action_running_session_wins_over_corrupt_metadata() -> None:
    actions = primary_actions(_status(session_running=True, workspace_metadata_error="bad data"))
    assert actions == [ProjectAction.RESUME]


def test_primary_action_missing_directory_offers_nothing_when_not_running() -> None:
    assert primary_actions(_status(project_dir_exists=False)) == []


def test_primary_action_missing_directory_still_offers_resume_when_running() -> None:
    actions = primary_actions(_status(session_running=True, project_dir_exists=False))
    assert actions == [ProjectAction.RESUME]


def test_secondary_actions_for_saved_workspace(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    actions = secondary_actions(_status(saved_workspace=workspace))
    assert actions == [
        ProjectAction.EDIT,
        ProjectAction.RESET,
        ProjectAction.FORGET,
        ProjectAction.SAVE_TEMPLATE,
    ]


def test_secondary_actions_empty_when_nothing_saved() -> None:
    assert secondary_actions(_status()) == []


def test_secondary_actions_offers_forget_for_corrupt_metadata_with_running_session() -> None:
    actions = secondary_actions(_status(session_running=True, workspace_metadata_error="bad data"))
    assert actions == [ProjectAction.FORGET]


# --- build_launch_request ---------------------------------------------------------


def test_build_launch_request_with_saved_workspace_attaches_with_workspace(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    request = build_launch_request(_status(saved_workspace=workspace))
    assert request == LaunchRequest(workspace=workspace, init_git=False, action=LaunchAction.ATTACH)


def test_build_launch_request_without_saved_workspace_attaches_by_session_name() -> None:
    request = build_launch_request(_status())
    assert request == LaunchRequest(
        workspace=None, init_git=False, action=LaunchAction.ATTACH, session_name="demo"
    )


def test_build_launch_request_is_the_same_whether_or_not_the_session_is_running(
    tmp_path: Path,
) -> None:
    """Resume and Recreate must produce the identical request either way --
    the orchestration layer re-checks whether the session is actually
    running at launch time, regardless of what ProjectStatus saw.
    """
    workspace = _workspace(tmp_path)
    running = build_launch_request(_status(saved_workspace=workspace, session_running=True))
    not_running = build_launch_request(_status(saved_workspace=workspace, session_running=False))
    assert running == not_running


def test_build_launch_request_with_corrupt_metadata_falls_back_to_session_name() -> None:
    request = build_launch_request(_status(workspace_metadata_error="bad data"))
    assert request.workspace is None
    assert request.session_name == "demo"
    assert request.action is LaunchAction.ATTACH


def test_build_launch_request_missing_project_directory_still_targets_session_name() -> None:
    request = build_launch_request(_status(project_dir_exists=False))
    assert request.action is LaunchAction.ATTACH
    assert request.workspace is None
    assert request.session_name == "demo"


def test_build_launch_request_never_sets_create_action(tmp_path: Path) -> None:
    """build_launch_request only ever expresses Resume/Recreate -- Open
    Default Workspace's LaunchAction.CREATE request is built separately,
    since it also creates and saves a brand-new workspace.
    """
    with_saved = build_launch_request(_status(saved_workspace=_workspace(tmp_path)))
    without_saved = build_launch_request(_status())
    assert with_saved.action is LaunchAction.ATTACH
    assert without_saved.action is LaunchAction.ATTACH


# --- status_badge ---------------------------------------------------------------


def test_status_badge_running_wins_over_everything() -> None:
    assert status_badge(_status(session_running=True, workspace_metadata_error="bad")) == "Running"


def test_status_badge_metadata_warning() -> None:
    assert status_badge(_status(workspace_metadata_error="bad data")) == "Metadata Warning"


def test_status_badge_saved_workspace(tmp_path: Path) -> None:
    assert status_badge(_status(saved_workspace=_workspace(tmp_path))) == "Saved Workspace"


def test_status_badge_not_configured() -> None:
    assert status_badge(_status()) == "Not Configured"


# --- scan_all_projects -----------------------------------------------------------


def test_scan_all_projects_returns_one_status_per_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(projects_module.tmux, "list_tmux_sessions", lambda: [])
    root = tmp_path / "projects"
    root.mkdir()
    (root / "alpha").mkdir()
    (root / "beta").mkdir()
    store_path = tmp_path / "workspaces.json"

    result = scan_all_projects(config=ProjectsConfig(roots=(root,)), store_path=store_path)

    assert sorted(s.project.name for s in result.statuses) == ["alpha", "beta"]
    assert result.truncated is False
    assert result.warnings == ()


def test_scan_all_projects_shares_one_tmux_session_list_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    call_count = {"n": 0}

    def fake_list_sessions():
        call_count["n"] += 1
        return []

    monkeypatch.setattr(projects_module.tmux, "list_tmux_sessions", fake_list_sessions)
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("alpha", "beta", "gamma"):
        (root / name).mkdir()

    scan_all_projects(config=ProjectsConfig(roots=(root,)), store_path=tmp_path / "workspaces.json")

    assert call_count["n"] == 1


def test_scan_all_projects_missing_root_returns_empty_result_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(projects_module.tmux, "list_tmux_sessions", lambda: [])

    result = scan_all_projects(config=ProjectsConfig(roots=(tmp_path / "does-not-exist",)))

    assert result.statuses == ()
    assert len(result.warnings) == 1


def test_scan_all_projects_surfaces_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(projects_module.tmux, "list_tmux_sessions", lambda: [])
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("a", "b", "c"):
        (root / name).mkdir()

    result = scan_all_projects(config=ProjectsConfig(roots=(root,), max_directories=1))

    assert len(result.statuses) == 1
    assert result.truncated is True
    assert "limit" in format_scan_warnings(result).lower()


def test_scan_all_projects_loads_saved_config_when_none_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects_module.tmux, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(projects_module.tmux, "list_tmux_sessions", lambda: [])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    root = tmp_path / "configured-root"
    root.mkdir()
    (root / "alpha").mkdir()
    save_projects_config(ProjectsConfig(roots=(root,)))

    result = scan_all_projects(store_path=tmp_path / "workspaces.json")

    assert [s.project.name for s in result.statuses] == ["alpha"]
