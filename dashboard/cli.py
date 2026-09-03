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
import json
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from dashboard import __version__
from dashboard.models import RemoteProjectRegistration, SshHost, SshModelValidationError
from dashboard.services.activity import codex_status, server_status, workspace_status
from dashboard.services.agent_deck_launcher import AgentDeckLaunchError, execute_agent_deck_attach
from dashboard.services.cli_colors import style_table_header
from dashboard.services.completion import (
    SHELLS,
    discover_project_selector_candidates,
    discover_template_names,
    render_completion,
)
from dashboard.services.doctor import exit_code_for, format_diagnostic, run_diagnostics
from dashboard.services.git import GitStatus, load_status
from dashboard.services.project_creation import (
    DEFAULT_PROJECTS_ROOT,
    ProjectCreationError,
    ProjectCreationRequest,
    create_project,
)
from dashboard.services.project_launch import (
    ProjectLaunchPreparationError,
    launch_status_line,
    prepare_project_launch_for_selector,
    resolve_project_plan,
)
from dashboard.services.project_selection import (
    RegisteredRemoteProject,
    list_selectable_projects,
    resolve_project_selector,
)
from dashboard.services.projects import (
    ProjectStatus,
    format_scan_warnings,
    gather_single_project_status,
    refresh_single_project_status,
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
from dashboard.services.settings_store import load_settings
from dashboard.services.slug import slugify
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


def _watch_interval(value: str) -> float:
    try:
        interval = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("watch interval must be a number") from exc
    if interval < 0.1:
        raise argparse.ArgumentTypeError("watch interval must be at least 0.1 seconds")
    return interval


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Terminal Home: a declarative tmux workspace manager. "
            "Run with no arguments to open the dashboard."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser(
        "list", help="List discovered projects and their status (read-only)."
    )
    list_parser.set_defaults(handler=_run_list)

    status_parser = subparsers.add_parser(
        "status", help="Show the current workspace, server, agent, and Git status."
    )
    status_parser.add_argument(
        "project", nargs="?", default=".", help="Project name or filesystem path (default: .)."
    )
    status_output = status_parser.add_mutually_exclusive_group()
    status_output.add_argument(
        "--json", action="store_true", help="Emit stable machine-readable JSON."
    )
    status_output.add_argument(
        "--watch", action="store_true", help="Refresh live every second until Ctrl+C."
    )
    status_parser.add_argument(
        "--interval",
        type=_watch_interval,
        default=1.0,
        metavar="SECONDS",
        help="Watch refresh interval in seconds (default: 1; minimum: 0.1).",
    )
    status_parser.set_defaults(handler=_run_status)

    agent_parser = subparsers.add_parser(
        "agent", help="Attach to the existing Agent Deck session for a project."
    )
    agent_parser.add_argument(
        "project", nargs="?", default=".", help="Project name or filesystem path (default: .)."
    )
    agent_parser.set_defaults(handler=_run_agent)

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

    new_parser = subparsers.add_parser(
        "new",
        help="Create a new local project without opening the dashboard.",
        description=(
            "Create a local Terminal Home project. Defaults are a slug-derived folder, "
            "Git initialization, the default workspace, and launch."
        ),
        epilog=(
            "Examples: th new my-project; th new my-project --no-git; "
            "th new my-project --path ~/school/csce331/my-project; "
            "th new my-project --template web-development"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    new_parser.add_argument("project_name", help="Display name for the new project.")
    destination_group = new_parser.add_mutually_exclusive_group()
    destination_group.add_argument(
        "--path", type=Path, help="Complete local destination path (expands '~')."
    )
    destination_group.add_argument(
        "--root", type=Path, help="Parent project root; the slug-derived folder is appended."
    )
    new_parser.add_argument(
        "--template", metavar="TEMPLATE", help="Registered local workspace template name."
    )
    git_group = new_parser.add_mutually_exclusive_group()
    git_group.add_argument(
        "--git", dest="init_git", action="store_true", help="Initialize Git (default)."
    )
    git_group.add_argument(
        "--no-git", dest="init_git", action="store_false", help="Do not initialize Git."
    )
    launch_group = new_parser.add_mutually_exclusive_group()
    launch_group.add_argument(
        "--launch", dest="launch", action="store_true", help="Launch after creation (default)."
    )
    launch_group.add_argument(
        "--no-launch", dest="launch", action="store_false", help="Create without launching tmux."
    )
    mode_group = new_parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--interactive", action="store_true", help="Prompt for unspecified choices."
    )
    mode_group.add_argument(
        "--non-interactive", action="store_true", help="Never prompt; use safe defaults."
    )
    new_parser.set_defaults(handler=_run_new, init_git=None, launch=None)

    doctor_parser = subparsers.add_parser(
        "doctor", help="Check the local environment or one project."
    )
    doctor_mode = doctor_parser.add_mutually_exclusive_group()
    doctor_mode.add_argument("project", nargs="?", help="Local project name or path.")
    doctor_mode.add_argument(
        "--remote", action="store_true", help="Inspect registered remote projects over SSH."
    )
    doctor_parser.set_defaults(handler=_run_doctor)

    setup_parser = subparsers.add_parser(
        "setup", help="Propose or safely apply local project setup actions."
    )
    setup_parser.add_argument("project", help="Local project name or path.")
    setup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the plan without prompting or changing anything.",
    )
    setup_parser.set_defaults(handler=_run_setup)

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
        host = update_ssh_host(args.id, display_name=args.name, destination=args.destination)
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
            hosts[project.host_id].display_name if project.host_id in hosts else project.host_id
        )
        status = (
            "registered" if project.id not in integrity.orphaned_project_ids else "missing host"
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
    header_color = load_settings().table_header_color
    for row_index, row in enumerate((headers, *rows)):
        line = "  ".join(cell.ljust(width) for cell, width in zip(row[:-1], widths))
        line += f"  {row[-1]}"
        if row_index == 0:
            line = style_table_header(line, header_color, sys.stdout)
        print(line)


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
    widths = [max(len(row[index]) for row in (headers, *rows)) for index in range(len(headers) - 1)]
    header_color = load_settings().table_header_color
    for row_index, row in enumerate((headers, *rows)):
        line = "  ".join(cell.ljust(width) for cell, width in zip(row[:-1], widths))
        line += f"  {row[-1]}"
        if row_index == 0:
            line = style_table_header(line, header_color, sys.stdout)
        print(line)


def _run_list(args: argparse.Namespace) -> int:
    selectable_projects = list_selectable_projects()
    remote_projects = tuple(
        project for project in selectable_projects if isinstance(project, RegisteredRemoteProject)
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


def _resolve_local_project(selector: str):
    resolved = resolve_project_selector(selector)
    if not resolved.ok:
        print(f"error: {resolved.error}", file=sys.stderr)
        return None
    if isinstance(resolved.project, RegisteredRemoteProject):
        print("error: status and agent commands support local projects only", file=sys.stderr)
        return None
    assert resolved.project is not None
    return resolved.project


def _agent_cli_state(status: ProjectStatus) -> tuple[str, int]:
    value, count = codex_status(status)
    labels = {
        "Working": "working",
        "Waiting": "waiting",
        "Idle": "idle",
        "Stopped": "stopped",
        "Error": "error",
        "Unknown": "unknown",
        "No Agent": "none",
    }
    return labels.get(value.label, "unknown"), count


def _status_payload(status: ProjectStatus, git: GitStatus) -> dict[str, object]:
    agent_state, agent_count = _agent_cli_state(status)
    workspace = workspace_status(status)
    server = server_status(status)
    return {
        "project": {"name": status.project.name, "path": str(status.canonical_path)},
        "workspace": {"status": workspace.label.casefold().replace(" ", "_")},
        "server": {"status": server.label.casefold().replace(" ", "_")},
        "agent": {"status": agent_state, "count": agent_count},
        "git": {
            "branch": git.branch,
            "detached": git.detached,
            "clean": git.clean,
            "changes": len(git.changes),
            "staged": git.staged_count,
            "modified": git.modified_count,
            "untracked": git.untracked_count,
            "available": git.available,
            "files": [
                {
                    "path": change.path,
                    "index_status": change.index_status,
                    "worktree_status": change.worktree_status,
                    **(
                        {"old_path": change.old_path}
                        if change.old_path is not None
                        else {}
                    ),
                }
                for change in git.changes
            ],
        },
    }


def _status_text(
    status: ProjectStatus, git: GitStatus, *, watch: bool = False, interval: float = 1.0
) -> str:
    agent_state, agent_count = _agent_cli_state(status)
    workspace = workspace_status(status)
    server = server_status(status)
    agent_label = "No Agent" if agent_state == "none" else agent_state.title()
    if agent_state == "working":
        agent_label = "Working"
    if agent_count > 1:
        agent_label = f"{agent_label} ({agent_count})"
    lines = [status.project.name, str(status.canonical_path), ""]
    lines.extend(
        [
            f"Workspace  {workspace.label}",
            f"Server     {server.label}",
            f"Codex      {agent_label}",
            "",
            "Git",
        ]
    )
    if git.is_repo is False:
        lines.append("  Status    Not a Git repository")
    elif git.error:
        lines.append(f"  Status    Unavailable ({git.error})")
    else:
        branch = "(detached HEAD)" if git.detached else (git.branch or "(unknown)")
        lines.append(f"  Branch    {branch}")
        if git.clean:
            lines.append("  Status    Clean")
        else:
            lines.extend(
                [
                    f"  Changes   {len(git.changes)}",
                    f"  Staged    {git.staged_count}",
                    f"  Modified  {git.modified_count}",
                    f"  Untracked {git.untracked_count}",
                ]
            )
            if git.changes:
                lines.append("")
                lines.append("Changed Files")
                for change in git.changes:
                    path = (
                        f"{change.old_path} -> {change.path}"
                        if change.old_path is not None
                        else change.path
                    )
                    lines.append(f"  {change.indicator:<2} {path}")
    if watch:
        lines.extend(["", f"Refreshing every {interval:g}s · Ctrl+C to exit"])
    return "\n".join(lines)


def _print_status(status: ProjectStatus, git: GitStatus) -> None:
    print(_status_text(status, git))


def _collect_status(project, previous: ProjectStatus | None = None):
    status = (
        gather_single_project_status(project)
        if previous is None
        else refresh_single_project_status(project, previous)
    )
    return status, load_status(status.canonical_path)


def _run_status_watch(project, interval: float) -> int:
    if not sys.stdout.isatty():
        print("error: --watch requires an interactive stdout terminal", file=sys.stderr)
        return 2
    previous: ProjectStatus | None = None
    previous_render: str | None = None
    try:
        while True:
            status, git = _collect_status(project, previous)
            rendered = _status_text(status, git, watch=True, interval=interval)
            if rendered != previous_render:
                if previous_render is not None:
                    sys.stdout.write("\x1b[2J\x1b[H")
                sys.stdout.write(rendered + "\n")
                sys.stdout.flush()
                previous_render = rendered
            previous = status
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    except (OSError, WorkspaceStoreVersionError) as exc:
        print(f"error: could not refresh project status: {exc}", file=sys.stderr)
        return 1


def _run_status(args: argparse.Namespace) -> int:
    if args.watch and args.json:
        print("error: --json and --watch cannot be used together", file=sys.stderr)
        return 2
    project = _resolve_local_project(args.project)
    if project is None:
        return 2
    if args.watch:
        return _run_status_watch(project, args.interval)
    try:
        status, git = _collect_status(project)
    except (OSError, WorkspaceStoreVersionError) as exc:
        print(f"error: could not gather project status: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(_status_payload(status, git), indent=2, sort_keys=True))
    else:
        _print_status(status, git)
    return 0


def _run_agent(args: argparse.Namespace) -> int:
    project = _resolve_local_project(args.project)
    if project is None:
        return 2
    try:
        status = gather_single_project_status(project)
    except (OSError, WorkspaceStoreVersionError) as exc:
        print(f"error: could not inspect project: {exc}", file=sys.stderr)
        return 1
    sessions = list(status.agent_sessions)
    if not sessions:
        print(f"No Agent Deck session found for {project.name}.", file=sys.stderr)
        return 1
    if len(sessions) > 1:
        print(f"Multiple Agent Deck sessions found for {project.name}:", file=sys.stderr)
        for session in sessions:
            print(f"  {session.id}  {session.title}  ({session.status.value})", file=sys.stderr)
        print("Specify a session identifier in a future version.", file=sys.stderr)
        return 2
    try:
        execute_agent_deck_attach(sessions[0].id)
    except AgentDeckLaunchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
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


class _PromptCancelled(Exception):
    pass


def _prompt_text(prompt: str, default: str, *, stdin, stdout) -> str:
    print(f"{prompt} [{default}]: ", end="", file=stdout, flush=True)
    try:
        answer = stdin.readline()
    except KeyboardInterrupt as exc:
        raise _PromptCancelled from exc
    if answer == "":
        return default
    return answer.strip() or default


def _prompt_yes_no(prompt: str, default: bool, *, stdin, stdout) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        print(f"{prompt} [{suffix}]: ", end="", file=stdout, flush=True)
        try:
            answer = stdin.readline()
        except KeyboardInterrupt as exc:
            raise _PromptCancelled from exc
        answer = answer.strip().casefold()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer yes or no.", file=stdout)


def _run_new(args: argparse.Namespace) -> int:
    interactive = bool(args.interactive) and not args.non_interactive
    project_name = args.project_name
    folder = slugify(project_name)
    if not folder:
        return _management_error("project name must contain at least one letter or number")

    try:
        if args.path is not None:
            destination = args.path.expanduser().resolve()
        else:
            root = (
                args.root.expanduser().resolve() if args.root is not None else DEFAULT_PROJECTS_ROOT
            )
            if interactive and args.root is None:
                folder = _prompt_text("Project folder", folder, stdin=sys.stdin, stdout=sys.stdout)
                root = (
                    Path(
                        _prompt_text("Project root", str(root), stdin=sys.stdin, stdout=sys.stdout)
                    )
                    .expanduser()
                    .resolve()
                )
            destination = (root / folder).resolve()

        init_git = args.init_git
        if init_git is None:
            init_git = (
                _prompt_yes_no("Initialize Git?", True, stdin=sys.stdin, stdout=sys.stdout)
                if interactive
                else True
            )

        template_name = args.template
        if template_name is None and interactive:
            template_answer = _prompt_text(
                "Workspace template", "default", stdin=sys.stdin, stdout=sys.stdout
            )
            template_name = (
                None if template_answer.casefold() in {"", "default", "none"} else template_answer
            )

        launch = args.launch
        if launch is None:
            launch = (
                _prompt_yes_no("Launch after creation?", True, stdin=sys.stdin, stdout=sys.stdout)
                if interactive
                else True
            )

        result = create_project(
            ProjectCreationRequest(
                project_name=project_name,
                destination=destination,
                init_git=init_git,
                launch=launch,
                template_name=template_name,
            )
        )
    except _PromptCancelled:
        print("error: interactive creation cancelled", file=sys.stderr)
        return 1
    except (OSError, ProjectCreationError, WorkspaceStoreVersionError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Created project: {project_name}")
    print(f"Path: {result.destination}")
    print(f"Workspace: {result.workspace_label}")
    print(f"Git: {'initialized' if result.git_initialized else 'skipped'}")
    if result.launch_request is None:
        print("Launch: skipped")
        return 0
    print("Opening tmux workspace...", flush=True)
    try:
        execute_launch_request(result.launch_request)
    except (LaunchError, TmuxCommandError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"error: project was created but launch failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _run_doctor(args: argparse.Namespace) -> int:
    if args.project:
        from dashboard.services.project_intelligence import (
            format_project_report,
            inspect_project,
            project_exit_code,
        )

        resolved = resolve_project_selector(args.project)
        if not resolved.ok:
            print(f"error: {resolved.error}", file=sys.stderr)
            return 1
        if isinstance(resolved.project, RegisteredRemoteProject):
            print(
                "error: remote project intelligence is unsupported; no SSH connection was made",
                file=sys.stderr,
            )
            return 1
        assert resolved.project is not None
        if not resolved.project.path.is_dir():
            print(
                f"error: project is not a readable directory: {resolved.project.path}",
                file=sys.stderr,
            )
            return 1
        try:
            info = inspect_project(resolved.project.path)
        except (OSError, RuntimeError) as exc:
            print(f"error: cannot inspect project: {exc}", file=sys.stderr)
            return 1
        print(format_project_report(info))
        return project_exit_code(info)
    print("Terminal Home doctor")
    print()
    diagnostics = run_diagnostics(remote=args.remote)
    for diagnostic in diagnostics:
        print(format_diagnostic(diagnostic))

    return exit_code_for(diagnostics)


def _resolve_local_intelligence(selector: str):
    resolved = resolve_project_selector(selector)
    if not resolved.ok:
        print(f"error: {resolved.error}", file=sys.stderr)
        return None
    if isinstance(resolved.project, RegisteredRemoteProject):
        print(
            "error: remote project setup is not supported yet; no SSH connection was made",
            file=sys.stderr,
        )
        return None
    assert resolved.project is not None
    if not resolved.project.path.is_dir():
        print(
            f"error: project is not a readable directory: {resolved.project.path}", file=sys.stderr
        )
        return None
    return resolved.project


def _run_setup(args: argparse.Namespace) -> int:
    from dashboard.services.project_intelligence import (
        execute_setup_action,
        format_project_report,
        inspect_project,
        project_exit_code,
        shell_display,
    )

    project = _resolve_local_intelligence(args.project)
    if project is None:
        return 1
    try:
        info = inspect_project(project.path)
    except (OSError, RuntimeError) as exc:
        print(f"error: cannot inspect project: {exc}", file=sys.stderr)
        return 1
    print(format_project_report(info))
    actions = info.setup_actions
    if not actions:
        print("\nNo changes made.")
        return project_exit_code(info)
    print("\nSetup plan")
    if args.dry_run:
        for index, action in enumerate(actions, 1):
            print(f"  {index}. {action.description}: {action.reason}")
            print(f"     cwd: {action.cwd}")
            if action.argv:
                print(f"     argv: {shell_display(action.argv)}")
            if action.source and action.destination:
                print(f"     copy: {action.source} -> {action.destination}")
            print(
                f"     risk: {action.risk.value}; evidence: {', '.join(map(str, action.evidence))}"
            )
        return project_exit_code(info)
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "error: setup requires an interactive terminal; use "
            "--dry-run for a non-interactive plan",
            file=sys.stderr,
        )
        return 1
    approved = []
    try:
        for action in actions:
            if _prompt_yes_no(
                f"Approve {action.description}?", False, stdin=sys.stdin, stdout=sys.stdout
            ):
                approved.append(action)
        final_approval = _prompt_yes_no(
            "Execute the approved setup actions?", False, stdin=sys.stdin, stdout=sys.stdout
        )
    except _PromptCancelled:
        print("No changes made.")
        return 0
    if not approved or not final_approval:
        print("No changes made.")
        return 0
    approved_ids = {a.id for a in approved}
    completed: set[str] = set()
    attempted: set[str] = set()
    stopped = False
    for action in approved:
        if any(dep not in completed for dep in action.dependencies):
            print(f"SKIP  {action.id}: prerequisite was not completed")
            attempted.add(action.id)
            continue
        attempted.add(action.id)
        try:
            ok, detail = execute_setup_action(action)
        except (OSError, subprocess.SubprocessError) as exc:
            ok, detail = False, str(exc)
        print(f"{'PASS' if ok else 'FAIL'}  {action.description}: {detail}")
        if not ok:
            print("Setup stopped after a failed action.")
            stopped = True
            break
        completed.add(action.id)
    for action in actions:
        if action.id not in approved_ids:
            print(f"SKIP  {action.description}: not approved")
        elif stopped and action.id not in attempted:
            print(f"NOT-RUN  {action.description}: setup stopped after a failed action")
    return 0


def _run_completion(args: argparse.Namespace) -> int:
    print(render_completion(args.shell), end="")
    return 0


def _run_internal_completion(argv: Sequence[str]) -> int | None:
    if list(argv) == ["__complete", "projects"]:
        for candidate in discover_project_selector_candidates():
            print(candidate)
        return 0
    if list(argv) == ["__complete", "templates"]:
        for candidate in discover_template_names():
            print(candidate)
        return 0
    return None


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
