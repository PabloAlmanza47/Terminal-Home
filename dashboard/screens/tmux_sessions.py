"""Resume tmux Session screen.

Version 1 only *displays* sessions -- attaching is intentionally left for a
future version, since Textual's own terminal takes over stdout and can't
hand control to `tmux attach` without exiting first.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, OptionList, Static
from textual.widgets.option_list import Option

from dashboard.services.tmux import is_tmux_installed, list_tmux_sessions


class TmuxSessionsScreen(Screen[None]):
    """Read-only list of currently running tmux sessions."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel"):
                yield Static("Resume tmux Session", id="screen-title")
                yield Static(id="tmux-status")
                yield OptionList(id="tmux-list")
        yield Footer()

    def on_mount(self) -> None:
        status = self.query_one("#tmux-status", Static)
        option_list = self.query_one("#tmux-list", OptionList)

        if not is_tmux_installed():
            status.update("tmux was not found on this system.")
            return

        sessions = list_tmux_sessions()
        if not sessions:
            status.update(
                "No tmux sessions are running.\nStart one from a terminal with: tmux new -s <name>"
            )
            return

        status.update(
            f"{len(sessions)} session(s) found. "
            "(Attaching from here arrives in a future version -- for now, "
            "note the name and attach with `tmux attach -t <name>`.)"
        )
        for session in sessions:
            attached = "attached" if session.attached else "detached"
            option_list.add_option(
                Option(
                    f"{session.name}  --  {session.windows} window(s), {attached}, "
                    f"created {session.created}",
                    id=session.name,
                )
            )
        option_list.focus()

    def action_go_back(self) -> None:
        self.app.pop_screen()
