"""Small, pure presentation-formatting helpers shared by the home screen.

No I/O, no Textual imports -- these only ever transform values already in
hand (a timestamp, the current time), so they're trivial to unit test.
"""

from __future__ import annotations

from datetime import datetime

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_RELATIVE_DAY_LIMIT = 7


def format_relative_time(when: datetime, now: datetime | None = None) -> str:
    """A compact "how long ago" label for *when*, e.g. "5m ago", "3h ago",
    "2d ago". Falls back to a plain date once it's more than a week old (or
    if *when* is somehow in the future, e.g. clock skew) -- so this never
    produces a nonsensical or ever-growing string.
    """
    now = now if now is not None else datetime.now()
    seconds = (now - when).total_seconds()

    if seconds < 0 or seconds >= _RELATIVE_DAY_LIMIT * _DAY:
        return when.strftime("%Y-%m-%d")
    if seconds < _MINUTE:
        return "just now"
    if seconds < _HOUR:
        return f"{int(seconds // _MINUTE)}m ago"
    if seconds < _DAY:
        return f"{int(seconds // _HOUR)}h ago"
    return f"{int(seconds // _DAY)}d ago"


def greeting_for(now: datetime) -> str:
    """A time-of-day greeting prefix, e.g. "Good morning"."""
    hour = now.hour
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 17:
        return "Good afternoon"
    return "Good evening"
