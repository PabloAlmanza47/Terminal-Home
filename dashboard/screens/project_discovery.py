"""Project Discovery screen, reachable from Settings: manages the
dashboard.models.projects_config.ProjectsConfig that
dashboard.services.projects.discover_projects scans by -- roots, max
scan depth, excluded directory names, and manually registered projects.

Every edit here persists immediately via projects_config_store, the same
"no separate Save step" pattern SettingsScreen already uses for
presentation preferences -- there's nothing destructive here that needs
confirmation, and the next project scan (Home, or Continue Project) picks
up the change automatically. Never creates a directory: adding a root or
a manual project only ever records the path, exactly as
dashboard.models.projects_config.ProjectsConfig documents.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Input, Static
from textual.widgets.option_list import Option

from dashboard.models.projects_config import ProjectsConfig, ProjectsConfigValidationError
from dashboard.services.projects_config_store import (
    load_projects_config_result,
    save_projects_config,
)
from dashboard.widgets import ActionItem, KeyboardActionList
from dashboard.widgets import KeyboardOptionList as OptionList


def _clean_path_input(value: str) -> Path | None:
    """A usable, expanded Path from raw user input, or None if *value* is
    blank -- never raises, and never checks whether the path exists
    (adding a root or manual project must not require it to exist yet).
    """
    stripped = value.strip()
    if not stripped:
        return None
    return Path(stripped).expanduser()


def _canonical_or_self(path: Path) -> Path:
    """*path*, resolved if possible -- used only to detect "you already
    added this" duplicates at edit time (the same notion of "the same
    project" discovery itself uses). Falls back to the expanded-but-
    unresolved path if resolution fails, so an edit is never blocked by a
    transient filesystem error.
    """
    try:
        return path.resolve()
    except OSError:
        return path


class ProjectDiscoveryScreen(Screen[None]):
    """View and edit project-discovery configuration: roots, max depth,
    excluded names, and manually registered projects.
    """

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        load_result = load_projects_config_result()
        self.config: ProjectsConfig = load_result.value
        self._recovery_warning = load_result.warning

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with VerticalScroll(classes="panel"):
                yield Static("Project Discovery", id="screen-title")
                yield Static(
                    "Changes here take effect the next time projects are scanned "
                    "(Home, or Continue Project).",
                    classes="wizard-hint",
                )
                yield Static(
                    self._recovery_warning or "",
                    id="discovery-recovery-warning",
                    classes="wizard-hint",
                )

                yield Static("Project roots", classes="field-label")
                yield OptionList(id="roots-list")
                yield Input(placeholder="e.g. ~/work", id="root-input")
                yield KeyboardActionList(
                    ActionItem("add-root", "Add Root"),
                    ActionItem("remove-root", "Remove Selected Root", dangerous=True),
                    id="root-actions",
                )

                yield Static("Max scan depth (levels below each root)", classes="field-label")
                yield Input(value=str(self.config.max_depth), id="depth-input")
                yield KeyboardActionList(
                    ActionItem("apply-depth", "Apply Depth"), id="depth-actions"
                )

                yield Static("Excluded directory names", classes="field-label")
                yield OptionList(id="excluded-list")
                yield Input(placeholder="e.g. dist", id="excluded-input")
                yield KeyboardActionList(
                    ActionItem("add-excluded", "Add Excluded Name"),
                    ActionItem("remove-excluded", "Remove Selected", dangerous=True),
                    id="excluded-actions",
                )

                yield Static("Manually registered projects", classes="field-label")
                yield OptionList(id="manual-list")
                yield Input(placeholder="e.g. ~/elsewhere/side-project", id="manual-input")
                yield KeyboardActionList(
                    ActionItem("add-manual", "Add Project"),
                    ActionItem("remove-manual", "Remove Selected", dangerous=True),
                    id="manual-actions",
                )

                yield Static("", id="wizard-error")
                yield KeyboardActionList(ActionItem("back", "Back"), id="discovery-actions")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_roots()
        self._refresh_excluded()
        self._refresh_manual()
        self.query_one("#roots-list", OptionList).focus()

    # --- Rendering each list from self.config ---------------------------------

    def _refresh_roots(self) -> None:
        option_list = self.query_one("#roots-list", OptionList)
        option_list.clear_options()
        if not self.config.roots:
            option_list.add_option(Option("No roots configured", disabled=True))
            return
        for root in self.config.roots:
            option_list.add_option(Option(str(root), id=str(root)))

    def _refresh_excluded(self) -> None:
        option_list = self.query_one("#excluded-list", OptionList)
        option_list.clear_options()
        if not self.config.excluded_names:
            option_list.add_option(Option("No excluded names configured", disabled=True))
            return
        for name in sorted(self.config.excluded_names):
            option_list.add_option(Option(name, id=name))

    def _refresh_manual(self) -> None:
        option_list = self.query_one("#manual-list", OptionList)
        option_list.clear_options()
        if not self.config.manual_projects:
            option_list.add_option(Option("No manually registered projects", disabled=True))
            return
        for path in self.config.manual_projects:
            option_list.add_option(Option(str(path), id=str(path)))

    def _show_error(self, message: str) -> None:
        self.query_one("#wizard-error", Static).update(message)

    def _save(self, new_config: ProjectsConfig) -> None:
        try:
            save_projects_config(new_config)
        except (OSError, ProjectsConfigValidationError) as exc:
            self._show_error(f"Project discovery settings could not be saved: {exc}")
            return
        self.config = new_config
        self._show_error("")

    # --- Action handling -------------------------------------------------------

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        handlers = {
            "add-root": self._add_root,
            "remove-root": self._remove_root,
            "apply-depth": self._apply_depth,
            "add-excluded": self._add_excluded,
            "remove-excluded": self._remove_excluded,
            "add-manual": self._add_manual,
            "remove-manual": self._remove_manual,
            "back": self.action_go_back,
        }
        handler = handlers.get(event.action_id)
        if handler is not None:
            handler()

    def _add_root(self) -> None:
        input_widget = self.query_one("#root-input", Input)
        path = _clean_path_input(input_widget.value)
        if path is None:
            self._show_error("Enter a root path first.")
            return
        canonical = _canonical_or_self(path)
        if any(_canonical_or_self(existing) == canonical for existing in self.config.roots):
            self._show_error(f"'{path}' is already a configured root.")
            return
        self._save(replace(self.config, roots=(*self.config.roots, path)))
        input_widget.value = ""
        self._refresh_roots()

    def _remove_root(self) -> None:
        option_list = self.query_one("#roots-list", OptionList)
        selected = option_list.highlighted
        if selected is None or not self.config.roots:
            self._show_error("Select a root to remove first.")
            return
        remaining = tuple(root for i, root in enumerate(self.config.roots) if i != selected)
        self._save(replace(self.config, roots=remaining))
        self._refresh_roots()

    def _apply_depth(self) -> None:
        raw = self.query_one("#depth-input", Input).value.strip()
        try:
            depth = int(raw)
        except ValueError:
            self._show_error("Max depth must be a whole number.")
            return
        try:
            self._save(replace(self.config, max_depth=depth))
        except ProjectsConfigValidationError as exc:
            self._show_error(str(exc))

    def _add_excluded(self) -> None:
        input_widget = self.query_one("#excluded-input", Input)
        name = input_widget.value.strip()
        if not name:
            self._show_error("Enter a directory name first.")
            return
        if name in self.config.excluded_names:
            self._show_error(f"'{name}' is already excluded.")
            return
        self._save(replace(self.config, excluded_names=self.config.excluded_names | {name}))
        input_widget.value = ""
        self._refresh_excluded()

    def _remove_excluded(self) -> None:
        option_list = self.query_one("#excluded-list", OptionList)
        selected_index = option_list.highlighted
        name = (
            option_list.get_option_at_index(selected_index).id
            if selected_index is not None
            else None
        )
        if name is None:
            self._show_error("Select an excluded name to remove first.")
            return
        self._save(replace(self.config, excluded_names=self.config.excluded_names - {name}))
        self._refresh_excluded()

    def _add_manual(self) -> None:
        input_widget = self.query_one("#manual-input", Input)
        path = _clean_path_input(input_widget.value)
        if path is None:
            self._show_error("Enter a project path first.")
            return
        canonical = _canonical_or_self(path)
        already_registered = any(
            _canonical_or_self(existing) == canonical for existing in self.config.manual_projects
        )
        if already_registered:
            self._show_error(f"'{path}' is already registered.")
            return
        self._save(replace(self.config, manual_projects=(*self.config.manual_projects, path)))
        input_widget.value = ""
        self._refresh_manual()

    def _remove_manual(self) -> None:
        option_list = self.query_one("#manual-list", OptionList)
        selected = option_list.highlighted
        if selected is None or not self.config.manual_projects:
            self._show_error("Select a project to remove first.")
            return
        remaining = tuple(
            path for i, path in enumerate(self.config.manual_projects) if i != selected
        )
        self._save(replace(self.config, manual_projects=remaining))
        self._refresh_manual()

    def action_go_back(self) -> None:
        self.app.pop_screen()
