"""Manage reusable local workspace templates."""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.widgets.option_list import Option

from dashboard.models import (
    TemplateValidationError,
    WorkspaceTemplate,
    normalize_template_name,
)
from dashboard.screens.confirm import ConfirmScreen
from dashboard.screens.template_import_review import ImportTemplateReviewScreen
from dashboard.screens.template_name import TemplateNameScreen
from dashboard.screens.template_path import TemplatePathScreen
from dashboard.services.template_portability import (
    ExportDestinationExistsError,
    LoadedPortableTemplate,
    TemplatePortabilityError,
    construct_imported_template,
    export_template,
    load_portable_template,
    safe_default_export_filename,
    verify_import_source_unchanged,
)
from dashboard.services.template_store import (
    DuplicateTemplateNameError,
    TemplateStoreError,
    TemplateStoreVersionError,
    create_template,
    delete_template,
    find_template_by_name,
    get_template,
    load_templates_result,
    rename_template,
)
from dashboard.widgets import ActionItem, KeyboardActionList
from dashboard.widgets import KeyboardOptionList as OptionList


class WorkspaceTemplatesScreen(Screen[None]):
    BINDINGS = [
        ("escape", "go_back", "Back"),
        ("r", "rename_selected", "Rename"),
        ("d", "delete_selected", "Delete"),
        ("i", "import_template", "Import"),
        ("e", "export_selected", "Export"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._templates: dict[str, WorkspaceTemplate] = {}
        self._pending_import: LoadedPortableTemplate | None = None

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
                yield KeyboardActionList(
                    ActionItem("import", "Import"),
                    ActionItem("export", "Export", disabled=True),
                    ActionItem("rename", "Rename", disabled=True),
                    ActionItem("delete", "Delete", disabled=True, dangerous=True),
                    ActionItem("back", "Back"),
                    id="template-actions",
                )
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
        self._update_action_buttons()

    def _update_action_buttons(self) -> None:
        disabled = self._selected() is None
        actions = self.query_one("#template-actions", KeyboardActionList)
        actions.set_actions(
            [
                ActionItem("import", "Import"),
                ActionItem("export", "Export", disabled=disabled),
                ActionItem("rename", "Rename", disabled=disabled),
                ActionItem("delete", "Delete", disabled=disabled, dangerous=True),
                ActionItem("back", "Back"),
            ],
            preferred_id=actions.selected_action_id,
        )

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
            self._update_action_buttons()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "import":
            self.action_import_template()
        elif event.action_id == "export":
            self.action_export_selected()
        elif event.action_id == "rename":
            self.action_rename_selected()
        elif event.action_id == "delete":
            self.action_delete_selected()
        else:
            self.action_go_back()

    def action_refresh(self) -> None:
        selected = self._selected()
        self._refresh(selected.id if selected else None)

    def action_go_back(self) -> None:
        self.app.pop_screen()

    def _show_status(self, message: str) -> None:
        self.query_one("#template-error", Static).update(message)

    async def _choose_available_import_name(self, initial_name: str) -> str | None:
        name = initial_name
        while find_template_by_name(name) is not None:
            chosen = await self.app.push_screen_wait(
                TemplateNameScreen(
                    "Choose a Different Template Name",
                    value=name,
                    submit_label="Continue",
                )
            )
            if chosen is None:
                return None
            try:
                candidate_name = normalize_template_name(chosen)
            except TemplateValidationError as exc:
                self.app.notify(str(exc), title="Import", severity="error")
                continue
            if find_template_by_name(candidate_name) is not None:
                self.app.notify(
                    f'A template named "{candidate_name}" already exists.',
                    title="Import",
                    severity="error",
                )
                name = candidate_name
                continue
            return candidate_name
        return name

    @work
    async def action_import_template(self) -> None:
        raw_path = await self.app.push_screen_wait(
            TemplatePathScreen("Import Workspace Template", submit_label="Review")
        )
        if raw_path is None:
            return
        try:
            self._pending_import = load_portable_template(raw_path)
            local_name = await self._choose_available_import_name(
                self._pending_import.template.name
            )
        except (TemplatePortabilityError, TemplateValidationError) as exc:
            self._show_status(str(exc))
            return
        if local_name is None:
            return
        assert self._pending_import is not None
        while True:
            confirmed = await self.app.push_screen_wait(
                ImportTemplateReviewScreen(self._pending_import.template, local_name)
            )
            if not confirmed:
                return
            try:
                verify_import_source_unchanged(self._pending_import)
                imported = construct_imported_template(
                    self._pending_import.template, name=local_name
                )
                create_template(imported)
            except DuplicateTemplateNameError:
                local_name = await self._choose_available_import_name(local_name)
                if local_name is None:
                    return
                continue
            except (
                TemplatePortabilityError,
                TemplateStoreError,
                TemplateValidationError,
                OSError,
            ) as exc:
                self._show_status(str(exc))
                return
            self._refresh(imported.id)
            self._show_status(
                f'Imported template "{imported.name}" from {self._pending_import.path}.'
            )
            return

    @work
    async def action_export_selected(self) -> None:
        selected = self._selected()
        if selected is None:
            return
        raw_path = await self.app.push_screen_wait(
            TemplatePathScreen(
                "Export Workspace Template",
                value=safe_default_export_filename(selected.name),
                submit_label="Export",
            )
        )
        if raw_path is None:
            return
        current = get_template(selected.id)
        if current is None:
            self._refresh()
            self._show_status("Template no longer exists.")
            return
        try:
            exported_path = export_template(current, raw_path)
        except ExportDestinationExistsError as exc:
            confirmed = await self.app.push_screen_wait(
                ConfirmScreen(
                    f"{exc}\nOverwrite it and preserve the previous file as a backup?",
                    confirm_label="Overwrite",
                )
            )
            if not confirmed:
                return
            current = get_template(selected.id)
            if current is None:
                self._refresh()
                self._show_status("Template no longer exists.")
                return
            try:
                exported_path = export_template(current, raw_path, overwrite=True)
            except (TemplatePortabilityError, OSError) as overwrite_exc:
                self._show_status(str(overwrite_exc))
                return
        except (TemplatePortabilityError, OSError) as exc:
            self._show_status(str(exc))
            return
        self._refresh(current.id)
        self._show_status(f"Exported template to {exported_path}.")

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
