"""Tests for the package's console-script entrypoints
(pyproject.toml [project.scripts]) -- parses the metadata directly with
tomllib and imports what it points at; never builds or installs the
package.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _scripts() -> dict[str, str]:
    with _PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["scripts"]


def test_terminal_home_th_and_dev_are_all_registered() -> None:
    scripts = _scripts()
    assert "terminal-home" in scripts
    assert "th" in scripts
    assert "dev" in scripts


def test_all_three_commands_map_to_the_same_entrypoint() -> None:
    scripts = _scripts()
    entrypoint = scripts["terminal-home"]
    assert scripts["th"] == entrypoint
    assert scripts["dev"] == entrypoint


def test_entrypoint_module_and_function_are_importable_and_callable() -> None:
    scripts = _scripts()
    module_path, _, func_name = scripts["terminal-home"].partition(":")

    module = importlib.import_module(module_path)
    func = getattr(module, func_name)

    assert callable(func)


def test_version_is_authoritative_and_cli_reports_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dashboard import __version__
    from dashboard.cli import run

    with pytest.raises(SystemExit) as excinfo:
        run(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip().endswith(__version__)


def test_python_dash_m_dashboard_targets_the_same_dispatcher() -> None:
    from dashboard.__main__ import main as module_main
    from dashboard.cli import main as cli_main

    assert module_main is cli_main


def test_entrypoint_is_the_cli_dispatcher_not_the_tui_launcher() -> None:
    """The console scripts must go through the dispatcher (which decides
    TUI vs subcommand), not straight into the TUI-only launcher -- this is
    the behavior that changed when `list`/`plan`/`doctor` were added.
    """
    scripts = _scripts()
    assert scripts["terminal-home"] == "dashboard.cli:main"


def test_release_version_is_030_and_console_scripts_report_it() -> None:
    from dashboard import __version__

    assert __version__ == "0.3.0"
    for command in ("th", "terminal-home", "dev"):
        executable = Path(sys.executable).parent / command
        if not executable.exists():
            pytest.skip(f"{command} is not installed in this test environment")
        result = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, check=True
        )
        assert result.stdout.strip().endswith("0.3.0")
