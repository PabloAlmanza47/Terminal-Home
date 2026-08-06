from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.services import project_intelligence as intelligence
from dashboard.services.project_intelligence import (
    ActionKind,
    Ecosystem,
    FindingLevel,
    build_setup_plan,
    execute_setup_action,
    inspect_project,
)


def _tools(monkeypatch: pytest.MonkeyPatch, *, node: bool = True) -> None:
    def which(name: str) -> str | None:
        if name == "node" and node:
            return "/usr/bin/node"
        if name in {"npm", "pnpm", "yarn", "bun", "python3", "dotnet"}:
            return f"/usr/bin/{name}"
        return None

    monkeypatch.setattr(intelligence.shutil, "which", which)
    monkeypatch.setattr(intelligence, "_version_command", lambda executable: "v24.18.1")


def test_next_prisma_npm_and_environment_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "npm@10",
                "engines": {"node": ">=24"},
                "scripts": {"dev": "next dev", "test": "vitest", "build": "next build"},
                "dependencies": {"next": "1", "react": "1", "prisma": "1"},
            }
        )
    )
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "prisma").mkdir()
    (tmp_path / "prisma/schema.prisma").write_text("datasource db {}")
    (tmp_path / ".env.local.example").write_text("DATABASE_URL=placeholder")

    info = inspect_project(tmp_path)

    assert info.ecosystems == (Ecosystem.NODE,)
    assert info.frameworks == ("Next.js", "Prisma", "React")
    assert info.package_manager == "npm"
    assert [command.argv for command in info.commands] == [
        ("npm", "run", "dev"),
        ("npm", "test"),
        ("npm", "run", "build"),
    ]
    assert any(".env.local" in finding.message for finding in info.findings)
    assert any(action.kind is ActionKind.COPY_FILE for action in info.setup_actions)
    prisma = next(action for action in info.setup_actions if action.id == "prisma-generate")
    assert prisma.dependencies == ("node-dependencies",)


def test_conflicting_lockfiles_have_no_install_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}))
    (tmp_path / "package-lock.json").touch()
    (tmp_path / "pnpm-lock.yaml").touch()

    info = inspect_project(tmp_path)

    assert info.package_manager is None
    assert any(
        f.level is FindingLevel.WARN and "Multiple package" in f.message for f in info.findings
    )
    assert not any(action.id == "node-dependencies" for action in info.setup_actions)


def test_environment_copy_rejects_broken_symlink_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch, node=False)
    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / ".env.example").write_text("SECRET=placeholder\n")
    outside = tmp_path.parent / "outside-env"
    try:
        (tmp_path / ".env").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")

    info = inspect_project(tmp_path)
    assert not any(action.id == "copy-.env" for action in info.setup_actions)


def test_environment_copy_revalidates_paths_before_writing(tmp_path: Path) -> None:
    source = tmp_path / ".env.example"
    destination = tmp_path / ".env"
    source.write_text("SECRET=placeholder\n")
    from dashboard.services.project_intelligence import ActionKind, Evidence, SetupAction

    planned = SetupAction(
        "copy-.env",
        ActionKind.COPY_FILE,
        "Create .env from .env.example",
        "test",
        (Evidence(".env.example"),),
        tmp_path,
        source=source,
        destination=destination,
    )
    destination.symlink_to(tmp_path.parent / "outside-env")
    ok, detail = execute_setup_action(planned)
    assert not ok
    assert "already exists" in detail


def test_malformed_and_symlinked_indicators_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"scripts": {"dev": "unsafe"}}))
    project = tmp_path / "project"
    project.mkdir()
    try:
        (project / "package.json").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    info = inspect_project(project)
    assert not info.commands
    assert not info.ecosystems

    (project / "package.json").unlink()
    (project / "package.json").write_text("{")
    malformed = inspect_project(project)
    assert malformed.malformed_critical
    assert any("malformed" in f.message for f in malformed.findings)


def test_python_requirements_venv_ordering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tools(monkeypatch, node=False)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nrequires-python = ">=3.10"\n'
    )
    (tmp_path / "requirements.txt").write_text("pytest\n")

    info = inspect_project(tmp_path)
    ids = [action.id for action in info.setup_actions]
    assert info.ecosystems == (Ecosystem.PYTHON,)
    assert ids == ["python-venv", "python-requirements"]
    assert info.setup_actions[1].dependencies == ("python-venv",)


def test_unknown_repository_is_informational(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    info = inspect_project(tmp_path)
    assert info.ecosystems == ()
    assert info.findings[0].level is FindingLevel.INFO
    assert info.setup_actions == ()


def test_dotnet_solution_restore_and_ambiguous_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _tools(monkeypatch)
    (tmp_path / "Demo.sln").write_text("Microsoft Visual Studio Solution File")
    (tmp_path / "one.csproj").write_text(
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
    )
    (tmp_path / "two.csproj").write_text(
        "<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup></Project>"
    )

    info = inspect_project(tmp_path)
    assert Ecosystem.DOTNET in info.ecosystems
    assert "net8.0" in info.frameworks
    assert any("restore evidence" in f.message for f in info.findings)
    assert any(action.id == "dotnet-restore" for action in build_setup_plan(info))
