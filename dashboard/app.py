"""The Textual App: wires up the theme and starts on the home screen.

main() runs the Textual app to completion, then -- only after Textual has
fully released the terminal -- hands any resulting LaunchRequest to the
non-Textual tmux orchestration layer. tmux/attach must never run while a
Textual screen is still mounted.
"""

from __future__ import annotations

import sys

from textual.app import App

from dashboard.models import LaunchRequest
from dashboard.screens.home import HomeScreen
from dashboard.services.tmux import TmuxCommandError
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request


class DevDashboardApp(App[LaunchRequest | None]):
    """Pablo's personal terminal development dashboard."""

    CSS_PATH = "app.tcss"
    TITLE = "Pablo's Workspace"

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())


def main() -> None:
    """Entry point used by `python -m dashboard` and the `dev` console script."""
    app = DevDashboardApp()
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
