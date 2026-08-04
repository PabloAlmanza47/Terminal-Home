"""Read-only environment diagnostics for `th doctor`.

Every check here only reads: no configuration directory is created, no
tmux server is started, no file is migrated or rewritten. Existing store
parsing/result APIs are reused wherever one exists (settings_store,
workspace_store, projects_config_store, projects) rather than
re-implementing their JSON or version rules here.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dashboard.services import tmux
from dashboard.services.atomic_file import backup_path_for
from dashboard.services.projects import discover_projects
from dashboard.services.projects_config_store import (
    default_projects_config_path,
    load_projects_config,
    load_projects_config_result,
)
from dashboard.services.settings_store import default_settings_path, load_settings_result
from dashboard.services.workspace_store import (
    WorkspaceStoreVersionError,
    default_store_path,
    ensure_workspace_store_writable,
    load_workspace_result,
)

MIN_SUPPORTED_PYTHON = (3, 10)


class DiagnosticLevel(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One doctor check result. `label` is a stable machine-readable key
    for tests/callers; `detail` is the human-readable text shown after the
    level (e.g. "tmux found: /usr/bin/tmux").
    """

    level: DiagnosticLevel
    label: str
    detail: str


def _check_python() -> Diagnostic:
    version = platform.python_version()
    if sys.version_info[:2] >= MIN_SUPPORTED_PYTHON:
        return Diagnostic(DiagnosticLevel.PASS, "python", f"Python {version}")
    return Diagnostic(
        DiagnosticLevel.WARN,
        "python",
        f"Python {version} -- Terminal Home supports 3.10+",
    )


def _check_tmux_binary() -> Diagnostic:
    path = shutil.which("tmux")
    if path is None:
        return Diagnostic(DiagnosticLevel.FAIL, "tmux_binary", "tmux not found on PATH")
    return Diagnostic(DiagnosticLevel.PASS, "tmux_binary", f"tmux found: {path}")


def _check_tmux_version() -> Diagnostic:
    version = tmux.get_tmux_version()
    if version is None:
        return Diagnostic(DiagnosticLevel.FAIL, "tmux_version", "tmux -V did not succeed")
    return Diagnostic(DiagnosticLevel.PASS, "tmux_version", f"tmux version: {version}")


def _check_json_file(path: Path, label: str, display_name: str) -> Diagnostic:
    """A generic "does this file at least parse as JSON" check, shared by
    settings and projects-config -- neither of their own field-validation
    rules are duplicated here, since a malformed field is already handled
    safely (falls back to defaults) by their own load functions; this only
    reports the raw file-level fact doctor promises to surface.
    """
    if not path.exists():
        return Diagnostic(DiagnosticLevel.PASS, label, f"{display_name}: {path} (not created yet)")
    try:
        json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return Diagnostic(
            DiagnosticLevel.WARN,
            label,
            f"{display_name}: {path} -- unreadable or malformed ({exc}); defaults will be used",
        )
    return Diagnostic(DiagnosticLevel.PASS, label, f"{display_name}: {path}")


def _check_workspace_store(path: Path) -> Diagnostic:
    label = "workspace_store"
    if not path.exists():
        return Diagnostic(DiagnosticLevel.PASS, label, f"workspace store: {path} (not created yet)")
    try:
        ensure_workspace_store_writable(path)
    except WorkspaceStoreVersionError as exc:
        return Diagnostic(DiagnosticLevel.FAIL, label, f"workspace store: {path} -- {exc}")
    # Any canonical path is sufficient to observe file-level backup recovery.
    result = load_workspace_result(Path("/"), path)
    if result.warning:
        return Diagnostic(
            DiagnosticLevel.WARN,
            label,
            f"workspace store: {path} -- recovered from {backup_path_for(path)}",
        )
    return Diagnostic(DiagnosticLevel.PASS, label, f"workspace store: {path}")


def _check_root(root: Path) -> Diagnostic:
    expanded = root.expanduser()
    try:
        exists = expanded.exists()
    except OSError:
        exists = False
    if not exists:
        return Diagnostic(DiagnosticLevel.WARN, "project_root", f"project root missing: {expanded}")
    if not expanded.is_dir():
        return Diagnostic(
            DiagnosticLevel.WARN, "project_root", f"project root is not a directory: {expanded}"
        )
    if not os.access(expanded, os.R_OK):
        return Diagnostic(
            DiagnosticLevel.WARN, "project_root", f"project root not readable: {expanded}"
        )
    return Diagnostic(DiagnosticLevel.PASS, "project_root", f"project root: {expanded}")


def _check_manual_project(path: Path) -> Diagnostic:
    expanded = path.expanduser()
    try:
        exists = expanded.is_dir()
    except OSError:
        exists = False
    if not exists:
        return Diagnostic(
            DiagnosticLevel.WARN, "manual_project", f"manual project missing: {expanded}"
        )
    return Diagnostic(DiagnosticLevel.PASS, "manual_project", f"manual project: {expanded}")


def run_diagnostics() -> tuple[Diagnostic, ...]:
    """Every doctor check, in display order. Never raises, never mutates
    anything -- reads only.
    """
    diagnostics: list[Diagnostic] = [_check_python(), _check_tmux_binary()]

    if diagnostics[-1].level is not DiagnosticLevel.FAIL:
        diagnostics.append(_check_tmux_version())

    settings_path = default_settings_path()
    settings_result = load_settings_result(settings_path)
    if settings_result.warning:
        diagnostics.append(
            Diagnostic(
                DiagnosticLevel.WARN,
                "settings",
                f"settings path: {settings_path} -- "
                f"recovered from {backup_path_for(settings_path)}",
            )
        )
    else:
        diagnostics.append(_check_json_file(settings_path, "settings", "settings path"))
    diagnostics.append(_check_workspace_store(default_store_path()))
    projects_path = default_projects_config_path()
    config_result = load_projects_config_result(projects_path)
    if config_result.warning:
        diagnostics.append(
            Diagnostic(
                DiagnosticLevel.WARN,
                "projects_config",
                f"project configuration: {projects_path} -- "
                f"recovered from {backup_path_for(projects_path)}",
            )
        )
    else:
        diagnostics.append(
            _check_json_file(projects_path, "projects_config", "project configuration")
        )

    # Keep this convenience API as the injection seam used by doctor callers/tests;
    # both reads are mutation-free and recovery is reported by the result above.
    config = load_projects_config()
    for root in config.roots:
        diagnostics.append(_check_root(root))
    for manual_path in config.manual_projects:
        diagnostics.append(_check_manual_project(manual_path))

    discovery = discover_projects(config)
    if discovery.truncated:
        diagnostics.append(
            Diagnostic(
                DiagnosticLevel.WARN,
                "project_discovery_truncated",
                "project discovery stopped early after reaching its directory limit "
                "-- some projects may be missing",
            )
        )
    diagnostics.append(
        Diagnostic(
            DiagnosticLevel.PASS,
            "project_discovery",
            f"project discovery: {len(discovery.projects)} projects",
        )
    )

    return tuple(diagnostics)


def exit_code_for(diagnostics: tuple[Diagnostic, ...]) -> int:
    """0 when every check is PASS or a nonblocking WARN; 1 when any check
    reports a blocking FAIL (tmux unavailable, or a launch-critical
    workspace-store version this build can't read).
    """
    if any(diagnostic.level is DiagnosticLevel.FAIL for diagnostic in diagnostics):
        return 1
    return 0


def format_diagnostic(diagnostic: Diagnostic) -> str:
    return f"{diagnostic.level.value:<4}  {diagnostic.detail}"
