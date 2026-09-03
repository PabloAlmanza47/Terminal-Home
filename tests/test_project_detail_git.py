from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from textual.widgets import OptionList

from dashboard.app import TerminalHomeApp
from dashboard.screens import project_detail as detail_module
from dashboard.screens.diff_view import DiffScreen
from dashboard.screens.home import HomeScreen
from dashboard.screens.project_detail import (
    ProjectDetailScreen,
    format_git_files,
    format_git_summary,
)
from dashboard.services.git import GitDiffResult, GitFileChange, GitStatus
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
    assert rendered == "main · Clean"


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
    assert "main · 2 changes" in summary
    assert "Modified 1" in summary
    assert "Untracked 1" in summary
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
    dirty = GitStatus(
        True,
        "main",
        False,
        (
            GitFileChange("staged.py", "M", "."),
            GitFileChange("new.py", ".", "M"),
            GitFileChange("notes.txt", "?", "?"),
        ),
        1,
        1,
        1,
    )
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
            files = "\n".join(
                str(option.prompt)
                for option in screen.query_one("#detail-git-files-list", OptionList).options
            )
            file_list = screen.query_one("#detail-git-files-list", OptionList)
            assert screen.query_one("#detail-git-files").display
            assert file_list.display
            assert file_list.can_focus
            assert file_list.option_count == 3
            assert file_list.region.height >= 3
            assert file_list.virtual_size.height >= 3
            assert file_list.region.intersection(screen.region).height > 0
            rendered = "\n".join(str(file_list.render_line(index)) for index in range(3))
            assert "staged.py" in rendered
            assert "new.py" in rendered
            assert "notes.txt" in rendered
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
    assert "main · 3 changes" in second
    assert "staged.py" in files
    assert "new.py" in files


def test_project_detail_selects_changed_file_and_opens_read_only_diff(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project("demo", tmp_path)
    changes = (
        GitFileChange("first.py", ".", "M"),
        GitFileChange("new.py", ".", "M"),
        GitFileChange("third.py", "?", "?"),
    )
    status = _status(project)
    git = GitStatus(True, "main", False, changes, 0, 2, 1)
    monkeypatch.setattr(HomeScreen, "_start_scan", lambda self: None)
    monkeypatch.setattr(detail_module, "gather_single_project_status", lambda _: status)
    monkeypatch.setattr(detail_module, "load_status", lambda _: git)
    monkeypatch.setattr(
        detail_module,
        "load_diff",
        lambda path, selected: GitDiffResult(
            path, selected.path, selected.old_path, True, True, working_tree="+ added\n"
        ),
    )

    async def scenario() -> str:
        app = TerminalHomeApp()
        async with app.run_test(size=(100, 40)) as pilot:
            screen = ProjectDetailScreen(project)
            app.push_screen(screen)
            await asyncio.sleep(0)
            await pilot.pause()
            # Apply the fake refresh after mount so the assertions exercise
            # the real mounted Textual widget and its render path.
            screen._on_git_status(git)
            await pilot.pause()
            files = screen.query_one("#detail-git-files-list", OptionList)
            files.focus()
            await pilot.pause()
            assert files.display
            assert files.option_count == 3
            files.highlighted = 1
            await pilot.pause()
            assert files.highlighted == 1
            assert app.focused is files
            await pilot.press("d")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, DiffScreen)
            assert "new.py" in str(app.screen.query_one("#diff-title").render())
            await pilot.press("escape")
            await pilot.pause()
            restored = app.screen.query_one("#detail-git-files-list", OptionList)
            assert app.focused is restored
            assert restored.highlighted == 1
            return type(app.screen).__name__

    async def owned() -> str:
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="diff-screen-test")
        loop.set_default_executor(executor)
        try:
            return await scenario()
        finally:
            executor.shutdown(wait=True)

    assert asyncio.run(owned()) == "ProjectDetailScreen"
