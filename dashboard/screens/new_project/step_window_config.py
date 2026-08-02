"""Step 2 of the New Project wizard: name a tmux window, pick 1-4 panes
from the fixed catalogue, and order them.

Cross-step navigation imports (to step_project_info, step_window_summary,
step_layout_preview) are done locally inside the methods that need them,
since those modules import back into this one -- keeping the imports local
avoids a circular import at module load time.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, OptionList, SelectionList, Static
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from dashboard.models import PANE_KIND_LABELS, PaneKind
from dashboard.models.workspace import PANE_KIND_ORDER
from dashboard.screens.new_project.state import PaneDraft, WizardState

MAX_PANES_PER_WINDOW = 4


class WindowConfigScreen(Screen[None]):
    """Window name + pane selection/ordering for one tmux window."""

    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, state: WizardState, back_target: str) -> None:
        super().__init__()
        self.state = state
        self.back_target = back_target
        self._panes: list[PaneDraft] = [
            PaneDraft(pane.kind, pane.custom_name, pane.custom_command)
            for pane in state.pending_window.panes
        ]
        self._suppress_toggle = False

    def compose(self) -> ComposeResult:
        selected_kinds = {pane.kind for pane in self._panes}
        custom_draft = next((p for p in self._panes if p.kind is PaneKind.CUSTOM_COMMAND), None)

        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static(
                    "Create New Project -- Step 2 of 5: Configure Window", id="screen-title"
                )
                yield Static("Window name", classes="field-label")
                yield Input(
                    value=self.state.pending_window.window_name,
                    placeholder="main",
                    id="window-name-input",
                )
                yield Static(
                    f"Panes (choose 1-{MAX_PANES_PER_WINDOW})", classes="field-label"
                )
                yield SelectionList[PaneKind](
                    *[
                        Selection(PANE_KIND_LABELS[kind], kind, kind in selected_kinds)
                        for kind in PANE_KIND_ORDER
                    ],
                    id="pane-selection-list",
                )
                yield Static("Pane order (first = leftmost/topmost)", classes="field-label")
                yield OptionList(id="pane-order-list")
                with Horizontal(classes="button-row"):
                    yield Button("Move Up", id="move-up-button")
                    yield Button("Move Down", id="move-down-button")
                with Vertical(id="custom-command-fields"):
                    yield Static("Custom pane display name", classes="field-label")
                    yield Input(
                        value=custom_draft.custom_name if custom_draft else "",
                        placeholder="e.g. Docs",
                        id="custom-name-input",
                    )
                    yield Static("Custom command", classes="field-label")
                    yield Input(
                        value=custom_draft.custom_command if custom_draft else "",
                        placeholder="e.g. mkdocs serve",
                        id="custom-command-input",
                    )
                    yield Static(
                        "This command will run automatically when the workspace starts.",
                        classes="wizard-hint",
                    )
                yield Static("", id="wizard-error")
                with Horizontal(classes="button-row"):
                    yield Button("Back", id="back-button")
                    yield Button("Next", id="next-button", variant="primary")
                    yield Button("Cancel", id="cancel-button")
        yield Footer()

    def on_mount(self) -> None:
        is_first_window_ever = self.state.editing_index is None and not self.state.windows
        if is_first_window_ever and not self.query_one("#window-name-input", Input).value:
            self.query_one("#window-name-input", Input).value = "main"
        self._refresh_order_list()
        self._refresh_custom_fields()

    def on_selection_list_selection_toggled(
        self, event: SelectionList.SelectionToggled
    ) -> None:
        if self._suppress_toggle:
            return
        kind = event.selection.value
        selection_list = self.query_one("#pane-selection-list", SelectionList)
        now_selected = kind in selection_list.selected

        if now_selected:
            if len(self._panes) >= MAX_PANES_PER_WINDOW:
                self._suppress_toggle = True
                selection_list.deselect(kind)
                self._suppress_toggle = False
                self._show_error(
                    f"You can select up to {MAX_PANES_PER_WINDOW} panes per window."
                )
                return
            self._panes.append(PaneDraft(kind))
        else:
            self._panes = [pane for pane in self._panes if pane.kind != kind]

        self._show_error("")
        self._refresh_order_list()
        self._refresh_custom_fields()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id not in ("custom-name-input", "custom-command-input"):
            return
        custom_draft = next((p for p in self._panes if p.kind is PaneKind.CUSTOM_COMMAND), None)
        if custom_draft is None:
            return
        if event.input.id == "custom-name-input":
            custom_draft.custom_name = event.value
        else:
            custom_draft.custom_command = event.value
        self._refresh_order_list()

    def _refresh_order_list(self) -> None:
        order_list = self.query_one("#pane-order-list", OptionList)
        highlighted = order_list.highlighted
        order_list.clear_options()
        for index, pane in enumerate(self._panes):
            order_list.add_option(Option(f"{index + 1}. {pane.display_name}", id=str(index)))
        if order_list.option_count:
            order_list.highlighted = min(highlighted or 0, order_list.option_count - 1)

    def _refresh_custom_fields(self) -> None:
        has_custom = any(pane.kind is PaneKind.CUSTOM_COMMAND for pane in self._panes)
        self.query_one("#custom-command-fields", Vertical).display = has_custom

    def _show_error(self, message: str) -> None:
        self.query_one("#wizard-error", Static).update(message)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "move-up-button":
            self._move_selected(-1)
        elif event.button.id == "move-down-button":
            self._move_selected(1)
        elif event.button.id == "next-button":
            self._go_next()
        elif event.button.id == "back-button":
            self.action_back()
        elif event.button.id == "cancel-button":
            self.action_cancel()

    def _move_selected(self, delta: int) -> None:
        order_list = self.query_one("#pane-order-list", OptionList)
        index = order_list.highlighted
        if index is None:
            return
        new_index = index + delta
        if not (0 <= new_index < len(self._panes)):
            return
        self._panes[index], self._panes[new_index] = self._panes[new_index], self._panes[index]
        self._refresh_order_list()
        order_list.highlighted = new_index

    def _go_next(self) -> None:
        window_name = self.query_one("#window-name-input", Input).value.strip()
        errors: list[str] = []

        if not window_name:
            errors.append("Window name cannot be empty.")
        elif window_name in self.state.other_window_names():
            errors.append(f"A window named '{window_name}' already exists in this workspace.")

        if not (1 <= len(self._panes) <= MAX_PANES_PER_WINDOW):
            errors.append(f"Select between 1 and {MAX_PANES_PER_WINDOW} panes.")

        has_custom = any(pane.kind is PaneKind.CUSTOM_COMMAND for pane in self._panes)
        if has_custom:
            name_value = self.query_one("#custom-name-input", Input).value.strip()
            command_value = self.query_one("#custom-command-input", Input).value.strip()
            if not name_value:
                errors.append("The custom command pane needs a display name.")
            if not command_value:
                errors.append("The custom command pane needs a command.")
            for pane in self._panes:
                if pane.kind is PaneKind.CUSTOM_COMMAND:
                    pane.custom_name = name_value
                    pane.custom_command = command_value

        if errors:
            self._show_error("\n".join(errors))
            return

        self.state.pending_window.window_name = window_name
        self.state.pending_window.panes = self._panes

        from dashboard.screens.new_project.step_layout_preview import LayoutPreviewScreen

        self.app.switch_screen(LayoutPreviewScreen(self.state, back_target=self.back_target))

    def _sync_pending_window(self) -> None:
        """Persist whatever's currently entered into state.pending_window,
        even though it hasn't passed Next's validation -- so navigating
        back to a previous step and forward again doesn't silently drop
        in-progress edits.
        """
        self.state.pending_window.window_name = self.query_one(
            "#window-name-input", Input
        ).value
        self.state.pending_window.panes = self._panes

    def action_back(self) -> None:
        self._sync_pending_window()
        if self.back_target == "project_info":
            from dashboard.screens.new_project.step_project_info import NewProjectScreen

            self.app.switch_screen(NewProjectScreen(self.state))
        else:
            from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen

            self.app.switch_screen(WindowSummaryScreen(self.state))

    def action_cancel(self) -> None:
        self.app.pop_screen()
