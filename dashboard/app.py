"""The Textual App: wires up the theme and starts on the home screen."""

from __future__ import annotations

from textual.app import App

from dashboard.screens.home import HomeScreen


class DevDashboardApp(App[None]):
    """Pablo's personal terminal development dashboard."""

    CSS_PATH = "app.tcss"
    TITLE = "Pablo's Workspace"

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())


def main() -> None:
    """Entry point used by `python -m dashboard` and the `dev` console script."""
    DevDashboardApp().run()


if __name__ == "__main__":
    main()
