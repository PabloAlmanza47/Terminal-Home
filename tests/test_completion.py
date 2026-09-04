"""Dynamic shell completion candidates, scripts, and CLI protocol tests."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import dashboard.cli as cli_module
import dashboard.services.completion as completion_module
from dashboard.models import RemoteProjectRegistration, SshProjectLocation
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services.project_selection import RegisteredRemoteProject
from dashboard.services.projects import Project, ProjectDiscoveryResult
from dashboard.services.projects_config_store import save_projects_config


def _project(path: Path, name: str | None = None) -> Project:
    return Project(name=name if name is not None else path.name, path=path)


def test_no_projects_produces_no_candidates() -> None:
    assert completion_module.project_selector_candidates(()) == ()


def test_unique_projects_use_short_names_in_deterministic_order(tmp_path: Path) -> None:
    projects = (
        _project(tmp_path / "zebra"),
        _project(tmp_path / "Alpha"),
        _project(tmp_path / "beta"),
    )
    assert completion_module.project_selector_candidates(projects) == ("Alpha", "beta", "zebra")


@pytest.mark.parametrize("duplicate_count", [2, 3])
def test_duplicate_names_use_canonical_paths(tmp_path: Path, duplicate_count: int) -> None:
    projects = tuple(
        _project(tmp_path / f"root-{index}" / "example") for index in range(duplicate_count)
    )
    assert completion_module.project_selector_candidates(projects) == tuple(
        str(project.path.resolve()) for project in projects
    )


def test_unique_names_mixed_with_duplicates(tmp_path: Path) -> None:
    duplicate_a = _project(tmp_path / "a" / "example")
    duplicate_b = _project(tmp_path / "b" / "example")
    unique = _project(tmp_path / "unique" / "portfolio")
    assert completion_module.project_selector_candidates((unique, duplicate_b, duplicate_a)) == (
        str(duplicate_a.path.resolve()),
        str(duplicate_b.path.resolve()),
        "portfolio",
    )


def test_names_and_paths_with_spaces_remain_raw_values(tmp_path: Path) -> None:
    unique = _project(tmp_path / "Demo Project")
    duplicate_a = _project(tmp_path / "School Work" / "example")
    duplicate_b = _project(tmp_path / "Office Work" / "example")
    candidates = completion_module.project_selector_candidates((unique, duplicate_a, duplicate_b))
    assert "Demo Project" in candidates
    assert str(duplicate_a.path.resolve()) in candidates
    assert str(duplicate_b.path.resolve()) in candidates
    assert all("\\ " not in candidate for candidate in candidates)


def test_case_differing_names_remain_unique_exact_selectors(tmp_path: Path) -> None:
    upper = _project(tmp_path / "one", "Example")
    lower = _project(tmp_path / "two", "example")
    assert completion_module.project_selector_candidates((lower, upper)) == ("Example", "example")


def test_line_delimiter_characters_are_not_emitted(tmp_path: Path) -> None:
    projects = (_project(tmp_path / "safe"), _project(tmp_path / "bad", "bad\nname"))
    assert completion_module.project_selector_candidates(projects) == ("safe",)


def test_remote_projects_use_exact_selectors_and_join_name_ambiguity(tmp_path: Path) -> None:
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    remote = RegisteredRemoteProject(
        "remote-api",
        SshProjectLocation(host_id, "/srv/Project With Spaces"),
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3",
            host_id,
            "remote-api",
            "/srv/Project With Spaces",
        ),
    )
    local = _project(tmp_path / "remote-api", "remote-api")

    candidates = completion_module.project_selector_candidates((local, remote))

    assert candidates == (
        str(local.path.resolve()),
        "ssh:d84aeefb-7c29-4c63-b39c-766d559df977:/srv/Project With Spaces",
    )


def test_discovery_candidates_include_manual_projects_and_ignore_missing_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    manual = tmp_path / "Manual Project"
    manual.mkdir()
    save_projects_config(
        ProjectsConfig(roots=(tmp_path / "missing",), manual_projects=(manual,))
    )
    assert completion_module.discover_project_selector_candidates() == ("Manual Project",)


def test_discovery_candidates_include_terminal_home_without_directory_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    root.mkdir()
    (root / "terminal-home").mkdir()
    (root / "node_modules").mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    save_projects_config(ProjectsConfig(roots=(root,)))

    assert completion_module.discover_project_selector_candidates() == ("terminal-home",)


def test_discovery_inherits_canonical_symlink_deduplication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "projects"
    real = root / "real"
    real.mkdir(parents=True)
    link = root / "link"
    try:
        link.symlink_to(real, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    save_projects_config(ProjectsConfig(roots=(root,)))
    assert len(completion_module.discover_project_selector_candidates()) == 1


def test_candidate_discovery_only_loads_config_and_discovers_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = ProjectsConfig(roots=(tmp_path,))
    calls: list[object] = []
    monkeypatch.setattr(
        completion_module,
        "load_projects_config_result",
        lambda: calls.append("config") or SimpleNamespace(value=config, warning="ignored"),
    )
    monkeypatch.setattr(
        completion_module,
        "discover_projects",
        lambda value: calls.append(value)
        or ProjectDiscoveryResult(
            projects=(_project(tmp_path / "demo"),),
            truncated=True,
            warnings=("ignored",),
        ),
    )
    assert completion_module.discover_project_selector_candidates() == ("demo",)
    assert calls == ["config", config]


@pytest.mark.parametrize("shell", ["bash", "zsh"])
def test_completion_cli_prints_script(shell: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_module.run(["completion", shell]) == 0
    assert capsys.readouterr().out == completion_module.render_completion(shell)


def test_invalid_completion_shell_keeps_argparse_exit_code_2() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli_module.run(["completion", "fish"])
    assert excinfo.value.code == 2


def test_internal_protocol_prints_raw_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli_module,
        "discover_project_selector_candidates",
        lambda: ("Demo Project", "/tmp/School Work/example"),
    )
    assert cli_module.run(["__complete", "projects"]) == 0
    assert capsys.readouterr().out == "Demo Project\n/tmp/School Work/example\n"


def test_internal_protocol_empty_and_warnings_do_not_contaminate_stdout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    save_projects_config(ProjectsConfig(roots=(tmp_path / "missing",)))
    assert cli_module.run(["__complete", "projects"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_internal_protocol_is_omitted_from_normal_help(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        cli_module.run(["--help"])
    output = capsys.readouterr().out
    assert "completion" in output
    assert "__complete" not in output


def test_completion_script_command_does_not_import_textual() -> None:
    code = (
        "import sys; from dashboard.cli import run; "
        "run(['completion', 'bash']); "
        "print('IMPORT_STATE', 'dashboard.app' in sys.modules, 'textual' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.rstrip().endswith("IMPORT_STATE False False")


@pytest.mark.parametrize(
    "renderer",
    [completion_module.render_bash_completion, completion_module.render_zsh_completion],
)
def test_script_contracts(renderer) -> None:
    script = renderer()
    for executable in ("th", "terminal-home", "dev"):
        assert executable in script
    for command in completion_module.SUBCOMMANDS:
        assert command in script
    assert "bash" in script and "zsh" in script
    assert "__complete projects" in script
    assert "plan" in script and "up" in script
    assert "th list" not in script


def test_bash_script_preserves_spaced_candidate_as_one_array_element() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash is unavailable")
    generated = completion_module.render_bash_completion()
    script = generated + r'''
th() { printf '%s\n' 'Alpha' 'Demo Project'; }
COMP_WORDS=(th up De)
COMP_CWORD=2
_terminal_home_complete
printf '<%s>\n' "${COMPREPLY[@]}"
'''
    result = subprocess.run(
        ["bash", "--noprofile", "--norc"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout == "<Demo Project>\n"


def test_bash_script_has_valid_syntax() -> None:
    if shutil.which("bash") is None:
        pytest.skip("bash is unavailable")
    subprocess.run(
        ["bash", "-n"],
        input=completion_module.render_bash_completion(),
        text=True,
        capture_output=True,
        check=True,
    )


def test_zsh_script_has_valid_syntax_when_available() -> None:
    if shutil.which("zsh") is None:
        pytest.skip("zsh is unavailable")
    subprocess.run(
        ["zsh", "-n"],
        input=completion_module.render_zsh_completion(),
        text=True,
        capture_output=True,
        check=True,
    )
