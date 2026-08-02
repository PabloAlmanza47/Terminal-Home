"""tmux session listing (version 1) plus workspace construction (version 2).

Command *construction* is kept as pure, deterministic functions -- given a
WorkspaceSpec and a map of pane launch plans, build_workspace_commands
returns the exact argv list tmux will be asked to run, with no subprocess
calls of its own. Command *execution* (create_workspace_session,
run_tmux_command) is a thin, separately-mockable layer on top, so the
construction logic can be unit tested without ever touching a real tmux
server, and callers can swap in a fake runner.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from dashboard.models import WorkspaceSpec
from dashboard.models.layout import tmux_layout_for_pane_count
from dashboard.services.pane_commands import PaneLaunchPlan

_LIST_FORMAT = "#{session_name}\t#{session_windows}\t#{session_created_string}\t#{session_attached}"
_SUBPROCESS_TIMEOUT_SECONDS = 3
_SESSION_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True, slots=True)
class TmuxSession:
    """One row from `tmux list-sessions`."""

    name: str
    windows: int
    created: str
    attached: bool


class TmuxCommandError(Exception):
    """Raised when a tmux command used to build or query a workspace fails."""


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


# --- Workspace construction (version 2) -------------------------------------


def sanitize_session_name(name: str) -> str:
    """Collapse *name* into characters tmux session names accept safely.

    tmux uses `:` and `.` as target-syntax separators and is unhappy about
    whitespace, so everything but letters, digits, `_`, and `-` is folded
    into a single hyphen.
    """
    sanitized = _SESSION_NAME_UNSAFE.sub("-", name.strip()).strip("-")
    return sanitized or "workspace"


def session_exists(session_name: str) -> bool:
    """Whether a tmux session named *session_name* is currently running."""
    if not is_tmux_installed():
        return False
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", session_name],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def generate_session_name(project_name: str, existing: Iterable[str] | None = None) -> str:
    """A deterministic, sanitized, collision-free session name for
    *project_name*.

    *existing* is the set of session names to avoid; if not given, the
    currently running tmux sessions are queried. On a collision, `-2`,
    `-3`, ... is appended until a free name is found.
    """
    base = sanitize_session_name(project_name)
    existing_names = (
        set(existing) if existing is not None else {s.name for s in list_tmux_sessions()}
    )
    if base not in existing_names:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing_names:
        suffix += 1
    return f"{base}-{suffix}"


def get_pane_base_index() -> int:
    """The tmux `pane-base-index` global option (default 0), so panes we
    create can be addressed correctly under a user's existing tmux.conf
    without this dashboard ever needing to read or change that file.
    """
    if not is_tmux_installed():
        return 0
    try:
        result = subprocess.run(
            ["tmux", "show-options", "-g", "pane-base-index"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    parts = result.stdout.strip().split()
    if len(parts) == 2 and parts[1].isdigit():
        return int(parts[1])
    return 0


def build_workspace_commands(
    workspace: WorkspaceSpec,
    pane_plans: Mapping[tuple[str, int], PaneLaunchPlan],
    *,
    pane_base_index: int = 0,
) -> list[list[str]]:
    """Build the full, ordered list of tmux argv commands that create
    *workspace* as a tmux session -- windows, panes, layouts, titles, and
    startup commands -- with no side effects. *pane_plans* maps
    (window_name, pane_index) to the PaneLaunchPlan for that pane.
    """
    commands: list[list[str]] = []
    session = workspace.session_name
    project_dir = str(workspace.project_path)
    first_window = workspace.windows[0]

    commands.append(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            session,
            "-n",
            first_window.window_name,
            "-c",
            project_dir,
        ]
    )

    for window_index, window in enumerate(workspace.windows):
        window_target = f"{session}:{window.window_name}"

        if window_index > 0:
            commands.append(
                ["tmux", "new-window", "-t", session, "-n", window.window_name, "-c", project_dir]
            )

        for _ in range(len(window.panes) - 1):
            commands.append(["tmux", "split-window", "-t", window_target, "-c", project_dir])

        layout = tmux_layout_for_pane_count(len(window.panes))
        if layout is not None:
            commands.append(["tmux", "select-layout", "-t", window_target, layout])

        for pane_index, _pane in enumerate(window.panes):
            plan = pane_plans.get((window.window_name, pane_index))
            if plan is None:
                continue
            pane_target = f"{window_target}.{pane_base_index + pane_index}"
            if plan.pane_title:
                commands.append(["tmux", "select-pane", "-t", pane_target, "-T", plan.pane_title])
            if plan.startup_command:
                commands.append(
                    ["tmux", "send-keys", "-t", pane_target, plan.startup_command, "Enter"]
                )

    first_window_target = f"{session}:{first_window.window_name}"
    commands.append(["tmux", "select-window", "-t", first_window_target])
    commands.append(["tmux", "select-pane", "-t", f"{first_window_target}.{pane_base_index}"])

    return commands


def run_tmux_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a single tmux argv command, capturing its output."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS
    )


def create_workspace_session(
    workspace: WorkspaceSpec,
    pane_plans: Mapping[tuple[str, int], PaneLaunchPlan],
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = run_tmux_command,
) -> None:
    """Create *workspace* as a real tmux session by running each build
    command in order via *runner*.

    Never touches a pre-existing session: raises immediately if one with
    this name is already running. If a later command fails partway
    through, the session this call just created is killed (never a
    pre-existing one) so a half-built workspace isn't left behind.
    """
    if session_exists(workspace.session_name):
        raise TmuxCommandError(f"A tmux session named '{workspace.session_name}' already exists.")

    pane_base_index = get_pane_base_index()
    commands = build_workspace_commands(workspace, pane_plans, pane_base_index=pane_base_index)

    session_created = False
    try:
        for argv in commands:
            result = runner(argv)
            if result.returncode != 0:
                raise TmuxCommandError(
                    f"Command failed: {' '.join(argv)}\n{result.stderr.strip()}"
                )
            if argv[1] == "new-session":
                session_created = True
    except TmuxCommandError:
        if session_created and session_exists(workspace.session_name):
            with contextlib.suppress(Exception):
                runner(["tmux", "kill-session", "-t", workspace.session_name])
        raise


def is_inside_tmux() -> bool:
    """Whether this process is itself running inside a tmux client."""
    return bool(os.environ.get("TMUX"))


def attach_or_switch_argv(session_name: str, *, inside_tmux: bool | None = None) -> list[str]:
    """The tmux argv to hand control to *session_name*: `switch-client`
    when already inside tmux, `attach-session` otherwise.
    """
    inside = is_inside_tmux() if inside_tmux is None else inside_tmux
    if inside:
        return ["tmux", "switch-client", "-t", session_name]
    return ["tmux", "attach-session", "-t", session_name]


def exec_attach(argv: list[str]) -> None:
    """Replace the current process with *argv*, handing the terminal over
    to tmux. Never returns on success -- only used by the non-Textual
    orchestration layer, after the Textual app has fully exited.
    """
    os.execvp(argv[0], argv)  # noqa: S606
