"""Resolves one user-supplied CLI selector (a project name or a filesystem
path) to exactly one discovered Project.

The single place this is decided, so `th plan` and (later) `th up` never
independently guess at what "example" or "~/work/example" means, and a
duplicate project name never resolves arbitrarily.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dashboard.models import RemoteProjectRegistration, SshProjectLocation
from dashboard.models.projects_config import ProjectsConfig
from dashboard.services.projects import Project, discover_projects
from dashboard.services.projects_config_store import load_projects_config
from dashboard.services.remote_project_store import load_all_remote_projects


@dataclass(frozen=True, slots=True)
class RegisteredRemoteProject:
    """One manually registered remote project, represented offline."""

    name: str
    location: SshProjectLocation
    registration: RemoteProjectRegistration

    @property
    def selector(self) -> str:
        """A deterministic selector containing host identity and remote path."""
        return f"ssh:{self.location.host_id}:{self.location.remote_path}"


SelectableProject = Project | RegisteredRemoteProject


@dataclass(frozen=True, slots=True)
class ProjectSelectionResult:
    """The outcome of resolving one selector string.

    Exactly one of `project` or `error` is set. `candidates` is populated
    only when `error` reports an ambiguous name match, in deterministic
    discovery order, for a caller that wants to format its own listing
    instead of the default `error` message.
    """

    project: SelectableProject | None = None
    error: str | None = None
    candidates: tuple[SelectableProject, ...] = ()

    @property
    def ok(self) -> bool:
        return self.project is not None


def _looks_like_path(selector: str) -> bool:
    """Whether *selector* was written as a path rather than a bare project
    name -- a plain word like "example" is always treated as a name, even
    if a same-named directory happens to exist under the current working
    directory, so name lookups stay predictable regardless of where `th`
    was invoked from.
    """
    return (
        "/" in selector
        or "\\" in selector
        or selector in (".", "..")
        or selector.startswith("~")
    )


def _display_path(path: Path) -> str:
    try:
        return f"~/{path.relative_to(Path.home()).as_posix()}"
    except ValueError:
        return str(path)


def _display_project(project: SelectableProject) -> str:
    if isinstance(project, Project):
        return _display_path(project.path)
    return project.selector


def _ambiguous_error(selector: str, candidates: tuple[SelectableProject, ...]) -> str:
    lines = [f'multiple projects match "{selector}":']
    for project in candidates:
        lines.append(f"  {project.name} — {_display_project(project)}")
    lines.append("")
    lines.append("Use an exact path or remote selector to select one.")
    return "\n".join(lines)


def _remote_selectable_project(
    registration: RemoteProjectRegistration,
) -> RegisteredRemoteProject:
    location = SshProjectLocation(registration.host_id, registration.remote_path)
    return RegisteredRemoteProject(
        name=registration.name,
        location=location,
        registration=registration,
    )


def list_selectable_projects(
    config: ProjectsConfig | None = None,
    *,
    remote_store_path: Path | None = None,
) -> tuple[SelectableProject, ...]:
    """Return discovered local and registered remote projects offline.

    Remote registrations are local metadata only.  No host lookup, SSH
    connection, remote inspection, or remote filesystem operation occurs.
    """
    effective_config = config if config is not None else load_projects_config()
    local_projects = discover_projects(effective_config).projects
    remote_projects = tuple(
        _remote_selectable_project(registration)
        for registration in load_all_remote_projects(remote_store_path)
    )
    return tuple(
        sorted(
            (*local_projects, *remote_projects),
            key=lambda project: (
                project.name.casefold(),
                _display_project(project).casefold(),
            ),
        )
    )


def _resolve_path(selector: str, projects: tuple[SelectableProject, ...]) -> ProjectSelectionResult:
    expanded = Path(selector).expanduser()
    try:
        exists = expanded.is_dir()
    except OSError:
        exists = False
    if not exists:
        return ProjectSelectionResult(
            error=f'Path does not exist or is not a directory: "{selector}"'
        )

    resolved = expanded.resolve()
    for project in projects:
        if isinstance(project, Project) and project.path.resolve() == resolved:
            return ProjectSelectionResult(project=project)
    # A real directory the user pointed at directly, even though it isn't
    # (or isn't yet) part of the configured discovery set.
    return ProjectSelectionResult(project=Project(name=resolved.name, path=resolved))


def resolve_project_selector(
    selector: str,
    *,
    config: ProjectsConfig | None = None,
    remote_store_path: Path | None = None,
) -> ProjectSelectionResult:
    """Resolve *selector* to exactly one Project.

    Tried in order: an exact/resolvable filesystem path; an exact
    (case-sensitive) project name, if unique among discovered projects;
    then a case-insensitive name match, if unique. A name matching more
    than one discovered project is reported as an ambiguous-selection
    error rather than picking one -- see ProjectSelectionResult.candidates.

    *config* defaults to the saved project-discovery configuration, the
    same one `th list` and the Continue Project screen use, so a name
    lookup here always agrees with what's actually discoverable.
    """
    selector = selector.strip()
    if not selector:
        return ProjectSelectionResult(error="No project selector given.")

    config = config if config is not None else load_projects_config()
    projects = list_selectable_projects(config, remote_store_path=remote_store_path)

    exact_remote_selectors = tuple(
        project
        for project in projects
        if isinstance(project, RegisteredRemoteProject) and project.selector == selector
    )
    if len(exact_remote_selectors) == 1:
        return ProjectSelectionResult(project=exact_remote_selectors[0])
    if len(exact_remote_selectors) > 1:
        return ProjectSelectionResult(
            error=_ambiguous_error(selector, exact_remote_selectors),
            candidates=exact_remote_selectors,
        )

    if _looks_like_path(selector):
        return _resolve_path(selector, projects)

    exact_matches = tuple(project for project in projects if project.name == selector)
    if len(exact_matches) == 1:
        return ProjectSelectionResult(project=exact_matches[0])
    if len(exact_matches) > 1:
        return ProjectSelectionResult(
            error=_ambiguous_error(selector, exact_matches), candidates=exact_matches
        )

    lowered = selector.lower()
    ci_matches = tuple(project for project in projects if project.name.lower() == lowered)
    if len(ci_matches) == 1:
        return ProjectSelectionResult(project=ci_matches[0])
    if len(ci_matches) > 1:
        return ProjectSelectionResult(
            error=_ambiguous_error(selector, ci_matches), candidates=ci_matches
        )

    return ProjectSelectionResult(error=f'No project matches "{selector}".')
