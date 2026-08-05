"""Read-only shell completion for commands and discovered project selectors."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from dashboard.models import SshProjectLocation
from dashboard.services.project_selection import (
    RegisteredRemoteProject,
    SelectableProject,
)
from dashboard.services.projects import discover_projects
from dashboard.services.projects_config_store import load_projects_config_result
from dashboard.services.remote_project_store import load_all_remote_projects
from dashboard.services.template_store import load_templates_result

SUBCOMMANDS = ("list", "plan", "up", "new", "doctor", "completion")
SHELLS = ("bash", "zsh")


def project_selector_candidates(projects: Iterable[SelectableProject]) -> tuple[str, ...]:
    """Return deterministic, unambiguous local and remote selectors."""
    ordered = sorted(
        projects,
        key=lambda project: (
            project.name.casefold(),
            project.selector.casefold()
            if isinstance(project, RegisteredRemoteProject)
            else str(project.path.resolve()).casefold(),
        ),
    )
    name_counts = Counter(project.name for project in ordered)
    candidates: list[str] = []
    for project in ordered:
        if isinstance(project, RegisteredRemoteProject):
            if name_counts[project.name] == 1 and project.name not in candidates:
                candidates.append(project.name)
            candidate = project.selector
        else:
            candidate = (
                project.name
                if name_counts[project.name] == 1
                else str(project.path.resolve())
            )
        # The internal protocol is line-delimited. A newline in a filesystem
        # name cannot be represented without corrupting the candidate stream.
        if "\n" not in candidate and "\r" not in candidate:
            candidates.append(candidate)
    return tuple(candidates)


def discover_project_selector_candidates() -> tuple[str, ...]:
    """Load configured discovery and return selectors without gathering status."""
    config = load_projects_config_result().value
    local_projects = discover_projects(config).projects
    remote_projects = tuple(
        RegisteredRemoteProject(
            name=registration.name,
            location=SshProjectLocation(registration.host_id, registration.remote_path),
            registration=registration,
        )
        for registration in load_all_remote_projects()
    )
    return project_selector_candidates((*local_projects, *remote_projects))


def discover_template_names() -> tuple[str, ...]:
    """Return registered template names without probing projects or hosts."""
    result = load_templates_result()
    return tuple(template.name for template in result.templates)


def render_bash_completion() -> str:
    """Render dependency-free Bash completion for every console-script alias."""
    return r'''_terminal_home_complete() {
    local cur command candidate
    COMPREPLY=()
    cur=${COMP_WORDS[COMP_CWORD]}
    command=${COMP_WORDS[1]-}

    if (( COMP_CWORD == 1 )); then
        while IFS= read -r candidate; do
            [[ $candidate == "$cur"* ]] && COMPREPLY+=("$candidate")
        done <<'EOF'
list
plan
up
new
doctor
completion
EOF
        return
    fi

    if [[ $command == completion && $COMP_CWORD -eq 2 ]]; then
        for candidate in bash zsh; do
            [[ $candidate == "$cur"* ]] && COMPREPLY+=("$candidate")
        done
        return
    fi

    if [[ ( $command == plan || $command == up ) && $COMP_CWORD -eq 2 ]]; then
        if [[ $cur == /* || $cur == ./* || $cur == ../* || $cur == ~* ]]; then
            compopt -o filenames
            mapfile -t COMPREPLY < <(compgen -d -- "$cur")
        else
            while IFS= read -r candidate; do
                [[ $candidate == "$cur"* ]] && COMPREPLY+=("$candidate")
            done < <("${COMP_WORDS[0]}" __complete projects 2>/dev/null)
        fi
    fi

    if [[ $command == new ]]; then
        if [[ ${COMP_WORDS[COMP_CWORD-1]-} == --template ]]; then
            while IFS= read -r candidate; do
                [[ $candidate == "$cur"* ]] && COMPREPLY+=("$candidate")
            done < <("${COMP_WORDS[0]}" __complete templates 2>/dev/null)
        elif [[ $cur == --* ]]; then
            COMPREPLY=( $(compgen -W '--path --root --template --git --no-git \
                --launch --no-launch --interactive --non-interactive' -- "$cur") )
        fi
    fi
}
complete -F _terminal_home_complete th terminal-home dev
'''


def render_zsh_completion() -> str:
    """Render dependency-free Zsh completion for every console-script alias."""
    return r'''#compdef th terminal-home dev
_terminal_home_complete() {
    local command output
    local -a commands candidates
    commands=(
        'list:List discovered projects and their status'
        'plan:Preview a project workspace launch'
        'up:Create or attach to a project workspace'
        'doctor:Check the local environment'
        'completion:Generate shell completion'
    )

    if (( CURRENT == 2 )); then
        _describe 'command' commands
        return
    fi

    command=${words[2]-}
    case $command in
        completion)
            (( CURRENT == 3 )) && compadd -- bash zsh
            ;;
        new)
            if [[ ${words[CURRENT-1]-} == --template ]]; then
                candidates=(${(@f)"$(command "${words[1]}" __complete templates 2>/dev/null)"})
                compadd -- "${candidates[@]}"
            fi
            ;;
        plan|up)
            if (( CURRENT == 3 )); then
                output=$(command "${words[1]}" __complete projects 2>/dev/null)
                if [[ -n $output ]]; then
                    candidates=("${(@f)output}")
                    compadd -- "${candidates[@]}"
                fi
                _directories
            fi
            ;;
    esac
}
compdef _terminal_home_complete th terminal-home dev
'''


def render_completion(shell: str) -> str:
    """Render completion for a supported shell."""
    if shell == "bash":
        return render_bash_completion()
    if shell == "zsh":
        return render_zsh_completion()
    raise ValueError(f"Unsupported shell: {shell}")
