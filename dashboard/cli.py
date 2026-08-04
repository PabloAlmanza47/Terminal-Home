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

from dashboard.services.completion import (
    SHELLS,
    discover_project_selector_candidates,
    render_completion,
)
from dashboard.services.doctor import exit_code_for, format_diagnostic, run_diagnostics
from dashboard.services.project_launch import (
    ProjectLaunchPreparationError,
    launch_status_line,
    prepare_project_launch,
    resolve_project_plan,
    resolve_project_status,
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
from dashboard.services.ssh_host_store import load_all_ssh_hosts
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
    doctor_parser.set_defaults(handler=_run_doctor)

    completion_parser = subparsers.add_parser(
        "completion", help="Generate dynamic shell completion."
    )
    completion_parser.add_argument("shell", choices=SHELLS, help="Shell to generate for.")
    completion_parser.set_defaults(handler=_run_completion)

    return parser


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
    resolved = resolve_project_status(args.project)
    for warning in resolved.warnings:
        print(warning, file=sys.stderr)
    if resolved.status is None:
        print(f"error: {resolved.error}", file=sys.stderr)
        return 1

    try:
        prepared = prepare_project_launch(resolved.status)
    except (OSError, ProjectLaunchPreparationError, WorkspaceStoreVersionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

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
    diagnostics = run_diagnostics()
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
