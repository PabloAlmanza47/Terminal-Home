"""The CLI dispatcher: decides whether to open the Textual dashboard or run
a read-only subcommand.

dashboard.app:main remains the TUI-only application launcher -- it is
never duplicated here, only called (lazily, so a subcommand invocation
never even imports Textual), and only when no subcommand is given. Every
subcommand handler below is a thin adapter over the existing
project/workspace/settings service layer; none of them re-implements
discovery, selection, session-status, persistence, or tmux launch rules.

Every package console script (`terminal-home`, `th`, `dev`) and
`python -m dashboard` point at this module's main(), so there is exactly
one place that decides "TUI or subcommand" regardless of which command
name launched the process.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Sequence
from uuid import uuid4

from dashboard.models import RemoteProjectRegistration, SshHost, SshModelValidationError
from dashboard.services.completion import (
    SHELLS,
    discover_project_selector_candidates,
    render_completion,
)
from dashboard.services.doctor import exit_code_for, format_diagnostic, run_diagnostics
from dashboard.services.project_launch import (
    ProjectLaunchPreparationError,
    launch_status_line,
    prepare_project_launch_for_selector,
    resolve_project_plan,
)
from dashboard.services.project_selection import (
    RegisteredRemoteProject,
    list_selectable_projects,
)
from dashboard.services.projects import (
    ProjectStatus,
    format_scan_warnings,
    scan_all_projects,
    status_badge,
)
from dashboard.services.remote_project_store import (
    RemoteProjectStoreError,
)
from dashboard.services.remote_registry import (
    RemoteRegistryError,
    inspect_remote_registry_integrity,
    register_remote_project,
    remove_registered_remote_project,
    remove_ssh_host,
    update_registered_remote_project,
)
from dashboard.services.ssh_host_store import (
    SshHostStoreError,
    create_ssh_host,
    load_all_ssh_hosts,
    update_ssh_host,
)
from dashboard.services.tmux import TmuxCommandError
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request
from dashboard.services.workspace_plan import format_plan
from dashboard.services.workspace_store import WorkspaceStoreVersionError

PROG = "th"

_STATUS_WORDS = {
    "Running": "running",
    "Saved Workspace": "saved",
    "Metadata Warning": "warning",
    "Not Configured": "default",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Terminal Home: a declarative tmux workspace manager. "
            "Run with no arguments to open the dashboard."
        ),
    )
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser(
        "list", help="List discovered projects and their status (read-only)."
    )
    list_parser.set_defaults(handler=_run_list)

    plan_parser = subparsers.add_parser(
        "plan",
        help="Show what `th up <project>` would do, without doing it (read-only).",
    )
    plan_parser.add_argument("project", help="Project name (if unique) or filesystem path.")
    plan_parser.set_defaults(handler=_run_plan)

    up_parser = subparsers.add_parser(
        "up", help="Create or attach to the selected project's tmux workspace."
    )
    up_parser.add_argument("project", help="Project name (if unique) or filesystem path.")
    up_parser.set_defaults(handler=_run_up)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check the local environment (read-only)."
    )
    doctor_parser.add_argument(
        "--remote",
        action="store_true",
        help="Also inspect manually registered remote projects over SSH.",
    )
    doctor_parser.set_defaults(handler=_run_doctor)

    completion_parser = subparsers.add_parser(
        "completion", help="Generate dynamic shell completion."
    )
    completion_parser.add_argument("shell", choices=SHELLS, help="Shell to generate for.")
    completion_parser.set_defaults(handler=_run_completion)

    host_parser = subparsers.add_parser("host", help="Manage registered SSH hosts.")
    host_subparsers = host_parser.add_subparsers(dest="host_command", required=True)
    host_list_parser = host_subparsers.add_parser("list", help="List SSH hosts.")
    host_list_parser.set_defaults(handler=_run_host_list)
    host_add_parser = host_subparsers.add_parser("add", help="Register an SSH host.")
    _add_host_fields(host_add_parser, include_id=True)
    host_add_parser.set_defaults(handler=_run_host_add)
    host_edit_parser = host_subparsers.add_parser("edit", help="Edit an SSH host.")
    host_edit_parser.add_argument("id", help="SSH host ID.")
    _add_host_fields(host_edit_parser, include_id=False)
    host_edit_parser.set_defaults(handler=_run_host_edit)
    host_remove_parser = host_subparsers.add_parser("remove", help="Remove an SSH host.")
    host_remove_parser.add_argument("id", help="SSH host ID.")
    host_remove_parser.set_defaults(handler=_run_host_remove)

    remote_parser = subparsers.add_parser("remote", help="Manage remote projects.")
    remote_subparsers = remote_parser.add_subparsers(dest="remote_command", required=True)
    remote_list_parser = remote_subparsers.add_parser("list", help="List remote projects.")
    remote_list_parser.set_defaults(handler=_run_remote_list)
    remote_add_parser = remote_subparsers.add_parser("add", help="Register a remote project.")
    _add_remote_fields(remote_add_parser, include_id=True, host_required=True)
    remote_add_parser.set_defaults(handler=_run_remote_add)
    remote_edit_parser = remote_subparsers.add_parser("edit", help="Edit a remote project.")
    remote_edit_parser.add_argument("id", help="Remote project registration ID.")
    _add_remote_fields(remote_edit_parser, include_id=False, host_required=False)
    remote_edit_parser.set_defaults(handler=_run_remote_edit)
    remote_remove_parser = remote_subparsers.add_parser("remove", help="Remove a remote project.")
    remote_remove_parser.add_argument("id", help="Remote project registration ID.")
    remote_remove_parser.set_defaults(handler=_run_remote_remove)

    return parser


def _add_host_fields(parser: argparse.ArgumentParser, *, include_id: bool) -> None:
    if include_id:
        parser.add_argument("--id", default=None, help="Stable host UUID (generated if omitted).")
    parser.add_argument("--name", required=True, help="Display name.")
    parser.add_argument("--destination", required=True, help="OpenSSH destination operand.")


def _add_remote_fields(
    parser: argparse.ArgumentParser, *, include_id: bool, host_required: bool
) -> None:
    if include_id:
        parser.add_argument("--id", default=None, help="Stable registration UUID.")
    parser.add_argument("--name", required=True, help="Project name.")
    parser.add_argument("--host-id", required=host_required, default=None, help="SSH host ID.")
    parser.add_argument("--remote-path", required=True, help="Absolute remote project path.")


def _management_error(exc: BaseException | str) -> int:
    print(f"error: {exc}", file=sys.stderr)
    return 1


def _run_host_list(args: argparse.Namespace) -> int:
    hosts = load_all_ssh_hosts()
    if not hosts:
        print("No SSH hosts registered.")
        return 0
    print("ID  NAME  DESTINATION")
    for host in hosts:
        print(f"{host.id}  {host.display_name}  {host.destination}")
    return 0


def _run_host_add(args: argparse.Namespace) -> int:
    try:
        host = create_ssh_host(SshHost(args.id or str(uuid4()), args.name, args.destination))
    except (SshModelValidationError, SshHostStoreError) as exc:
        return _management_error(exc)
    print(f"Added SSH host {host.id} ({host.display_name}).")
    return 0


def _run_host_edit(args: argparse.Namespace) -> int:
    try:
        host = update_ssh_host(
            args.id, display_name=args.name, destination=args.destination
        )
    except (SshModelValidationError, SshHostStoreError) as exc:
        return _management_error(exc)
    if host is None:
        return _management_error(f"SSH host {args.id} is not registered.")
    print(f"Updated SSH host {host.id}.")
    return 0


def _run_host_remove(args: argparse.Namespace) -> int:
    try:
        removed = remove_ssh_host(args.id)
    except (RemoteRegistryError, SshHostStoreError) as exc:
        return _management_error(exc)
    if not removed:
        return _management_error(f"SSH host {args.id} is not registered.")
    print(f"Removed SSH host {args.id}.")
    return 0


def _run_remote_list(args: argparse.Namespace) -> int:
    integrity = inspect_remote_registry_integrity()
    if integrity.project_error:
        return _management_error(integrity.project_error)
    hosts = {host.id: host for host in integrity.hosts}
    if not integrity.projects:
        print("No remote projects registered.")
        return 0
    print("ID  NAME  HOST  REMOTE PATH  STATUS")
    for project in integrity.projects:
        host_label = (
            hosts[project.host_id].display_name
            if project.host_id in hosts
            else project.host_id
        )
        status = (
            "registered"
            if project.id not in integrity.orphaned_project_ids
            else "missing host"
        )
        print(f"{project.id}  {project.name}  {host_label}  {project.remote_path}  {status}")
    return 0


def _run_remote_add(args: argparse.Namespace) -> int:
    try:
        project = register_remote_project(
            RemoteProjectRegistration(
                args.id or str(uuid4()), args.host_id, args.name, args.remote_path
            )
        )
    except (SshModelValidationError, RemoteRegistryError, RemoteProjectStoreError) as exc:
        return _management_error(exc)
    print(f"Added remote project {project.id} ({project.name}).")
    return 0


def _run_remote_edit(args: argparse.Namespace) -> int:
    try:
        project = update_registered_remote_project(
            args.id,
            name=args.name,
            host_id=args.host_id,
            remote_path=args.remote_path,
        )
    except (SshModelValidationError, RemoteRegistryError, RemoteProjectStoreError) as exc:
        return _management_error(exc)
    if project is None:
        return _management_error(f"Remote project {args.id} is not registered.")
    print(f"Updated remote project {project.id} ({project.name}).")
    return 0


def _run_remote_remove(args: argparse.Namespace) -> int:
    try:
        removed = remove_registered_remote_project(args.id)
    except RemoteProjectStoreError as exc:
        return _management_error(exc)
    if not removed:
        return _management_error(f"Remote project {args.id} is not registered.")
    print(f"Removed remote project {args.id}.")
    return 0


def _print_project_table(statuses: Sequence[ProjectStatus]) -> None:
    headers = ("NAME", "STATUS", "SESSION", "PATH")
    rows = [
        (
            status.project.name,
            _STATUS_WORDS[status_badge(status)],
            status.expected_session_name,
            str(status.canonical_path),
        )
        for status in statuses
    ]
    widths = [max(len(row[i]) for row in (headers, *rows)) for i in range(len(headers) - 1)]
    for row in (headers, *rows):
        line = "  ".join(cell.ljust(width) for cell, width in zip(row[:-1], widths))
        print(f"{line}  {row[-1]}")


def _print_remote_project_table(
    projects: Sequence[RegisteredRemoteProject],
) -> None:
    headers = ("NAME", "SELECTOR", "HOST", "PATH", "STATUS")
    known_host_ids = {host.id for host in load_all_ssh_hosts()}
    rows = [
        (
            project.name,
            project.selector,
            project.location.host_id,
            project.location.remote_path,
            "orphaned (missing host)"
            if project.location.host_id not in known_host_ids
            else "registered",
        )
        for project in projects
    ]
    widths = [
        max(len(row[index]) for row in (headers, *rows))
        for index in range(len(headers) - 1)
    ]
    for row in (headers, *rows):
        line = "  ".join(cell.ljust(width) for cell, width in zip(row[:-1], widths))
        print(f"{line}  {row[-1]}")


def _run_list(args: argparse.Namespace) -> int:
    selectable_projects = list_selectable_projects()
    remote_projects = tuple(
        project
        for project in selectable_projects
        if isinstance(project, RegisteredRemoteProject)
    )
    result = scan_all_projects()

    if not result.statuses:
        if not remote_projects:
            print("No projects discovered.")
    else:
        _print_project_table(result.statuses)

    if remote_projects:
        if result.statuses:
            print()
        print("REMOTE PROJECTS")
        _print_remote_project_table(remote_projects)

    warning = format_scan_warnings(result)
    if warning:
        print(warning, file=sys.stderr)

    return 0


def _run_plan(args: argparse.Namespace) -> int:
    resolved = resolve_project_plan(args.project)
    for warning in resolved.warnings:
        print(warning, file=sys.stderr)
    if resolved.plan is None:
        print(f"error: {resolved.error}", file=sys.stderr)
        return 1

    print(format_plan(resolved.plan))
    return 1 if resolved.plan.blocked else 0


def _run_up(args: argparse.Namespace) -> int:
    try:
        resolved = prepare_project_launch_for_selector(args.project)
    except (OSError, ProjectLaunchPreparationError, WorkspaceStoreVersionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for warning in resolved.warnings:
        print(warning, file=sys.stderr)
    if resolved.prepared is None:
        print(f"error: {resolved.error}", file=sys.stderr)
        return 1

    prepared = resolved.prepared
    assert prepared is not None

    print(launch_status_line(prepared), flush=True)
    try:
        execute_launch_request(prepared.request)
    except (LaunchError, TmuxCommandError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    print("Terminal Home doctor")
    print()
    diagnostics = run_diagnostics(remote=args.remote)
    for diagnostic in diagnostics:
        print(format_diagnostic(diagnostic))

    return exit_code_for(diagnostics)


def _run_completion(args: argparse.Namespace) -> int:
    print(render_completion(args.shell), end="")
    return 0


def _run_internal_completion(argv: Sequence[str]) -> int | None:
    if list(argv) != ["__complete", "projects"]:
        return None
    for candidate in discover_project_selector_candidates():
        print(candidate)
    return 0


def run(argv: Sequence[str] | None = None) -> int:
    """Parse *argv* (defaulting to sys.argv[1:]) and either open the
    Textual dashboard (no subcommand given) or dispatch to a CLI command
    handler. Like argparse itself, `--help` and an invalid invocation exit
    the process directly (SystemExit) rather than returning.
    """
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    internal_result = _run_internal_completion(effective_argv)
    if internal_result is not None:
        return internal_result

    parser = _build_parser()
    args = parser.parse_args(effective_argv)

    if args.command is None:
        from dashboard.app import main as app_main

        app_main()
        return 0

    return args.handler(args)


def main() -> None:
    exit_code = run()
    if exit_code:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
