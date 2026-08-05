"""Keyboard-first selection of currently running local tmux sessions."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.widgets.option_list import Option

from dashboard.models import TmuxSessionAttachRequest
from dashboard.services.tmux import is_tmux_installed, list_tmux_sessions
from dashboard.widgets import KeyboardOptionList as OptionList


class TmuxSessionsScreen(Screen[TmuxSessionAttachRequest | None]):
    """List and select a local tmux session; attachment happens after TUI exit."""

    BINDINGS = [("escape", "go_back", "Back")]

    def compose(self) -> ComposeResult:
        with Container(classes="screen-root"):
            with Vertical(classes="panel", id="tmux-panel"):
                yield Static("Resume tmux Session", id="screen-title")
                yield Static(id="tmux-status")
                yield OptionList(id="tmux-list", classes="tmux-session-list")
        yield Footer()

    def on_mount(self) -> None:
        status = self.query_one("#tmux-status", Static)
        option_list = self.query_one("#tmux-list", OptionList)

        if not is_tmux_installed():
            status.update("tmux was not found. Install tmux to resume a session.")
            option_list.display = False
            return

        sessions = list_tmux_sessions()
        if not sessions:
            status.update(
                "No tmux sessions are running. Start one with: tmux new -s <name>"
            )
            option_list.display = False
            return

        status.update(
            f"{len(sessions)} running session(s). Enter or Space resumes the highlighted session."
        )
        narrow = self.size.width < 64
        for session in sessions:
            attached = "attached" if session.attached else "detached"
            if narrow:
                label = f"{session.name}  {session.windows}w  {attached}"
            else:
                label = (
                    f"{session.name:<24} {session.windows:>3} windows  "
                    f"{attached:<8} {session.created}"
                )
            option_list.add_option(Option(label, id=session.name))
        option_list.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and event.option.id:
            self.app.exit(TmuxSessionAttachRequest(str(event.option.id)))

    def action_go_back(self) -> None:
        self.app.pop_screen()
