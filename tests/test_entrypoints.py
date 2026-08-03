"""Tests for the package's console-script entrypoints
(pyproject.toml [project.scripts]) -- parses the metadata directly with
tomllib and imports what it points at; never builds or installs the
package.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import tomllib

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


def test_python_dash_m_dashboard_targets_the_same_main() -> None:
    from dashboard.__main__ import main as module_main
    from dashboard.app import main as app_main

    assert module_main is app_main
