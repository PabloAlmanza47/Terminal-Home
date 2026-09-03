from __future__ import annotations

import io

from dashboard.services.terminal import clear_terminal_display


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_clear_terminal_display_clears_screen_scrollback_and_moves_cursor_home() -> None:
    stream = _Tty()

    clear_terminal_display(stream)

    assert stream.getvalue() == "\033[2J\033[3J\033[H"


def test_clear_terminal_display_skips_noninteractive_output() -> None:
    stream = io.StringIO()

    clear_terminal_display(stream)

    assert stream.getvalue() == ""
