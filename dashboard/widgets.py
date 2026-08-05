"""Small keyboard-first widgets shared by the Textual screens."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar

from rich.style import Style
from rich.text import Text
from textual import events
from textual.content import Content
from textual.message import Message
from textual.strip import Segment, Strip
from textual.visual import VisualType
from textual.widgets import Checkbox, OptionList, RadioButton, SelectionList, Static
from textual.widgets.option_list import Option


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
        reset_on_blur: bool = False,
    ) -> None:
        super().__init__(id=id, classes=classes)
        self.actions: list[ActionItem] = list(actions)
        self.selected_index: int | None = None
        self._preferred_id: str | None = None
        self.reset_on_blur = reset_on_blur
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

    def on_blur(self) -> None:
        if self.reset_on_blur:
            self._select_first_safe()
            self.refresh()

    def on_focus(self) -> None:
        self.refresh()

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
            selected = self.has_focus and index == self.selected_index and not action.disabled
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

    def __init__(self, *options, reset_on_blur: bool = False, **kwargs) -> None:
        self._original_prompts: dict[int, object] = {}
        canonical_options = [self._canonical_option(option) for option in options]
        super().__init__(*canonical_options, **kwargs)
        self.reset_on_blur = reset_on_blur

    def _canonical_option(self, option: Option | VisualType | None) -> Option | None:
        """Normalize and record an option before Textual can notify watchers."""
        if option is None:
            return None
        canonical = option if isinstance(option, Option) else Option(option)
        self._original_prompts[id(canonical)] = (
            canonical.prompt.copy() if isinstance(canonical.prompt, Text) else canonical.prompt
        )
        return canonical

    def add_options(
        self, options: Iterable[Option | VisualType | None]
    ) -> KeyboardOptionList:
        # Capture every prompt before handing the batch to Textual. This is
        # important for a mounted, focused empty list: adding its first item
        # may synchronously trigger highlight/refresh callbacks.
        canonical_options = [self._canonical_option(option) for option in options]
        super().add_options(canonical_options)
        if self.highlighted is None and self._options:
            self.highlighted = next(
                (index for index, item in enumerate(self._options) if not item.disabled),
                None,
            )
        self._update_prompt_markers()
        return self

    def add_option(self, option: Option | VisualType | None = None) -> KeyboardOptionList:
        return self.add_options([option])

    def clear_options(self) -> KeyboardOptionList:
        super().clear_options()
        self._original_prompts.clear()
        return self

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
        self._update_prompt_markers(highlighted)

    def _update_prompt_markers(self, highlighted: int | None = None) -> None:
        highlighted = self.highlighted if highlighted is None else highlighted
        for index, option in enumerate(self.options):
            original = self._original_prompts[id(option)]
            marker = "› " if index == highlighted and self.has_focus else "  "
            if isinstance(original, Text):
                prompt = Text(marker)
                prompt.append(original.copy())
            else:
                prompt = Text(marker + str(original))
            option._set_prompt(prompt)
        self.refresh()

    def on_focus(self) -> None:
        if self.reset_on_blur:
            self.highlighted = next(
                (index for index, option in enumerate(self.options) if not option.disabled),
                None,
            )
        self.call_after_refresh(self._update_prompt_markers)

    def on_blur(self) -> None:
        if self.reset_on_blur:
            self.highlighted = None
        self.call_after_refresh(self._update_prompt_markers)


class CircularCheckbox(Checkbox):
    """Checkbox with a circular indicator, preserving Checkbox semantics."""

    def render(self) -> Content:
        return _circular_content(self)


SelectionType = TypeVar("SelectionType")

CIRCULAR_SELECTED = "●"
CIRCULAR_UNSELECTED = "○"


def circular_indicator(value: bool) -> str:
    """Return the shared one-cell indicator used by every circular control."""
    return CIRCULAR_SELECTED if value else CIRCULAR_UNSELECTED


def _circular_content(widget: Checkbox | RadioButton) -> Content:
    """Render a control label with one shared indicator and separator."""
    indicator_style = widget.get_visual_style("toggle--button")
    label_style = widget.get_visual_style("toggle--label")
    label = widget._label.stylize_before(label_style)
    return Content.assemble((circular_indicator(widget.value), indicator_style), " ", label)


class CircularRadioButton(RadioButton):
    """RadioButton with the shared clean one-cell circular indicator."""

    def render(self) -> Content:
        return _circular_content(self)


class CircularSelectionList(SelectionList[SelectionType], Generic[SelectionType]):
    """SelectionList with clean circular multi-select indicators."""

    COMPONENT_CLASSES = SelectionList.COMPONENT_CLASSES | {
        "toggle--button",
        "toggle--label",
    }

    def render_line(self, y: int) -> Strip:
        line = OptionList.render_line(self, y)
        _, scroll_y = self.scroll_offset
        selection_index = scroll_y + y
        if selection_index >= self.option_count:
            return line

        selection = self.get_option_at_index(selection_index)
        underlying_style = next(iter(line)).style or self.rich_style
        component = "selection-list--button"
        if selection.value in self._selected:
            component += "-selected"
        if self.highlighted == selection_index:
            component += "-highlighted"
        button_style = self.get_component_rich_style(component)
        indicator_style = Style(
            color=button_style.color,
            bgcolor=button_style.bgcolor,
            meta={"option": selection_index},
        )
        return Strip(
            [
                Segment(circular_indicator(selection.value in self._selected), indicator_style),
                Segment(" ", style=underlying_style),
                *line,
            ]
        )
