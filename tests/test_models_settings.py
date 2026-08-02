"""Tests for the home screen presentation preferences model
(dashboard.models.settings).
"""

from __future__ import annotations

from dashboard.models.settings import AppSettings, LayoutMode


def test_defaults() -> None:
    settings = AppSettings()
    assert settings.artwork_enabled is True
    assert settings.layout_mode is LayoutMode.EXPANDED
    assert settings.clock_visible is True


def test_round_trips_through_dict() -> None:
    settings = AppSettings(
        artwork_enabled=False, layout_mode=LayoutMode.COMPACT, clock_visible=False
    )
    restored = AppSettings.from_dict(settings.to_dict())
    assert restored == settings


def test_to_dict_uses_plain_json_safe_values() -> None:
    settings = AppSettings(layout_mode=LayoutMode.COMPACT)
    data = settings.to_dict()
    assert data == {
        "artwork_enabled": True,
        "layout_mode": "compact",
        "clock_visible": True,
    }


def test_from_dict_rejects_invalid_layout_mode() -> None:
    import pytest

    with pytest.raises(ValueError):
        AppSettings.from_dict(
            {"artwork_enabled": True, "layout_mode": "not-a-mode", "clock_visible": True}
        )
