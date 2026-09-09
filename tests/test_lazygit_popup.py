from __future__ import annotations

from dashboard.services import tmux


def test_lazygit_popup_binding_uses_focused_pane_directory_and_large_popup() -> None:
    argv = tmux.lazygit_popup_argv("demo")

    assert argv[:5] == ["tmux", "bind-key", "-T", "prefix", "g"]
    assert "display-popup" in argv[-1]
    assert "-w 90%" in argv[-1]
    assert "-h 90%" in argv[-1]
    assert "-d '#{pane_current_path}'" in argv[-1]
    assert "lazygit" in argv[-1]


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
    assert calls[2][:5] == ["tmux", "bind-key", "-T", "prefix", "g"]
    assert "@terminal_home_workspace" in calls[2][-2]


def test_install_lazygit_popup_skips_repeated_install_for_marked_session() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        if argv[1] == "show-options":
            return type("Result", (), {"returncode": 0, "stdout": "1\n", "stderr": ""})()
        raise AssertionError("marked sessions need no additional tmux commands")

    tmux.install_lazygit_popup("demo", runner=runner)

    assert calls == [[
        "tmux", "show-options", "-t", "demo", "-qv", "@terminal_home_workspace"
    ]]


def test_lazygit_popup_contains_graceful_missing_tool_message_and_graph_config() -> None:
    command = tmux.lazygit_popup_argv("demo")[-1]

    assert "command -v lazygit" in command
    assert "Lazygit is not installed" in command
    assert "order: topo-order" in command
    assert "showGraph: always" in command
    assert "showWholeGraph: true" in command
    assert "nerdFontsVersion" in command
