"""Responsive Terminal Home identity artwork."""

from __future__ import annotations

FULL_ARTWORK = "╭──────╮   TERMINAL HOME\n│ >_   │   Projects, workspaces, and tmux\n╰──────╯"
COMPACT_ARTWORK = "╭─>_─╮  TERMINAL HOME"


def artwork_for_size(width: int, height: int, enabled: bool) -> str | None:
    """Choose full, compact, or hidden artwork for the available terminal."""
    if not enabled or width < 32 or height < 12:
        return None
    if width >= 100 and height >= 24:
        return FULL_ARTWORK
    return COMPACT_ARTWORK
