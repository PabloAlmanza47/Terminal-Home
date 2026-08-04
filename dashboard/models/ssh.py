"""Validated, persistence-ready SSH configuration records."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from dashboard.models.project_location import (
    SshProjectLocation,
    _normalize_uuid,
    _validate_remote_path,
)

MAX_REMOTE_NAME_LENGTH = 80


class SshModelValidationError(ValueError):
    """Raised when SSH host or remote project metadata is invalid."""


def _normalize_name(value: object, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise SshModelValidationError(f"{field_name} must be a string.")
    normalized = value.strip()
    if not normalized:
        raise SshModelValidationError(f"{field_name} cannot be empty.")
    if len(normalized) > MAX_REMOTE_NAME_LENGTH:
        raise SshModelValidationError(
            f"{field_name} cannot exceed {MAX_REMOTE_NAME_LENGTH} characters."
        )
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise SshModelValidationError(f"{field_name} cannot contain control characters.")
    return normalized


def _normalize_destination(value: object) -> str:
    if not isinstance(value, str):
        raise SshModelValidationError("SSH destination must be a string.")
    normalized = value.strip()
    if not normalized:
        raise SshModelValidationError("SSH destination cannot be empty.")
    if normalized.startswith("-"):
        raise SshModelValidationError("SSH destination cannot begin with '-'.")
    if any(character.isspace() for character in normalized):
        raise SshModelValidationError(
            "SSH destination must be exactly one operand without whitespace."
        )
    if any(unicodedata.category(character).startswith("C") for character in normalized):
        raise SshModelValidationError("SSH destination cannot contain control characters.")
    return normalized


def _require_exact_keys(data: object, expected: set[str], *, record: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise SshModelValidationError(f"{record} must be an object.")
    actual = set(data)
    if actual != expected:
        raise SshModelValidationError(f"{record} has invalid fields.")
    return data


@dataclass(frozen=True, slots=True)
class SshHost:
    """A stable local reference to one OpenSSH destination operand."""

    id: str
    display_name: str
    destination: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            _normalize_uuid(self.id, field_name="SSH host ID", error_type=SshModelValidationError),
        )
        object.__setattr__(
            self,
            "display_name",
            _normalize_name(self.display_name, field_name="SSH host display name"),
        )
        object.__setattr__(self, "destination", _normalize_destination(self.destination))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "destination": self.destination,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SshHost:
        validated = _require_exact_keys(
            data, {"id", "display_name", "destination"}, record="SSH host"
        )
        return cls(
            id=validated["id"],
            display_name=validated["display_name"],
            destination=validated["destination"],
        )


@dataclass(frozen=True, slots=True)
class RemoteProjectRegistration:
    """A locally managed registration for an existing remote project."""

    id: str
    host_id: str
    name: str
    remote_path: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            _normalize_uuid(
                self.id,
                field_name="Remote project ID",
                error_type=SshModelValidationError,
            ),
        )
        object.__setattr__(
            self,
            "host_id",
            _normalize_uuid(
                self.host_id,
                field_name="SSH host ID",
                error_type=SshModelValidationError,
            ),
        )
        object.__setattr__(
            self, "name", _normalize_name(self.name, field_name="Remote project name")
        )
        _validate_remote_path(self.remote_path, error_type=SshModelValidationError)

    @property
    def location(self) -> SshProjectLocation:
        """Return the project-neutral SSH location represented by this record."""

        return SshProjectLocation(host_id=self.host_id, remote_path=self.remote_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "host_id": self.host_id,
            "name": self.name,
            "remote_path": self.remote_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RemoteProjectRegistration:
        validated = _require_exact_keys(
            data,
            {"id", "host_id", "name", "remote_path"},
            record="Remote project registration",
        )
        return cls(
            id=validated["id"],
            host_id=validated["host_id"],
            name=validated["name"],
            remote_path=validated["remote_path"],
        )
