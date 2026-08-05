"""Focused tests for the non-TUI ``th new`` workflow."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

import dashboard.cli as cli
import dashboard.services.project_creation as creation
from dashboard.models import LocalProjectLocation, WorkspaceTemplate, template_from_workspace
from dashboard.services.template_store import create_template
from dashboard.services.workspace_defaults import build_default_workspace
from dashboard.services.workspace_store import load_workspace


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(creation, "generate_session_name", lambda name: name.lower())


def test_new_help_and_required_name(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as help_exit:
        cli.run(["new", "--help"])
    assert help_exit.value.code == 0
    output = capsys.readouterr().out
    assert "--template" in output
    assert "--non-interactive" in output
    with pytest.raises(SystemExit) as missing_exit:
        cli.run(["new"])
    assert missing_exit.value.code == 2


def test_new_rejects_conflicting_destination_options() -> None:
    with pytest.raises(SystemExit) as result:
        cli.run(["new", "demo", "--path", "/tmp/demo", "--root", "/tmp"])
    assert result.value.code == 2


def test_new_default_creation_uses_slug_git_and_no_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "projects"
    git_calls: list[Path] = []
    monkeypatch.setattr(creation, "init_git_repo", lambda path: git_calls.append(path))

    assert (
        cli.run(
            ["new", "My Cool API", "--root", str(root), "--no-launch", "--non-interactive"]
        )
        == 0
    )
    destination = root / "my-cool-api"
    assert destination.is_dir()
    assert git_calls == [destination]
    workspace = load_workspace(destination)
    assert workspace is not None
    assert workspace.project_name == "My Cool API"
    assert workspace.windows[0].window_name == "code"
    output = capsys.readouterr().out
    assert "Workspace: default" in output
    assert "Launch: skipped" in output


def test_new_path_and_no_git_do_not_call_git_or_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    destination = tmp_path / "school" / "my-api"
    git_calls: list[Path] = []
    launch_calls: list[object] = []
    monkeypatch.setattr(creation, "init_git_repo", lambda path: git_calls.append(path))
    monkeypatch.setattr(cli, "execute_launch_request", lambda request: launch_calls.append(request))

    assert cli.run(
        [
            "new", "my-api", "--path", str(destination), "--no-git", "--no-launch",
            "--non-interactive",
        ]
    ) == 0
    assert destination.is_dir()
    assert git_calls == []
    assert launch_calls == []


def test_new_template_is_copied_without_mutating_template_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    template_workspace = build_default_workspace(
        "Template", LocalProjectLocation(tmp_path / "template"), "template"
    )
    template = template_from_workspace(template_workspace, "python")
    create_template(template)

    root = tmp_path / "projects"
    assert (
        cli.run(
            [
                "new", "my-api", "--root", str(root), "--template", "PYTHON", "--no-git",
                "--no-launch",
            ]
        )
        == 0
    )
    workspace = load_workspace(root / "my-api")
    assert workspace is not None
    assert workspace.windows == template.windows
    assert template.name == "python"


def test_interactive_answers_override_defaults_and_retry_yes_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "chosen-root"
    monkeypatch.setattr(creation, "init_git_repo", lambda path: None)
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(f"custom-folder\n{root}\nmaybe\nn\n"))

    assert cli.run(["new", "Demo Project", "--interactive", "--no-launch"]) == 0
    assert (root / "custom-folder").is_dir()
    assert load_workspace(root / "custom-folder") is not None


def test_new_existing_destination_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _isolate(monkeypatch, tmp_path)
    root = tmp_path / "projects"
    (root / "demo").mkdir(parents=True)
    assert cli.run(["new", "demo", "--root", str(root), "--no-git", "--no-launch"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_template_completion_reads_only_local_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    template = WorkspaceTemplate(
        "12345678-1234-4234-8234-123456789012",
        "web-development",
        build_default_workspace(
            "Template",
            LocalProjectLocation(tmp_path / "template"),
            "template",
        ).windows,
    )
    create_template(template)
    assert cli.run(["__complete", "templates"]) == 0
