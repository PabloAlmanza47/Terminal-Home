"""Filesystem-neutral project location models."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID


class ProjectLocationValidationError(ValueError):
    """Raised when project location data is invalid."""


class ProjectLocationKind(str, Enum):
    """The supported kinds of project locations."""

    LOCAL = "local"
    SSH = "ssh"


def _normalize_uuid(value: object, *, field_name: str, error_type: type[ValueError]) -> str:
    if not isinstance(value, str):
        raise error_type(f"{field_name} must be a valid UUID string.")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise error_type(f"{field_name} must be a valid UUID string.") from exc


def _validate_remote_path(value: object, *, error_type: type[ValueError]) -> str:
    if not isinstance(value, str):
        raise error_type("Remote project path must be a string.")
    if not value:
        raise error_type("Remote project path cannot be empty.")
    if not value.startswith("/"):
        raise error_type("Remote project path must be an absolute POSIX path.")
    if any(unicodedata.category(character).startswith("C") for character in value):
        raise error_type("Remote project path cannot contain control characters.")
    return value


def _require_exact_keys(data: object, expected: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ProjectLocationValidationError("Project location must be an object.")
    actual = set(data)
    if actual != expected:
        missing = expected - actual
        extra = actual - expected
        details: list[str] = []
        if missing:
            details.append(f"missing fields: {', '.join(sorted(missing))}")
        if extra:
            details.append(f"unknown fields: {', '.join(sorted(extra))}")
        raise ProjectLocationValidationError(
            f"Invalid project location fields ({'; '.join(details)})."
        )
    return data


@dataclass(frozen=True, slots=True)
class LocalProjectLocation:
    """A local absolute path, without filesystem probing or normalization."""

    path: Path

    @property
    def kind(self) -> ProjectLocationKind:
        return ProjectLocationKind.LOCAL

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise ProjectLocationValidationError("Local project path must be a Path.")
        if not self.path.is_absolute():
            raise ProjectLocationValidationError("Local project path must be absolute.")

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "path": str(self.path)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocalProjectLocation:
        validated = _require_exact_keys(data, {"kind", "path"})
        if validated["kind"] != ProjectLocationKind.LOCAL.value:
            raise ProjectLocationValidationError("Location kind must be 'local'.")
        path = validated["path"]
        if not isinstance(path, str):
            raise ProjectLocationValidationError("Local project path must be a string.")
        return cls(path=Path(path))


@dataclass(frozen=True, slots=True)
class SshProjectLocation:
    """An absolute POSIX path on a registered SSH host."""

    host_id: str
    remote_path: str

    @property
    def kind(self) -> ProjectLocationKind:
        return ProjectLocationKind.SSH

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "host_id",
            _normalize_uuid(
                self.host_id,
                field_name="SSH host ID",
                error_type=ProjectLocationValidationError,
            ),
        )
        _validate_remote_path(self.remote_path, error_type=ProjectLocationValidationError)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "host_id": self.host_id,
            "remote_path": self.remote_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SshProjectLocation:
        validated = _require_exact_keys(data, {"kind", "host_id", "remote_path"})
        if validated["kind"] != ProjectLocationKind.SSH.value:
            raise ProjectLocationValidationError("Location kind must be 'ssh'.")
        return cls(
            host_id=validated["host_id"],
            remote_path=validated["remote_path"],
        )


ProjectLocation = LocalProjectLocation | SshProjectLocation


def project_location_from_dict(data: dict[str, object]) -> ProjectLocation:
    """Parse a location using its required discriminator without guessing."""

    if not isinstance(data, dict):
        raise ProjectLocationValidationError("Project location must be an object.")
    kind = data.get("kind")
    if not isinstance(kind, str):
        raise ProjectLocationValidationError("Project location requires a string 'kind' field.")
    try:
        location_kind = ProjectLocationKind(kind)
    except ValueError as exc:
        raise ProjectLocationValidationError(f"Unknown project location kind: {kind!r}.") from exc
    if location_kind is ProjectLocationKind.LOCAL:
        return LocalProjectLocation.from_dict(data)
    return SshProjectLocation.from_dict(data)
