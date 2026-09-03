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
from pathlib import Path
from typing import Literal

from dashboard.models import LocalProjectLocation, SshProjectLocation, WindowSpec, WorkspaceSpec
from dashboard.models.layout import tmux_layout_for_pane_count
from dashboard.services.pane_commands import PaneLaunchPlan
from dashboard.services.pane_layout_store import PaneLayout
from dashboard.services.ssh import SshCommandResult, quote_remote_argument, run_ssh_command
from dashboard.services.ssh_host_store import get_ssh_host

_LIST_FORMAT = "#{session_name}\t#{session_windows}\t#{session_created_string}\t#{session_attached}"
_WINDOW_LAYOUT_FORMAT = "#{window_name}\t#{window_panes}\t#{window_layout}"
_PANE_STATUS_FORMAT = (
    "#{session_name}\t#{window_name}\t#{pane_title}\t"
    "#{pane_current_command}\t#{pane_dead}"
)
_SUBPROCESS_TIMEOUT_SECONDS = 3
_SESSION_NAME_UNSAFE = re.compile(r"[^a-zA-Z0-9_-]+")

TmuxCommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
TmuxRunnerResolutionStatus = Literal["resolved", "missing-host"]


@dataclass(frozen=True, slots=True)
class TmuxSession:
    """One row from `tmux list-sessions`."""

    name: str
    windows: int
    created: str
    attached: bool


@dataclass(frozen=True, slots=True)
class TmuxPaneRuntime:
    session_name: str
    window_name: str
    title: str
    current_command: str
    dead: bool


def _parse_window_layouts(output: str) -> dict[str, PaneLayout]:
    """Parse tmux's tab-separated window layout report, skipping bad rows."""
    layouts: dict[str, PaneLayout] = {}
    ambiguous: set[str] = set()
    for line in output.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        name, pane_count, layout = fields
        try:
            parsed_count = int(pane_count)
            parsed = PaneLayout(name, parsed_count, layout)
        except (TypeError, ValueError):
            continue
        if name in layouts or name in ambiguous:
            # Two windows with one name cannot be associated safely.
            ambiguous.add(name)
            layouts.pop(name, None)
            continue
        layouts[name] = parsed
    return layouts


def capture_tmux_window_layouts(
    session_name: str, *, runner: TmuxCommandRunner | None = None
) -> dict[str, PaneLayout]:
    """Capture current layouts by window name.

    A failed tmux command raises ``TmuxCommandError``. Individual malformed
    output rows are ignored so a partial or stale report cannot affect other
    state.
    """
    argv = ["tmux", "list-windows", "-t", session_name, "-F", _WINDOW_LAYOUT_FORMAT]
    result = _run_step(runner or run_tmux_command, argv)
    return _parse_window_layouts(result.stdout)


class TmuxCommandError(Exception):
    """Raised when a tmux command used to build or query a workspace fails."""


@dataclass(frozen=True, slots=True)
class TmuxRunnerResolutionError:
    """A structured failure to resolve a workspace's tmux runner."""

    status: Literal["missing-host"]
    host_id: str
    message: str


@dataclass(frozen=True, slots=True)
class TmuxRunnerResolution:
    """The runner selected for a workspace, or a structured resolution error."""

    status: TmuxRunnerResolutionStatus
    runner: TmuxCommandRunner | None = None
    error: TmuxRunnerResolutionError | None = None


def resolve_tmux_runner(
    workspace: WorkspaceSpec,
    *,
    host_store_path: Path | None = None,
) -> TmuxRunnerResolution:
    """Resolve the local or SSH tmux runner for *workspace*.

    This only selects a runner; it does not execute tmux or SSH commands and
    does not inspect or modify a project.  Remote paths remain the strings
    held by ``SshProjectLocation`` and are not needed to construct the runner.
    """
    location = workspace.project_location
    if isinstance(location, LocalProjectLocation):
        return TmuxRunnerResolution(status="resolved", runner=run_tmux_command)

    if not isinstance(location, SshProjectLocation):
        raise TypeError("Unsupported workspace project location.")

    host = get_ssh_host(location.host_id, host_store_path)
    if host is None:
        return TmuxRunnerResolution(
            status="missing-host",
            error=TmuxRunnerResolutionError(
                status="missing-host",
                host_id=location.host_id,
                message=f"SSH host {location.host_id} is not registered.",
            ),
        )
    return TmuxRunnerResolution(
        status="resolved",
        runner=SshTmuxCommandRunner(host.destination),
    )


def workspace_project_dir(workspace: WorkspaceSpec) -> str:
    """Return the tmux working directory without converting remote paths."""
    location = workspace.project_location
    if isinstance(location, LocalProjectLocation):
        return str(location.path)
    if isinstance(location, SshProjectLocation):
        return location.remote_path
    raise TypeError("Unsupported workspace project location.")


def is_tmux_installed() -> bool:
    """Whether the `tmux` binary is on PATH."""
    return shutil.which("tmux") is not None


def run_local_tmux_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute one tmux argv command locally, capturing its output."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_SECONDS)


def run_tmux_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Backward-compatible name for the local tmux command runner."""
    return run_local_tmux_command(argv)


class SshTmuxCommandRunner:
    """Run tmux argv through one noninteractive SSH command.

    The callable accepts tmux argv only.  It quotes each individual argument
    for the remote POSIX shell and passes the SSH destination separately to
    the transport; callers cannot provide a precomposed shell command here.
    """

    def __init__(
        self,
        destination: str,
        *,
        connection_timeout: int = _SUBPROCESS_TIMEOUT_SECONDS,
        execution_timeout: float = _SUBPROCESS_TIMEOUT_SECONDS,
        max_output_chars: int | None = None,
        ssh_runner: Callable[..., SshCommandResult] = run_ssh_command,
    ) -> None:
        self.destination = destination
        self.connection_timeout = connection_timeout
        self.execution_timeout = execution_timeout
        self.max_output_chars = max_output_chars
        self.ssh_runner = ssh_runner

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if not argv:
            raise ValueError("tmux argv cannot be empty")

        remote_command = " ".join(quote_remote_argument(argument) for argument in argv)
        options: dict[str, object] = {
            "connection_timeout": self.connection_timeout,
            "execution_timeout": self.execution_timeout,
        }
        if self.max_output_chars is not None:
            options["max_output_chars"] = self.max_output_chars
        result = self.ssh_runner(self.destination, remote_command, **options)

        if result.status == "timeout":
            raise subprocess.TimeoutExpired(
                cmd=argv,
                timeout=self.execution_timeout,
                output=result.stdout,
                stderr=result.stderr,
            )
        if result.status == "missing-ssh":
            raise FileNotFoundError(result.error or "The ssh executable was not found.")
        if result.returncode is None:
            raise TmuxCommandError(
                result.error or f"SSH tmux command failed with status {result.status}."
            )

        return subprocess.CompletedProcess(
            argv,
            result.returncode,
            result.stdout,
            result.stderr,
        )


def list_tmux_sessions(*, runner: TmuxCommandRunner = run_tmux_command) -> list[TmuxSession]:
    """Return currently running tmux sessions.

    Returns an empty list if tmux is not installed, no server is running, or
    the command fails for any other reason -- callers use is_tmux_installed()
    separately to tell "not installed" from "installed, no sessions" in the UI.
    """
    if runner is run_tmux_command and not is_tmux_installed():
        return []

    try:
        result = runner(["tmux", "list-sessions", "-F", _LIST_FORMAT])
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


def list_tmux_panes(*, runner: TmuxCommandRunner = run_tmux_command) -> list[TmuxPaneRuntime]:
    """Capture all local pane runtime fields in one tmux query."""
    if runner is run_tmux_command and not is_tmux_installed():
        return []
    try:
        result = runner(["tmux", "list-panes", "-a", "-F", _PANE_STATUS_FORMAT])
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    panes: list[TmuxPaneRuntime] = []
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        session, window, title, command, dead = fields
        panes.append(TmuxPaneRuntime(session, window, title, command, dead == "1"))
    return panes


def get_tmux_version(*, runner: TmuxCommandRunner = run_tmux_command) -> str | None:
    """Return the `tmux -V` output, or None if tmux is unavailable."""
    if runner is run_tmux_command and not is_tmux_installed():
        return None
    try:
        result = runner(["tmux", "-V"])
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


def session_exists(
    session_name: str,
    *,
    runner: TmuxCommandRunner = run_tmux_command,
) -> bool:
    """Whether a tmux session named *session_name* is currently running.

    Takes the same injectable *runner* as create_workspace_session so a
    caller pointed at an alternate tmux socket (e.g. `-L terminal-home-test`)
    checks that socket, not whatever server the bare `tmux` binary defaults
    to -- callers that don't pass one still hit the real server via
    run_tmux_command, unchanged.
    """
    if runner is run_tmux_command and not is_tmux_installed():
        return False
    try:
        result = runner(["tmux", "has-session", "-t", session_name])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def generate_session_name(
    project_name: str,
    existing: Iterable[str] | None = None,
    *,
    runner: TmuxCommandRunner | None = None,
) -> str:
    """A deterministic, sanitized, collision-free session name for
    *project_name*.

    *existing* is the set of session names to avoid; if not given, the
    currently running tmux sessions are queried. On a collision, `-2`,
    `-3`, ... is appended until a free name is found.
    """
    base = sanitize_session_name(project_name)
    if existing is not None:
        existing_names = set(existing)
    elif runner is None:
        existing_names = {s.name for s in list_tmux_sessions()}
    else:
        existing_names = {s.name for s in list_tmux_sessions(runner=runner)}
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


def _run_step(runner: TmuxCommandRunner, argv: list[str]) -> subprocess.CompletedProcess[str]:
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
    runner: TmuxCommandRunner,
    window: WindowSpec,
    window_id: str,
    first_pane_id: str,
    project_dir: str,
    pane_plans: Mapping[tuple[str, int], PaneLaunchPlan],
    saved_window_layouts: Mapping[str, PaneLayout] | None = None,
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

    default_layout = tmux_layout_for_pane_count(len(window.panes))
    remembered = (saved_window_layouts or {}).get(window.window_name)
    layout = (
        remembered.tmux_layout
        if remembered is not None and remembered.pane_count == len(window.panes)
        else default_layout
    )
    if layout is not None:
        try:
            _run_step(runner, _select_layout_argv(window_id, layout))
        except TmuxCommandError:
            if layout == default_layout or default_layout is None:
                raise
            _run_step(runner, _select_layout_argv(window_id, default_layout))

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
    runner: TmuxCommandRunner = run_tmux_command,
    saved_window_layouts: Mapping[str, PaneLayout] | None = None,
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
    project_dir = workspace_project_dir(workspace)
    first_window = workspace.windows[0]

    session_created = False
    try:
        argv = _new_session_argv(session, first_window.window_name, project_dir)
        result = _run_step(runner, argv)
        session_created = True
        first_window_id, first_pane_id = _parse_create_capture(result, argv)

        first_window_pane_ids = _create_window_panes(
            runner,
            first_window,
            first_window_id,
            first_pane_id,
            project_dir,
            pane_plans,
            saved_window_layouts,
        )

        for window in workspace.windows[1:]:
            argv = _new_window_argv(session, window.window_name, project_dir)
            result = _run_step(runner, argv)
            window_id, pane_id = _parse_create_capture(result, argv)
            _create_window_panes(
                runner,
                window,
                window_id,
                pane_id,
                project_dir,
                pane_plans,
                saved_window_layouts,
            )

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


def run_interactive_tmux(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run an external tmux attach while inheriting the terminal streams."""
    try:
        return subprocess.run(argv, stdin=None, stdout=None, stderr=None, text=True)
    except OSError as exc:
        raise TmuxCommandError(f"Could not start interactive tmux: {exc}") from exc
