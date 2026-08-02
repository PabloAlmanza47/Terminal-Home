"""Tests for filesystem-safe slug generation (dashboard.services.slug)."""

from __future__ import annotations

import pytest

from dashboard.services.slug import slugify


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("My Cool Project", "my-cool-project"),
        ("  leading and trailing  ", "leading-and-trailing"),
        ("Already-slugged", "already-slugged"),
        ("Weird!!  Chars??", "weird-chars"),
        ("Under_score Name", "under-score-name"),
        ("dots.and.dashes--combo", "dots-and-dashes-combo"),
        ("UPPERCASE", "uppercase"),
        ("---trim---", "trim"),
        ("café project", "caf-project"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert slugify(name) == expected


def test_slugify_empty_input_returns_empty_string() -> None:
    assert slugify("") == ""


def test_slugify_only_symbols_returns_empty_string() -> None:
    assert slugify("!!!???") == ""
