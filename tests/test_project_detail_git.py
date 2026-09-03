from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dashboard.app import TerminalHomeApp
from dashboard.screens import project_detail as detail_module
from dashboard.screens.home import HomeScreen
from dashboard.screens.project_detail import (
    ProjectDetailScreen,
    format_git_files,
    format_git_summary,
)
from dashboard.services.git import GitFileChange, GitStatus
from dashboard.services.projects import Project, ProjectStatus


def _status(project: Project) -> ProjectStatus:
    return ProjectStatus(
        project=project,
        canonical_path=project.path,
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="demo",
        tmux_available=False,
        session_running=False,
        last_modified=None,
    )


def test_project_detail_git_summary_for_clean_repository() -> None:
    rendered = format_git_summary(GitStatus(True, "main", False))
    assert rendered == "Branch        main\nWorking Tree  Clean"


def test_project_detail_git_summary_and_files_for_dirty_repository() -> None:
    status = GitStatus(
        True,
        "main",
        False,
        changes=(
            GitFileChange("changed.py", ".", "M"),
            GitFileChange("new.py", "?", "?"),
        ),
        staged_count=0,
        modified_count=1,
        untracked_count=1,
    )
    summary = format_git_summary(status)
    files = format_git_files(status)
    assert "Changes       2 files" in summary
    assert "Modified      1" in summary
    assert "Untracked     1" in summary
    assert "  .M changed.py" in files
    assert "  ?  new.py" in files


def test_project_detail_git_summary_for_non_repository_and_detached_head() -> None:
    assert format_git_summary(GitStatus(False, None, False)) == "Not a Git repository"
    assert "(detached HEAD)" in format_git_summary(GitStatus(True, None, True))


def test_project_detail_refresh_updates_git_state_without_recomposing(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project("demo", tmp_path)
    clean = GitStatus(True, "main", False)
    dirty = GitStatus(True, "main", False, (GitFileChange("new.py", "?", "?"),), 0, 0, 1)
    current = [clean]
    monkeypatch.setattr(HomeScreen, "_start_scan", lambda self: None)
    monkeypatch.setattr(detail_module, "gather_single_project_status", lambda _: _status(project))
    monkeypatch.setattr(detail_module, "load_status", lambda _: current[0])

    async def scenario() -> tuple[str, str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=(100, 40)) as pilot:
            screen = ProjectDetailScreen(project)
            app.push_screen(screen)
            await asyncio.sleep(0)
            await pilot.pause()
            await app.workers.wait_for_complete()
            first = str(screen.query_one("#detail-git", detail_module.Static).render())
            current[0] = dirty
            screen._start_git_refresh()
            await app.workers.wait_for_complete()
            second = str(screen.query_one("#detail-git", detail_module.Static).render())
            files = str(screen.query_one("#detail-git-files", detail_module.Static).render())
            return first, second, files

    async def owned() -> tuple[str, str, str]:
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="git-detail-test")
        loop.set_default_executor(executor)
        try:
            return await scenario()
        finally:
            executor.shutdown(wait=True)

    first, second, files = asyncio.run(owned())
    assert "Clean" in first
    assert "Changes       1 file" in second
    assert "new.py" in files
