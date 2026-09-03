"""Read-only, terminal-native Git diff view."""

from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from dashboard.services.git import GitDiffResult


def format_diff_text(diff: GitDiffResult) -> Text:
    """Style diff lines without hiding their standard Git markers."""
    rendered = Text()
    if diff.error:
        rendered.append("Diff unavailable\n\n", style="bold #ffb86b")
        rendered.append(diff.error)
        return rendered
    if diff.untracked_content is not None:
        rendered.append("Untracked file\n\n", style="bold #ffb86b")
        rendered.append(diff.untracked_content)
        return rendered
    sections = (("Staged", diff.staged), ("Working Tree", diff.working_tree))
    for title, content in sections:
        if content is None:
            continue
        if rendered:
            rendered.append("\n")
        rendered.append(f"{title}\n", style="bold #72d7ff")
        for line in content.splitlines(keepends=True):
            stripped = line.rstrip("\n")
            if stripped.startswith("@@"):
                style = "bold #c792ea"
            elif stripped.startswith("+") and not stripped.startswith("+++"):
                style = "#98c379"
            elif stripped.startswith("-") and not stripped.startswith("---"):
                style = "#e06c75"
            elif stripped.startswith(("diff ", "index ", "---", "+++")):
                style = "#7f8c98"
            else:
                style = None
            rendered.append(line, style=style)
    return rendered


class DiffScreen(Screen[None]):
    """A scrollable, non-editable view of one Git file's diff."""

    BINDINGS = [("escape", "go_back", "Back")]

    def __init__(self, diff: GitDiffResult) -> None:
        super().__init__()
        self.diff = diff

    def compose(self) -> ComposeResult:
        title = self.diff.path
        if self.diff.old_path is not None:
            title = f"{self.diff.old_path} -> {title}"
        with Container(classes="screen-root diff-root"):
            with VerticalScroll(classes="panel diff-panel"):
                yield Static(title, id="diff-title")
                yield Static(format_diff_text(self.diff), id="diff-content")
        yield Static("Esc Back   q Quit", id="diff-footer")

    def on_mount(self) -> None:
        scroll = self.query_one(".diff-panel", VerticalScroll)
        scroll.scroll_home(animate=False)

    def action_go_back(self) -> None:
        self.app.pop_screen()
