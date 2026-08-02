"""The home screen's compact ASCII logo.

Kept as its own module (rather than inlined in the screen) so it's easy to
swap out independently of the screen's layout logic.
"""

from __future__ import annotations

# A compact, tasteful terminal/workspace glyph. Kept small and plain-ASCII
# so it renders identically (and stays legible) at 80 columns, and never
# overwhelms the title or the dashboard below it.
ASCII_ART = r"""
        ╭───────────────────────────╮
        │  >_                       │
        │      code · tmux · ssh    │
        ╰───────────────────────────╯
              ╰───────────────╯
""".strip("\n")
