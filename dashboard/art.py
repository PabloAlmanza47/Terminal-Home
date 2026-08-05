"""Terminal-safe Terminal Home identity artwork."""

from __future__ import annotations

FULL_ART = "\n".join(
    (
        "╭──────╮   TERMINAL HOME",
        "│ >_   │   Projects, workspaces, and tmux",
        "╰──────╯",
    )
)
COMPACT_ART = "╭─>_─╮  TERMINAL HOME"
ASCII_ART = FULL_ART


def artwork_for_size(width: int, height: int, enabled: bool = True) -> str | None:
    if not enabled or height < 20 or width < 60:
        return None
    if width >= 120 and height >= 30:
        return FULL_ART
    return COMPACT_ART
