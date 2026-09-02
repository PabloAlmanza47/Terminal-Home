"""Filesystem-only project development and test command detection."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dashboard.models import PaneKind, PaneSpec
from dashboard.services.project_commands import CommandSource, detect_project_commands


def _write_package(project: Path, scripts: object = None) -> None:
    project.mkdir(parents=True, exist_ok=True)
    payload = {} if scripts is None else {"scripts": scripts}
    (project / "package.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.mark.parametrize(
    ("lockfile", "expected_dev", "expected_test"),
    [
        (None, "npm run dev", "npm test"),
        ("package-lock.json", "npm run dev", "npm test"),
        ("pnpm-lock.yaml", "pnpm run dev", "pnpm test"),
        ("yarn.lock", "yarn dev", "yarn test"),
        ("bun.lock", "bun run dev", "bun run test"),
        ("bun.lockb", "bun run dev", "bun run test"),
    ],
)
def test_node_manager_commands(
    tmp_path: Path, lockfile: str | None, expected_dev: str, expected_test: str
) -> None:
    _write_package(tmp_path, {"dev": "ignored body", "test": "ignored body"})
    if lockfile is not None:
        (tmp_path / lockfile).touch()
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == expected_dev
    assert detected.test is not None
    assert detected.test.command == expected_test


@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [
        (None, "npm start"),
        ("pnpm-lock.yaml", "pnpm start"),
        ("yarn.lock", "yarn start"),
        ("bun.lock", "bun run start"),
    ],
)
def test_node_start_is_used_when_dev_is_missing(
    tmp_path: Path, lockfile: str | None, expected: str
) -> None:
    _write_package(tmp_path, {"start": "ignored"})
    if lockfile is not None:
        (tmp_path / lockfile).touch()
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == expected
    assert detected.development.source is CommandSource.NODE_START


def test_node_dev_wins_over_start(tmp_path: Path) -> None:
    _write_package(tmp_path, {"dev": "one", "start": "two"})
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == "npm run dev"
    assert detected.development.source is CommandSource.NODE_DEV


def test_multiple_lockfiles_follow_documented_precedence(tmp_path: Path) -> None:
    _write_package(tmp_path, {"dev": "ignored"})
    for name in ("package-lock.json", "bun.lock", "yarn.lock", "pnpm-lock.yaml"):
        (tmp_path / name).touch()
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == "pnpm run dev"


@pytest.mark.parametrize("scripts", [None, {}, {"dev": ""}, {"dev": 7}, {"test": []}])
def test_missing_empty_or_non_string_scripts_are_ignored(tmp_path: Path, scripts: object) -> None:
    _write_package(tmp_path, scripts)
    detected = detect_project_commands(tmp_path)
    assert detected.development is None
    assert detected.test is None


@pytest.mark.parametrize(
    "script",
    [
        'echo "Error: no test specified" && exit 1',
        " ECHO no test specified ; EXIT 1 ",
    ],
)
def test_npm_placeholder_test_script_is_ignored(tmp_path: Path, script: str) -> None:
    _write_package(tmp_path, {"test": script})
    assert detect_project_commands(tmp_path).test is None


@pytest.mark.parametrize("payload", ["{", "[]", "42", '"text"', '{"scripts": []}'])
def test_malformed_or_wrong_shaped_package_json_is_unsupported(
    tmp_path: Path, payload: str
) -> None:
    (tmp_path / "package.json").write_text(payload, encoding="utf-8")
    detected = detect_project_commands(tmp_path)
    assert detected.development is None
    assert detected.test is None


def test_unreadable_package_json_is_unsupported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_path = tmp_path / "package.json"
    package_path.touch()
    original = Path.read_text

    def fail_package_read(path: Path, *args, **kwargs):
        if path == package_path:
            raise PermissionError("unreadable")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_package_read)
    assert detect_project_commands(tmp_path).development is None


def test_invalid_utf8_package_json_is_unsupported(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_bytes(b"\xff\xfe")
    assert detect_project_commands(tmp_path).development is None


def test_lockfile_directory_does_not_select_package_manager(tmp_path: Path) -> None:
    _write_package(tmp_path, {"dev": "ignored"})
    (tmp_path / "pnpm-lock.yaml").mkdir()
    assert detect_project_commands(tmp_path).development.command == "npm run dev"  # type: ignore[union-attr]


def test_symlinked_indicators_outside_project_are_not_read(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside-package.json"
    outside.write_text('{"scripts": {"dev": "ignored"}}')
    try:
        (project / "package.json").symlink_to(outside)
        (project / "tests").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    detected = detect_project_commands(project)
    assert detected.development is None
    assert detected.test is None


def test_project_path_with_spaces(tmp_path: Path) -> None:
    project = tmp_path / "Project With Spaces"
    _write_package(project, {"dev": "ignored"})
    detected = detect_project_commands(project)
    assert detected.development is not None
    assert detected.development.command == "npm run dev"


@pytest.mark.parametrize(
    ("filename", "contents"),
    [
        ("pyproject.toml", "[tool.pytest.ini_options]\naddopts = '-q'\n"),
        ("pytest.ini", "[pytest]\n"),
        ("setup.cfg", "[tool:pytest]\naddopts = -q\n"),
        ("tox.ini", "[testenv]\ncommands = python -m pytest\n"),
    ],
)
def test_python_pytest_configuration_is_detected(
    tmp_path: Path, filename: str, contents: str
) -> None:
    (tmp_path / filename).write_text(contents, encoding="utf-8")
    detected = detect_project_commands(tmp_path)
    assert detected.test is not None
    assert detected.test.command == "pytest"
    assert detected.test.source is CommandSource.PYTEST


def test_root_tests_directory_is_a_pytest_indicator(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    assert detect_project_commands(tmp_path).test is not None


def test_root_manage_py_selects_django_server(tmp_path: Path) -> None:
    (tmp_path / "manage.py").touch()
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == "python manage.py runserver"
    assert detected.development.source is CommandSource.DJANGO


def test_unsupported_python_and_nested_indicators_do_not_count(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    nested = tmp_path / "nested"
    _write_package(nested, {"dev": "ignored"})
    (nested / "manage.py").touch()
    (nested / "tests").mkdir()
    detected = detect_project_commands(tmp_path)
    assert detected.development is None
    assert detected.test is None


def test_node_dev_and_start_win_over_django(tmp_path: Path) -> None:
    (tmp_path / "manage.py").touch()
    _write_package(tmp_path, {"dev": "ignored", "start": "ignored"})
    assert detect_project_commands(tmp_path).development.command == "npm run dev"  # type: ignore[union-attr]


def test_node_start_wins_over_django(tmp_path: Path) -> None:
    (tmp_path / "manage.py").touch()
    _write_package(tmp_path, {"start": "ignored"})
    assert detect_project_commands(tmp_path).development.command == "npm start"  # type: ignore[union-attr]


def test_django_is_used_when_node_has_no_development_script(tmp_path: Path) -> None:
    (tmp_path / "manage.py").touch()
    _write_package(tmp_path, {"test": "ignored"})
    assert detect_project_commands(tmp_path).development.command == (  # type: ignore[union-attr]
        "python manage.py runserver"
    )


def test_root_index_html_selects_static_server(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == "python3 -m http.server 8000"
    assert detected.development.source is CommandSource.STATIC_HTML


def test_nested_index_html_does_not_select_static_server(tmp_path: Path) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/index.html").touch()
    assert detect_project_commands(tmp_path).development is None


def test_symlinked_root_index_html_does_not_select_static_server(tmp_path: Path) -> None:
    outside = tmp_path / "outside-index.html"
    outside.write_text("<h1>Outside</h1>", encoding="utf-8")
    try:
        (tmp_path / "index.html").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable")
    assert detect_project_commands(tmp_path).development is None


def test_node_dev_takes_precedence_over_static_server(tmp_path: Path) -> None:
    (tmp_path / "index.html").touch()
    _write_package(tmp_path, {"dev": "vite"})
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.development.command == "npm run dev"
    assert detected.development.source is CommandSource.NODE_DEV


@pytest.mark.parametrize("indicator", ["manage.py", "pyproject.toml", "Demo.csproj"])
def test_recognized_non_static_indicator_blocks_static_fallback(
    tmp_path: Path, indicator: str
) -> None:
    (tmp_path / "index.html").touch()
    (tmp_path / indicator).touch()
    detected = detect_project_commands(tmp_path)
    assert detected.development is None or detected.development.source is not CommandSource.STATIC_HTML


def test_node_test_wins_over_pytest(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    _write_package(tmp_path, {"test": "ignored"})
    assert detect_project_commands(tmp_path).test.command == "npm test"  # type: ignore[union-attr]


@pytest.mark.parametrize("node_test", [None, "", 'echo "no test specified" && exit 1'])
def test_pytest_is_used_when_node_has_no_supported_test(
    tmp_path: Path, node_test: str | None
) -> None:
    (tmp_path / "tests").mkdir()
    scripts = {} if node_test is None else {"test": node_test}
    _write_package(tmp_path, scripts)
    assert detect_project_commands(tmp_path).test.command == "pytest"  # type: ignore[union-attr]


def test_detection_does_not_execute_subprocesses_or_modify_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_package(tmp_path, {"dev": "touch SHOULD_NOT_EXIST", "test": "also ignored"})
    before = (tmp_path / "package.json").read_bytes()

    def unexpected(*args, **kwargs):
        raise AssertionError("detection executed a subprocess")

    monkeypatch.setattr(subprocess, "run", unexpected)
    detected = detect_project_commands(tmp_path)
    assert detected.development is not None
    assert detected.test is not None
    assert (tmp_path / "package.json").read_bytes() == before
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_detection_service_imports_no_external_project_subsystems() -> None:
    code = (
        "import sys; import dashboard.services.project_commands; "
        "names = ('textual', 'dashboard.services.tmux', 'dashboard.services.git_info', "
        "'dashboard.services.workspace_store', 'dashboard.services.projects'); "
        "print([name for name in names if name in sys.modules])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"


def test_persisted_pane_intent_does_not_contain_detected_command() -> None:
    pane = PaneSpec(kind=PaneKind.DEV_SERVER, display_name="Development Server")
    assert pane.to_dict() == {
        "kind": "dev_server",
        "display_name": "Development Server",
        "custom_command": None,
    }
