"""Tests for pane layout rules (dashboard.models.layout)."""

from __future__ import annotations

import pytest

from dashboard.models.layout import render_pane_preview, tmux_layout_for_pane_count


@pytest.mark.parametrize(
    ("pane_count", "expected_layout"),
    [
        (1, None),
        (2, "even-horizontal"),
        (3, "main-vertical"),
        (4, "tiled"),
    ],
)
def test_tmux_layout_for_pane_count(pane_count: int, expected_layout: str | None) -> None:
    assert tmux_layout_for_pane_count(pane_count) == expected_layout


@pytest.mark.parametrize("pane_count", [0, 5, -1])
def test_tmux_layout_for_pane_count_rejects_unsupported_counts(pane_count: int) -> None:
    with pytest.raises(ValueError, match="Unsupported pane count"):
        tmux_layout_for_pane_count(pane_count)


@pytest.mark.parametrize("pane_count", [1, 2, 3, 4])
def test_render_pane_preview_returns_uniform_width_rows(pane_count: int) -> None:
    labels = [f"pane-{i}" for i in range(pane_count)]
    lines = render_pane_preview(labels, width=40, height=10)

    assert len(lines) == 10
    assert all(len(line) == 40 for line in lines)


def test_render_pane_preview_includes_every_label() -> None:
    labels = ["editor", "claude", "git"]
    text = "\n".join(render_pane_preview(labels, width=42, height=11))

    for label in labels:
        assert label in text


def test_render_pane_preview_two_panes_are_side_by_side() -> None:
    lines = render_pane_preview(["left", "right"], width=20, height=6)
    middle_row = lines[2]
    left_half = middle_row[:10]
    right_half = middle_row[10:]
    assert "left" in left_half
    assert "right" in right_half


def test_render_pane_preview_rejects_unsupported_pane_count() -> None:
    with pytest.raises(ValueError, match="Unsupported pane count"):
        render_pane_preview(["a", "b", "c", "d", "e"])
