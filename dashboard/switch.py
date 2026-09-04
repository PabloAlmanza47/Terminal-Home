"""Standalone Textual entry point for ``th switch``."""

from __future__ import annotations

import sys

from textual.app import App

from dashboard.models import LaunchRequest
from dashboard.screens.quick_switch import QuickSwitchScreen
from dashboard.services.tmux import TmuxCommandError
from dashboard.services.workspace_launcher import LaunchError, execute_launch_request


class QuickSwitchApp(App[LaunchRequest | None]):
    CSS_PATH = "app.tcss"
    TITLE = "Terminal Home Quick Switch"

    def on_mount(self) -> None:
        self.push_screen(QuickSwitchScreen())


def main() -> None:
    request = QuickSwitchApp().run()
    if request is None:
        return
    try:
        execute_launch_request(request)
    except (LaunchError, TmuxCommandError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
