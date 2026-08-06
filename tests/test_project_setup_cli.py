from __future__ import annotations

import json
from pathlib import Path

import dashboard.cli as cli
from dashboard.models import RemoteProjectRegistration, SshProjectLocation


def test_setup_dry_run_is_read_only(tmp_path: Path, monkeypatch, capsys) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}))
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {"isatty": lambda self: False})())

    assert cli.run(["setup", str(project), "--dry-run"]) == 0
    assert not (project / "node_modules").exists()
    assert "Setup plan" in capsys.readouterr().out


def test_doctor_project_rejects_remote_without_ssh(tmp_path: Path, monkeypatch, capsys) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    registration = RemoteProjectRegistration(
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "remote",
        "/srv/demo",
    )
    remote = cli.RegisteredRemoteProject(
        "remote", SshProjectLocation(registration.host_id, registration.remote_path), registration
    )
    monkeypatch.setattr(
        cli,
        "resolve_project_selector",
        lambda selector: type("Result", (), {"ok": True, "project": remote})(),
    )
    assert cli.run(["doctor", str(project)]) == 1
    assert "remote project intelligence" in capsys.readouterr().err
