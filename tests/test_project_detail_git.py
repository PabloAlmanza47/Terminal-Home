from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from textual.widgets import OptionList

from dashboard.app import TerminalHomeApp
from dashboard.models import LocalProjectLocation
from dashboard.screens import project_detail as detail_module
from dashboard.screens.diff_view import DiffScreen
from dashboard.screens.home import HomeScreen
from dashboard.screens.project_detail import (
    ProjectDetailScreen,
    format_activity_block,
    format_git_files,
    format_git_summary,
    format_project_commands,
    format_project_tools,
    format_workspace_summary,
)
from dashboard.services.git import GitDiffResult, GitFileChange, GitStatus
from dashboard.services.project_commands import (
    CommandSource,
    DetectedCommand,
    DetectedProjectCommands,
)
from dashboard.services.project_tools import ProjectTools
from dashboard.services.projects import Project, ProjectStatus
from dashboard.services.workspace_defaults import build_default_workspace


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


def test_project_detail_operational_sections_present_compactly() -> None:
    project = Project("demo", Path("/tmp/demo"))
    status = ProjectStatus(
        project=project,
        canonical_path=project.path,
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=None,
        workspace_metadata_error=None,
        expected_session_name="demo",
        tmux_available=True,
        session_running=True,
        last_modified=None,
        running_window_count=2,
        running_pane_count=3,
        session_attached=False,
    )
    assert format_workspace_summary(status) == "● Running · 2 windows · 3 panes · detached"
    commands = DetectedProjectCommands(
        DetectedCommand("npm run dev", CommandSource.NODE_DEV),
        DetectedCommand("npm test", CommandSource.NODE_TEST),
    )
    assert "Dev server    npm run dev" in format_project_commands(commands)
    assert "Tests         npm test" in format_project_commands(commands)
    assert "Lazygit" in format_project_tools(ProjectTools(True, True, True))


def test_project_detail_omits_empty_agent_state() -> None:
    project = Project("demo", Path("/tmp/demo"))
    rendered = format_activity_block(_status(project))

    assert rendered == "Server  — Not Configured"
    assert "Agent" not in rendered


def test_project_detail_marks_narrow_layout_without_replacing_content(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project("demo", tmp_path)
    status = _status(project)
    monkeypatch.setattr(detail_module, "gather_single_project_status", lambda _: status)
    monkeypatch.setattr(detail_module, "load_status", lambda _: GitStatus(True, "main", True))

    async def scenario() -> bool:
        app = TerminalHomeApp()
        async with app.run_test(size=(70, 30)) as pilot:
            screen = ProjectDetailScreen(project)
            app.push_screen(screen)
            await pilot.pause()
            return screen.query_one(".project-detail-root").has_class("detail-narrow")

    assert asyncio.run(scenario())


def test_stale_git_callback_does_not_clear_new_refresh() -> None:
    project = Project("demo", Path("/tmp/demo"))
    screen = ProjectDetailScreen(project)
    screen._git_refresh_active = True
    screen._git_refresh_generation = 2
    screen._git_refreshing = True

    screen._on_git_status(GitStatus(True, "main", True), generation=1)

    assert screen._git_refreshing is True


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
            assert not screen.query_one("#detail-git-files").display
            assert "Loading" not in str(
                screen.query_one("#detail-git-files-list", OptionList)
                .get_option_at_index(0)
                .prompt
            )
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


def test_project_detail_paints_detected_commands_and_tools(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project("demo", tmp_path)
    status = _status(project)
    commands = DetectedProjectCommands(
        DetectedCommand("npm run dev", CommandSource.NODE_DEV),
        DetectedCommand("npm test", CommandSource.NODE_TEST),
        DetectedCommand("npm run build", CommandSource.NODE_BUILD),
        DetectedCommand("npm run lint", CommandSource.NODE_LINT),
    )
    monkeypatch.setattr(detail_module, "gather_single_project_status", lambda _: status)
    monkeypatch.setattr(detail_module, "load_status", lambda _: GitStatus(True, "main", False))
    monkeypatch.setattr(detail_module, "detect_project_commands", lambda _: commands)
    monkeypatch.setattr(
        detail_module,
        "detect_project_tools",
        lambda *args, **kwargs: ProjectTools(True, True, True),
    )

    async def scenario() -> tuple[str, str]:
        app = TerminalHomeApp()
        async with app.run_test(size=(100, 40)) as pilot:
            screen = ProjectDetailScreen(project)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert screen.query("#detail-status-grid")
            assert screen.query("#detail-command-tool-grid")
            assert screen.query("#detail-tools")
            assert not screen.query("#detail-workspace")
            return (
                str(screen.query_one("#detail-commands", detail_module.Static).render()),
                str(screen.query_one("#detail-tools", detail_module.Static).render()),
            )

    async def owned() -> tuple[str, str]:
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(thread_name_prefix="detail-command-test")
        loop.set_default_executor(executor)
        try:
            return await scenario()
        finally:
            executor.shutdown(wait=True)

    rendered_commands, rendered_tools = asyncio.run(owned())
    assert "npm run build" in rendered_commands
    assert "npm run lint" in rendered_commands
    assert "Lazygit" in rendered_tools


def test_enter_on_executable_project_command_returns_run_request(
    tmp_path: Path, monkeypatch
) -> None:
    project = Project("demo", tmp_path)
    workspace = build_default_workspace("demo", LocalProjectLocation(project.path), "demo")
    status = replace(_status(project), saved_workspace=workspace, tmux_available=True)
    command = DetectedProjectCommands(
        development=None,
        test=DetectedCommand("pytest", CommandSource.PYTEST),
    )
    monkeypatch.setattr(detail_module, "gather_single_project_status", lambda _: status)
    monkeypatch.setattr(detail_module, "load_status", lambda _: GitStatus(True, "main", True))
    monkeypatch.setattr(detail_module, "detect_project_commands", lambda _: command)
    monkeypatch.setattr(
        detail_module,
        "detect_project_tools",
        lambda *args, **kwargs: ProjectTools(False, True, True),
    )

    async def scenario():
        app = TerminalHomeApp()
        async with app.run_test(size=(100, 40)) as pilot:
            screen = ProjectDetailScreen(project)
            app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            commands = screen.query_one("#detail-commands", detail_module.ProjectCommandList)
            commands.focus()
            await pilot.press("enter")
            await pilot.pause()
        return app.return_value

    result = asyncio.run(scenario())
    assert result is not None
    assert result.command_id == "test"
    assert result.category == "test"
    assert result.argv == ("pytest",)


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
