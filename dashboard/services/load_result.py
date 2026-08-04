"""Shared, explicit source metadata for tolerant persistence reads."""

from __future__ import annotations

from enum import Enum


class LoadSource(str, Enum):
    PRIMARY = "primary"
    BACKUP = "backup"
    DEFAULT = "default"
