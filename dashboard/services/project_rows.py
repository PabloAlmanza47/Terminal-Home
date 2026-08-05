"""Pure width-aware formatting for project selection rows."""

from __future__ import annotations

from dashboard.services.project_selection import RegisteredRemoteProject

_STATUS_WIDTH = 22
_NAME_WIDTH = 24


def _ellipsis(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return "…"
    left = max(1, (width - 1) // 2)
    return value[:left] + "…" + value[-(width - left - 1) :]


def format_project_row(
    name: str,
    status: str,
    branch: str | None,
    width: int,
) -> str:
    """Return one non-wrapping local project row no wider than *width*.

    The marker is owned by KeyboardOptionList, so every content row starts at
    the same column regardless of focus.
    """
    width = max(1, width)
    if width < 52:
        inline = f"{name} [{status}]"
        if branch:
            inline += f" {branch}"
        return _ellipsis(inline, width)

    branch_text = branch or ""
    branch_width = min(max(10, len(branch_text)), max(10, width - _NAME_WIDTH - _STATUS_WIDTH - 2))
    status_width = _STATUS_WIDTH
    name_width = max(8, width - status_width - branch_width - 4)
    if name_width < 8:
        return _ellipsis(f"{name} [{status}] {branch_text}".strip(), width)
    row = (
        f"{_ellipsis(name, name_width):<{name_width}}  "
        f"[{_ellipsis(status, status_width - 2):<{status_width - 2}}]"
    )
    if branch:
        row += f"  {_ellipsis(branch, branch_width):>{branch_width}}"
    return _ellipsis(row, width)


def format_remote_project_row(
    project: RegisteredRemoteProject,
    host_label: str,
    width: int,
) -> str:
    """Return a consistent local-only representation of a remote project."""
    if width < 80:
        return _ellipsis(
            f"{project.name} [Remote] {project.location.remote_path}", width
        )
    return _ellipsis(
        f"{project.name:<24}  [Remote]  {host_label:<18}  {project.location.remote_path}", width
    )
