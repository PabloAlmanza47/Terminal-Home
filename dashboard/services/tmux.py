"""tmux session listing (version 1) plus workspace construction (version 2).

Command *construction* and *execution* are interleaved in
create_workspace_session: each window/pane-creating command asks tmux to
report back the stable id it just assigned (`-P -F`), and every later
command in that workspace targets ids, never an assumed numeric window/pane
index. The individual argv-builder functions (`_new_session_argv`, etc.) are
pure and separately testable; create_workspace_session accepts an injectable
*runner*, so the whole flow can be unit tested with a fake tmux without ever
touching a real server.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from dashboard.models import WindowSpec, WorkspaceSpec
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


def run_tmux_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a single tmux argv command, capturing its output."""
    return subprocess.run(
        argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS
    )


def session_exists(
    session_name: str,
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = run_tmux_command,
) -> bool:
    """Whether a tmux session named *session_name* is currently running.

    Takes the same injectable *runner* as create_workspace_session so a
    caller pointed at an alternate tmux socket (e.g. `-L terminal-home-test`)
    checks that socket, not whatever server the bare `tmux` binary defaults
    to -- callers that don't pass one still hit the real server via
    run_tmux_command, unchanged.
    """
    if not is_tmux_installed():
        return False
    try:
        result = runner(["tmux", "has-session", "-t", session_name])
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


# A window/pane's numeric *index* shifts with the user's `base-index` /
# `pane-base-index` tmux.conf settings, so a target string built from an
# assumed index (e.g. "session:window.0") can point at the wrong pane -- or
# none at all -- depending on what the user's config says. Every command
# below instead asks tmux to report back the stable id (`@N` / `%N`) it just
# assigned via `-P -F`, and every later command targets that id directly, so
# construction never has to know or guess any base-index value.
_CREATE_CAPTURE_FORMAT = "#{window_id} #{pane_id}"
_PANE_CAPTURE_FORMAT = "#{pane_id}"


def _new_session_argv(session_name: str, window_name: str, project_dir: str) -> list[str]:
    return [
        "tmux",
        "new-session",
        "-d",
        "-s",
        session_name,
        "-n",
        window_name,
        "-c",
        project_dir,
        "-P",
        "-F",
        _CREATE_CAPTURE_FORMAT,
    ]


def _new_window_argv(session_name: str, window_name: str, project_dir: str) -> list[str]:
    return [
        "tmux",
        "new-window",
        "-t",
        session_name,
        "-n",
        window_name,
        "-c",
        project_dir,
        "-P",
        "-F",
        _CREATE_CAPTURE_FORMAT,
    ]


def _split_window_argv(window_id: str, project_dir: str) -> list[str]:
    return [
        "tmux",
        "split-window",
        "-t",
        window_id,
        "-c",
        project_dir,
        "-P",
        "-F",
        _PANE_CAPTURE_FORMAT,
    ]


def _select_layout_argv(window_id: str, layout: str) -> list[str]:
    return ["tmux", "select-layout", "-t", window_id, layout]


def _select_pane_title_argv(pane_id: str, title: str) -> list[str]:
    return ["tmux", "select-pane", "-t", pane_id, "-T", title]


def _send_keys_argv(pane_id: str, command: str) -> list[str]:
    return ["tmux", "send-keys", "-t", pane_id, command, "Enter"]


def _select_window_argv(window_id: str) -> list[str]:
    return ["tmux", "select-window", "-t", window_id]


def _select_pane_argv(pane_id: str) -> list[str]:
    return ["tmux", "select-pane", "-t", pane_id]


def _run_step(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]], argv: list[str]
) -> subprocess.CompletedProcess[str]:
    result = runner(argv)
    if result.returncode != 0:
        raise TmuxCommandError(f"Command failed: {' '.join(argv)}\n{result.stderr.strip()}")
    return result


def _parse_create_capture(
    result: subprocess.CompletedProcess[str], argv: list[str]
) -> tuple[str, str]:
    parts = result.stdout.strip().split(" ", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise TmuxCommandError(
            f"Command succeeded but did not report back a window/pane id: {' '.join(argv)}"
        )
    return parts[0], parts[1]


def _parse_pane_capture(result: subprocess.CompletedProcess[str], argv: list[str]) -> str:
    pane_id = result.stdout.strip()
    if not pane_id:
        raise TmuxCommandError(
            f"Command succeeded but did not report back a pane id: {' '.join(argv)}"
        )
    return pane_id


def _create_window_panes(
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
    window: WindowSpec,
    window_id: str,
    first_pane_id: str,
    project_dir: str,
    pane_plans: Mapping[tuple[str, int], PaneLaunchPlan],
) -> list[str]:
    """Split *window_id* out to its full pane count, apply its layout, then
    apply each pane's title/startup command -- by the pane id tmux reported
    back for it, in WorkspaceSpec pane order. Returns the pane ids in that
    same order (index 0 is *first_pane_id*, the pane the window was created
    with).
    """
    pane_ids = [first_pane_id]
    for _ in range(len(window.panes) - 1):
        argv = _split_window_argv(window_id, project_dir)
        result = _run_step(runner, argv)
        pane_ids.append(_parse_pane_capture(result, argv))

    layout = tmux_layout_for_pane_count(len(window.panes))
    if layout is not None:
        _run_step(runner, _select_layout_argv(window_id, layout))

    for pane_index, pane_id in enumerate(pane_ids):
        plan = pane_plans.get((window.window_name, pane_index))
        if plan is None:
            continue
        if plan.pane_title:
            _run_step(runner, _select_pane_title_argv(pane_id, plan.pane_title))
        if plan.startup_command:
            _run_step(runner, _send_keys_argv(pane_id, plan.startup_command))

    return pane_ids


def create_workspace_session(
    workspace: WorkspaceSpec,
    pane_plans: Mapping[tuple[str, int], PaneLaunchPlan],
    *,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] = run_tmux_command,
) -> None:
    """Create *workspace* as a real tmux session by running each tmux
    command in order via *runner*.

    Every window and pane is targeted by the stable id (`@N` / `%N`) tmux
    itself reports back via `-P -F` when it's created -- never by an
    assumed numeric window/pane index -- so this is correct under any
    base-index/pane-base-index the user's tmux.conf sets, and regardless of
    whether a tmux server happens to already be running.

    Never touches a pre-existing session: raises immediately if one with
    this name is already running. If a later command fails partway
    through, the session this call just created is killed (never a
    pre-existing one) so a half-built workspace isn't left behind.
    """
    if session_exists(workspace.session_name, runner=runner):
        raise TmuxCommandError(f"A tmux session named '{workspace.session_name}' already exists.")

    session = workspace.session_name
    project_dir = str(workspace.project_path)
    first_window = workspace.windows[0]

    session_created = False
    try:
        argv = _new_session_argv(session, first_window.window_name, project_dir)
        result = _run_step(runner, argv)
        session_created = True
        first_window_id, first_pane_id = _parse_create_capture(result, argv)

        first_window_pane_ids = _create_window_panes(
            runner, first_window, first_window_id, first_pane_id, project_dir, pane_plans
        )

        for window in workspace.windows[1:]:
            argv = _new_window_argv(session, window.window_name, project_dir)
            result = _run_step(runner, argv)
            window_id, pane_id = _parse_create_capture(result, argv)
            _create_window_panes(runner, window, window_id, pane_id, project_dir, pane_plans)

        _run_step(runner, _select_window_argv(first_window_id))
        _run_step(runner, _select_pane_argv(first_window_pane_ids[0]))
    except TmuxCommandError:
        if session_created and session_exists(session, runner=runner):
            with contextlib.suppress(Exception):
                runner(["tmux", "kill-session", "-t", session])
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
