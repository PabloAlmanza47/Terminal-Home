"""Pure pane-layout rules, shared by the wizard's live preview, the final
review screen, and the tmux orchestration layer -- one source of truth for
"what does an N-pane window look like" used by three different consumers.
"""

from __future__ import annotations

# tmux built-in layout name for each supported pane count. One pane needs no
# layout at all (nothing to arrange).
_TMUX_LAYOUTS: dict[int, str | None] = {
    1: None,
    2: "even-horizontal",
    3: "main-vertical",
    4: "tiled",
}

MAX_SUPPORTED_PANES = 4


def tmux_layout_for_pane_count(pane_count: int) -> str | None:
    """The tmux `select-layout` argument for *pane_count* panes.

    Returns None for a single pane, since a lone pane fills the window and
    no layout needs to be applied.
    """
    if pane_count not in _TMUX_LAYOUTS:
        raise ValueError(
            f"Unsupported pane count: {pane_count} (must be 1-{MAX_SUPPORTED_PANES})"
        )
    return _TMUX_LAYOUTS[pane_count]


# (left, top, width, height) as fractions of the window, one box per pane,
# in pane order -- used only to render the ASCII preview below.
_BOX_FRACTIONS: dict[int, list[tuple[float, float, float, float]]] = {
    1: [(0.0, 0.0, 1.0, 1.0)],
    2: [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 1.0)],
    3: [(0.0, 0.0, 0.5, 1.0), (0.5, 0.0, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
    4: [(0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 0.5, 0.5), (0.0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)],
}


def render_pane_preview(pane_labels: list[str], width: int = 42, height: int = 11) -> list[str]:
    """Render a compact ASCII box diagram of how *pane_labels* will be laid
    out, in order, following the same rules as tmux_layout_for_pane_count.

    Returns a list of equal-length text lines (no trailing newline).
    """
    count = len(pane_labels)
    if count not in _BOX_FRACTIONS:
        raise ValueError(f"Unsupported pane count: {count} (must be 1-{MAX_SUPPORTED_PANES})")

    grid = [[" "] * width for _ in range(height)]

    for index, (left_f, top_f, width_f, height_f) in enumerate(_BOX_FRACTIONS[count]):
        left = round(left_f * width)
        top = round(top_f * height)
        right = round((left_f + width_f) * width) - 1
        bottom = round((top_f + height_f) * height) - 1
        right = max(right, left + 2)
        bottom = max(bottom, top + 2)
        right = min(right, width - 1)
        bottom = min(bottom, height - 1)

        for x in range(left, right + 1):
            grid[top][x] = "-"
            grid[bottom][x] = "-"
        for y in range(top, bottom + 1):
            grid[y][left] = "|"
            grid[y][right] = "|"
        grid[top][left] = "+"
        grid[top][right] = "+"
        grid[bottom][left] = "+"
        grid[bottom][right] = "+"

        label = pane_labels[index]
        inner_width = right - left - 1
        if inner_width > 0:
            text = label if len(label) <= inner_width else label[: max(inner_width - 1, 1)] + "…"
            text = text[:inner_width]
            row = top + (bottom - top) // 2
            col = left + 1 + max((inner_width - len(text)) // 2, 0)
            for offset, char in enumerate(text):
                if left + 1 <= col + offset <= right - 1:
                    grid[row][col + offset] = char

    return ["".join(row) for row in grid]
