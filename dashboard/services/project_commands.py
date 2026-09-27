"""Filesystem-only launch-time detection of safe project commands."""

from __future__ import annotations

import configparser
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from dashboard.services.project_files import is_directory, is_regular_file, load_json_object


class CommandSource(str, Enum):
    NODE_DEV = "package.json dev script"
    NODE_START = "package.json start script"
    NODE_TEST = "package.json test script"
    NODE_BUILD = "package.json build script"
    NODE_LINT = "package.json lint script"
    PYTEST = "pytest project indicator"
    DJANGO = "Django manage.py"
    STATIC_HTML = "root index.html"


class CommandCategory(str, Enum):
    DEV = "dev"
    TEST = "test"
    BUILD = "build"
    LINT = "lint"


class CommandExecution(str, Enum):
    EXECUTABLE = "executable"
    INFORMATIONAL = "informational"


_SOURCE_IDS: dict[CommandSource, tuple[str, CommandCategory]] = {
    CommandSource.NODE_DEV: ("dev", CommandCategory.DEV),
    CommandSource.NODE_START: ("dev", CommandCategory.DEV),
    CommandSource.NODE_TEST: ("test", CommandCategory.TEST),
    CommandSource.PYTEST: ("test", CommandCategory.TEST),
    CommandSource.NODE_BUILD: ("build", CommandCategory.BUILD),
    CommandSource.NODE_LINT: ("lint", CommandCategory.LINT),
    CommandSource.DJANGO: ("dev", CommandCategory.DEV),
    CommandSource.STATIC_HTML: ("dev", CommandCategory.DEV),
}


_UNSAFE_SHELL_SYNTAX = re.compile(r"[;&|<>$`\n\r]")


def safe_command_argv(command: str) -> tuple[str, ...] | None:
    """Normalize a simple detected command without interpreting shell syntax.

    Project metadata can contain arbitrary shell expressions. Only commands
    that can be represented as ordinary argv tokens are eligible for explicit
    execution; the rest remain visible as informational detections.
    """
    if not command.strip() or _UNSAFE_SHELL_SYNTAX.search(command):
        return None
    try:
        tokens = tuple(shlex.split(command, posix=True))
    except ValueError:
        return None
    return tokens or None


@dataclass(frozen=True, slots=True)
class DetectedCommand:
    command: str
    source: CommandSource
    argv: tuple[str, ...] | None = None
    execution: CommandExecution = CommandExecution.EXECUTABLE
    id: str | None = None

    def __post_init__(self) -> None:
        command_id, _ = _SOURCE_IDS[self.source]
        if self.id is None:
            object.__setattr__(self, "id", command_id)
        if self.argv is None:
            normalized = safe_command_argv(self.command)
            object.__setattr__(self, "argv", normalized)
            if normalized is None:
                object.__setattr__(self, "execution", CommandExecution.INFORMATIONAL)

    @property
    def category(self) -> CommandCategory:
        return _SOURCE_IDS[self.source][1]

    @property
    def executable(self) -> bool:
        return self.execution is CommandExecution.EXECUTABLE and bool(self.argv)


@dataclass(frozen=True, slots=True)
class DetectedProjectCommands:
    development: DetectedCommand | None
    test: DetectedCommand | None
    build: DetectedCommand | None = None
    lint: DetectedCommand | None = None


@dataclass(frozen=True, slots=True)
class _NodeCommands:
    development: DetectedCommand | None
    test: DetectedCommand | None
    build: DetectedCommand | None
    lint: DetectedCommand | None


_PYTEST_COMMAND_PATTERN = re.compile(r"(?:^|\s)pytest(?:\s|$)")


def _is_file(path: Path) -> bool:
    return is_regular_file(path)


def _is_dir(path: Path) -> bool:
    return is_directory(path)


def _load_package_json(project_path: Path) -> dict[str, Any] | None:
    package_path = project_path / "package.json"
    payload, error = load_json_object(package_path)
    if error or payload is None:
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


def _detected(command: str, source: CommandSource, argv: tuple[str, ...]) -> DetectedCommand:
    return DetectedCommand(command, source, argv=argv)


def _node_detected(manager: str, script: str, source: CommandSource) -> DetectedCommand:
    command = _node_command(manager, script)
    argv: tuple[str, ...]
    if manager == "bun":
        argv = ("bun", "run", script)
    elif manager == "yarn":
        argv = ("yarn", script)
    elif script in {"start", "test"}:
        argv = (manager, script)
    else:
        argv = (manager, "run", script)
    return _detected(command, source, argv)


def _detect_node_commands(project_path: Path) -> _NodeCommands | None:
    package = _load_package_json(project_path)
    if package is None:
        return None
    scripts = package.get("scripts", {})
    assert isinstance(scripts, dict)
    manager = _package_manager(project_path)

    development: DetectedCommand | None = None
    if _script_exists(scripts, "dev"):
        development = _node_detected(manager, "dev", CommandSource.NODE_DEV)
    elif _script_exists(scripts, "start"):
        development = _node_detected(manager, "start", CommandSource.NODE_START)

    test: DetectedCommand | None = None
    test_script = scripts.get("test")
    if (
        isinstance(test_script, str)
        and test_script.strip()
        and not _is_placeholder_test(test_script)
    ):
        test = _node_detected(manager, "test", CommandSource.NODE_TEST)
    build = (
        _node_detected(manager, "build", CommandSource.NODE_BUILD)
        if _script_exists(scripts, "build")
        else None
    )
    lint = (
        _node_detected(manager, "lint", CommandSource.NODE_LINT)
        if _script_exists(scripts, "lint")
        else None
    )
    return _NodeCommands(development=development, test=test, build=build, lint=lint)


def _pyproject_configures_pytest(project_path: Path) -> bool:
    path = project_path / "pyproject.toml"
    if not _is_file(path):
        return False
    try:
        try:
            import tomllib
        except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
            import tomli as tomllib  # type: ignore[no-redef]
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return False
    tool = payload.get("tool")
    return (
        isinstance(tool, dict)
        and isinstance(tool.get("pytest"), dict)
        and isinstance(tool["pytest"].get("ini_options"), dict)
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


def _has_higher_level_indicator(project_path: Path) -> bool:
    """Return whether a recognized non-static project indicator is present."""
    return (
        _is_file(project_path / "package.json")
        or any(
            _is_file(project_path / name)
            for name in (
                "pyproject.toml",
                "requirements.txt",
                "requirements-dev.txt",
                "setup.cfg",
                "setup.py",
                "manage.py",
                "pytest.ini",
                "tox.ini",
                ".python-version",
                "global.json",
            )
        )
        or any(
            _is_file(candidate)
            for pattern in ("*.sln", "*.slnx", "*.csproj", "*.fsproj", "*.vbproj")
            for candidate in project_path.glob(pattern)
        )
    )


def detect_project_commands(project_path: Path) -> DetectedProjectCommands:
    """Detect dev/test commands from fixed root-level project indicators."""
    node = _detect_node_commands(project_path)
    development = node.development if node is not None else None
    test = node.test if node is not None else None
    build = node.build if node is not None else None
    lint = node.lint if node is not None else None

    if development is None and _is_file(project_path / "manage.py"):
        development = _detected(
            "python manage.py runserver", CommandSource.DJANGO,
            ("python", "manage.py", "runserver"),
        )
    if (
        development is None
        and _is_file(project_path / "index.html")
        and not _has_higher_level_indicator(project_path)
    ):
        development = _detected(
            "python3 -m http.server 8000", CommandSource.STATIC_HTML,
            ("python3", "-m", "http.server", "8000"),
        )
    if test is None and _has_pytest_indicator(project_path):
        test = _detected("pytest", CommandSource.PYTEST, ("pytest",))
    return DetectedProjectCommands(
        development=development, test=test, build=build, lint=lint
    )
