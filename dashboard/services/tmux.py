"""Read-only tmux session listing. Version 1 never attaches to a session."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

_LIST_FORMAT = "#{session_name}\t#{session_windows}\t#{session_created_string}\t#{session_attached}"
_SUBPROCESS_TIMEOUT_SECONDS = 3


@dataclass(frozen=True, slots=True)
class TmuxSession:
    """One row from `tmux list-sessions`."""

    name: str
    windows: int
    created: str
    attached: bool


def is_tmux_installed() -> bool:
    """Whether the `tmux` binary is on PATH."""
    return shutil.which("tmux") is not None


def list_tmux_sessions() -> list[TmuxSession]:
    """Return currently running tmux sessions.

    Returns an empty list if tmux is not installed, no server is running, or
    the command fails for any other reason -- callers use is_tmux_installed()
    separately to tell "not installed" from "installed, no sessions" in the UI.
    """
    if not is_tmux_installed():
        return []

    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", _LIST_FORMAT],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    # A non-zero exit here almost always just means "no server running".
    if result.returncode != 0 or not result.stdout.strip():
        return []

    sessions: list[TmuxSession] = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        name, windows, created, attached = parts
        sessions.append(
            TmuxSession(
                name=name,
                windows=int(windows) if windows.isdigit() else 0,
                created=created,
                attached=attached == "1",
            )
        )
    return sessions


def get_tmux_version() -> str | None:
    """Return the `tmux -V` output, or None if tmux is unavailable."""
    if not is_tmux_installed():
        return None
    try:
        result = subprocess.run(
            ["tmux", "-V"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None
