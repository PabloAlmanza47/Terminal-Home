from __future__ import annotations

from rich.cells import cell_len
from textual.app import App, ComposeResult
from textual.widgets.selection_list import Selection

from dashboard.art import COMPACT_ARTWORK, FULL_ARTWORK, artwork_for_size
from dashboard.services.project_categories import group_project_entries, project_category
from dashboard.services.project_rows import (
    RecentProjectRow,
    format_project_row,
    format_recent_project_rows,
)
from dashboard.widgets import CircularSelectionList


def test_artwork_is_responsive_and_setting_aware() -> None:
    assert artwork_for_size(120, 35, True) == FULL_ARTWORK
    assert FULL_ARTWORK.splitlines() == [
        "╭──────╮",
        "│ >_   │   TERMINAL HOME",
        "╰──────╯   Projects, workspaces, and tmux",
    ]
    assert artwork_for_size(80, 24, True) == COMPACT_ARTWORK
    assert artwork_for_size(80, 11, True) is None
    assert artwork_for_size(120, 35, False) is None


def test_open_project_wide_branch_column_is_left_aligned_and_tight() -> None:
    rows = [
        format_project_row("Project Name", "Saved Workspace", "main", 80),
        format_project_row("Another Project", "Not Configured", "dev", 80),
        format_project_row("Branchless", "Running", None, 80),
    ]
    status_starts = [row.index("[") for row in rows]
    branch_starts = [row.index(branch) for row, branch in zip(rows[:2], ("main", "dev"))]
    assert len(set(status_starts)) == 1
    assert len(set(branch_starts)) == 1
    for row in rows[:2]:
        status_end = row.index("]")
        assert row[status_end + 1 : status_end + 4] == "   "
        assert cell_len(row) <= 80
    assert rows[2].rstrip().endswith("[Running]")


def test_recent_project_rows_align_badges_and_branches() -> None:
    rows = format_recent_project_rows(
        [
            RecentProjectRow("Terminal-Home", "Saved Workspace", "feature/v0.2.1"),
            RecentProjectRow("SHPE-Connect", "Running", "dev"),
            RecentProjectRow("portfolio", "Not Configured", "main"),
        ],
        76,
        compact=False,
    )
    badge_starts = [row.index("[") for row in rows]
    branch_starts = [row.index("(") for row in rows]
    assert len(set(badge_starts)) == 1
    assert len(set(branch_starts)) == 1
    assert all("  ]" not in row and cell_len(row) <= 76 for row in rows)


def test_recent_project_rows_stay_columnar_in_compact_half_width() -> None:
    rows = format_recent_project_rows(
        [
            RecentProjectRow("Terminal-Home", "Saved Workspace", "feature/ui"),
            RecentProjectRow("portfolio", "Not Configured", "main"),
            RecentProjectRow("x", "Running", None),
        ],
        44,
        compact=True,
    )
    assert len({row.index("[") for row in rows}) == 1
    branch_rows = [row for row in rows if "(" in row]
    assert len({row.index("(") for row in branch_rows}) == 1
    assert all(cell_len(row) <= 44 for row in rows)


def test_recent_project_rows_use_compact_fallback_without_wrapping() -> None:
    rows = format_recent_project_rows(
        [RecentProjectRow("非常に長いプロジェクト名", "Saved Workspace", "feature/ui")],
        32,
        compact=False,
    )
    assert len(rows) == 1
    assert "[Saved Workspace]" not in rows[0] or "[" in rows[0] and "]" in rows[0]
    assert cell_len(rows[0]) <= 32


def test_pane_selection_renders_circular_markers() -> None:
    class Host(App[None]):
        def compose(self) -> ComposeResult:
            yield CircularSelectionList(
                Selection("Editor", "editor", True),
                Selection("Shell", "shell", False),
            )

    async def scenario() -> tuple[str, str]:
        app = Host()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            selection_list = app.query_one(CircularSelectionList)
            return (
                "".join(segment.text for segment in selection_list.render_line(0)),
                "".join(segment.text for segment in selection_list.render_line(1)),
            )

    import asyncio

    selected, unselected = asyncio.run(scenario())
    assert "●" in selected and "▐" not in selected and "▌" not in selected
    assert "○" in unselected and "▐" not in unselected and "▌" not in unselected


def test_project_categories_have_stable_order_and_remote_is_separate(tmp_path) -> None:
    from dashboard.models import RemoteProjectRegistration, SshProjectLocation
    from dashboard.services.project_selection import RegisteredRemoteProject
    from dashboard.services.projects import Project, ProjectStatus

    configured = ProjectStatus(
        project=Project("configured", tmp_path / "configured"),
        canonical_path=tmp_path / "configured",
        project_dir_exists=True,
        is_git_repo=True,
        git_branch="main",
        saved_workspace=object(),  # type: ignore[arg-type]
        workspace_metadata_error=None,
        expected_session_name="configured",
        tmux_available=True,
        session_running=False,
        last_modified=None,
    )
    # The pure helper's contract is also covered with the real status shape;
    # the remaining fields are intentionally irrelevant to classification.
    assert project_category(configured) == "Configured Projects"
    remote = RegisteredRemoteProject(
        "remote",
        SshProjectLocation("d84aeefb-7c29-4c63-b39c-766d559df977", "/srv/remote"),
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            "d84aeefb-7c29-4c63-b39c-766d559df977",
            "remote",
            "/srv/remote",
        ),
    )
    assert project_category(remote) == "Remote Projects"
    assert [group.title for group in group_project_entries([remote, configured])] == [
        "Configured Projects",
        "Remote Projects",
    ]
