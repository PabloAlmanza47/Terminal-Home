"""Pure width-aware formatting for project selection rows."""

from __future__ import annotations

from dataclasses import dataclass

from rich.cells import cell_len

from dashboard.services.project_selection import RegisteredRemoteProject

_STATUS_COLUMN_WIDTH = 20
_BRANCH_COLUMN_WIDTH = 24
_COLUMN_GAP = 3
_MIN_NAME_WIDTH = 8


@dataclass(frozen=True, slots=True)
class RecentProjectRow:
    name: str
    status: str
    branch: str | None = None
    detail: str | None = None


def project_row_width(available_width: int, *, leading_indent: int = 0) -> int:
    """Return label width from the OptionList content region.

    Textual's ``content_region`` already excludes the widget border, padding,
    and a visible scrollbar. The marker is rendered by KeyboardOptionList, so
    reserve its two terminal cells here before formatting the label itself.
    """
    return max(1, available_width - 2 - leading_indent)


def _ellipsis(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if cell_len(value) <= width:
        return value
    if width == 1:
        return "…"
    # Project disambiguation puts the useful root suffix at the end of a
    # long label. Keep more of that suffix so similarly named projects remain
    # distinguishable after truncation.
    left_budget = max(1, (width - 1) // 3)
    left = ""
    used = 0
    for character in value:
        cells = cell_len(character)
        if used + cells > left_budget:
            break
        left += character
        used += cells
    right_budget = width - 1 - used
    right_chars: list[str] = []
    used_right = 0
    for character in reversed(value):
        cells = cell_len(character)
        if used_right + cells > right_budget:
            break
        right_chars.append(character)
        used_right += cells
    return left + "…" + "".join(reversed(right_chars))


def _pad_right(value: str, width: int) -> str:
    return value + " " * max(0, width - cell_len(value))


def _pad_left(value: str, width: int) -> str:
    return " " * max(0, width - cell_len(value)) + value


def _status_token(status: str, width: int | None = None) -> str:
    token = f"[{status}]"
    if width is None or cell_len(token) <= width:
        return token
    inner = _ellipsis(status, max(0, width - 2))
    return f"[{inner}]"


def _compact_row(name: str, status: str, branch: str | None, width: int) -> str:
    token = _status_token(status)
    if cell_len(token) > width:
        token = _status_token(status, width)
    branch_text = branch or ""
    separators = 2 if branch_text else 1
    branch_budget = max(0, width - cell_len(token) - separators - 1)
    branch_text = _ellipsis(branch_text, branch_budget)
    if branch_text:
        name_budget = max(1, width - cell_len(token) - cell_len(branch_text) - 2)
        line = f"{_ellipsis(name, name_budget)} {token} {branch_text}"
    else:
        name_budget = max(1, width - cell_len(token) - 1)
        line = f"{_ellipsis(name, name_budget)} {token}"
    return _ellipsis(line, width)


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
    status_width = max(_STATUS_COLUMN_WIDTH, cell_len(_status_token(status)))
    # Reserve the branch column even when this row has no branch. That keeps
    # every status column aligned, while the branchless row still ends at its
    # closing status bracket without trailing field padding.
    branch_width = min(
        _BRANCH_COLUMN_WIDTH,
        max(1, width - _MIN_NAME_WIDTH - 2 * _COLUMN_GAP - status_width),
    )
    required = _MIN_NAME_WIDTH + _COLUMN_GAP + status_width
    required += _COLUMN_GAP + branch_width
    if width < required:
        return _compact_row(name, status, branch, width)

    name_width = width - _COLUMN_GAP - status_width
    name_width -= _COLUMN_GAP + branch_width
    if name_width < _MIN_NAME_WIDTH:
        return _compact_row(name, status, branch, width)

    name_text = _pad_right(_ellipsis(name, name_width), name_width)
    token = _status_token(status)
    row = f"{name_text}{' ' * _COLUMN_GAP}{token}"
    if branch:
        row += " " * (status_width - cell_len(token) + _COLUMN_GAP)
        row += _pad_right(_ellipsis(branch, branch_width), branch_width)
    return row


def format_remote_project_row(
    project: RegisteredRemoteProject,
    host_label: str,
    width: int,
) -> str:
    """Return a consistent local-only representation of a remote project."""
    remote = f"[Remote] {host_label} {project.location.remote_path}"
    wide = f"{_pad_right(_ellipsis(project.name, 24), 24)}  {remote}"
    if cell_len(wide) <= width:
        return wide
    status = "[Remote]"
    suffix = f"{host_label} {project.location.remote_path}"
    name_budget = max(1, width - cell_len(status) - 2)
    name_text = _ellipsis(project.name, name_budget)
    suffix_budget = max(0, width - cell_len(name_text) - cell_len(status) - 2)
    suffix_text = _ellipsis(suffix, suffix_budget)
    return _ellipsis(f"{name_text} {status} {suffix_text}".rstrip(), width)


def format_recent_project_rows(
    rows: list[RecentProjectRow], width: int, *, compact: bool
) -> list[str]:
    """Align Recent Projects columns within a marker-excluded width.

    Compact mode omits secondary detail, but keeps the three useful columns
    whenever the actual list content region can accommodate them.
    """
    width = max(1, width)
    if not rows:
        return []

    gap = 2
    status_width = max(cell_len(_status_token(row.status)) for row in rows)
    branch_rows = [row for row in rows if row.branch]
    branch_width = (
        min(22, max(5, max(cell_len(f"({row.branch})") for row in branch_rows)))
        if branch_rows
        else 0
    )
    name_width = min(28, max(8, max(cell_len(row.name) for row in rows)))

    # Keep the status token and a useful branch column first. Let the name
    # absorb the remaining room, then shorten it before falling back to the
    # genuinely narrow inline representation.
    required = name_width + gap + status_width + (gap + branch_width if branch_width else 0)
    if required > width:
        name_width = max(
            4,
            width - gap - status_width - (gap + branch_width if branch_width else 0),
        )
    if name_width + gap + status_width + (gap + branch_width if branch_width else 0) > width:
        branch_width = max(3, width - name_width - gap - status_width - gap)
    if name_width + gap + status_width + (gap + branch_width if branch_width else 0) > width:
        return [
            _ellipsis(
                f"{row.name} {_status_token(row.status)}"
                + (
                    f" ({_ellipsis(row.branch or '', max(1, width // 3) - 2)})"
                    if row.branch
                    else ""
                ),
                width,
            )
            for row in rows
        ]

    result = []
    for row in rows:
        detail = ""
        if row.branch:
            branch_budget = max(1, branch_width - 2)
            detail = f"({_ellipsis(row.branch, branch_budget)})"
        if row.detail and not compact:
            detail = f"{detail}  {row.detail}".strip()
        name = _pad_right(_ellipsis(row.name, name_width), name_width)
        token = _status_token(row.status)
        line = f"{name}{' ' * gap}{_pad_right(token, status_width)}{' ' * gap}"
        detail_width = max(0, width - cell_len(line))
        result.append((line + _ellipsis(detail, detail_width)).rstrip())
    return result
