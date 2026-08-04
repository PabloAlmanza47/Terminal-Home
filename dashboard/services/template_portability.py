"""Strict, file-based import and export for one workspace template at a time."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from dashboard.models import PaneKind, WindowSpec, WorkspaceTemplate
from dashboard.services.atomic_file import atomic_write_text

PORTABLE_TEMPLATE_FORMAT = "terminal-home-workspace-template"
PORTABLE_TEMPLATE_SCHEMA_VERSION = 1
MAX_IMPORT_BYTES = 1024 * 1024
RECOMMENDED_TEMPLATE_EXTENSION = ".th-template.json"


class TemplatePortabilityError(Exception):
    """Base class for user-facing portable-template failures."""


class ImportPathError(TemplatePortabilityError):
    pass


class ImportTooLargeError(TemplatePortabilityError):
    pass


class PortableEncodingError(TemplatePortabilityError):
    pass


class PortableJsonError(TemplatePortabilityError):
    pass


class PortableFormatError(TemplatePortabilityError):
    pass


class PortableSchemaError(TemplatePortabilityError):
    pass


class UnsupportedPortableSchemaError(PortableSchemaError):
    pass


class PortableTemplateValidationError(TemplatePortabilityError):
    pass


class ImportSourceChangedError(TemplatePortabilityError):
    pass


class ExportPathError(TemplatePortabilityError):
    pass


class ExportDestinationExistsError(ExportPathError):
    pass


@dataclass(frozen=True, slots=True)
class PortableWorkspaceTemplate:
    name: str
    windows: tuple[WindowSpec, ...]


@dataclass(frozen=True, slots=True)
class LoadedPortableTemplate:
    template: PortableWorkspaceTemplate
    path: Path
    fingerprint: str


def _portable_payload(name: str, windows: tuple[WindowSpec, ...]) -> dict[str, object]:
    return {
        "name": name,
        "windows": [window.to_dict() for window in windows],
    }


def build_portable_envelope(template: WorkspaceTemplate) -> dict[str, object]:
    """Build an identity-free portable envelope from a local template."""
    return {
        "format": PORTABLE_TEMPLATE_FORMAT,
        "schema_version": PORTABLE_TEMPLATE_SCHEMA_VERSION,
        "template": _portable_payload(template.name, template.windows),
    }


def serialize_portable_template(template: WorkspaceTemplate) -> str:
    return (
        json.dumps(
            build_portable_envelope(template),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n"
    )


def _require_exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    missing = expected - value.keys()
    extra = value.keys() - expected
    if missing:
        raise PortableFormatError(
            f"Portable template {context} is missing required field(s): "
            f"{', '.join(sorted(missing))}."
        )
    if extra:
        raise PortableFormatError(
            f"Portable template {context} contains unsupported field(s): "
            f"{', '.join(sorted(extra))}."
        )


def _parse_pane(value: object, window_index: int, pane_index: int) -> dict[str, Any]:
    context = f"window {window_index + 1}, pane {pane_index + 1}"
    if not isinstance(value, dict):
        raise PortableTemplateValidationError(f"Portable template {context} must be an object.")
    _require_exact_keys(value, {"kind", "display_name", "custom_command"}, context)
    kind_value = value["kind"]
    try:
        kind = PaneKind(kind_value)
    except (TypeError, ValueError) as exc:
        raise PortableTemplateValidationError(
            f"Portable template {context} has an unknown pane kind: {kind_value!r}."
        ) from exc
    command = value["custom_command"]
    if kind is PaneKind.CUSTOM_COMMAND:
        if not isinstance(command, str):
            raise PortableTemplateValidationError(
                f"Portable template {context} custom command must be a string."
            )
    elif command is not None:
        raise PortableTemplateValidationError(
            f"Portable template {context} may not store a command for {kind.value}."
        )
    return value


def parse_portable_template(text: str) -> PortableWorkspaceTemplate:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PortableJsonError(f"Portable template is not valid JSON: {exc.msg}.") from exc
    if not isinstance(data, dict):
        raise PortableFormatError("Portable template envelope must be a JSON object.")
    _require_exact_keys(data, {"format", "schema_version", "template"}, "envelope")
    if data["format"] != PORTABLE_TEMPLATE_FORMAT:
        raise PortableFormatError(f'Portable template format must be "{PORTABLE_TEMPLATE_FORMAT}".')
    version = data["schema_version"]
    if type(version) is not int:
        raise PortableSchemaError("Portable template schema_version must be an integer.")
    if version > PORTABLE_TEMPLATE_SCHEMA_VERSION:
        raise UnsupportedPortableSchemaError(
            f"Portable template schema version {version} is newer than this version of "
            "Terminal Home supports."
        )
    if version != PORTABLE_TEMPLATE_SCHEMA_VERSION:
        raise PortableSchemaError(f"Portable template schema version {version} is not supported.")
    payload = data["template"]
    if not isinstance(payload, dict):
        raise PortableFormatError("Portable template payload must be a JSON object.")
    _require_exact_keys(payload, {"name", "windows"}, "payload")
    windows_value = payload["windows"]
    if not isinstance(windows_value, list):
        raise PortableTemplateValidationError("Portable template windows must be a list.")
    parsed_windows: list[WindowSpec] = []
    for window_index, window_value in enumerate(windows_value):
        if not isinstance(window_value, dict):
            raise PortableTemplateValidationError(
                f"Portable template window {window_index + 1} must be an object."
            )
        _require_exact_keys(window_value, {"window_name", "panes"}, f"window {window_index + 1}")
        panes_value = window_value["panes"]
        if not isinstance(panes_value, list):
            raise PortableTemplateValidationError(
                f"Portable template window {window_index + 1} panes must be a list."
            )
        clean_window = {
            "window_name": window_value["window_name"],
            "panes": [
                _parse_pane(pane, window_index, pane_index)
                for pane_index, pane in enumerate(panes_value)
            ],
        }
        try:
            parsed_windows.append(WindowSpec.from_dict(clean_window))
        except (KeyError, TypeError, ValueError) as exc:
            raise PortableTemplateValidationError(
                f"Portable template window {window_index + 1} is invalid: {exc}"
            ) from exc
    try:
        validated = WorkspaceTemplate(str(uuid4()), payload["name"], tuple(parsed_windows))
    except (KeyError, TypeError, ValueError) as exc:
        raise PortableTemplateValidationError(
            f"Portable template payload is invalid: {exc}"
        ) from exc
    return PortableWorkspaceTemplate(validated.name, validated.windows)


def construct_imported_template(
    portable: PortableWorkspaceTemplate, *, name: str | None = None
) -> WorkspaceTemplate:
    """Create a fresh local identity from reviewed portable data."""
    windows = tuple(WindowSpec.from_dict(window.to_dict()) for window in portable.windows)
    return WorkspaceTemplate(str(uuid4()), name if name is not None else portable.name, windows)


def resolve_user_path(raw_path: str, *, cwd: Path | None = None) -> Path:
    if not raw_path.strip():
        raise ImportPathError("A file path is required.")
    expanded = Path(raw_path.strip()).expanduser()
    if not expanded.is_absolute():
        expanded = (cwd or Path.cwd()) / expanded
    return expanded.resolve(strict=False)


def _read_import_bytes(path: Path, maximum_bytes: int) -> bytes:
    try:
        if not path.exists():
            raise ImportPathError(f"Import file does not exist: {path}")
        if not path.is_file():
            raise ImportPathError(f"Import path is not a regular file: {path}")
        size = path.stat().st_size
        if size > maximum_bytes:
            raise ImportTooLargeError(
                f"Import file exceeds the {maximum_bytes} byte size limit: {path}"
            )
        with path.open("rb") as source:
            content = source.read(maximum_bytes + 1)
    except TemplatePortabilityError:
        raise
    except OSError as exc:
        raise ImportPathError(f"Could not read import file {path}: {exc}") from exc
    if len(content) > maximum_bytes:
        raise ImportTooLargeError(
            f"Import file exceeds the {maximum_bytes} byte size limit: {path}"
        )
    return content


def load_portable_template(
    raw_path: str | Path,
    *,
    cwd: Path | None = None,
    maximum_bytes: int = MAX_IMPORT_BYTES,
) -> LoadedPortableTemplate:
    path = resolve_user_path(str(raw_path), cwd=cwd)
    content = _read_import_bytes(path, maximum_bytes)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PortableEncodingError(f"Import file is not valid UTF-8: {path}") from exc
    portable = parse_portable_template(text)
    return LoadedPortableTemplate(portable, path, hashlib.sha256(content).hexdigest())


def verify_import_source_unchanged(loaded: LoadedPortableTemplate) -> None:
    current = load_portable_template(loaded.path)
    if current.fingerprint != loaded.fingerprint:
        raise ImportSourceChangedError(
            f"Import file changed after review; review it again: {loaded.path}"
        )


def safe_default_export_filename(name: str) -> str:
    stem = "".join(character.lower() if character.isalnum() else "-" for character in name)
    stem = "-".join(part for part in stem.split("-") if part)[:60] or "workspace-template"
    return f"{stem}{RECOMMENDED_TEMPLATE_EXTENSION}"


def resolve_export_path(raw_path: str, *, cwd: Path | None = None) -> Path:
    if not raw_path.strip():
        raise ExportPathError("An export file path is required.")
    unresolved = Path(raw_path.strip()).expanduser()
    if not unresolved.is_absolute():
        unresolved = (cwd or Path.cwd()) / unresolved
    parent = unresolved.parent.resolve(strict=False)
    if not parent.exists():
        raise ExportPathError(f"Export parent directory does not exist: {parent}")
    if not parent.is_dir():
        raise ExportPathError(f"Export parent is not a directory: {parent}")
    return parent.resolve() / unresolved.name


def export_template(
    template: WorkspaceTemplate,
    raw_path: str | Path,
    *,
    overwrite: bool = False,
    cwd: Path | None = None,
) -> Path:
    path = resolve_export_path(str(raw_path), cwd=cwd)
    try:
        if path.is_symlink():
            raise ExportPathError(f"Export destination may not be a symbolic link: {path}")
        if path.exists():
            if path.is_dir():
                raise ExportPathError(f"Export destination is a directory: {path}")
            if not path.is_file():
                raise ExportPathError(f"Export destination is not a regular file: {path}")
            if not overwrite:
                raise ExportDestinationExistsError(f"Export destination already exists: {path}")
        atomic_write_text(
            path,
            serialize_portable_template(template),
            preserve_existing=path.exists(),
        )
    except TemplatePortabilityError:
        raise
    except OSError as exc:
        raise ExportPathError(f"Could not export template to {path}: {exc}") from exc
    return path
