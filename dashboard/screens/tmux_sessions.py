"""Keyboard-first selection of currently running local tmux sessions."""

from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static
from textual.widgets.option_list import Option

from dashboard.models import TmuxSessionAttachRequest
from dashboard.services.tmux import TmuxSession, is_tmux_installed, list_tmux_sessions
from dashboard.widgets import KeyboardOptionList as OptionList


def _ellipsis(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    return value[: max(0, width - 1)] + "…"


def format_tmux_session_row(session: TmuxSession, width: int) -> str:
    """Format one session row to fit the available list content width."""
    width = max(12, width)
    state = "attached" if session.attached else "detached"
    if width < 40:
        return _ellipsis(f"{session.name}  {session.windows}w  {state}", width)
    name_width = max(8, width - 34)
    row = (
        f"{_ellipsis(session.name, name_width):<{name_width}}  "
        f"{session.windows:>2} windows  {state:<8}"
    )
    if session.created and width >= 62:
        remaining = width - len(row) - 2
        if remaining > 0:
            row += f"  {_ellipsis(session.created, remaining)}"
    return _ellipsis(row, width)


class TmuxSessionsScreen(Screen[TmuxSessionAttachRequest | None]):
    """List and select a local tmux session; attachment happens after TUI exit."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self.sessions: list[TmuxSession] = []

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

        self.sessions = list_tmux_sessions()
        if not self.sessions:
            status.update("No tmux sessions are running. Start one with: tmux new -s <name>")
            option_list.display = False
            return

        status.update(
            f"{len(self.sessions)} running session(s). Enter or Space resumes "
            "the highlighted session."
        )
        self._populate_rows()
        option_list.focus()

    def _populate_rows(self) -> None:
        option_list = self.query_one("#tmux-list", OptionList)
        selected = option_list.highlighted
        option_list.clear_options()
        width = max(12, option_list.content_region.width or self.size.width - 12)
        for session in self.sessions:
            option_list.add_option(Option(format_tmux_session_row(session, width), id=session.name))
        if selected is not None and selected < option_list.option_count:
            option_list.highlighted = selected

    def on_resize(self, event: events.Resize) -> None:
        if self.sessions:
            self._populate_rows()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option and event.option.id:
            self.app.exit(TmuxSessionAttachRequest(str(event.option.id)))

    def action_go_back(self) -> None:
        self.app.pop_screen()
