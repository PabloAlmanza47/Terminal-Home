"""Tests for tmux workspace construction (dashboard.services.tmux): command
building, session creation, and attach/switch decisions.

create_workspace_session interleaves construction with execution -- each
window/pane-creating command asks tmux to report back the stable id it just
assigned (`-P -F`), and every later command targets that id, never an
assumed numeric window/pane index. _FakeTmux below stands in for a real
tmux server: it hands back fresh, unique window/pane ids for every
new-session/new-window/split-window call, exactly like tmux itself would,
so these tests never touch a real tmux server yet still exercise the full
id-capture-and-target flow.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceSpec
from dashboard.services import pane_commands
from dashboard.services.tmux import (
    TmuxCommandError,
    attach_or_switch_argv,
    create_workspace_session,
    generate_session_name,
    sanitize_session_name,
    session_exists,
)


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _FakeTmux:
    """A fake tmux server for create_workspace_session tests.

    Every new-session/new-window call is handed a fresh `@window_id
    %pane_id` pair (as real tmux reports via -P -F), and every split-window
    call a fresh `%pane_id`, counting up from *window_start*/*pane_start* --
    deliberately not always 0, so tests can't accidentally pass just because
    ids happen to look like assumed pane indexes. *fail_on* makes the given
    tmux subcommand (e.g. "select-layout") fail once, to exercise cleanup.

    Also tracks which session names exist, the same way a real tmux server
    would: new-session adds one, kill-session removes it, and has-session
    reports on it. This lets session_exists's injected *runner* -- the same
    one passed to create_workspace_session -- be answered entirely by this
    fake, so tests never need to monkeypatch session_exists itself, and
    never touch a real tmux server.
    """

    def __init__(
        self,
        *,
        window_start: int = 0,
        pane_start: int = 0,
        fail_on: str | None = None,
        existing_sessions: Iterable[str] = (),
    ) -> None:
        self._next_window = window_start
        self._next_pane = pane_start
        self.fail_on = fail_on
        self.executed: list[list[str]] = []
        self.sessions: set[str] = set(existing_sessions)

    def __call__(self, argv: list[str]) -> _FakeCompletedProcess:
        self.executed.append(argv)
        command = argv[1]
        if command == "has-session":
            target = argv[argv.index("-t") + 1]
            return _FakeCompletedProcess(returncode=0 if target in self.sessions else 1)
        if self.fail_on is not None and command == self.fail_on:
            return _FakeCompletedProcess(returncode=1, stderr="boom")
        if command == "new-session":
            self.sessions.add(argv[argv.index("-s") + 1])
            window_id, pane_id = f"@{self._next_window}", f"%{self._next_pane}"
            self._next_window += 1
            self._next_pane += 1
            return _FakeCompletedProcess(returncode=0, stdout=f"{window_id} {pane_id}")
        if command == "new-window":
            window_id, pane_id = f"@{self._next_window}", f"%{self._next_pane}"
            self._next_window += 1
            self._next_pane += 1
            return _FakeCompletedProcess(returncode=0, stdout=f"{window_id} {pane_id}")
        if command == "split-window":
            pane_id = f"%{self._next_pane}"
            self._next_pane += 1
            return _FakeCompletedProcess(returncode=0, stdout=pane_id)
        if command == "kill-session":
            self.sessions.discard(argv[argv.index("-t") + 1])
            return _FakeCompletedProcess(returncode=0)
        return _FakeCompletedProcess(returncode=0)


def _workspace(project_path: Path, *windows: WindowSpec) -> WorkspaceSpec:
    return WorkspaceSpec.for_local_project(
        project_name="demo",
        project_path=project_path,
        session_name="demo",
        windows=windows or (WindowSpec(window_name="main", panes=(_pane(),)),),
    )


def _pane(kind: PaneKind = PaneKind.BLANK_TERMINAL, name: str | None = None) -> PaneSpec:
    return PaneSpec(kind=kind, display_name=name or kind.value)


def _plan(startup_command: str | None = None, pane_title: str | None = None):
    return pane_commands.PaneLaunchPlan(startup_command=startup_command, pane_title=pane_title)


def _targets(commands: list[list[str]], subcommand: str) -> list[str]:
    """The `-t` value of every command matching *subcommand*, in order."""
    values = []
    for argv in commands:
        if argv[1] == subcommand:
            values.append(argv[argv.index("-t") + 1])
    return values


def _first_command(commands: list[list[str]], subcommand: str) -> list[str]:
    """The first executed command matching *subcommand* -- used instead of a
    fixed index since the has-session precondition check now runs (through
    the same injected runner) before any session-creating command.
    """
    return next(argv for argv in commands if argv[1] == subcommand)


_ASSUMED_INDEX_TARGET = re.compile(r"\.\d+$")


def _assert_no_assumed_index_targets(commands: list[list[str]]) -> None:
    """No `-t` target may look like `window.0` / `session:name.1` -- those
    are the assumed-numeric-pane-index targets this fix eliminates.
    """
    for argv in commands:
        if "-t" not in argv:
            continue
        target = argv[argv.index("-t") + 1]
        assert not _ASSUMED_INDEX_TARGET.search(target), (
            f"command targets an assumed numeric pane index: {argv}"
        )


# --- sanitize_session_name / generate_session_name -----------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My Project", "My-Project"),
        ("weird!!chars??", "weird-chars"),
        ("already-safe_123", "already-safe_123"),
        ("   ", "workspace"),
    ],
)
def test_sanitize_session_name(raw: str, expected: str) -> None:
    assert sanitize_session_name(raw) == expected


def test_generate_session_name_no_collision() -> None:
    assert generate_session_name("My Project", existing=set()) == "My-Project"


def test_generate_session_name_appends_suffix_on_collision() -> None:
    name = generate_session_name("My Project", existing={"My-Project", "My-Project-2"})
    assert name == "My-Project-3"


# --- session_exists: isolation from a real tmux server --------------------------


def test_session_exists_uses_the_injected_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The isolation fix: session_exists must query through whatever runner
    it's given -- e.g. one that targets `tmux -L terminal-home-test` -- not
    shell out to the default tmux server itself.
    """
    monkeypatch.setattr("dashboard.services.tmux.shutil.which", lambda name: "/usr/bin/tmux")
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> _FakeCompletedProcess:
        calls.append(argv)
        return _FakeCompletedProcess(returncode=0 if argv[-1] == "there" else 1)

    assert session_exists("there", runner=runner) is True
    assert session_exists("not-there", runner=runner) is False
    assert calls == [
        ["tmux", "has-session", "-t", "there"],
        ["tmux", "has-session", "-t", "not-there"],
    ]


def test_create_workspace_session_with_fake_runner_never_calls_real_tmux(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end isolation guarantee: when a fake runner is injected, the
    has-session precondition check, every construction command, and any
    cleanup all go through that fake -- subprocess.run (a real tmux server)
    must never be reached, even indirectly via session_exists's default.
    """

    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("real tmux subprocess.run must never be called")

    monkeypatch.setattr("dashboard.services.tmux.subprocess.run", fail_if_called)
    monkeypatch.setattr("dashboard.services.tmux.shutil.which", lambda name: "/usr/bin/tmux")
    workspace = _workspace(tmp_path)
    fake = _FakeTmux()

    create_workspace_session(workspace, {("main", 0): _plan()}, runner=fake)

    assert fake.executed  # the fake did the work; subprocess.run was never hit


# --- create_workspace_session: pane counts / layouts ----------------------------


def test_one_pane_has_no_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(tmp_path, WindowSpec(window_name="main", panes=(_pane(),)))
    fake = _FakeTmux()

    create_workspace_session(workspace, {("main", 0): _plan()}, runner=fake)

    assert _first_command(fake.executed, "new-session")[:8] == [
        "tmux",
        "new-session",
        "-d",
        "-s",
        "demo",
        "-n",
        "main",
        "-c",
    ]
    assert not any(cmd[1] == "split-window" for cmd in fake.executed)
    assert not any(cmd[1] == "select-layout" for cmd in fake.executed)


def test_two_panes_even_horizontal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane())))
    fake = _FakeTmux()

    create_workspace_session(workspace, {("main", 0): _plan(), ("main", 1): _plan()}, runner=fake)

    split_commands = [c for c in fake.executed if c[1] == "split-window"]
    layout_commands = [c for c in fake.executed if c[1] == "select-layout"]
    assert len(split_commands) == 1
    assert layout_commands == [["tmux", "select-layout", "-t", "@0", "even-horizontal"]]


def test_three_panes_main_vertical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane(), _pane()))
    )
    fake = _FakeTmux()

    create_workspace_session(workspace, {("main", i): _plan() for i in range(3)}, runner=fake)

    split_commands = [c for c in fake.executed if c[1] == "split-window"]
    layout_commands = [c for c in fake.executed if c[1] == "select-layout"]
    assert len(split_commands) == 2
    assert layout_commands == [["tmux", "select-layout", "-t", "@0", "main-vertical"]]


def test_four_panes_tiled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = _workspace(
        tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane(), _pane(), _pane()))
    )
    fake = _FakeTmux()

    create_workspace_session(workspace, {("main", i): _plan() for i in range(4)}, runner=fake)

    split_commands = [c for c in fake.executed if c[1] == "split-window"]
    layout_commands = [c for c in fake.executed if c[1] == "select-layout"]
    assert len(split_commands) == 3
    assert layout_commands == [["tmux", "select-layout", "-t", "@0", "tiled"]]


def test_preserves_pane_order_for_titles_and_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(
        tmp_path,
        WindowSpec(
            window_name="main",
            panes=(
                _pane(PaneKind.CODE_EDITOR, "Code Editor"),
                _pane(PaneKind.GIT, "Git"),
            ),
        ),
    )
    pane_plans = {
        ("main", 0): _plan(startup_command="nvim .", pane_title="editor"),
        ("main", 1): _plan(startup_command="lazygit", pane_title="git"),
    }
    fake = _FakeTmux()

    create_workspace_session(workspace, pane_plans, runner=fake)

    title_commands = [c for c in fake.executed if c[1] == "select-pane" and "-T" in c]
    send_keys_commands = [c for c in fake.executed if c[1] == "send-keys"]

    # pane 0 (%0) is the pane new-session created; pane 1 (%1) is the split.
    assert title_commands == [
        ["tmux", "select-pane", "-t", "%0", "-T", "editor"],
        ["tmux", "select-pane", "-t", "%1", "-T", "git"],
    ]
    assert send_keys_commands == [
        ["tmux", "send-keys", "-t", "%0", "nvim .", "Enter"],
        ["tmux", "send-keys", "-t", "%1", "lazygit", "Enter"],
    ]
    # Final select-pane (no -T) brings focus back to the first pane.
    assert fake.executed[-1] == ["tmux", "select-pane", "-t", "%0"]


@pytest.mark.parametrize("start", [0, 1, 5])
def test_pane_targeting_is_independent_of_base_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: int
) -> None:
    """Regardless of what numbers tmux happens to hand back -- which is
    what pane-base-index/base-index actually shifts in real tmux -- pane
    and window targeting must use exactly those ids, never a computed
    `base_index + pane_index` guess.
    """
    workspace = _workspace(tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane())))
    fake = _FakeTmux(window_start=start, pane_start=start)

    create_workspace_session(
        workspace,
        {("main", 0): _plan(pane_title="left"), ("main", 1): _plan(pane_title="right")},
        runner=fake,
    )

    title_commands = _targets(fake.executed, "select-pane")
    assert title_commands[0] == f"%{start}"
    assert title_commands[1] == f"%{start + 1}"
    _assert_no_assumed_index_targets(fake.executed)


def test_multiple_windows_target_their_own_window_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(
        tmp_path,
        WindowSpec(window_name="main", panes=(_pane(),)),
        WindowSpec(window_name="tests", panes=(_pane(), _pane())),
    )
    pane_plans = {
        ("main", 0): _plan(),
        ("tests", 0): _plan(pane_title="one"),
        ("tests", 1): _plan(pane_title="two"),
    }
    fake = _FakeTmux()

    create_workspace_session(workspace, pane_plans, runner=fake)

    new_window_commands = [c for c in fake.executed if c[1] == "new-window"]
    assert len(new_window_commands) == 1
    assert new_window_commands[0][:6] == ["tmux", "new-window", "-t", "demo", "-n", "tests"]

    # The second window's split/layout/title commands target its own
    # window/pane ids (@1/%1/%2), not the first window's (@0/%0).
    layout_commands = [c for c in fake.executed if c[1] == "select-layout"]
    assert layout_commands == [["tmux", "select-layout", "-t", "@1", "even-horizontal"]]
    title_commands = [c for c in fake.executed if c[1] == "select-pane" and "-T" in c]
    assert _targets(title_commands, "select-pane") == ["%1", "%2"]

    # First window is selected/focused before attach, not the second one.
    assert fake.executed[-2] == ["tmux", "select-window", "-t", "@0"]
    assert fake.executed[-1] == ["tmux", "select-pane", "-t", "%0"]


@pytest.mark.parametrize(
    "window_name",
    [
        "Tools, Debug",
        "server (dev)",
        "a:weird.name",
        "spaces and, commas",
    ],
)
def test_window_names_with_spaces_commas_and_punctuation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, window_name: str
) -> None:
    """Window names are only ever passed as a literal -n argument, never
    embedded into a tmux target string, so tmux target-syntax separators
    (`:` and `.`) inside a window name can't corrupt targeting.
    """
    workspace = _workspace(tmp_path, WindowSpec(window_name=window_name, panes=(_pane(), _pane())))
    fake = _FakeTmux()

    create_workspace_session(workspace, {(window_name, i): _plan() for i in range(2)}, runner=fake)

    new_session_argv = _first_command(fake.executed, "new-session")
    assert new_session_argv[new_session_argv.index("-n") + 1] == window_name
    _assert_no_assumed_index_targets(fake.executed)


def test_shpe_connect_regression_tools_window_tree_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the exact reported failure: recreating a saved workspace
    with a "Tools" window whose first pane is a file tree, under a
    pane-base-index 1 tmux.conf. The old code computed the target as
    `SHPE-Connect:Tools.0` (assuming pane-base-index 0) and tmux rejected
    it with "can't find pane: 0" because the real base-index-1 pane was
    numbered 1, not 0. The fix must never guess -- it must select-pane on
    whatever pane id tmux actually reports back.
    """
    workspace = WorkspaceSpec.for_local_project(
        project_name="SHPE-Connect",
        project_path=tmp_path,
        session_name="SHPE-Connect",
        windows=(WindowSpec(window_name="Tools", panes=(_pane(PaneKind.FILE_TREE, "tree"),)),),
    )
    # A tmux server with pane-base-index 1 hands back pane %1 for the only
    # pane of the first window it creates (not %0).
    fake = _FakeTmux(window_start=0, pane_start=1)

    create_workspace_session(workspace, {("Tools", 0): _plan(pane_title="tree")}, runner=fake)

    title_commands = [c for c in fake.executed if c[1] == "select-pane" and "-T" in c]
    assert title_commands == [["tmux", "select-pane", "-t", "%1", "-T", "tree"]]
    _assert_no_assumed_index_targets(fake.executed)
    assert not any(":Tools" in " ".join(argv) for argv in fake.executed)


# --- create_workspace_session: execution + cleanup on failure -------------------


def test_create_workspace_session_refuses_existing_session(tmp_path: Path) -> None:
    """The pre-creation has-session check must go through the same injected
    runner as everything else, so a caller pointed at an isolated tmux
    socket checks *that* socket, not a real server -- proven here by seeding
    the fake (never a real tmux) with the colliding name. The pre-existing
    session is only ever queried, never killed.
    """
    workspace = _workspace(tmp_path)
    fake = _FakeTmux(existing_sessions={"demo"})

    with pytest.raises(TmuxCommandError, match="already exists"):
        create_workspace_session(workspace, {("main", 0): _plan()}, runner=fake)

    assert fake.executed == [["tmux", "has-session", "-t", "demo"]]
    assert "demo" in fake.sessions  # the pre-existing session survives untouched


def test_create_workspace_session_cleans_up_only_the_session_it_created(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path, WindowSpec(window_name="main", panes=(_pane(), _pane())))
    fake = _FakeTmux(fail_on="select-layout")

    with pytest.raises(TmuxCommandError, match="boom"):
        create_workspace_session(
            workspace, {("main", 0): _plan(), ("main", 1): _plan()}, runner=fake
        )

    assert fake.executed[-1] == ["tmux", "kill-session", "-t", "demo"]
    assert "demo" not in fake.sessions


def test_create_workspace_session_does_not_kill_when_new_session_itself_fails(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path)
    fake = _FakeTmux(fail_on="new-session")

    with pytest.raises(TmuxCommandError):
        create_workspace_session(workspace, {("main", 0): _plan()}, runner=fake)

    # Precondition has-session check, then the failed new-session -- nothing
    # else, and in particular no kill-session.
    assert [cmd[1] for cmd in fake.executed] == ["has-session", "new-session"]


@pytest.mark.parametrize(
    "stdout",
    [
        "",  # missing entirely (e.g. an old tmux ignoring -P -F)
        "@0",
        "@0 ",
        " %1",
        "garbage",
    ],
)
def test_create_workspace_session_raises_on_malformed_capture_output(
    tmp_path: Path, stdout: str
) -> None:
    """Anything other than exactly `<window_id> <pane_id>` from new-session
    is treated as malformed/missing, never partially parsed into a target.
    """
    workspace = _workspace(tmp_path)

    def runner(argv: list[str]) -> _FakeCompletedProcess:
        if argv[1] == "has-session":
            return _FakeCompletedProcess(returncode=1)
        return _FakeCompletedProcess(returncode=0, stdout=stdout)

    with pytest.raises(TmuxCommandError, match="did not report back"):
        create_workspace_session(workspace, {("main", 0): _plan()}, runner=runner)


# --- attach_or_switch_argv -------------------------------------------------------


def test_attach_outside_tmux() -> None:
    assert attach_or_switch_argv("demo", inside_tmux=False) == [
        "tmux",
        "attach-session",
        "-t",
        "demo",
    ]


def test_switch_client_inside_tmux() -> None:
    assert attach_or_switch_argv("demo", inside_tmux=True) == [
        "tmux",
        "switch-client",
        "-t",
        "demo",
    ]
