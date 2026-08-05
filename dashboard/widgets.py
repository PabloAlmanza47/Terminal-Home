"""Small keyboard-first widgets shared by the Textual screens."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import OptionList, Static


@dataclass(frozen=True, slots=True)
class ActionItem:
    """One command row in a :class:`KeyboardActionList`."""

    id: str
    label: str
    disabled: bool = False
    dangerous: bool = False


class KeyboardActionList(Static):
    """A compact terminal-style, keyboard and mouse compatible action menu."""

    can_focus = True

    class ActionSelected(Message):
        def __init__(self, action_list: KeyboardActionList, item: ActionItem, index: int) -> None:
            super().__init__()
            self.action_list = action_list
            self.action = item
            self.action_id = item.id
            self.index = index

    def __init__(
        self,
        *actions: ActionItem,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.actions: list[ActionItem] = list(actions)
        self.selected_index: int | None = None
        self._preferred_id: str | None = None
        self._select_first_safe()

    @property
    def selected_action_id(self) -> str | None:
        if self.selected_index is None:
            return None
        return self.actions[self.selected_index].id

    def set_actions(self, actions: list[ActionItem], preferred_id: str | None = None) -> None:
        previous_id = preferred_id or self.selected_action_id or self._preferred_id
        self.actions = list(actions)
        self._preferred_id = previous_id
        if previous_id is not None:
            for index, action in enumerate(self.actions):
                if action.id == previous_id and not action.disabled:
                    self.selected_index = index
                    self.refresh()
                    return
        self._select_first_safe()
        self.refresh()

    def _select_first_safe(self) -> None:
        self.selected_index = next(
            (index for index, action in enumerate(self.actions) if not action.disabled),
            None,
        )

    def _move(self, direction: int) -> None:
        if self.selected_index is None:
            self._select_first_safe()
            return
        index = self.selected_index + direction
        while 0 <= index < len(self.actions):
            if not self.actions[index].disabled:
                self.selected_index = index
                self.refresh()
                return
            index += direction

    def _activate(self) -> None:
        if self.selected_index is None:
            return
        action = self.actions[self.selected_index]
        if not action.disabled:
            self.post_message(self.ActionSelected(self, action, self.selected_index))

    def on_key(self, event: events.Key) -> None:
        if event.key == "up":
            self._move(-1)
        elif event.key == "down":
            self._move(1)
        elif event.key in {"enter", "space"}:
            self._activate()
        else:
            return
        event.stop()

    def on_click(self, event: events.Click) -> None:
        index = event.offset.y
        if 0 <= index < len(self.actions) and not self.actions[index].disabled:
            self.selected_index = index
            self.focus()
            self.refresh()
            self._activate()
        event.stop()

    def render(self) -> Text:
        rendered = Text()
        for index, action in enumerate(self.actions):
            selected = index == self.selected_index and not action.disabled
            marker = "› " if selected else "  "
            style = "bold #72d7ff" if selected else ("#e08b9b" if action.dangerous else "#d4e2f2")
            if action.disabled:
                style = "dim #8a9bb2"
            rendered.append(marker + action.label, style=style)
            if index != len(self.actions) - 1:
                rendered.append("\n")
        return rendered


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

    def watch_highlighted(self, highlighted: int | None) -> None:
        super().watch_highlighted(highlighted)
        for option in self.options:
            original = getattr(option, "_terminal_home_prompt", None)
            if original is None:
                original = str(option.prompt)
                setattr(option, "_terminal_home_prompt", original)
            marker = "› " if self.options.index(option) == highlighted else "  "
            option._set_prompt(marker + original)
        self.refresh()
