"""Discovers project directories for the Open Project screen (per a
configurable dashboard.models.projects_config.ProjectsConfig), and
gathers each one's status (git, saved workspace, running tmux session)
for the project list and detail screens.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path

from dashboard.models import LaunchAction, LaunchRequest, LocalProjectLocation, WorkspaceSpec
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services import tmux
from dashboard.services.agent_deck import AgentDeckSession, AgentDeckSnapshot
from dashboard.services.agent_deck import snapshot as agent_deck_snapshot
from dashboard.services.git_info import gather_git_info
from dashboard.services.pane_layout_store import has_saved_pane_layouts
from dashboard.services.projects_config_store import (
    load_projects_config,
    load_projects_config_result,
)
from dashboard.services.workspace_store import load_workspace_result

# Hex characters of a canonical path's SHA-256 kept for a collision-
# disambiguating session-name suffix -- short enough to stay readable
# (e.g. "example-a1b2c3d4"), long enough that two different real project
# paths never plausibly produce the same suffix.
_SESSION_SUFFIX_LENGTH = 8


@dataclass(frozen=True, slots=True)
class Project:
    """One discovered (or manually registered) project directory."""

    name: str
    path: Path

    @property
    def location(self) -> LocalProjectLocation:
        """The location identity used by selection and workspace models."""
        return LocalProjectLocation(self.path)


@dataclass(frozen=True, slots=True)
class ProjectDiscoveryResult:
    """The outcome of one discover_projects() pass: the projects found (in
    deterministic, alphabetical-by-name order), plus whether the hard
    directory-processing limit was hit -- in which case *projects* is a
    safe, non-empty partial list, never silently presented as complete --
    and any other nonfatal warnings (e.g. a configured root that couldn't
    be read at all).
    """

    projects: tuple[Project, ...]
    truncated: bool
    warnings: tuple[str, ...]


class _ScanBudget:
    """A directories-examined counter shared across one discover_projects
    call, so exceeding ProjectsConfig.max_directories anywhere in the walk
    stops it safely rather than letting an accidentally broad root crawl
    an entire filesystem. Only real, non-excluded, non-hidden directories
    consume the budget -- files and skipped names are free, so listing an
    expensive directory name in excluded_names (e.g. node_modules) truly
    costs nothing, even once the budget has otherwise run out.

    truncated becomes True only when a real directory is actually passed
    over for lack of budget -- exactly using up the last unit of budget on
    the final directory examined is not truncation, since nothing was
    left unexamined.
    """

    __slots__ = ("remaining", "truncated")

    def __init__(self, max_directories: int) -> None:
        self.remaining = max_directories
        self.truncated = False

    @property
    def exhausted(self) -> bool:
        return self.remaining <= 0

    def consume(self) -> None:
        self.remaining -= 1


def _should_skip(name: str, excluded_names: frozenset[str]) -> bool:
    return name.startswith(".") or name in excluded_names


def _register(entry: Path, seen_canonical: dict[Path, Project]) -> None:
    """Record *entry* as a project, unless its canonical (symlink-resolved)
    location has already been claimed by an earlier-encountered path --
    the first path discover_projects reaches for a given real location
    always wins, so display name/path stay deterministic across runs.
    """
    try:
        canonical = entry.resolve()
    except OSError:
        return
    if canonical in seen_canonical:
        return
    seen_canonical[canonical] = Project(name=entry.name, path=entry)


def _scan_entries(
    entries: list[Path],
    excluded_names: frozenset[str],
    max_depth: int,
    depth: int,
    budget: _ScanBudget,
    seen_canonical: dict[Path, Project],
) -> None:
    for entry in entries:
        # Excluded/hidden names are free -- checked, and skipped, before
        # ever consulting the budget, so listing an expensive directory
        # name in excluded_names truly costs nothing, even once the
        # budget has otherwise run out.
        if _should_skip(entry.name, excluded_names):
            continue
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if not is_dir:
            continue

        if budget.exhausted:
            # There genuinely was another real directory to examine, but
            # no budget left for it -- this, and only this, is truncation.
            budget.truncated = True
            return
        budget.consume()
        _register(entry, seen_canonical)

        # Never recurse into a symlinked directory -- the simplest way to
        # guarantee no cycle regardless of max_depth. It's still listed as
        # a project above; only walking *through* it is skipped.
        if depth < max_depth and not entry.is_symlink():
            try:
                child_entries = sorted(entry.iterdir(), key=lambda e: e.name.lower())
            except OSError:
                continue
            _scan_entries(
                child_entries, excluded_names, max_depth, depth + 1, budget, seen_canonical
            )


def _scan_root(
    root: Path,
    excluded_names: frozenset[str],
    max_depth: int,
    budget: _ScanBudget,
    seen_canonical: dict[Path, Project],
) -> str | None:
    """Scan one configured root's children (and, up to max_depth,
    grandchildren, ...). Returns a warning message if *root* itself
    couldn't be listed at all (missing, not a directory, or unreadable),
    else None -- a problem with one root never stops the others.
    """
    root = root.expanduser()
    if budget.exhausted:
        return None
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name.lower())
    except OSError:
        return f"Projects root is missing or unreadable: {root}"
    _scan_entries(entries, excluded_names, max_depth, 1, budget, seen_canonical)
    return None


def _consider_manual_project(manual_path: Path, seen_canonical: dict[Path, Project]) -> None:
    """Register *manual_path* if it's an existing, accessible directory --
    silently skipped otherwise (missing, inaccessible, or not a
    directory), since a manually registered project that has since
    vanished is not an error, just nothing to show. Never subject to the
    directory-processing budget: registering an explicit path is O(1),
    no recursion involved.
    """
    expanded = manual_path.expanduser()
    try:
        is_dir = expanded.is_dir()
    except OSError:
        return
    if not is_dir:
        return
    _register(expanded, seen_canonical)


def discover_projects(config: ProjectsConfig | None = None) -> ProjectDiscoveryResult:
    """Discover every project reachable from *config*'s roots (down to
    max_depth levels below each) plus its manually registered projects.

    A project reachable more than once -- through two roots, through both
    a root and the manual list, or through a symlink and its real path --
    appears exactly once: root-scanned entries are considered strictly
    before manual ones, roots are scanned in *config*'s order, and within
    a root, entries are visited depth-first in alphabetical order -- the
    first path to reach a given canonical (resolved) location wins and
    supplies the returned Project's display name/path, so a scanned entry
    always wins over a manual registration of the same underlying project.

    Never raises: a missing/unreadable root is recorded as a warning and
    skipped, never blocking the other roots; a missing/inaccessible manual
    project is silently skipped. See ProjectDiscoveryResult for how a
    scan that hit max_directories is reported.
    """
    config = config if config is not None else ProjectsConfig()

    seen_canonical: dict[Path, Project] = {}
    warnings: list[str] = []
    budget = _ScanBudget(config.max_directories)

    for root in config.roots:
        warning = _scan_root(root, config.excluded_names, config.max_depth, budget, seen_canonical)
        if warning is not None:
            warnings.append(warning)

    for manual_path in config.manual_projects:
        _consider_manual_project(manual_path, seen_canonical)

    projects = tuple(sorted(seen_canonical.values(), key=lambda project: project.name.lower()))
    return ProjectDiscoveryResult(
        projects=projects, truncated=budget.truncated, warnings=tuple(warnings)
    )


@dataclass(frozen=True, slots=True)
class ProjectStatus:
    """Everything the Open Project list and detail screens need to know
    about one project, gathered in a single best-effort pass. Nothing here
    ever raises -- every field degrades to a safe "unknown" value instead.
    """

    project: Project
    canonical_path: Path
    project_dir_exists: bool
    is_git_repo: bool
    git_branch: str | None
    saved_workspace: WorkspaceSpec | None
    workspace_metadata_error: str | None
    expected_session_name: str
    tmux_available: bool
    session_running: bool
    last_modified: datetime | None
    workspace_metadata_warning: str | None = None
    remembered_pane_layouts: bool = False
    agent_sessions: tuple[AgentDeckSession, ...] = ()
    server_status: str = "not_configured"


@dataclass(frozen=True, slots=True)
class ProjectScanResult:
    """Every project's live status from one scan_all_projects() pass, plus
    the underlying discovery outcome -- the single shape both the Home
    screen and the Continue Project screen consume, so neither
    independently loads project-discovery configuration or re-implements
    discovery on its own.
    """

    statuses: tuple[ProjectStatus, ...]
    truncated: bool
    warnings: tuple[str, ...]
    tmux_sessions: tuple[tmux.TmuxSession, ...] = ()
    agent_snapshot: AgentDeckSnapshot | None = None


def scan_all_projects(
    config: ProjectsConfig | None = None,
    store_path: Path | None = None,
) -> ProjectScanResult:
    """Discover every configured project and gather each one's status in a
    single pass, sharing one `tmux list-sessions` call across all of them
    instead of one `tmux has-session` per project.

    *config* defaults to the saved project-discovery configuration
    (dashboard.services.projects_config_store.load_projects_config) --
    this is the one place that configuration is loaded and interpreted,
    so the Open Project list screen and the home screen's recent-projects/
    active-sessions panels both consume the same result rather than each
    loading or interpreting configuration on its own.
    """
    config_warning: str | None = None
    if config is None:
        config_result = load_projects_config_result()
        config = config_result.value
        config_warning = config_result.warning
    discovery = discover_projects(config)
    tmux_sessions = tuple(tmux.list_tmux_sessions())
    running_sessions = {session.name for session in tmux_sessions}
    panes = tuple(tmux.list_tmux_panes())
    agent_snapshot = agent_deck_snapshot()
    base_session_name_counts = _base_session_name_counts(discovery.projects)
    statuses = tuple(
        gather_project_status(
            project,
            store_path=store_path,
            running_sessions=running_sessions,
            base_session_name_counts=base_session_name_counts,
            agent_sessions=tuple(
                session
                for session in agent_snapshot.sessions
                if session.path == project.path.resolve()
            ),
            pane_runtimes=panes,
        )
        for project in discovery.projects
    )
    warnings = list(discovery.warnings)
    if config_warning:
        warnings.append(config_warning)
    warnings.extend(
        dict.fromkeys(
            status.workspace_metadata_warning
            for status in statuses
            if status.workspace_metadata_warning is not None
        )
    )
    return ProjectScanResult(
        statuses=statuses,
        truncated=discovery.truncated,
        warnings=tuple(dict.fromkeys(warnings)),
        tmux_sessions=tmux_sessions,
        agent_snapshot=agent_snapshot,
    )


def format_scan_warnings(result: ProjectDiscoveryResult | ProjectScanResult) -> str:
    """A single, concise, user-facing string for *result*'s warnings and
    truncation state -- shared by every screen that surfaces them, so
    none of them independently decides how to phrase this.

    Returns an empty string when there's nothing to report.
    """
    parts = list(result.warnings)
    if result.truncated:
        parts.append(
            "Project scan stopped early after reaching its directory limit -- "
            "some projects may be missing."
        )
    return "  ".join(parts)


def _base_session_name_counts(projects: Iterable[Project]) -> dict[str, int]:
    """How many of *projects* sanitize to each base tmux session name.

    The one place session-name collisions are detected -- computed once
    per scan_all_projects() pass, or once per gather_single_project_status()
    call, and handed to gather_project_status so a project's
    expected_session_name is decided identically regardless of which of
    those two paths asked for it.
    """
    counts: dict[str, int] = {}
    for project in projects:
        base = tmux.sanitize_session_name(project.name)
        counts[base] = counts.get(base, 0) + 1
    return counts


def _canonical_path_suffix(canonical_path: Path) -> str:
    """A short, deterministic suffix derived from *canonical_path*, used
    only to disambiguate two projects that would otherwise expect the
    same tmux session name.

    A SHA-256 hash, not Python's randomized hash() -- the same canonical
    path always produces the same suffix, on any machine, in any process,
    regardless of the order configured roots happen to be scanned in.
    """
    digest = hashlib.sha256(str(canonical_path).encode("utf-8")).hexdigest()
    return digest[:_SESSION_SUFFIX_LENGTH]


def _expected_new_session_name(
    project: Project,
    canonical_path: Path,
    base_session_name_counts: dict[str, int] | None,
) -> str:
    """The tmux session name a brand-new (never-saved) workspace for
    *project* would use.

    Plain sanitized project name -- the legacy, readable behavior --
    unless *base_session_name_counts* shows another project in the same
    batch sanitizes to that same base name, in which case a short
    canonical-path-derived suffix is appended so the two can never
    collide. base_session_name_counts is None for callers that never
    supply sibling-project context (existing single-project tests, etc.):
    they keep the plain name, exactly as before this collision check
    existed.
    """
    base = tmux.sanitize_session_name(project.name)
    if base_session_name_counts is None or base_session_name_counts.get(base, 1) <= 1:
        return base
    return f"{base}-{_canonical_path_suffix(canonical_path)}"


def project_option_id(status: ProjectStatus) -> str:
    """A stable identifier for *status*'s project, safe to use as an
    OptionList option id or a lookup-dict key.

    Derived from the canonical path rather than project.name: two
    different projects reachable through different configured roots (or a
    manually registered project) may legitimately share a directory
    basename, e.g. ~/school/example and ~/work/example. A name-keyed id
    would let one silently overwrite the other in a lookup dict, or
    resolve a selection to the wrong project entirely. Deterministic --
    never Python's randomized hash().
    """
    return str(status.canonical_path)


def _home_relative_path(path: Path) -> str:
    """path, with the user's home directory contracted to '~', for a
    concise disambiguating label -- falls back to the full path when it
    isn't under the home directory at all. Always rendered with forward
    slashes (this app targets Linux/WSL only), regardless of what
    separator str(Path) would otherwise use.
    """
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def disambiguated_display_names(statuses: list[ProjectStatus]) -> list[str]:
    """The display name for each status in *statuses*, in the same order.

    Ordinarily just project.name. When two or more statuses in the same
    batch share a name (e.g. two projects named "example" under different
    configured roots), each of those -- and only those -- gets a concise
    '<name> — <~-relative path>' suffix instead, so they stay visually
    distinguishable without cluttering every uniquely-named project with
    a path nobody needs to disambiguate.
    """
    name_counts: dict[str, int] = {}
    for status in statuses:
        name = status.project.name
        name_counts[name] = name_counts.get(name, 0) + 1

    display_names: list[str] = []
    for status in statuses:
        name = status.project.name
        if name_counts[name] > 1:
            display_names.append(f"{name} — {_home_relative_path(status.canonical_path)}")
        else:
            display_names.append(name)
    return display_names


def gather_project_status(
    project: Project,
    *,
    store_path: Path | None = None,
    running_sessions: set[str] | None = None,
    base_session_name_counts: dict[str, int] | None = None,
    agent_sessions: tuple[AgentDeckSession, ...] = (),
    pane_runtimes: Iterable[tmux.TmuxPaneRuntime] = (),
    expected_session_name: str | None = None,
) -> ProjectStatus:
    """Gather *project*'s full status.

    *running_sessions*, when given, is the set of currently running tmux
    session names -- passing one pre-fetched set lets a caller checking
    many projects (the project list) make a single `tmux list-sessions`
    call instead of one `tmux has-session` per project. Left as None, this
    checks the one session it cares about directly (cheap for a single
    project, e.g. the detail screen).

    *base_session_name_counts*, when given, makes expected_session_name
    collision-aware -- see _expected_new_session_name. Left as None (the
    default), a project with no saved workspace just gets its plain
    sanitized name, as before this collision check existed; callers that
    know or can cheaply compute the full discovered project set
    (scan_all_projects, gather_single_project_status) should always pass
    it, so two same-named projects never end up expecting the same
    session.
    """
    canonical_path = project.path.resolve()
    project_dir_exists = project.path.is_dir()

    git_info = gather_git_info(project.path) if project_dir_exists else None

    load_result = load_workspace_result(project.path, store_path=store_path)
    saved_workspace = load_result.workspace
    if saved_workspace is not None and saved_workspace.project_path.resolve() != canonical_path:
        # The store is keyed by canonical path already, so this only
        # happens if the persisted record's own project_path field is
        # stale (e.g. hand-edited) -- correct it rather than let a launch
        # later `cd` into the wrong directory.
        saved_workspace = replace(
            saved_workspace, project_location=LocalProjectLocation(canonical_path)
        )

    resolved_session_name = (
        expected_session_name
        if expected_session_name is not None
        else (
            saved_workspace.session_name
            if saved_workspace is not None
            else _expected_new_session_name(project, canonical_path, base_session_name_counts)
        )
    )

    tmux_available = tmux.is_tmux_installed()
    if running_sessions is not None:
        session_running = resolved_session_name in running_sessions
    elif tmux_available:
        session_running = tmux.session_exists(resolved_session_name)
    else:
        session_running = False

    try:
        last_modified = datetime.fromtimestamp(project.path.stat().st_mtime)
    except OSError:
        last_modified = None

    server_status = "not_configured"
    if saved_workspace is not None:
        server_panes = [
            (window.window_name, pane.display_name)
            for window in saved_workspace.windows
            for pane in window.panes
            if pane.kind.value == "dev_server"
        ]
        if server_panes:
            server_status = "stopped" if not session_running else "unknown"
            for window_name, display_name in server_panes:
                matches = [
                    pane for pane in pane_runtimes
                    if pane.session_name == resolved_session_name
                    and pane.window_name == window_name
                    and (pane.title == "server" or pane.title == display_name)
                ]
                if matches:
                    server_status = "stopped" if matches[0].dead else "running"
                    break
    return ProjectStatus(
        project=project,
        canonical_path=canonical_path,
        project_dir_exists=project_dir_exists,
        is_git_repo=git_info.is_repo if git_info is not None else False,
        git_branch=git_info.branch if git_info is not None else None,
        saved_workspace=saved_workspace,
        workspace_metadata_error=load_result.error,
        workspace_metadata_warning=load_result.warning,
        expected_session_name=resolved_session_name,
        tmux_available=tmux_available,
        session_running=session_running,
        last_modified=last_modified,
        remembered_pane_layouts=(
            saved_workspace is not None
            and has_saved_pane_layouts(LocalProjectLocation(canonical_path))
        ),
        agent_sessions=agent_sessions,
        server_status=server_status,
    )


def gather_single_project_status(
    project: Project,
    *,
    store_path: Path | None = None,
    config: ProjectsConfig | None = None,
) -> ProjectStatus:
    """Like gather_project_status, but for a caller (Project Detail) that
    only has one project in hand rather than a full scan_all_projects
    batch -- still collision-aware.

    A fresh, cheap (filesystem-listing only -- no git, workspace-store, or
    tmux calls for any project but this one) discover_projects() call
    over *config* (defaulting to the saved project-discovery
    configuration, same default scan_all_projects uses) supplies the
    sibling project set needed to decide whether *project*'s session name
    needs a collision suffix. Since that set is recomputed fresh from the
    current on-disk project layout every time, this always agrees with
    what the original scan_all_projects assigned -- including on a later
    independent refresh (Project Detail's F5 / on_screen_resume), which
    never has access to that original scan's in-memory result and must
    not "forget" the collision it found.
    """
    config = config if config is not None else load_projects_config()
    discovery = discover_projects(config)
    base_session_name_counts = _base_session_name_counts(discovery.projects)
    agent_snapshot = agent_deck_snapshot()
    panes = tuple(tmux.list_tmux_panes())
    return gather_project_status(
        project,
        store_path=store_path,
        base_session_name_counts=base_session_name_counts,
        agent_sessions=tuple(
            session for session in agent_snapshot.sessions if session.path == project.path.resolve()
        ),
        pane_runtimes=panes,
    )


def refresh_single_project_status(
    project: Project,
    previous: ProjectStatus,
    *,
    store_path: Path | None = None,
) -> ProjectStatus:
    """Refresh one project's live runtime state without rediscovering projects."""
    agent_snapshot = agent_deck_snapshot()
    panes = tuple(tmux.list_tmux_panes())
    return gather_project_status(
        project,
        store_path=store_path,
        agent_sessions=tuple(
            session for session in agent_snapshot.sessions if session.path == project.path.resolve()
        ),
        pane_runtimes=panes,
        expected_session_name=previous.expected_session_name,
    )


class ProjectAction(str, Enum):
    """One button the Project Detail screen might offer, depending on
    ProjectStatus. RESUME and RECREATE both resolve to the same
    LaunchAction.ATTACH request -- the orchestration layer re-checks
    what's actually running at launch time regardless of which label the
    user saw, so the distinction is purely about what the button says.
    """

    RESUME = "resume"
    RECREATE = "recreate"
    OPEN_DEFAULT = "open_default"
    CONFIGURE = "configure"
    EDIT = "edit"
    SAVE_TEMPLATE = "save_template"
    RESET = "reset"
    FORGET = "forget"
    RESET_PANE_SIZES = "reset_pane_sizes"


def status_badge(status: ProjectStatus) -> str:
    """A single, human-readable status word for *status*, using the same
    priority order as primary_actions: a running session always wins,
    then corrupt metadata, then a saved workspace, then "nothing yet".
    """
    if status.session_running:
        return "Running"
    if status.workspace_metadata_error:
        return "Metadata Warning"
    if status.saved_workspace is not None:
        return "Saved Workspace"
    return "Not Configured"


def primary_actions(status: ProjectStatus) -> list[ProjectAction]:
    """The main call-to-action(s) for a project, in priority order.

    A running session always wins (attaching needs no filesystem access at
    all, so it's offered even if the directory has since vanished or the
    saved metadata is corrupt). After that: a vanished directory rules out
    every action that would need to create or read it; corrupt metadata
    offers a way out without ever guessing at its content; a saved
    workspace offers to recreate it; otherwise, the project has nothing
    saved yet.
    """
    if status.session_running:
        return [ProjectAction.RESUME]
    if not status.project_dir_exists:
        return []
    if status.workspace_metadata_error:
        return [ProjectAction.FORGET, ProjectAction.CONFIGURE]
    if status.saved_workspace is not None:
        return [ProjectAction.RECREATE]
    return [ProjectAction.OPEN_DEFAULT, ProjectAction.CONFIGURE]


def secondary_actions(status: ProjectStatus) -> list[ProjectAction]:
    """Additional metadata-only actions available alongside the primary
    one(s) above.
    """
    if status.saved_workspace is not None:
        actions = [
            ProjectAction.EDIT,
        ]
        if status.remembered_pane_layouts:
            actions.append(ProjectAction.RESET_PANE_SIZES)
        actions.extend([ProjectAction.RESET, ProjectAction.FORGET, ProjectAction.SAVE_TEMPLATE])
        return actions
    if status.workspace_metadata_error and status.session_running:
        return [ProjectAction.FORGET]
    return []


def build_launch_request(status: ProjectStatus) -> LaunchRequest:
    """The LaunchRequest for a Resume/Recreate-style action on *status* --
    shared by every screen that offers one (Project Detail's Resume/Recreate
    buttons, Home's Active Sessions selection).

    Always LaunchAction.ATTACH: the orchestration layer re-checks whether
    the session is actually running at launch time regardless of what
    *status* saw, so "resume" and "recreate" are the same request -- only
    the button label the caller showed differs. When there's no saved
    workspace to recreate from, the request carries the expected session
    name directly instead, so attaching to a session that's running but was
    never configured through Terminal Home still works.
    """
    if status.saved_workspace is not None:
        return LaunchRequest(
            workspace=status.saved_workspace, init_git=False, action=LaunchAction.ATTACH
        )
    return LaunchRequest(
        workspace=None,
        init_git=False,
        action=LaunchAction.ATTACH,
        session_name=status.expected_session_name,
    )
