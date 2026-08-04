"""Filesystem-only launch-time detection of safe project commands."""

from __future__ import annotations

import configparser
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib  # type: ignore[no-redef]


class CommandSource(str, Enum):
    NODE_DEV = "package.json dev script"
    NODE_START = "package.json start script"
    NODE_TEST = "package.json test script"
    PYTEST = "pytest project indicator"
    DJANGO = "Django manage.py"


@dataclass(frozen=True, slots=True)
class DetectedCommand:
    command: str
    source: CommandSource


@dataclass(frozen=True, slots=True)
class DetectedProjectCommands:
    development: DetectedCommand | None
    test: DetectedCommand | None


@dataclass(frozen=True, slots=True)
class _NodeCommands:
    development: DetectedCommand | None
    test: DetectedCommand | None


_PYTEST_COMMAND_PATTERN = re.compile(r"(?:^|\s)pytest(?:\s|$)")


def _is_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file()
    except OSError:
        return False


def _is_dir(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_dir()
    except OSError:
        return False


def _load_package_json(project_path: Path) -> dict[str, Any] | None:
    package_path = project_path / "package.json"
    if not _is_file(package_path):
        return None
    try:
        payload = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    scripts = payload.get("scripts", {})
    if not isinstance(scripts, dict):
        return None
    return payload


def _package_manager(project_path: Path) -> str:
    if _is_file(project_path / "pnpm-lock.yaml"):
        return "pnpm"
    if _is_file(project_path / "yarn.lock"):
        return "yarn"
    if _is_file(project_path / "bun.lock") or _is_file(project_path / "bun.lockb"):
        return "bun"
    return "npm"


def _script_exists(scripts: dict[str, Any], name: str) -> bool:
    value = scripts.get(name)
    return isinstance(value, str) and bool(value.strip())


def _is_placeholder_test(script: str) -> bool:
    return "no test specified" in " ".join(script.casefold().split())


def _node_command(manager: str, script: str) -> str:
    if manager == "bun":
        return f"bun run {script}"
    if manager == "yarn":
        return f"yarn {script}"
    if script == "start":
        return f"{manager} start"
    if script == "test":
        return f"{manager} test"
    return f"{manager} run {script}"


def _detect_node_commands(project_path: Path) -> _NodeCommands | None:
    package = _load_package_json(project_path)
    if package is None:
        return None
    scripts = package.get("scripts", {})
    assert isinstance(scripts, dict)
    manager = _package_manager(project_path)

    development: DetectedCommand | None = None
    if _script_exists(scripts, "dev"):
        development = DetectedCommand(
            _node_command(manager, "dev"), CommandSource.NODE_DEV
        )
    elif _script_exists(scripts, "start"):
        development = DetectedCommand(
            _node_command(manager, "start"), CommandSource.NODE_START
        )

    test: DetectedCommand | None = None
    test_script = scripts.get("test")
    if (
        isinstance(test_script, str)
        and test_script.strip()
        and not _is_placeholder_test(test_script)
    ):
        test = DetectedCommand(_node_command(manager, "test"), CommandSource.NODE_TEST)
    return _NodeCommands(development=development, test=test)


def _pyproject_configures_pytest(project_path: Path) -> bool:
    path = project_path / "pyproject.toml"
    if not _is_file(path):
        return False
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool = payload.get("tool")
    return isinstance(tool, dict) and isinstance(tool.get("pytest"), dict) and isinstance(
        tool["pytest"].get("ini_options"), dict
    )


def _config_has_section(path: Path, section: str) -> bool:
    if not _is_file(path):
        return False
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error):
        return False
    return parser.has_section(section)


def _tox_invokes_pytest(project_path: Path) -> bool:
    path = project_path / "tox.ini"
    if not _is_file(path):
        return False
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error):
        return False
    if parser.has_section("pytest"):
        return True
    return any(
        _PYTEST_COMMAND_PATTERN.search(value) is not None
        for section in parser.sections()
        for key, value in parser.items(section)
        if key == "commands"
    )


def _has_pytest_indicator(project_path: Path) -> bool:
    return (
        _pyproject_configures_pytest(project_path)
        or _is_file(project_path / "pytest.ini")
        or _config_has_section(project_path / "setup.cfg", "tool:pytest")
        or _tox_invokes_pytest(project_path)
        or _is_dir(project_path / "tests")
    )


def detect_project_commands(project_path: Path) -> DetectedProjectCommands:
    """Detect dev/test commands from fixed root-level project indicators."""
    node = _detect_node_commands(project_path)
    development = node.development if node is not None else None
    test = node.test if node is not None else None

    if development is None and _is_file(project_path / "manage.py"):
        development = DetectedCommand(
            "python manage.py runserver", CommandSource.DJANGO
        )
    if test is None and _has_pytest_indicator(project_path):
        test = DetectedCommand("pytest", CommandSource.PYTEST)
    return DetectedProjectCommands(development=development, test=test)
