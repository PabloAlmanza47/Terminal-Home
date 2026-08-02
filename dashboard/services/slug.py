"""Converts a project display name into a filesystem-safe directory slug."""

from __future__ import annotations

import re

_UNSAFE_RUN = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase *name*, replace runs of non-alphanumeric characters with a
    single hyphen, and strip leading/trailing hyphens.

    Returns an empty string if *name* contains no alphanumeric characters at
    all -- callers are responsible for rejecting that as invalid input.
    """
    lowered = name.strip().lower()
    slug = _UNSAFE_RUN.sub("-", lowered).strip("-")
    return slug
