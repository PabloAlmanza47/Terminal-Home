"""Raw-terminal entry point for ``th switch``.

The switcher is deliberately not a Textual application. It runs inside a
tmux popup, so the popup owns the shell while this module only draws selector
content and handles its keys.
"""

from __future__ import annotations

import os
import select
import shutil
import sys
import termios
import tty
from dataclasses import dataclass
from typing import TextIO

from dashboard.models import LaunchRequest
from dashboard.services.project_launch import ProjectLaunchPreparationError, prepare_project_launch
from dashboard.services.projects import ProjectScanResult, build_launch_request, scan_all_projects
from dashboard.services.quick_switch import (
    QuickSwitchEntry,
    build_quick_switch_entries,
    filter_quick_switch_entries,
    format_quick_switch_row,
)
from dashboard.services.tmux import TmuxCommandError
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request
from dashboard.services.workspace_store import WorkspaceStoreVersionError


@dataclass(frozen=True, slots=True)
class QuickSwitchRow:
    """A renderable row, with headings kept separate from entries."""

    text: str
    entry_index: int | None = None
    heading: bool = False


def decode_key(first: bytes, remainder: bytes = b"") -> str | None:
    """Decode one terminal key into a small, testable command vocabulary."""
    if first == b"\x03":
        return "interrupt"
    if first == b"\x1b":
        sequence = first + remainder
        if sequence in (b"\x1b[A", b"\x1bOA"):
            return "up"
        if sequence in (b"\x1b[B", b"\x1bOB"):
            return "down"
        return "escape"
    if first in (b"\r", b"\n"):
        return "enter"
    if first in (b"\x7f", b"\x08"):
        return "backspace"
    if first and first[0] >= 32:
        return first.decode("utf-8", errors="replace")
    return None


def move_selection(selected: int, count: int, direction: int) -> int:
    """Move through project entries without selecting a section heading."""
    if count <= 0:
        return 0
    return max(0, min(count - 1, selected + direction))


def build_quick_switch_rows(entries: list[QuickSwitchEntry]) -> list[QuickSwitchRow]:
    """Build ACTIVE/RECENT rows while retaining selectable-entry indexes."""
    rows: list[QuickSwitchRow] = []
    active = [entry for entry in entries if entry.is_running]
    recent = [entry for entry in entries if not entry.is_running]
    entry_index = 0
    if active:
        rows.append(QuickSwitchRow("ACTIVE", heading=True))
        for _entry in active:
            rows.append(QuickSwitchRow("", entry_index=entry_index))
            entry_index += 1
    if recent:
        rows.append(QuickSwitchRow("RECENT", heading=True))
        for _entry in recent:
            rows.append(QuickSwitchRow("", entry_index=entry_index))
            entry_index += 1
    if not rows:
        rows.append(QuickSwitchRow("No matching projects", heading=True))
    return rows


def _read_key(stream: TextIO) -> str | None:
    first = os.read(stream.fileno(), 1)
    if not first:
        return "escape"
    if first != b"\x1b":
        return decode_key(first)
    remainder = b""
    while len(remainder) < 2:
        ready, _, _ = select.select([stream], [], [], 0.03)
        if not ready:
            break
        remainder += os.read(stream.fileno(), 1)
    return decode_key(first, remainder)


def _terminal_size(stream: TextIO) -> tuple[int, int]:
    size = shutil.get_terminal_size((80, 24))
    return max(1, size.columns), max(4, size.lines)


def position_rendered_rows(rows: list[str], height: int) -> str:
    """Render rows at absolute terminal coordinates with no newline reliance."""
    positioned: list[str] = []
    for row_number in range(1, height + 1):
        text = rows[row_number - 1] if row_number <= len(rows) else ""
        positioned.append(f"\x1b[{row_number};1H\x1b[2K{text}")
    return "".join(positioned)


def _content_width(columns: int) -> int:
    """Keep status columns readable without stretching them to the popup edge."""
    return max(1, min(60, columns - 4))


def _entry_status(entry: QuickSwitchEntry) -> str:
    return "current" if entry.is_current else ("running" if entry.is_running else "stopped")


def _render_entry(entry: QuickSwitchEntry, selected: bool, columns: int) -> str:
    row = format_quick_switch_row(entry, _content_width(columns))
    prefix = "> " if selected else "  "
    if selected:
        return f"\x1b[1m{prefix}{row}\x1b[0m"
    status = _entry_status(entry)
    status_start = len(row) - len(status)
    return f"{prefix}{row[:status_start]}\x1b[2m{row[status_start:]}\x1b[0m"


def _render(
    entries: list[QuickSwitchEntry], query: str, selected: int, scroll: int, stream: TextIO
) -> int:
    """Draw the selector and return the corrected scroll offset."""
    columns, lines = _terminal_size(stream)
    rows = build_quick_switch_rows(entries)
    footer_row = lines - 1
    list_height = max(1, lines - 3)
    selected_row = next(
        (index for index, row in enumerate(rows) if row.entry_index == selected), 0
    )
    scroll = max(0, min(scroll, max(0, len(rows) - list_height)))
    if selected_row < scroll:
        scroll = selected_row
    elif selected_row >= scroll + list_height:
        scroll = selected_row - list_height + 1

    output = [f"› {query or 'Search projects...'}", ""]
    for row in rows[scroll : scroll + list_height]:
        if row.entry_index is None:
            text = row.text
        else:
            entry = entries[row.entry_index]
            text = _render_entry(entry, row.entry_index == selected, columns)
        output.append(f"\x1b[2m{text}\x1b[0m" if row.heading else text)
    while len(output) < footer_row:
        output.append("")
    output = output[:footer_row]
    output.append("\x1b[2m↑↓ navigate   ↵ switch   esc close\x1b[0m")
    stream.write(position_rendered_rows(output, lines) + "\x1b[0m")
    stream.flush()
    return scroll


def run_selector(stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> LaunchRequest | None:
    """Run the raw selector and return the normal shared launch request."""
    if not stdin.isatty() or not stdout.isatty():
        raise RuntimeError("th switch requires an interactive terminal")

    scan: ProjectScanResult = scan_all_projects()
    from dashboard.services import tmux

    entries = build_quick_switch_entries(scan, tmux.current_client_session())
    query = ""
    selected = 0
    scroll = 0
    fd = stdin.fileno()
    original_termios = termios.tcgetattr(fd)
    stdout.write("\x1b[?25l")
    stdout.flush()
    try:
        tty.setraw(fd)
        while True:
            visible = filter_quick_switch_entries(entries, query)
            selected = move_selection(selected, len(visible), 0)
            scroll = _render(visible, query, selected, scroll, stdout)
            key = _read_key(stdin)
            if key in ("escape", "interrupt"):
                return None
            if key == "up":
                selected = move_selection(selected, len(visible), -1)
            elif key == "down":
                selected = move_selection(selected, len(visible), 1)
            elif key == "backspace":
                query = query[:-1]
                selected = 0
                scroll = 0
            elif key == "enter":
                if visible:
                    entry = visible[selected]
                    return (
                        build_launch_request(entry.status)
                        if entry.is_running
                        else prepare_project_launch(entry.status).request
                    )
            elif key and len(key) == 1 and key.isprintable():
                query += key
                selected = 0
                scroll = 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original_termios)
        stdout.write("\x1b[?25h\x1b[H\x1b[J")
        stdout.flush()


def main() -> None:
    try:
        request = run_selector()
        if request is not None:
            execute_launch_request(request)
    except (
        LaunchError,
        TmuxCommandError,
        OSError,
        ProjectLaunchPreparationError,
        WorkspaceStoreVersionError,
        RuntimeError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
