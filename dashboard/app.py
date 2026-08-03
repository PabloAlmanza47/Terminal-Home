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
from textual.theme import Theme

from dashboard.models import LaunchRequest
from dashboard.models.settings import AppSettings
from dashboard.screens.home import HomeScreen
from dashboard.services.settings_store import load_settings, save_settings
from dashboard.services.tmux import TmuxCommandError
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request


class TerminalHomeApp(App[LaunchRequest | None]):
    """Terminal Home: a declarative tmux workspace manager."""

    CSS_PATH = "app.tcss"
    TITLE = "Terminal Home"

    def __init__(self) -> None:
        super().__init__()
        self.settings: AppSettings = load_settings()

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


def main() -> None:
    """Shared entry point for every supported launch method: the
    `terminal-home` and `th` console scripts, the retained `dev` compatibility
    alias, and `python -m dashboard` -- all four resolve to this same
    function, so there is exactly one startup path to keep correct.
    """
    app = TerminalHomeApp()
    launch_request = app.run()

    if launch_request is None:
        return

    try:
        execute_launch_request(launch_request)
    except (LaunchError, TmuxCommandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
