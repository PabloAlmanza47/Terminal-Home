"""Worktree inventory and its scan_all_projects integration, against real
temporary Git repositories (no network, no fetch, tmux and Agent Deck faked).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

import dashboard.services.projects as projects_module
import dashboard.services.worktree_inventory as inventory_module
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux as tmux_module
from dashboard.services.agent_deck import AgentDeckSnapshot
from dashboard.services.git import parse_status
from dashboard.services.project_categories import (
    matches_project_query,
    split_worktree_children,
    with_worktree_parents,
)
from dashboard.services.project_rows import worktree_branch_label
from dashboard.services.projects import scan_all_projects
from dashboard.services.worktree_inventory import (
    ProjectWorktree,
    inventory_worktrees,
    repository_common_dir,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setattr(tmux_module, "list_tmux_sessions", lambda: [])
    monkeypatch.setattr(tmux_module, "list_tmux_panes", lambda: [])
    monkeypatch.setattr(tmux_module, "is_tmux_installed", lambda: True)
    monkeypatch.setattr(tmux_module, "session_exists", lambda name: False)
    monkeypatch.setattr(projects_module, "agent_deck_snapshot", lambda: AgentDeckSnapshot(False))


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def add_fake_origin(repo: Path) -> None:
    """A remote-tracking ref without any network or fetch."""
    git(repo, "remote", "add", "origin", str(repo))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def by_path(inventory) -> dict[Path, ProjectWorktree]:
    return {path: item for path, item in inventory.worktrees.items()}


# --- inventory ----------------------------------------------------------------


def test_groups_main_and_external_linked_worktree(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "projects" / "app")
    linked = tmp_path / "elsewhere" / "app-feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(linked))

    found = by_path(inventory_worktrees([repo]))

    assert set(found) == {repo.resolve(), linked.resolve()}
    main, feature = found[repo.resolve()], found[linked.resolve()]
    assert main.is_main and not feature.is_main
    assert main.parent_path == feature.parent_path == repo.resolve()
    assert main.repository == feature.repository == (repo / ".git").resolve()
    assert (main.branch, feature.branch) == ("main", "feature")
    assert main.clean is True and feature.clean is True
    assert feature.upstream is None and feature.upstream_warning is None


def test_identity_is_common_dir_from_any_checkout(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    linked = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "-b", "x", str(linked))
    assert repository_common_dir(linked) == repository_common_dir(repo) == (repo / ".git").resolve()
    # Reaching the repository through either checkout gives the same group.
    assert set(inventory_worktrees([linked]).worktrees) == {repo.resolve(), linked.resolve()}


def test_detached_head_and_dirty_status(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    detached = tmp_path / "detached"
    git(repo, "worktree", "add", "-q", "--detach", str(detached))
    (detached / "scratch.txt").write_text("x")

    item = by_path(inventory_worktrees([repo]))[detached.resolve()]

    assert item.detached and item.branch is None
    assert item.head == git(repo, "rev-parse", "HEAD")
    assert item.clean is False
    assert worktree_branch_label(item) == f"detached {item.head[:7]} *"


def test_feature_branch_tracking_origin_main_is_flagged(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    add_fake_origin(repo)
    git(repo, "branch", "-q", "-u", "origin/main", "main")
    linked = tmp_path / "feature"
    git(repo, "worktree", "add", "-q", "-b", "design/contract", str(linked))
    git(linked, "branch", "-q", "-u", "origin/main")

    found = by_path(inventory_worktrees([repo]))

    main, feature = found[repo.resolve()], found[linked.resolve()]
    assert (main.upstream, main.ahead, main.behind) == ("origin/main", 0, 0)
    assert main.upstream_warning is None
    assert feature.upstream == "origin/main"
    assert feature.upstream_warning == "tracks origin/main"
    assert worktree_branch_label(feature) == "design/contract !"


def test_missing_upstream_ref_is_flagged_but_no_upstream_is_not(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    git(repo, "remote", "add", "origin", str(repo))
    git(repo, "config", "branch.main.remote", "origin")
    git(repo, "config", "branch.main.merge", "refs/heads/main")

    item = by_path(inventory_worktrees([repo]))[repo.resolve()]

    assert item.upstream == "origin/main" and item.ahead is None
    assert item.upstream_warning == "upstream origin/main not found"
    no_upstream = ProjectWorktree(
        repository=tmp_path, parent_path=tmp_path, path=tmp_path, is_main=True,
        branch="feature", detached=False, head="abc", clean=True,
    )
    assert no_upstream.upstream_warning is None


def test_deleted_worktree_path_is_skipped_with_warning(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    gone = tmp_path / "gone"
    git(repo, "worktree", "add", "-q", "-b", "gone", str(gone))
    shutil.rmtree(gone)

    inventory = inventory_worktrees([repo])

    assert set(inventory.worktrees) == {repo.resolve()}
    assert inventory.warnings == ("app: 1 worktree path is missing (Git lists it as prunable)",)


def test_broken_worktree_metadata_degrades_to_plain_directory(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    orphan = tmp_path / "orphan"
    git(repo, "worktree", "add", "-q", "-b", "orphan", str(orphan))
    shutil.rmtree(repo / ".git" / "worktrees" / "orphan")  # .git file now dangles

    assert repository_common_dir(orphan) is None
    inventory = inventory_worktrees([orphan, repo])
    assert set(inventory.worktrees) == {repo.resolve()}


def test_non_git_directories_and_subdirectories_are_ignored(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    repo = make_repo(tmp_path / "app")
    sub = repo / "pkg"
    sub.mkdir()
    assert inventory_worktrees([plain, sub]).worktrees == {}


def test_bare_repository_groups_under_first_linked_worktree(tmp_path: Path) -> None:
    seed = make_repo(tmp_path / "seed")
    bare = tmp_path / "repo.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(bare))
    first, second = tmp_path / "first", tmp_path / "second"
    git(bare, "worktree", "add", "-q", str(first), "main")
    git(bare, "worktree", "add", "-q", "-b", "two", str(second))

    found = by_path(inventory_worktrees([second]))

    assert set(found) == {first.resolve(), second.resolve()}
    assert all(item.parent_path == first.resolve() for item in found.values())
    assert not any(item.is_main for item in found.values())


def test_worktrees_per_repository_are_bounded(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    for name in ("a", "b", "c"):
        git(repo, "worktree", "add", "-q", "-b", name, str(tmp_path / f"wt-{name}"))

    inventory = inventory_worktrees([repo], max_per_repository=2)

    assert len(inventory.worktrees) == 2
    assert inventory.warnings == ("app: showing 2 worktrees; more are not listed",)


def test_git_command_failures_are_warnings(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        if "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 0, ".git\n", "")
        return subprocess.CompletedProcess(argv, 128, "", "fatal: broken")

    inventory = inventory_worktrees([repo], runner=runner)
    assert inventory.worktrees == {}
    assert inventory.warnings == ("app: Git worktree inspection failed",)

    def timeout(argv: list[str]) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, 3)

    assert inventory_worktrees([repo], runner=timeout).worktrees == {}


def test_status_never_runs_repository_configured_fsmonitor(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "app")
    marker = tmp_path / "fsmonitor-ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch {marker}\n")
    hook.chmod(0o755)
    git(repo, "config", "core.fsmonitor", str(hook))
    (repo / "file.txt").write_text("x")

    item = inventory_worktrees([repo]).worktrees[repo.resolve()]

    assert item.clean is False
    assert not marker.exists()


def test_missing_git_binary_returns_empty_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = make_repo(tmp_path / "app")
    monkeypatch.setattr(inventory_module.shutil, "which", lambda name: None)
    assert inventory_worktrees([repo]).worktrees == {}


def test_parse_status_reads_upstream_and_ahead_behind() -> None:
    output = (
        "# branch.oid abc\0# branch.head feat\0"
        "# branch.upstream origin/main\0# branch.ab +2 -5\0"
    )
    status = parse_status(output)
    assert (status.branch, status.upstream, status.ahead, status.behind) == (
        "feat", "origin/main", 2, 5,
    )
    assert parse_status("# branch.head main\0").upstream is None


# --- scan_all_projects integration -------------------------------------------


def _scan(root: Path, *, manual: tuple[Path, ...] = (), include_worktrees: bool = True):
    config = ProjectsConfig(roots=(root,), manual_projects=manual)
    return scan_all_projects(config, include_worktrees=include_worktrees)


def test_scan_adds_external_worktrees_only_when_asked(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repo = make_repo(root / "orbit")
    (root / "notes").mkdir()
    external = tmp_path / "worktrees" / "orbit" / "m3-docs"
    external.parent.mkdir(parents=True)
    git(repo, "worktree", "add", "-q", "-b", "docs/m3", str(external))

    legacy = _scan(root, include_worktrees=False)
    grouped = _scan(root)

    assert [s.project.name for s in legacy.statuses] == ["notes", "orbit"]
    assert all(s.worktree is None for s in legacy.statuses)
    names = {s.project.name: s for s in grouped.statuses}
    assert set(names) == {"notes", "orbit", "orbit/m3-docs"}
    assert names["notes"].worktree is None  # non-Git project unchanged
    child = names["orbit/m3-docs"]
    assert child.canonical_path == external.resolve()
    assert child.worktree is not None and child.worktree.parent_path == repo.resolve()
    assert child.expected_session_name == "orbit-m3-docs"
    assert child.worktree.branch == "docs/m3"


def test_scan_includes_manual_repository_worktrees(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    repo = make_repo(tmp_path / "orbit")  # outside roots, registered by hand
    external = tmp_path / "worktrees" / "orbit" / "m3"
    git(repo, "worktree", "add", "-q", "-b", "m3", str(external))

    names = sorted(s.project.name for s in _scan(root, manual=(repo,)).statuses)
    assert names == ["orbit", "orbit/m3"]


def test_scan_never_duplicates_symlinked_or_discovered_worktrees(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repo = make_repo(root / "app")
    inside = root / "app-docs"
    git(repo, "worktree", "add", "-q", "-b", "docs", str(inside))
    (root / "alias").symlink_to(repo, target_is_directory=True)

    statuses = _scan(root).statuses

    paths = [s.canonical_path for s in statuses]
    assert sorted(paths) == sorted({repo.resolve(), inside.resolve()})
    docs = next(s for s in statuses if s.canonical_path == inside.resolve())
    assert docs.project.name == "app-docs"  # discovered name is kept
    assert docs.worktree is not None and docs.worktree.parent_path == repo.resolve()


def test_scan_reports_inventory_warnings(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repo = make_repo(root / "app")
    gone = tmp_path / "gone"
    git(repo, "worktree", "add", "-q", "-b", "gone", str(gone))
    shutil.rmtree(gone)
    assert "app: 1 worktree path is missing (Git lists it as prunable)" in _scan(root).warnings


# --- grouping helpers ---------------------------------------------------------


def test_grouping_nests_children_and_keeps_orphans_top_level(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    repo = make_repo(root / "app")
    git(repo, "worktree", "add", "-q", "-b", "feat", str(tmp_path / "feat"))
    statuses = list(_scan(root).statuses)
    parent = next(s for s in statuses if s.project.name == "app")
    child = next(s for s in statuses if s.project.name == "app/feat")

    top, children = split_worktree_children(statuses)
    assert top == [parent] and children == {parent.canonical_path: [child]}

    top, children = split_worktree_children([child])
    assert top == [child] and children == {}

    query = "feat"
    visible = [s for s in statuses if matches_project_query(s, query)]
    assert with_worktree_parents(visible, statuses) == [parent, child]
    assert matches_project_query(child, "worktree clean")
