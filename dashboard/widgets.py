"""Small keyboard-first widgets shared by the Textual screens."""

from __future__ import annotations

from textual import events
from textual.widgets import OptionList


class KeyboardOptionList(OptionList):
    """An OptionList whose selection is also activatable with Space.

    Textual already provides arrow navigation and Enter for OptionList. Space
    is added here because it is the conventional activation key for cards and
    selectable rows, and keeping it in one widget prevents screen drift.
    """

    async def handle_key(self, event: events.Key) -> bool:
        if event.key == "space" and self.highlighted is not None:
            option = self.get_option_at_index(self.highlighted)
            if not option.disabled:
                self.post_message(self.OptionSelected(self, option, self.highlighted))
            event.stop()
            return True
        return await super().handle_key(event)
