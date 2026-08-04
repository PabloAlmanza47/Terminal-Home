"""Versioned local storage for reusable workspace templates."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dashboard.models import WorkspaceTemplate, normalize_template_name
from dashboard.services.atomic_file import atomic_write_text, backup_path_for
from dashboard.services.load_result import LoadSource

TEMPLATE_STORE_SCHEMA_VERSION = 1
_APP_DIR_NAME = "terminal-home"
_STORE_FILENAME = "templates.json"


class TemplateStoreError(Exception):
    """Base class for user-facing template-store errors."""


class DuplicateTemplateNameError(TemplateStoreError):
    pass


class TemplateStoreVersionError(TemplateStoreError):
    pass


class _CorruptStoreError(Exception):
    pass


def default_template_store_path() -> Path:
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / _APP_DIR_NAME / _STORE_FILENAME


def _parse_file(path: Path) -> list[object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _CorruptStoreError from exc
    if not isinstance(data, dict):
        raise _CorruptStoreError
    version = data.get("schema_version")
    if isinstance(version, int) and version > TEMPLATE_STORE_SCHEMA_VERSION:
        raise TemplateStoreVersionError(
            f"Template store schema version {version} is newer than this version of "
            "Terminal Home supports."
        )
    if version != TEMPLATE_STORE_SCHEMA_VERSION or not isinstance(data.get("templates"), list):
        raise _CorruptStoreError
    return data["templates"]


@dataclass(frozen=True, slots=True)
class TemplateLoadResult:
    templates: tuple[WorkspaceTemplate, ...]
    source: LoadSource = LoadSource.DEFAULT
    warning: str | None = None
    error: str | None = None


def _sort(templates: list[WorkspaceTemplate]) -> tuple[WorkspaceTemplate, ...]:
    return tuple(sorted(templates, key=lambda item: (item.name.casefold(), item.id)))


def load_templates_result(store_path: Path | None = None) -> TemplateLoadResult:
    path = store_path or default_template_store_path()
    if not path.exists():
        return TemplateLoadResult(())
    try:
        raw = _parse_file(path)
        source = LoadSource.PRIMARY
        warning = None
    except TemplateStoreVersionError as exc:
        return TemplateLoadResult((), LoadSource.PRIMARY, error=str(exc))
    except _CorruptStoreError:
        backup = backup_path_for(path)
        try:
            raw = _parse_file(backup)
        except (TemplateStoreVersionError, _CorruptStoreError):
            return TemplateLoadResult(
                (),
                error=(
                    f"Template store {path} could not be loaded, and no valid backup is available."
                ),
            )
        source = LoadSource.BACKUP
        warning = f"Recovered template data from {backup} because {path} could not be loaded."

    templates: list[WorkspaceTemplate] = []
    invalid = 0
    ids: set[str] = set()
    names: set[str] = set()
    for value in raw:
        if not isinstance(value, dict):
            invalid += 1
            continue
        try:
            template = WorkspaceTemplate.from_dict(value)
        except (KeyError, TypeError, ValueError):
            invalid += 1
            continue
        folded = template.name.casefold()
        if template.id in ids or folded in names:
            invalid += 1
            continue
        ids.add(template.id)
        names.add(folded)
        templates.append(template)
    if invalid:
        suffix = f"Skipped {invalid} invalid template record(s)."
        warning = f"{warning} {suffix}" if warning else suffix
    return TemplateLoadResult(_sort(templates), source, warning, None)


def load_all_templates(store_path: Path | None = None) -> tuple[WorkspaceTemplate, ...]:
    return load_templates_result(store_path).templates


def get_template(template_id: str, store_path: Path | None = None) -> WorkspaceTemplate | None:
    return next((item for item in load_all_templates(store_path) if item.id == template_id), None)


def find_template_by_name(name: str, store_path: Path | None = None) -> WorkspaceTemplate | None:
    normalized = normalize_template_name(name).casefold()
    return next(
        (item for item in load_all_templates(store_path) if item.name.casefold() == normalized),
        None,
    )


def _load_for_write(path: Path) -> list[WorkspaceTemplate]:
    if not path.exists():
        return []
    try:
        _parse_file(path)
    except TemplateStoreVersionError:
        raise
    except _CorruptStoreError:
        result = load_templates_result(path)
        return list(result.templates)
    result = load_templates_result(path)
    if result.error:
        raise TemplateStoreError(result.error)
    return list(result.templates)


def _write(path: Path, templates: list[WorkspaceTemplate]) -> None:
    ordered = _sort(templates)
    envelope = {
        "schema_version": TEMPLATE_STORE_SCHEMA_VERSION,
        "templates": [item.to_dict() for item in ordered],
    }
    serialized = json.dumps(envelope, indent=2)
    json.loads(serialized)
    preserve = False
    if path.exists():
        try:
            _parse_file(path)
        except _CorruptStoreError:
            pass
        else:
            preserve = True
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, serialized, preserve_existing=preserve)


def create_template(
    template: WorkspaceTemplate, store_path: Path | None = None
) -> WorkspaceTemplate:
    path = store_path or default_template_store_path()
    templates = _load_for_write(path)
    if any(item.id == template.id for item in templates):
        raise TemplateStoreError(f"A template with ID {template.id} already exists.")
    if any(item.name.casefold() == template.name.casefold() for item in templates):
        raise DuplicateTemplateNameError(f'A template named "{template.name}" already exists.')
    templates.append(template)
    _write(path, templates)
    return template


def rename_template(
    template_id: str, name: str, store_path: Path | None = None
) -> WorkspaceTemplate | None:
    path = store_path or default_template_store_path()
    normalized = normalize_template_name(name)
    templates = _load_for_write(path)
    target = next((item for item in templates if item.id == template_id), None)
    if target is None:
        return None
    if any(
        item.id != template_id and item.name.casefold() == normalized.casefold()
        for item in templates
    ):
        raise DuplicateTemplateNameError(f'A template named "{normalized}" already exists.')
    replacement = WorkspaceTemplate(target.id, normalized, target.windows)
    _write(path, [replacement if item.id == template_id else item for item in templates])
    return replacement


def delete_template(template_id: str, store_path: Path | None = None) -> bool:
    path = store_path or default_template_store_path()
    templates = _load_for_write(path)
    remaining = [item for item in templates if item.id != template_id]
    if len(remaining) == len(templates):
        return False
    _write(path, remaining)
    return True


def replace_template_contents(
    template_id: str,
    windows: tuple,
    store_path: Path | None = None,
) -> WorkspaceTemplate | None:
    path = store_path or default_template_store_path()
    templates = _load_for_write(path)
    target = next((item for item in templates if item.id == template_id), None)
    if target is None:
        return None
    replacement = WorkspaceTemplate(target.id, target.name, windows)
    _write(path, [replacement if item.id == template_id else item for item in templates])
    return replacement
