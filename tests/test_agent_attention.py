from __future__ import annotations

import shutil
import subprocess

import pytest

from dashboard.services import tmux


def test_agent_attention_popup_binding_is_centered_and_guarded() -> None:
    argv = tmux.agent_attention_popup_argv("demo")

    assert argv[:5] == ["tmux", "bind-key", "-T", "prefix", "a"]
    assert argv[5:8] == ["if-shell", "-F", "#{==:#{@terminal_home_workspace},1}"]
    assert argv[8].startswith("display-popup -E -w 70% -h 65%")
    assert "-d '#{pane_current_path}'" in argv[8]
    assert "-T ' Agents '" in argv[8]
    assert "th attention" in argv[8]


def test_agent_attention_install_has_dedicated_marker() -> None:
    calls: list[list[str]] = []

    def runner(argv: list[str]):
        calls.append(argv)
        if argv[-1] == "@terminal_home_workspace":
            return type("Result", (), {"returncode": 0, "stdout": "1\n"})()
        if argv[-1] == "@terminal_home_agent_attention_popup":
            return type("Result", (), {"returncode": 1, "stdout": ""})()
        return type("Result", (), {"returncode": 0, "stdout": ""})()

    tmux.install_agent_attention_popup("demo", runner=runner)
    assert calls[-2][:5] == ["tmux", "bind-key", "-T", "prefix", "a"]
    assert calls[-1][-2:] == ["@terminal_home_agent_attention_popup", "1"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_agent_attention_binding_passes_tmux_parser_smoke(tmp_path) -> None:
    socket = tmp_path / "tmux.sock"
    prefix = ["tmux", "-S", str(socket)]
    start = subprocess.run(
        [*prefix, "-f", "/dev/null", "new-session", "-d", "-s", "smoke"],
        capture_output=True, text=True, check=False,
    )
    if start.returncode != 0:
        pytest.skip(f"tmux server unavailable: {start.stderr.strip()}")
    try:
        result = subprocess.run(
            [prefix[0], *prefix[1:], *tmux.agent_attention_popup_argv("demo")[1:]],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
    finally:
        subprocess.run([*prefix, "kill-server"], capture_output=True, check=False)
