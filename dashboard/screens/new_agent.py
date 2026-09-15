"""Keyboard-first, non-mutating New Agent wizard."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Input, Static, TextArea
from textual.widgets.option_list import Option

from dashboard.services.agent_creation import (
    AgentCreationMode,
    AgentCreationRequest,
    default_agent_worktree_root,
    validate_agent_creation_request,
)
from dashboard.services.projects import ProjectStatus, scan_all_projects
from dashboard.services.slug import slugify
from dashboard.widgets import ActionItem, KeyboardActionList
from dashboard.widgets import KeyboardOptionList as OptionList

_GENERATED_SLUG_LIMIT = 48
_GENERATED_SUFFIX_LENGTH = 7


def _bounded_slug(value: str) -> str:
    """Keep generated path/branch components portable and deterministic."""
    if len(value) <= _GENERATED_SLUG_LIMIT:
        return value
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:_GENERATED_SUFFIX_LENGTH]
    prefix_length = _GENERATED_SLUG_LIMIT - _GENERATED_SUFFIX_LENGTH - 1
    return f"{value[:prefix_length].rstrip('-')}-{digest}"


def _is_generated_collision(error: str | None) -> bool:
    return bool(
        error
        and (error.startswith("Branch already exists:")
             or error.startswith("Worktree path already exists or is registered:"))
    )


@dataclass(slots=True)
class AgentWizardState:
    project: ProjectStatus | None = None
    mode: AgentCreationMode = AgentCreationMode.NEW_WORKTREE
    task_name: str = ""
    tool: str = "codex"
    prompt: str | None = None
    branch_name: str = ""
    worktree_path: Path | None = None
    preflight_warning: str | None = None
    generated_branch_name: str | None = None
    generated_worktree_path: Path | None = None

    def suggest_paths(self) -> None:
        if self.branch_name:
            return
        slug = _bounded_slug(slugify(self.task_name) or "task")
        branch = f"agent/{slug}"
        self.branch_name = branch
        self.generated_branch_name = branch
        if self.project is not None:
            self.worktree_path = (
                default_agent_worktree_root()
                / _bounded_slug(slugify(self.project.project.name) or "project")
                / slug
            )
            self.generated_worktree_path = self.worktree_path

    def request(self) -> AgentCreationRequest:
        assert self.project is not None
        return AgentCreationRequest(
            project_path=self.project.canonical_path,
            mode=self.mode,
            task_name=self.task_name,
            tool=self.tool,
            prompt=self.prompt,
            branch_name=self.branch_name or None,
            worktree_path=self.worktree_path,
        )


class AgentWizardScreen(Screen[None]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, state: AgentWizardState | None = None) -> None:
        super().__init__()
        self.state = state or AgentWizardState()

    def action_cancel(self) -> None:
        self.app.pop_screen()


class AgentProjectScreen(AgentWizardScreen):
    def __init__(
        self,
        state: AgentWizardState | None = None,
        statuses: tuple[ProjectStatus, ...] = (),
    ) -> None:
        super().__init__(state)
        self._statuses = statuses
        self._scanning = not bool(statuses)

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("New Agent — Step 1 of 4: Project", id="screen-title")
                yield Static("Select a local project.", classes="wizard-hint")
                yield Input(placeholder="Filter projects...", id="project-filter")
                yield OptionList(id="new-agent-project-list")
                yield Static("", id="wizard-error")
                yield KeyboardActionList(
                    ActionItem("next", "Next"), ActionItem("cancel", "Cancel"), id="wizard-actions"
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#project-filter", Input).focus()
        if self._statuses:
            self._populate()
        else:
            self.run_worker(self._scan, thread=True, exclusive=True)

    def _scan(self) -> tuple[ProjectStatus, ...]:
        result = scan_all_projects()
        statuses = tuple(result.statuses)
        self.app.call_from_thread(self._set_statuses, statuses)
        return statuses

    def _set_statuses(self, statuses: tuple[ProjectStatus, ...]) -> None:
        self._scanning = False
        self._statuses = statuses
        self._populate()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "project-filter":
            self._populate()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "project-filter":
            self._next()

    def _populate(self) -> None:
        options = self.query_one("#new-agent-project-list", OptionList)
        options.clear_options()
        query = self.query_one("#project-filter", Input).value.casefold()
        projects = [
            status for status in self._statuses
            if status.project_dir_exists and (not query or query in status.project.name.casefold())
        ]
        if not projects:
            options.add_option(Option("No local projects found", disabled=True))
            return
        for status in projects:
            branch = status.git_branch or "branch unknown"
            label = f"{status.project.name} — {status.canonical_path} ({branch})"
            options.add_option(Option(label, id=str(status.canonical_path)))
        options.highlighted = 0

    def _next(self) -> None:
        options = self.query_one("#new-agent-project-list", OptionList)
        if options.highlighted is None:
            return
        selected = options.get_option_at_index(options.highlighted).id
        self.state.project = next(
            (
                status
                for status in self._statuses
                if str(status.canonical_path) == str(selected)
            ),
            None,
        )
        if self.state.project is None:
            self.query_one("#wizard-error", Static).update("Select a local project.")
            return
        from dashboard.screens.new_agent import AgentTaskScreen

        self.app.switch_screen(AgentTaskScreen(self.state))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._next()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "next":
            self._next()
        else:
            self.action_cancel()


class AgentWorkspaceScreen(AgentWizardScreen):
    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("New Agent — Step 3 of 4: Workspace", id="screen-title")
                yield OptionList(
                    Option("New worktree — recommended", id="new_worktree"),
                    Option("Current checkout", id="current_checkout"),
                    id="agent-mode-list",
                )
                yield Static("", id="checkout-warning", classes="wizard-hint")
                yield Static("Branch name", classes="field-label", id="branch-label")
                yield Input(id="branch-input")
                yield Static("Worktree path", classes="field-label", id="worktree-label")
                yield Input(id="worktree-input")
                yield Static("", id="wizard-error")
                yield KeyboardActionList(
                    ActionItem("next", "Next"), ActionItem("back", "Back"),
                    ActionItem("cancel", "Cancel"), id="wizard-actions"
                )
        yield Footer()

    def on_mount(self) -> None:
        self.state.suggest_paths()
        self.query_one("#agent-mode-list", OptionList).highlighted = 0
        self._sync_mode()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_list.id == "agent-mode-list":
            self._sync_mode()

    def _sync_mode(self) -> None:
        options = self.query_one("#agent-mode-list", OptionList)
        mode = (
            options.get_option_at_index(options.highlighted).id
            if options.highlighted is not None
            else "new_worktree"
        )
        self.state.mode = (
            AgentCreationMode.CURRENT_CHECKOUT
            if mode == "current_checkout"
            else AgentCreationMode.NEW_WORKTREE
        )
        current = self.state.mode is AgentCreationMode.CURRENT_CHECKOUT
        self.query_one("#checkout-warning", Static).update(
            "The agent may modify this checkout directly." if current else ""
        )
        for widget_id in ("#branch-label", "#branch-input", "#worktree-label", "#worktree-input"):
            self.query_one(widget_id).display = not current
        if not current:
            self.query_one("#branch-input", Input).value = self.state.branch_name
            self.query_one("#worktree-input", Input).value = str(self.state.worktree_path or "")

    def _next(self) -> None:
        self.state.branch_name = self.query_one("#branch-input", Input).value.strip()
        raw_path = self.query_one("#worktree-input", Input).value.strip()
        self.state.worktree_path = Path(raw_path).expanduser().resolve() if raw_path else None
        if self.state.mode is AgentCreationMode.NEW_WORKTREE and (
            not self.state.branch_name or self.state.worktree_path is None
        ):
            self.query_one("#wizard-error", Static).update("Branch and worktree path are required.")
            return
        validation = validate_agent_creation_request(self.state.request())
        if (
            not validation.success
            and self.state.branch_name == self.state.generated_branch_name
            and self.state.worktree_path == self.state.generated_worktree_path
            and _is_generated_collision(validation.error)
        ):
            self._suggest_available_generated_paths()
            validation = validate_agent_creation_request(self.state.request())
        if not validation.success:
            self.query_one("#wizard-error", Static).update(validation.error or "Preflight failed.")
            return
        self.state.preflight_warning = validation.warning
        if validation.warning:
            self.query_one("#checkout-warning", Static).update(validation.warning)
        self.app.switch_screen(AgentReviewScreen(self.state))

    def _suggest_available_generated_paths(self) -> None:
        """Suffix untouched generated defaults until both values are available."""
        assert self.state.generated_branch_name is not None
        assert self.state.generated_worktree_path is not None
        base_branch = self.state.generated_branch_name
        base_path = self.state.generated_worktree_path
        for number in range(2, 1000):
            branch = f"{base_branch}-{number}"
            path = base_path.with_name(f"{base_path.name}-{number}")
            candidate = AgentWizardState(
                project=self.state.project,
                mode=self.state.mode,
                task_name=self.state.task_name,
                tool=self.state.tool,
                prompt=self.state.prompt,
                branch_name=branch,
                worktree_path=path,
            )
            validation = validate_agent_creation_request(candidate.request())
            if validation.success:
                self.state.branch_name = branch
                self.state.worktree_path = path
                return
            if not _is_generated_collision(validation.error):
                return

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in {"branch-input", "worktree-input"}:
            self._next()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "next":
            self._next()
        elif event.action_id == "back":
            self.app.switch_screen(AgentTaskScreen(self.state))
        else:
            self.action_cancel()


class AgentTaskScreen(AgentWizardScreen):
    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("New Agent — Step 2 of 4: Task", id="screen-title")
                yield Static("Task name", classes="field-label")
                yield Input(value=self.state.task_name, id="task-input")
                yield Static("Agent provider", classes="field-label")
                yield Static("Codex", id="agent-provider")
                yield Static("Optional initial prompt", classes="field-label")
                yield TextArea(self.state.prompt or "", id="prompt-input")
                yield Static(
                    "Use the Review action to continue; Enter inserts a newline.",
                    classes="wizard-hint",
                )
                yield Static("", id="wizard-error")
                yield KeyboardActionList(
                    ActionItem("next", "Review"), ActionItem("back", "Back"),
                    ActionItem("cancel", "Cancel"), id="wizard-actions"
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#task-input", Input).focus()

    def _next(self) -> None:
        self.state.task_name = self.query_one("#task-input", Input).value
        self.state.prompt = self.query_one("#prompt-input", TextArea).text or None
        if not self.state.task_name.strip():
            self.query_one("#wizard-error", Static).update("Task name is required.")
            return
        self.app.switch_screen(AgentWorkspaceScreen(self.state))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "task-input":
            self.state.task_name = event.value

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "prompt-input":
            self.state.prompt = event.text_area.text or None

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "next":
            self._next()
        elif event.action_id == "back":
            self.app.switch_screen(AgentProjectScreen(self.state))
        else:
            self.action_cancel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "task-input":
            self._next()


class AgentReviewScreen(AgentWizardScreen):
    def compose(self) -> ComposeResult:
        assert self.state.project is not None
        request = self.state.request()
        prompt_text = "(provided; hidden for safety)" if request.prompt else "(none)"
        deck_status = (
            "Agent Deck: unavailable — install agent-deck before creating."
            if shutil.which("agent-deck") is None
            else "Agent Deck: available"
        )
        lines = [
            f"Project: {self.state.project.project.name}",
            f"Canonical source: {request.project_path}",
            f"Workspace mode: {request.mode.value}",
        ]
        if request.mode is AgentCreationMode.NEW_WORKTREE:
            lines.extend((f"Branch: {request.branch_name}", f"Worktree: {request.worktree_path}"))
            if self.state.preflight_warning:
                lines.extend(("", f"WARNING: {self.state.preflight_warning}"))
            lines.extend(
                (
                    "",
                    "This will:",
                    "1. Create the branch and worktree.",
                    "2. Launch one Codex session through Agent Deck.",
                    "3. Attach after creation.",
                )
            )
        else:
            lines.extend(
                (
                    "",
                    "WARNING: The agent may modify this checkout directly.",
                    "",
                    "This will:",
                    "1. Use the existing clean checkout.",
                    "2. Launch one Codex session through Agent Deck.",
                    "3. Attach after creation.",
                )
            )
        lines.extend(
            (
                "",
                f"Agent: {request.tool}",
                deck_status,
                f"Task: {request.task_name}",
                f"Prompt: {prompt_text}",
            )
        )
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("New Agent — Step 4 of 4: Review", id="screen-title")
                yield Static("\n".join(lines), id="agent-review")
                yield Static("", id="wizard-error")
                yield KeyboardActionList(
                    ActionItem("create", "Create"), ActionItem("back", "Back"),
                    ActionItem("cancel", "Cancel"), id="wizard-actions"
                )
        yield Footer()

    def on_keyboard_action_list_action_selected(
        self, event: KeyboardActionList.ActionSelected
    ) -> None:
        if event.action_id == "create":
            self.app.exit(self.state.request())
        elif event.action_id == "back":
            self.app.switch_screen(AgentWorkspaceScreen(self.state))
        else:
            self.action_cancel()
