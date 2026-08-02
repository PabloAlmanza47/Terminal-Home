"""Textual-independent model for the dashboard's own presentation
preferences (not project data) -- artwork, clock, and layout density.

Kept alongside the workspace models for the same reason: plain, validated
dataclasses with no Textual imports and no subprocess calls, so they can be
unit tested in isolation from both the Settings screen and the home
screen that reads them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class LayoutMode(str, Enum):
    """The user's preferred information density for the home screen,
    independent of the terminal's actual width -- a narrow terminal still
    stacks panels regardless of this setting; this only controls padding
    and how much secondary detail is shown within each panel.
    """

    COMPACT = "compact"
    EXPANDED = "expanded"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Home screen presentation preferences."""

    artwork_enabled: bool = True
    layout_mode: LayoutMode = LayoutMode.EXPANDED
    clock_visible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "artwork_enabled": self.artwork_enabled,
            "layout_mode": self.layout_mode.value,
            "clock_visible": self.clock_visible,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppSettings:
        return cls(
            artwork_enabled=bool(data["artwork_enabled"]),
            layout_mode=LayoutMode(data["layout_mode"]),
            clock_visible=bool(data["clock_visible"]),
        )
