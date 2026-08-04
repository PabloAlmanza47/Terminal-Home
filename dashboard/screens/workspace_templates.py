"""Manage reusable local workspace templates."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.models import TemplateValidationError, WorkspaceTemplate
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.template_name import TemplateNameScreen
from dashboard.services.template_store import (
    DuplicateTemplateNameError,
    TemplateStoreError,
    TemplateStoreVersionError,
    delete_template,
    load_templates_result,
    rename_template,
)


class WorkspaceTemplatesScreen(Screen[None]):
    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "rename_selected", "Rename"),
        ("d", "delete_selected", "Delete"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._templates: dict[str, WorkspaceTemplate] = {}

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static("Workspace Templates", id="screen-title")
                yield Static(
                    "Templates are local layout copies. Apply them while configuring a workspace.",
                    classes="wizard-hint",
                )
                yield OptionList(id="template-list")
                yield Static("", id="template-summary")
                yield Static("", id="template-error")
                with Horizontal(classes="button-row"):
                    yield Button("Rename", id="rename-template-button")
                    yield Button("Delete", id="delete-template-button", variant="error")
                    yield Button("Back", id="back-button")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#template-list", OptionList).focus()

    def _refresh(self, preferred_id: str | None = None) -> None:
        result = load_templates_result()
        self._templates = {item.id: item for item in result.templates}
        option_list = self.query_one("#template-list", OptionList)
        option_list.clear_options()
        if not result.templates:
            option_list.add_option(Option("No workspace templates saved yet", disabled=True))
            self.query_one("#template-summary", Static).update(
                "Save a configured project workspace as a template from Project Detail."
            )
        else:
            for template in result.templates:
                option_list.add_option(Option(template.name, id=template.id))
            ids = [item.id for item in result.templates]
            option_list.highlighted = ids.index(preferred_id) if preferred_id in ids else 0
            self._show_selected()
        self.query_one("#template-error", Static).update(result.error or result.warning or "")

    def _selected(self) -> WorkspaceTemplate | None:
        option_list = self.query_one("#template-list", OptionList)
        if option_list.highlighted is None or not self._templates:
            return None
        option_id = option_list.get_option_at_index(option_list.highlighted).id or ""
        return self._templates.get(option_id)

    def _show_selected(self) -> None:
        template = self._selected()
        if template is None:
            return
        pane_count = sum(len(window.panes) for window in template.windows)
        lines = [f"{len(template.windows)} window(s) · {pane_count} pane(s)"]
        lines.extend(
            f"{window.window_name}: {', '.join(pane.display_name for pane in window.panes)}"
            for window in template.windows
        )
        self.query_one("#template-summary", Static).update("\n".join(lines))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "template-list":
            self._show_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-template-button":
            self.action_rename_selected()
        elif event.button.id == "delete-template-button":
            self.action_delete_selected()
        else:
            self.action_go_back()

    def action_refresh(self) -> None:
        selected = self._selected()
        self._refresh(selected.id if selected else None)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    @work
    async def action_rename_selected(self) -> None:
        template = self._selected()
        if template is None:
            return
        name = await self.app.push_screen_wait(
            TemplateNameScreen(
                "Rename Workspace Template", value=template.name, submit_label="Rename"
            )
        )
        if name is None:
            return
        try:
            renamed = rename_template(template.id, name)
        except (
            DuplicateTemplateNameError,
            TemplateStoreError,
            TemplateStoreVersionError,
            TemplateValidationError,
            OSError,
        ) as exc:
            self.query_one("#template-error", Static).update(str(exc))
            return
        if renamed is None:
            self._refresh()
            self.query_one("#template-error", Static).update("Template no longer exists.")
        else:
            self._refresh(renamed.id)

    @work
    async def action_delete_selected(self) -> None:
        template = self._selected()
        if template is None:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmScreen(
                f'Delete workspace template "{template.name}"?\n'
                "Projects already created from it are not affected.",
                confirm_label="Delete",
            )
        )
        if not confirmed:
            return
        try:
            deleted = delete_template(template.id)
        except (TemplateStoreError, TemplateStoreVersionError, OSError) as exc:
            self.query_one("#template-error", Static).update(str(exc))
            return
        self._refresh()
        if not deleted:
            self.query_one("#template-error", Static).update("Template no longer exists.")
