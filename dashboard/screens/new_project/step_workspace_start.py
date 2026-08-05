"""Choose the initial layout for a new workspace configuration."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Static
from textual.widgets.option_list import Option

from dashboard.screens.new_project.state import WizardMode, WizardState
from dashboard.services.template_store import load_templates_result
from dashboard.services.workspace_defaults import default_workspace_windows
from dashboard.widgets import KeyboardOptionList as OptionList

BLANK_WORKSPACE = "blank"
DEFAULT_WORKSPACE = "default"
TEMPLATE_PREFIX = "template:"


class WorkspaceStartScreen(Screen[None]):
    BINDINGS = [("escape", "back", "Back")]

    def __init__(self, state: WizardState) -> None:
        super().__init__()
        self.state = state
        self._result = load_templates_result()
        self._templates = {template.id: template for template in self._result.templates}

    def compose(self) -> ComposeResult:
        if self.state.mode is WizardMode.NEW_PROJECT:
            title = "Create New Project -- Step 2 of 6: Start From"
        else:
            title = "Configure Workspace -- Step 1 of 5: Start From"
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static(title, id="screen-title")
                yield Static(
                    "Choose an initial layout. You can edit it before saving.",
                    classes="wizard-hint",
                )
                options = [
                    Option("Blank Workspace", id=BLANK_WORKSPACE),
                    Option("Default Workspace", id=DEFAULT_WORKSPACE),
                ]
                options.extend(
                    Option(f"Saved Template: {template.name}", id=f"{TEMPLATE_PREFIX}{template.id}")
                    for template in self._result.templates
                )
                yield OptionList(*options, id="workspace-start-list")
                message = self._result.error or self._result.warning or ""
                yield Static(message, id="wizard-error")
                with Horizontal(classes="button-row"):
                    yield Button("Continue", id="continue-button", variant="primary")
                    yield Button("Back", id="back-button")
                    yield Button("Cancel", id="cancel-button")
        yield Footer()

    def on_mount(self) -> None:
        option_list = self.query_one("#workspace-start-list", OptionList)
        option_list.highlighted = 0
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "workspace-start-list":
            self._apply(event.option.id)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-button":
            option_list = self.query_one("#workspace-start-list", OptionList)
            if option_list.highlighted is not None:
                self._apply(option_list.get_option_at_index(option_list.highlighted).id)
        elif event.button.id == "back-button":
            self.action_back()
        else:
            self.action_cancel()

    def _apply(self, option_id: str | None) -> None:
        if option_id == BLANK_WORKSPACE:
            self.state.windows.clear()
            if not self.state.pending_window.panes:
                self.state.start_new_window()
            from dashboard.screens.new_project.step_window_config import WindowConfigScreen

            self.app.switch_screen(WindowConfigScreen(self.state, back_target="workspace_start"))
            return
        if option_id == DEFAULT_WORKSPACE:
            windows = default_workspace_windows()
        elif option_id and option_id.startswith(TEMPLATE_PREFIX):
            template = self._templates.get(option_id.removeprefix(TEMPLATE_PREFIX))
            if template is None:
                self.query_one("#wizard-error", Static).update(
                    "That template is no longer available."
                )
                return
            windows = template.windows
        else:
            return
        self.state.replace_windows(windows)
        from dashboard.screens.new_project.step_window_summary import WindowSummaryScreen

        self.app.switch_screen(WindowSummaryScreen(self.state))

    def action_back(self) -> None:
        if self.state.mode is WizardMode.NEW_PROJECT:
            from dashboard.screens.new_project.step_project_info import NewProjectScreen

            self.app.switch_screen(NewProjectScreen(self.state))
        else:
            self.app.pop_screen()

    def action_cancel(self) -> None:
        self.app.pop_screen()
