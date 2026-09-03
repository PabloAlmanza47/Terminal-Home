"""The Textual App: wires up the theme and starts on the home screen.

main() runs the Textual app to completion, then -- only after Textual has
fully released the terminal -- hands any resulting LaunchRequest to the
non-Textual tmux orchestration layer. tmux/attach must never run while a
Textual screen is still mounted.
"""

from __future__ import annotations

import sys
from dataclasses import replace

from textual.app import App
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widgets import Button, Checkbox, Input, TextArea

from dashboard.models import AgentDeckAttachRequest, LaunchRequest, TmuxSessionAttachRequest
from dashboard.models.settings import AppSettings
from dashboard.screens.home import HomeScreen
from dashboard.services.agent_deck_launcher import AgentDeckLaunchError, execute_agent_deck_attach
from dashboard.services.settings_store import load_settings_result, save_settings
from dashboard.services.tmux import TmuxCommandError
from dashboard.services.workspace_launcher import (
    LaunchError,
    execute_launch_request,
    execute_tmux_session_attach,
)
from dashboard.widgets import KeyboardActionList

AppResult = LaunchRequest | TmuxSessionAttachRequest | AgentDeckAttachRequest | None


class TerminalHomeApp(App[AppResult]):
    """Terminal Home: a declarative tmux workspace manager."""

    CSS_PATH = "app.tcss"
    TITLE = "Terminal Home"
    BINDINGS = [
        ("?", "show_help", "Keyboard help"),
        ("q", "quit_app", "Quit"),
        ("/", "focus_search", "Search"),
        ("n", "new_project", "New Project"),
        ("p", "open_projects", "Projects"),
        ("s", "open_settings", "Settings"),
    ]

    def __init__(self) -> None:
        super().__init__()
        settings_result = load_settings_result()
        self.settings: AppSettings = settings_result.value
        self._settings_recovery_warning = settings_result.warning

    def on_mount(self) -> None:
        # Subscribed only once running (Signal.subscribe requires it), and
        # before the theme is applied so our own startup assignment below is
        # captured too -- _on_theme_changed no-ops when the name already
        # matches what was loaded, so that doesn't cause a redundant save.
        self.theme_changed_signal.subscribe(self, self._on_theme_changed)
        if self.settings.theme is not None and self.settings.theme in self.available_themes:
            self.theme = self.settings.theme
        self.push_screen(HomeScreen())

    def _on_theme_changed(self, theme: Theme) -> None:
        """Persist theme changes made through any supported path -- the
        command palette's built-in "Change Theme" command, or (in the
        future) any in-app theme control -- since they all funnel through
        the same App.theme reactive and this signal.
        """
        if theme.name == self.settings.theme:
            return
        self.settings = replace(self.settings, theme=theme.name)
        try:
            save_settings(self.settings)
        except OSError as exc:
            self.notify(
                f"Theme applied for this session, but couldn't be saved: {exc}",
                title="Settings",
                severity="error",
            )

    def _editing(self) -> bool:
        return isinstance(self.focused, (Input, TextArea))

    def on_key(self, event) -> None:
        """Provide spatial arrows for simple controls without disturbing
        text cursor movement or list/radio widgets' native arrow behavior.

        Tab is intentionally inert; command screens use terminal action rows
        and forms use arrows, preserving normal Input cursor movement.
        """
        if event.key in {"tab", "shift+tab"}:
            event.stop()
            return
        focused = self.focused
        if isinstance(focused, Checkbox) and event.key in {"enter", "space"}:
            focused.value = not focused.value
            event.stop()
            return
        if not isinstance(focused, (Button, Checkbox)):
            return
        if event.key not in {"up", "down", "left", "right"}:
            return
        candidates = [
            widget
            for widget in [
                *self.screen.query(Button),
                *self.screen.query(Checkbox),
                *self.screen.query(KeyboardActionList),
            ]
            if widget.display and widget is not focused and widget.can_focus
        ]
        if not candidates:
            return
        current = focused.region
        cx, cy = current.x + current.width / 2, current.y + current.height / 2
        directional = []
        for widget in candidates:
            region = widget.region
            wx, wy = region.x + region.width / 2, region.y + region.height / 2
            dx, dy = wx - cx, wy - cy
            if event.key == "left" and dx < 0:
                directional.append((abs(dx) + abs(dy), widget))
            elif event.key == "right" and dx > 0:
                directional.append((abs(dx) + abs(dy), widget))
            elif event.key == "up" and dy < 0:
                directional.append((abs(dy) + abs(dx), widget))
            elif event.key == "down" and dy > 0:
                directional.append((abs(dy) + abs(dx), widget))
        if directional:
            directional.sort(key=lambda item: item[0])
            directional[0][1].focus()
            event.stop()

    def action_quit_app(self) -> None:
        if not self._editing() and not isinstance(self.screen, ModalScreen):
            self.exit()

    def action_show_help(self) -> None:
        if self._editing() or isinstance(self.screen, ModalScreen):
            return
        from dashboard.screens.help import HelpScreen

        bindings: list[tuple[str, str]] = []
        for binding in self.screen.BINDINGS:
            if isinstance(binding, Binding):
                bindings.append((binding.key, binding.description or binding.action))
            else:
                bindings.append((binding[0], binding[2] if len(binding) > 2 else binding[1]))
        self.push_screen(HelpScreen(bindings))

    def action_focus_search(self) -> None:
        if self._editing():
            return
        for widget in self.screen.query(Input):
            widget.focus()
            return

    def action_new_project(self) -> None:
        if self._editing() or isinstance(self.screen, ModalScreen):
            return
        from dashboard.screens.new_project import NewProjectScreen

        if self.screen.__class__.__name__ in {"HomeScreen", "ProjectsScreen"}:
            self.push_screen(NewProjectScreen())

    def action_open_projects(self) -> None:
        if self._editing() or isinstance(self.screen, ModalScreen):
            return
        from dashboard.screens.projects import ProjectsScreen

        if self.screen.__class__.__name__ in {"HomeScreen", "ProjectsScreen", "SettingsScreen"}:
            self.push_screen(ProjectsScreen())

    def action_open_settings(self) -> None:
        if self._editing() or isinstance(self.screen, ModalScreen):
            return
        from dashboard.screens.settings import SettingsScreen

        if self.screen.__class__.__name__ in {"HomeScreen", "ProjectsScreen"}:
            self.push_screen(SettingsScreen())


def main() -> None:
    """The TUI-only application launcher. Every console script
    (`terminal-home`, `th`, `dev`) and `python -m dashboard` go through
    dashboard.cli:main first, which calls this function only when no
    read-only subcommand (`list`, `plan`, `doctor`) was given -- this stays
    the one place that knows how to run Textual to completion and hand off
    to tmux, regardless of which command name launched the process.
    """
    while True:
        app = TerminalHomeApp()
        launch_request = app.run()
        if launch_request is None:
            return
        try:
            if isinstance(launch_request, AgentDeckAttachRequest):
                execute_agent_deck_attach(launch_request.session_id)
                continue
            if isinstance(launch_request, TmuxSessionAttachRequest):
                execute_tmux_session_attach(launch_request)
            else:
                execute_launch_request(launch_request)
        except (LaunchError, TmuxCommandError, AgentDeckLaunchError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
