"""Step 4 of the New Project wizard: a summary of every configured window,
with controls to add, edit, or remove one, or move on to the final review.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static
from textual.widgets.option_list import Option

from dashboard.screens.new_project.state import WizardState
from dashboard.services import tmux
from dashboard.widgets import KeyboardOptionList as OptionList


class WindowSummaryScreen(Screen[None]):
    """Lists configured windows; add/edit/remove/finish the workspace."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static(self.state.step_label(4, "Windows"), id="screen-title")
                yield OptionList(id="window-list")
                yield Static("", id="wizard-error")
                with Horizontal(classes="button-row"):
                    yield Button("Add Another Window", id="add-window-button")
                    yield Button("Edit Selected", id="edit-window-button")
                    yield Button("Remove Selected", id="remove-window-button")
                with Horizontal(classes="button-row"):
                    yield Button("Finish Workspace", id="finish-button", variant="primary")
                    yield Button("Cancel", id="cancel-button")
        yield Footer()

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#window-list", OptionList).focus()

    def _populate(self) -> None:
        option_list = self.query_one("#window-list", OptionList)
        previous_highlight = option_list.highlighted
        option_list.clear_options()
        for index, window in enumerate(self.state.windows):
            pane_summary = ", ".join(pane.display_name for pane in window.panes)
            option_list.add_option(
                Option(f"{window.window_name}  --  {pane_summary}", id=str(index))
            )
        if option_list.option_count:
            option_list.highlighted = min(previous_highlight or 0, option_list.option_count - 1)

    def _selected_index(self) -> int | None:
        return self.query_one("#window-list", OptionList).highlighted

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-window-button":
            self._add_window()
        elif event.button.id == "edit-window-button":
            self._edit_selected()
        elif event.button.id == "remove-window-button":
            self._remove_selected()
        elif event.button.id == "finish-button":
            self._finish()
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "window-list":
            self._edit_selected()

    def _add_window(self) -> None:
        self.state.start_new_window()

        from dashboard.screens.new_project.step_window_config import WindowConfigScreen

        self.app.switch_screen(WindowConfigScreen(self.state, back_target="window_summary"))

    def _edit_selected(self) -> None:
        index = self._selected_index()
        if index is None:
            self._show_error("Select a window to edit first.")
            return
        self.state.start_editing_window(index)

        from dashboard.screens.new_project.step_window_config import WindowConfigScreen

        self.app.switch_screen(WindowConfigScreen(self.state, back_target="window_summary"))

    def _remove_selected(self) -> None:
        if len(self.state.windows) <= 1:
            self._show_error("A workspace must keep at least one window.")
            return
        index = self._selected_index()
        if index is None:
            self._show_error("Select a window to remove first.")
            return
        del self.state.windows[index]
        self._show_error("")
        self._populate()

    def _finish(self) -> None:
        if not self.state.session_name:
            self.state.session_name = tmux.generate_session_name(self.state.project_name)

        from dashboard.screens.new_project.step_review import ReviewScreen

        self.app.switch_screen(ReviewScreen(self.state))

    def _show_error(self, message: str) -> None:
        self.query_one("#wizard-error", Static).update(message)

    def action_cancel(self) -> None:
        self.app.pop_screen()
