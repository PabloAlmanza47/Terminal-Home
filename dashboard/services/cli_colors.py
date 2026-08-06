"""Textual-independent color policy for CLI table headings."""

from __future__ import annotations

import os
from typing import TextIO

from dashboard.models.settings import TableHeaderColor

_ANSI = {
    TableHeaderColor.THEME: "96",
    TableHeaderColor.BLUE: "34",
    TableHeaderColor.CYAN: "36",
    TableHeaderColor.GREEN: "32",
    TableHeaderColor.MAGENTA: "35",
    TableHeaderColor.YELLOW: "33",
    TableHeaderColor.WHITE: "37",
}


def color_output_enabled(stream: TextIO) -> bool:
    return "NO_COLOR" not in os.environ and bool(getattr(stream, "isatty", lambda: False)())


def style_table_header(line: str, setting: TableHeaderColor, stream: TextIO) -> str:
    if setting is TableHeaderColor.NONE or not color_output_enabled(stream):
        return line
    code = _ANSI.get(setting, _ANSI[TableHeaderColor.THEME])
    return f"\033[{code}m{line}\033[0m"
