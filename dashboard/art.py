"""Small presentation helpers shared by the home screen: ASCII art and a
"fullwidth text" trick that makes a title read as noticeably larger in a
terminal without needing an external figlet-style font dependency.
"""

from __future__ import annotations

# A compact, tasteful terminal/workspace glyph. Kept small so it never
# overwhelms the title or the menu on a modest-sized terminal window.
ASCII_ART = r"""
        ╭───────────────────────────╮
        │  >_                       │
        │      code · tmux · ssh    │
        ╰───────────────────────────╯
              ╰───────────────╯
""".strip("\n")


def to_wide_text(text: str) -> str:
    """Convert ASCII letters/digits/punctuation to their fullwidth Unicode
    equivalents, e.g. "A" -> "Ａ". Rendered in a terminal, fullwidth glyphs
    take up roughly twice the horizontal space of normal ones, which gives
    a short title a "large heading" feel using only styling, no extra fonts.
    """
    chars: list[str] = []
    for char in text:
        if char == " ":
            chars.append("　")  # ideographic space, matches fullwidth width
        elif "!" <= char <= "~":
            chars.append(chr(ord(char) + 0xFEE0))
        else:
            chars.append(char)
    return "".join(chars)
