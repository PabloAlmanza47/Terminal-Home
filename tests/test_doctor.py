"""Tests for the read-only environment diagnostics behind `th doctor`
(dashboard.services.doctor). Every test isolates XDG_CONFIG_HOME/
XDG_DATA_HOME so nothing ever touches the user's real configuration, and
none of them start a real tmux server.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from dashboard.models import RemoteProjectRegistration, SshHost
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import doctor as doctor_module
from dashboard.services.doctor import DiagnosticLevel, exit_code_for, run_diagnostics
from dashboard.services.remote_project_inspection import RemoteProjectInspectionResult
from dashboard.services.remote_project_store import create_remote_project
from dashboard.services.ssh_host_store import create_ssh_host


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    # An empty, always-readable default so tests don't depend on the real
    # ~/projects directory existing on the machine running them.
    monkeypatch.setattr(
        doctor_module,
        "load_projects_config",
        lambda: ProjectsConfig(roots=(tmp_path / "empty-root",), manual_projects=()),
    )
    (tmp_path / "empty-root").mkdir()


def _assume_tmux_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(doctor_module.tmux, "get_tmux_version", lambda: "tmux 3.4")


def _by_label(diagnostics, label: str):
    return [d for d in diagnostics if d.label == label]


def test_healthy_environment_is_all_pass_and_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)

    diagnostics = run_diagnostics()

    assert all(d.level is not DiagnosticLevel.FAIL for d in diagnostics)
    assert exit_code_for(diagnostics) == 0
    assert _by_label(diagnostics, "project_discovery")[0].level is DiagnosticLevel.PASS


def test_missing_tmux_is_a_blocking_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)

    diagnostics = run_diagnostics()

    tmux_binary = _by_label(diagnostics, "tmux_binary")[0]
    assert tmux_binary.level is DiagnosticLevel.FAIL
    assert _by_label(diagnostics, "tmux_version") == []
    assert exit_code_for(diagnostics) == 1


def test_tmux_version_failure_is_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: "/usr/bin/tmux")
    monkeypatch.setattr(doctor_module.tmux, "get_tmux_version", lambda: None)

    diagnostics = run_diagnostics()

    tmux_version = _by_label(diagnostics, "tmux_version")[0]
    assert tmux_version.level is DiagnosticLevel.FAIL
    assert exit_code_for(diagnostics) == 1


def test_missing_optional_config_files_are_nonblocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)

    diagnostics = run_diagnostics()

    settings = _by_label(diagnostics, "settings")[0]
    store = _by_label(diagnostics, "workspace_store")[0]
    assert settings.level is DiagnosticLevel.PASS
    assert store.level is DiagnosticLevel.PASS
    assert exit_code_for(diagnostics) == 0


def test_malformed_settings_is_nonblocking_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    settings_path = doctor_module.default_settings_path()
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not valid json")

    diagnostics = run_diagnostics()

    settings = _by_label(diagnostics, "settings")[0]
    assert settings.level is DiagnosticLevel.WARN
    assert exit_code_for(diagnostics) == 0


def test_unsupported_workspace_store_version_is_blocking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    store_path = doctor_module.default_store_path()
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps({"schema_version": 999, "workspaces": {}}))

    diagnostics = run_diagnostics()

    store = _by_label(diagnostics, "workspace_store")[0]
    assert store.level is DiagnosticLevel.FAIL
    assert exit_code_for(diagnostics) == 1


def test_missing_project_root_is_a_nonblocking_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    monkeypatch.setattr(
        doctor_module,
        "load_projects_config",
        lambda: ProjectsConfig(roots=(tmp_path / "does-not-exist",)),
    )

    diagnostics = run_diagnostics()

    roots = _by_label(diagnostics, "project_root")
    assert len(roots) == 1
    assert roots[0].level is DiagnosticLevel.WARN
    assert "missing" in roots[0].detail
    assert exit_code_for(diagnostics) == 0


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permission bits only")
def test_unreadable_project_root_is_a_nonblocking_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    unreadable = tmp_path / "no-access"
    unreadable.mkdir()
    os.chmod(unreadable, 0o000)
    try:
        if os.access(unreadable, os.R_OK):
            pytest.skip("this environment does not enforce directory read permissions")

        monkeypatch.setattr(
            doctor_module, "load_projects_config", lambda: ProjectsConfig(roots=(unreadable,))
        )

        diagnostics = run_diagnostics()

        roots = _by_label(diagnostics, "project_root")
        assert roots[0].level is DiagnosticLevel.WARN
        assert exit_code_for(diagnostics) == 0
    finally:
        os.chmod(unreadable, 0o755)


def test_discovery_truncation_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    root = tmp_path / "many"
    root.mkdir()
    for i in range(5):
        (root / f"project-{i}").mkdir()
    monkeypatch.setattr(
        doctor_module,
        "load_projects_config",
        lambda: ProjectsConfig(roots=(root,), max_directories=2),
    )

    diagnostics = run_diagnostics()

    truncated = _by_label(diagnostics, "project_discovery_truncated")
    assert len(truncated) == 1
    assert truncated[0].level is DiagnosticLevel.WARN
    assert exit_code_for(diagnostics) == 0


def test_no_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)

    run_diagnostics()

    assert not doctor_module.default_settings_path().exists()
    assert not doctor_module.default_store_path().exists()
    assert not doctor_module.default_projects_config_path().exists()


def test_backup_recovery_is_warn_and_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    settings_path = doctor_module.default_settings_path()
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("broken")
    backup = Path(f"{settings_path}.bak")
    backup.write_text("{}")
    before = (settings_path.read_bytes(), backup.read_bytes())

    diagnostics = run_diagnostics()

    settings = _by_label(diagnostics, "settings")[0]
    assert settings.level is DiagnosticLevel.WARN
    assert str(settings_path) in settings.detail
    assert str(backup) in settings.detail
    assert (settings_path.read_bytes(), backup.read_bytes()) == before


def test_default_doctor_checks_remote_metadata_without_ssh_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    create_ssh_host(SshHost(host_id, "builder", "builder"))
    create_remote_project(
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66d559df977", host_id, "api", "/srv/api"
        )
    )
    monkeypatch.setattr(
        doctor_module,
        "inspect_registered_remote_project",
        lambda project: pytest.fail("default doctor must not inspect remote projects"),
    )

    diagnostics = run_diagnostics()

    assert _by_label(diagnostics, "remote_project_orphans")[0].level is DiagnosticLevel.PASS


def test_missing_ssh_executable_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(doctor_module.shutil, "which", lambda name: None)

    diagnostics = run_diagnostics()

    assert _by_label(diagnostics, "ssh_binary")[0].level is DiagnosticLevel.FAIL


def test_remote_store_future_schema_and_orphan_are_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    remote_store = doctor_module.default_remote_project_store_path()
    remote_store.parent.mkdir(parents=True)
    remote_store.write_text(json.dumps({"schema_version": 999, "projects": []}))
    orphan_store = doctor_module.default_ssh_host_store_path()
    orphan_store.write_text(json.dumps({"schema_version": 1, "hosts": []}))

    diagnostics = run_diagnostics()

    assert _by_label(diagnostics, "remote_project_store")[0].level is DiagnosticLevel.FAIL
    assert _by_label(diagnostics, "remote_project_orphans")[0].level is DiagnosticLevel.PASS


def test_remote_doctor_continues_after_mixed_registration_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)
    _assume_tmux_ok(monkeypatch)
    host_id = "d84aeefb-7c29-4c63-b39c-766d559df977"
    create_ssh_host(SshHost(host_id, "builder", "builder"))
    projects = (
        RemoteProjectRegistration(
            "c27c7b67-8e3f-4ebc-8dce-d66d559df977", host_id, "ok", "/srv/ok"
        ),
        RemoteProjectRegistration(
            "f38d8c78-9f4a-5e85-d5be-988f77f3bb19", host_id, "bad", "/srv/bad"
        ),
    )
    for project in projects:
        create_remote_project(project)

    def fake_inspection(project: RemoteProjectRegistration) -> RemoteProjectInspectionResult:
        if project.name == "ok":
            return RemoteProjectInspectionResult(
                "inspected",
                project,
                SshHost(host_id, "builder", "builder"),
                True,
                True,
                True,
                True,
                "",
                "",
                0,
            )
        return RemoteProjectInspectionResult(
            "authentication-failure",
            project,
            SshHost(host_id, "builder", "builder"),
            None,
            None,
            None,
            None,
            "",
            "Permission denied",
            255,
            "authentication failed",
        )

    monkeypatch.setattr(doctor_module, "inspect_registered_remote_project", fake_inspection)

    diagnostics = run_diagnostics(remote=True)

    results = {
        diagnostic.label: diagnostic
        for diagnostic in diagnostics
        if diagnostic.label.startswith("remote_project:")
    }
    assert results, [diagnostic.label for diagnostic in diagnostics]
    assert results[f"remote_project:{projects[0].id}"].level is DiagnosticLevel.PASS
    assert results[f"remote_project:{projects[1].id}"].level is DiagnosticLevel.FAIL
    assert "Permission denied" in results[f"remote_project:{projects[1].id}"].detail
