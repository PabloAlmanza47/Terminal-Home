"""Textual-independent model for project-discovery configuration: which
directories to scan for projects, how deep, what to skip, and any project
paths registered by hand.

Kept separate from AppSettings (dashboard/models/settings.py), which is
purely home-screen presentation preferences -- this describes what
"projects" even means to the scanner (dashboard.services.projects), a
different concern with its own store (dashboard.services.
projects_config_store) and lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The current, pre-slice default: a single root under the user's home
# directory. Evaluated once at import time -- callers that need this to
# reflect a different location (tests, an alternate HOME) should pass an
# explicit ProjectsConfig rather than relying on this default.
DEFAULT_ROOT = Path.home() / "projects"

# Directories that are almost never themselves projects, and are usually
# expensive or pointless to recurse into -- excluded by default so a
# fresh install behaves sensibly without the user needing to know these
# names exist. Excluding a name means it is skipped entirely: neither
# returned as a project nor recursed into.
DEFAULT_EXCLUDED_NAMES = frozenset(
    {".git", "node_modules", ".venv", "venv", "__pycache__"}
)

# Immediate children of each root only -- matches the original,
# pre-slice discovery behavior.
DEFAULT_MAX_DEPTH = 1

# A generous but finite cap so an accidentally broad root (e.g. a home
# directory containing thousands of unrelated entries) can't make a scan
# walk an entire filesystem.
DEFAULT_MAX_DIRECTORIES = 5000


class ProjectsConfigValidationError(ValueError):
    """Raised when a ProjectsConfig would be invalid."""


@dataclass(frozen=True, slots=True)
class ProjectsConfig:
    """Where and how dashboard.services.projects.discover_projects looks
    for projects.

    roots: directories scanned for project subdirectories, in the order
        they're scanned -- also the order that decides which path wins
        when the same project is reachable through more than one root
        (see discover_projects's docstring for the exact tie-break rule).
        Expected to already be absolute/expanded (see from_dict).
    excluded_names: directory basenames skipped entirely -- neither
        returned as a project nor recursed into.
    max_depth: how many directory levels below each root count as a
        project. 1 = immediate children only; 2 = children and
        grandchildren; and so on. The root itself is never a project.
    manual_projects: individual project paths considered regardless of
        whether they fall under any configured root. Expected to already
        be absolute/expanded (see from_dict).
    max_directories: a hard cap on directories examined in one scan.
    """

    roots: tuple[Path, ...] = (DEFAULT_ROOT,)
    excluded_names: frozenset[str] = DEFAULT_EXCLUDED_NAMES
    max_depth: int = DEFAULT_MAX_DEPTH
    manual_projects: tuple[Path, ...] = ()
    max_directories: int = DEFAULT_MAX_DIRECTORIES

    def __post_init__(self) -> None:
        if self.max_depth < 1:
            raise ProjectsConfigValidationError("max_depth must be at least 1.")
        if self.max_directories < 1:
            raise ProjectsConfigValidationError("max_directories must be at least 1.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "roots": [str(root) for root in self.roots],
            "excluded_names": sorted(self.excluded_names),
            "max_depth": self.max_depth,
            "manual_projects": [str(path) for path in self.manual_projects],
            "max_directories": self.max_directories,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectsConfig:
        """Rebuild from to_dict()'s shape, expanding `~` in every stored
        path -- the one place that normalization happens, so callers
        elsewhere (discovery, the settings screen) can assume roots and
        manual_projects are already usable Paths.

        Raises TypeError/ValueError/KeyError on any malformed field --
        callers (projects_config_store.load_projects_config) catch these
        and fall back to defaults, same as every other tolerant store in
        this codebase.
        """
        roots_raw = data["roots"]
        excluded_raw = data["excluded_names"]
        manual_raw = data["manual_projects"]
        if not isinstance(roots_raw, list):
            raise TypeError("roots must be a list")
        if not isinstance(excluded_raw, list):
            raise TypeError("excluded_names must be a list")
        if not isinstance(manual_raw, list):
            raise TypeError("manual_projects must be a list")

        return cls(
            roots=tuple(Path(root).expanduser() for root in roots_raw),
            excluded_names=frozenset(str(name) for name in excluded_raw),
            max_depth=int(data["max_depth"]),
            manual_projects=tuple(Path(p).expanduser() for p in manual_raw),
            max_directories=int(data["max_directories"]),
        )
