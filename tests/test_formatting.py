"""Tests for pure presentation-formatting helpers (dashboard.services.formatting)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dashboard.services.formatting import format_relative_time, greeting_for


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=0), "just now"),
        (timedelta(seconds=30), "just now"),
        (timedelta(minutes=1), "1m ago"),
        (timedelta(minutes=45), "45m ago"),
        (timedelta(hours=1), "1h ago"),
        (timedelta(hours=23), "23h ago"),
        (timedelta(days=1), "1d ago"),
        (timedelta(days=6), "6d ago"),
    ],
)
def test_format_relative_time(delta: timedelta, expected: str) -> None:
    now = datetime(2026, 8, 2, 12, 0, 0)
    when = now - delta
    assert format_relative_time(when, now=now) == expected


def test_format_relative_time_older_than_a_week_shows_date() -> None:
    now = datetime(2026, 8, 2, 12, 0, 0)
    when = now - timedelta(days=10)
    assert format_relative_time(when, now=now) == when.strftime("%Y-%m-%d")


def test_format_relative_time_future_timestamp_shows_date_not_negative() -> None:
    now = datetime(2026, 8, 2, 12, 0, 0)
    when = now + timedelta(hours=1)
    assert format_relative_time(when, now=now) == when.strftime("%Y-%m-%d")


def test_format_relative_time_defaults_now_to_current_time() -> None:
    when = datetime.now() - timedelta(seconds=5)
    assert format_relative_time(when) == "just now"


@pytest.mark.parametrize(
    ("hour", "expected"),
    [
        (0, "Good evening"),
        (4, "Good evening"),
        (5, "Good morning"),
        (11, "Good morning"),
        (12, "Good afternoon"),
        (16, "Good afternoon"),
        (17, "Good evening"),
        (23, "Good evening"),
    ],
)
def test_greeting_for(hour: int, expected: str) -> None:
    assert greeting_for(datetime(2026, 8, 2, hour, 0, 0)) == expected
