"""Versioned persistence for per-project, user-adjusted tmux pane layouts.

Remembered layouts are runtime preferences and deliberately live outside the
workspace model and workspace store.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dashboard.models import LocalProjectLocation, ProjectLocation, SshProjectLocation
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource
from dashboard.services.workspace_store import workspace_storage_key

PANE_LAYOUT_STORE_SCHEMA_VERSION = 1
_APP_DIR_NAME = "terminal-home"
_STORE_FILENAME = "pane-layouts.json"


class PaneLayoutStoreError(Exception):
    """Base class for pane-layout persistence failures."""


class PaneLayoutStoreVersionError(PaneLayoutStoreError):
    """Raised when a future store version must be preserved."""


class _CorruptStoreError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PaneLayout:
    window_name: str
    pane_count: int
    tmux_layout: str

    def __post_init__(self) -> None:
        if not isinstance(self.window_name, str) or not self.window_name.strip():
            raise ValueError("Pane layout window name must be non-empty.")
        if type(self.pane_count) is not int or self.pane_count < 1:
            raise ValueError("Pane layout pane count must be a positive integer.")
        if not isinstance(self.tmux_layout, str) or not self.tmux_layout.strip():
            raise ValueError("Pane layout tmux layout must be non-empty.")

    def to_dict(self) -> dict[str, object]:
        return {
            "window_name": self.window_name,
            "pane_count": self.pane_count,
            "tmux_layout": self.tmux_layout,
        }

    @classmethod
    def from_dict(cls, value: object) -> PaneLayout:
        if not isinstance(value, dict) or set(value) != {
            "window_name", "pane_count", "tmux_layout"
        }:
            raise ValueError("Pane layout record has invalid fields.")
        return cls(value["window_name"], value["pane_count"], value["tmux_layout"])


@dataclass(frozen=True, slots=True)
class PaneLayoutLoadResult:
    projects: dict[str, dict[str, PaneLayout]]
    locations: dict[str, ProjectLocation] = field(default_factory=dict)
    source: LoadSource = LoadSource.DEFAULT
    warning: str | None = None
    error: str | None = None


def default_pane_layout_store_path() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _canonical_location(location: ProjectLocation) -> ProjectLocation:
    if isinstance(location, LocalProjectLocation):
        return LocalProjectLocation(location.path.resolve())
    if isinstance(location, SshProjectLocation):
        return location
    raise TypeError("Unsupported project location type.")


def pane_layout_storage_key(location: ProjectLocation) -> str:
    return workspace_storage_key(_canonical_location(location))


def _parse_file(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _CorruptStoreError from exc
    if not isinstance(data, dict):
        raise _CorruptStoreError
    version = data.get("schema_version")
    if type(version) is int and version > PANE_LAYOUT_STORE_SCHEMA_VERSION:
        raise PaneLayoutStoreVersionError(
            f"Pane layout store schema version {version} is newer than this version "
            "of Terminal Home supports."
        )
    if version != PANE_LAYOUT_STORE_SCHEMA_VERSION or not isinstance(data.get("projects"), dict):
        raise _CorruptStoreError
    return data["projects"]


def _decode(
    raw: dict[str, object],
) -> tuple[dict[str, dict[str, PaneLayout]], dict[str, ProjectLocation], int]:
    projects: dict[str, dict[str, PaneLayout]] = {}
    locations: dict[str, ProjectLocation] = {}
    invalid = 0
    for key, value in raw.items():
        try:
            if not isinstance(key, str) or not isinstance(value, dict):
                raise ValueError
            if set(value) != {"project_location", "layouts"}:
                raise ValueError
            location_data = value["project_location"]
            if not isinstance(location_data, dict):
                raise ValueError
            location: ProjectLocation
            if location_data.get("kind") == "local":
                location = LocalProjectLocation.from_dict(location_data)
            elif location_data.get("kind") == "ssh":
                location = SshProjectLocation.from_dict(location_data)
            else:
                raise ValueError
            if pane_layout_storage_key(location) != key:
                raise ValueError
            layouts_value = value["layouts"]
            if not isinstance(layouts_value, dict):
                raise ValueError
            layouts: dict[str, PaneLayout] = {}
            for name, layout_value in layouts_value.items():
                layout = PaneLayout.from_dict(layout_value)
                if name != layout.window_name or name in layouts:
                    raise ValueError
                layouts[name] = layout
            projects[key] = layouts
            locations[key] = location
        except (KeyError, TypeError, ValueError):
            invalid += 1
    return projects, locations, invalid


def _load(path: Path) -> PaneLayoutLoadResult:
    if not path.exists():
        return PaneLayoutLoadResult({}, {})
    try:
        raw = _parse_file(path)
        source = LoadSource.PRIMARY
        warning = None
    except PaneLayoutStoreVersionError as exc:
        return PaneLayoutLoadResult({}, {}, LoadSource.PRIMARY, error=str(exc))
    except _CorruptStoreError:
        backup = backup_path_for(path)
        try:
            raw = _parse_file(backup)
        except PaneLayoutStoreVersionError as exc:
            return PaneLayoutLoadResult({}, {}, error=f"Pane layout backup cannot be loaded: {exc}")
        except _CorruptStoreError:
            return PaneLayoutLoadResult(
                {},
                {},
                error=(
                    f"Pane layout store {path} could not be loaded, and no valid backup "
                    "is available."
                ),
            )
        source = LoadSource.BACKUP
        warning = f"Recovered pane layout data from {backup} because {path} could not be loaded."
    projects, locations, invalid = _decode(raw)
    if invalid:
        suffix = f"Skipped {invalid} invalid pane layout record(s)."
        warning = f"{warning} {suffix}" if warning else suffix
    return PaneLayoutLoadResult(projects, locations, source, warning)


def _load_for_write(path: Path) -> PaneLayoutLoadResult:
    result = _load(path)
    if result.error:
        if path.exists():
            try:
                _parse_file(path)
            except PaneLayoutStoreVersionError:
                raise
            except _CorruptStoreError:
                pass
        raise PaneLayoutStoreError(result.error)
    return result


def _write(
    path: Path,
    projects: dict[str, dict[str, PaneLayout]],
    locations: dict[str, ProjectLocation],
    *,
    preserve_existing: bool,
) -> None:
    entries = {
        key: {
            "project_location": _canonical_location(locations[key]).to_dict(),
            "layouts": {name: layouts[name].to_dict() for name in sorted(layouts)},
        }
        for key, layouts in sorted(projects.items())
        if layouts
    }
    serialized = json.dumps(
        {"schema_version": PANE_LAYOUT_STORE_SCHEMA_VERSION, "projects": entries},
        indent=2,
        ensure_ascii=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, serialized, preserve_existing=preserve_existing)


def load_pane_layouts_for_location(
    location: ProjectLocation, store_path: Path | None = None
) -> dict[str, PaneLayout]:
    result = _load(store_path or default_pane_layout_store_path())
    if result.error:
        return {}
    return dict(result.projects.get(pane_layout_storage_key(location), {}))


def load_pane_layouts(
    location: ProjectLocation, store_path: Path | None = None
) -> dict[str, PaneLayout]:
    return load_pane_layouts_for_location(location, store_path)


def has_saved_pane_layouts(
    location: ProjectLocation, store_path: Path | None = None
) -> bool:
    """Return whether a project has remembered layouts, without repairing it."""
    result = _load(store_path or default_pane_layout_store_path())
    if result.error:
        return False
    return bool(result.projects.get(pane_layout_storage_key(location)))


def has_saved_layouts(location: ProjectLocation, store_path: Path | None = None) -> bool:
    return has_saved_pane_layouts(location, store_path)


def save_pane_layouts_for_location(
    location: ProjectLocation,
    layouts: Mapping[str, PaneLayout] | Mapping[str, object],
    store_path: Path | None = None,
) -> None:
    path = store_path or default_pane_layout_store_path()
    result = _load_for_write(path)
    canonical = _canonical_location(location)
    key = pane_layout_storage_key(canonical)
    normalized: dict[str, PaneLayout] = {}
    for name, value in layouts.items():
        normalized[name] = value if isinstance(value, PaneLayout) else PaneLayout.from_dict(value)
    if any(name != layout.window_name for name, layout in normalized.items()):
        raise ValueError("Pane layout mapping keys must match window names.")
    projects = dict(result.projects)
    locations = dict(result.locations)
    locations[key] = canonical
    if normalized:
        projects[key] = dict(normalized)
    else:
        projects.pop(key, None)
        locations.pop(key, None)
    _write(path, projects, locations, preserve_existing=result.source is LoadSource.PRIMARY)


def save_pane_layouts(
    location: ProjectLocation,
    layouts: Mapping[str, PaneLayout],
    store_path: Path | None = None,
) -> None:
    save_pane_layouts_for_location(location, layouts, store_path)


def update_pane_layouts_for_location(
    location: ProjectLocation,
    layouts: Mapping[str, PaneLayout],
    store_path: Path | None = None,
) -> None:
    """Merge successfully captured layouts into one project's preferences."""
    if not layouts:
        return
    path = store_path or default_pane_layout_store_path()
    result = _load_for_write(path)
    current = dict(result.projects.get(pane_layout_storage_key(location), {}))
    current.update(layouts)
    save_pane_layouts_for_location(location, current, path)


def forget_pane_layouts_for_location(
    location: ProjectLocation, store_path: Path | None = None
) -> bool:
    path = store_path or default_pane_layout_store_path()
    result = _load_for_write(path)
    key = pane_layout_storage_key(location)
    if key not in result.projects:
        return False
    projects = dict(result.projects)
    del projects[key]
    locations = {key: result.locations[key] for key in projects}
    _write(path, projects, locations, preserve_existing=result.source is LoadSource.PRIMARY)
    return True


def forget_pane_layouts(location: ProjectLocation, store_path: Path | None = None) -> bool:
    return forget_pane_layouts_for_location(location, store_path)
