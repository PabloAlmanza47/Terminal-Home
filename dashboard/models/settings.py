"""Textual-independent model for dashboard presentation and agent preferences.

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


class CodingAgent(str, Enum):
    """The optional command used by legacy coding-agent panes."""

    NONE = "none"
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"


@dataclass(frozen=True, slots=True)
class AppSettings:
    """Home screen presentation preferences.

    `theme` stores the *name* of a registered Textual theme (never a Theme
    object -- those aren't JSON-safe and aren't guaranteed to still exist the
    next time the app starts). `None` means "use the default theme".
    """

    artwork_enabled: bool = True
    layout_mode: LayoutMode = LayoutMode.EXPANDED
    clock_visible: bool = True
    theme: str | None = None
    coding_agent: CodingAgent = CodingAgent.NONE

    def to_dict(self) -> dict[str, Any]:
        return {
            "artwork_enabled": self.artwork_enabled,
            "layout_mode": self.layout_mode.value,
            "clock_visible": self.clock_visible,
            "theme": self.theme,
            "coding_agent": self.coding_agent.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppSettings:
        """Load settings field-by-field, so one malformed optional field
        (e.g. a saved theme that no longer exists) falls back to that
        field's default instead of discarding the whole file.
        """
        defaults = cls()

        artwork_enabled = data.get("artwork_enabled", defaults.artwork_enabled)
        if not isinstance(artwork_enabled, bool):
            artwork_enabled = defaults.artwork_enabled

        layout_mode = defaults.layout_mode
        raw_layout_mode = data.get("layout_mode")
        if isinstance(raw_layout_mode, str):
            try:
                layout_mode = LayoutMode(raw_layout_mode)
            except ValueError:
                layout_mode = defaults.layout_mode

        clock_visible = data.get("clock_visible", defaults.clock_visible)
        if not isinstance(clock_visible, bool):
            clock_visible = defaults.clock_visible

        theme = data.get("theme", defaults.theme)
        if theme is not None and not isinstance(theme, str):
            theme = defaults.theme

        coding_agent = defaults.coding_agent
        raw_agent = data.get("coding_agent")
        if isinstance(raw_agent, str):
            try:
                coding_agent = CodingAgent(raw_agent)
            except ValueError:
                coding_agent = defaults.coding_agent

        return cls(
            artwork_enabled=artwork_enabled,
            layout_mode=layout_mode,
            clock_visible=clock_visible,
            theme=theme,
            coding_agent=coding_agent,
        )
