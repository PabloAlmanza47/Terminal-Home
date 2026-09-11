from __future__ import annotations

import shutil
import subprocess

import pytest

from dashboard.services import tmux


def test_lazygit_popup_binding_uses_focused_pane_directory_and_large_popup() -> None:
    argv = tmux.lazygit_popup_argv("demo")

    assert argv[:5] == ["tmux", "bind-key", "-T", "prefix", "g"]
    assert argv[5:8] == ["if-shell", "-F", "#{==:#{@terminal_home_workspace},1}"]

    nested_popup = argv[8:]
    assert len(nested_popup) == 1
    assert nested_popup[0].startswith("display-popup -E -w 90% -h 90%")
    assert "-d '#{pane_current_path}'" in nested_popup[0]
    assert "-T ' Lazygit '" in nested_popup[0]
    assert "lazygit" in nested_popup[0]

    assert argv[8] != "display-popup"
    assert argv[9:] == []


def test_install_lazygit_popup_marks_only_target_session_and_binds_prefix_g() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    tmux.install_lazygit_popup("demo", runner=runner)

    assert calls[0] == [
        "tmux",
        "show-options",
        "-t",
        "demo",
        "-qv",
        "@terminal_home_workspace",
    ]
    assert calls[1] == [
        "tmux", "set-option", "-t", "demo", "@terminal_home_workspace", "1"
    ]
    assert calls[2] == [
        "tmux", "show-options", "-t", "demo", "-qv", "@terminal_home_lazygit_popup"
    ]
    assert calls[3][:5] == ["tmux", "bind-key", "-T", "prefix", "g"]
    assert "@terminal_home_workspace" in calls[3][-2]
    assert calls[4] == [
        "tmux", "set-option", "-t", "demo", "@terminal_home_lazygit_popup", "1"
    ]


def test_install_lazygit_popup_skips_repeated_install_for_marked_session() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        if argv[-1] == "@terminal_home_workspace":
            return type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        if argv[-1] == "@terminal_home_lazygit_popup":
            return type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        raise AssertionError("marked sessions need no additional tmux commands")

    tmux.install_lazygit_popup("demo", runner=runner)

    assert calls == [
        ["tmux", "show-options", "-t", "demo", "-qv", "@terminal_home_workspace"],
        ["tmux", "show-options", "-t", "demo", "-qv", "@terminal_home_lazygit_popup"],
    ]


def test_install_lazygit_popup_does_not_mark_when_binding_fails() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        if argv[1] == "show-options" and argv[-1] == "@terminal_home_workspace":
            return type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        if argv[1] == "show-options":
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        if argv[1] == "bind-key":
            raise tmux.TmuxCommandError("binding failed")
        raise AssertionError("the marker must not be set after a failed binding")

    with pytest.raises(tmux.TmuxCommandError, match="binding failed"):
        tmux.install_lazygit_popup("demo", runner=runner)

    assert [call[1] for call in calls] == ["show-options", "show-options", "bind-key"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_lazygit_popup_binding_passes_tmux_parser_smoke(tmp_path) -> None:
    socket_path = tmp_path / "tmux.sock"
    tmux_prefix = ["tmux", "-S", str(socket_path)]
    start = subprocess.run(
        [*tmux_prefix, "-f", "/dev/null", "new-session", "-d", "-s", "smoke"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert start.returncode == 0, start.stderr
    try:
        command = [tmux_prefix[0], *tmux_prefix[1:], *tmux.lazygit_popup_argv("demo")[1:]]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stderr
    finally:
        subprocess.run([*tmux_prefix, "kill-server"], capture_output=True, check=False)


def test_old_managed_session_without_lazygit_marker_is_migrated() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        if argv[1] == "show-options" and argv[-1] == "@terminal_home_workspace":
            return type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        if argv[1] == "show-options":
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    tmux.install_lazygit_popup("terminal-home", runner=runner)

    assert [call[1] for call in calls] == ["show-options", "show-options", "bind-key", "set-option"]
    assert calls[-1][-2:] == ["@terminal_home_lazygit_popup", "1"]


def test_lazygit_popup_contains_graceful_missing_tool_message_and_graph_config() -> None:
    command = tmux.lazygit_popup_argv("demo")[-1]

    assert "command -v lazygit" in command
    assert "Lazygit is not installed" in command
    assert "order: topo-order" in command
    assert "showGraph: always" in command
    assert "showWholeGraph: true" in command
    assert "nerdFontsVersion" in command
