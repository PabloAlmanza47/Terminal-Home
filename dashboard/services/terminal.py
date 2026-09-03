"""Small terminal-display helpers used after interactive launches."""

from __future__ import annotations

import sys
from typing import TextIO


def clear_terminal_display(stream: TextIO | None = None) -> None:
    """Clear the visible terminal and supported scrollback for a TTY."""
    output = stream if stream is not None else sys.stdout
    if not output.isatty():
        return
    # CSI 3 J clears scrollback where supported; unsupported terminals ignore it.
    try:
        output.write("\033[2J\033[3J\033[H")
        output.flush()
    except (OSError, ValueError):
        # Cleanup must never change the attach command's result or error.
        return
