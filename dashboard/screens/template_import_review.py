"""Explicit trust review shown before a portable template is imported."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from dashboard.models import PaneKind
from dashboard.services.template_portability import PortableWorkspaceTemplate


def format_import_review(template: PortableWorkspaceTemplate, local_name: str) -> str:
    lines = [f"Template name: {local_name}", ""]
    for window in template.windows:
        lines.append(f"Window: {window.window_name}")
        for pane in window.panes:
            lines.append(f"  Pane: {pane.display_name} [{pane.kind.value}]")
            if pane.kind is PaneKind.CUSTOM_COMMAND:
                lines.append("    Literal custom command:")
                lines.append(f"    {pane.custom_command}")
        lines.append("")
    lines.extend(
        [
            "Custom commands are not executed during import.",
            "They run only if this template is later applied and that workspace is launched.",
        ]
    )
    return "\n".join(lines)


class ImportTemplateReviewScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, template: PortableWorkspaceTemplate, local_name: str) -> None:
        super().__init__()
        self.template = template
        self.local_name = local_name

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="template-import-review-panel", classes="panel"):
            yield Static("Review Imported Workspace Template", id="template-import-review-title")
            yield Static(
                format_import_review(self.template, self.local_name),
                id="template-import-review-body",
                markup=False,
            )
            with Horizontal(classes="button-row"):
                yield Button("Import", id="template-import-confirm", variant="primary")
                yield Button("Cancel", id="template-import-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "template-import-confirm")

    def on_mount(self) -> None:
        self.query_one("#template-import-confirm", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)
