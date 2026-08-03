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
    assert settings.theme is None


def test_round_trips_through_dict() -> None:
    settings = AppSettings(
        artwork_enabled=False,
        layout_mode=LayoutMode.COMPACT,
        clock_visible=False,
        theme="nord",
    )
    restored = AppSettings.from_dict(settings.to_dict())
    assert restored == settings


def test_to_dict_uses_plain_json_safe_values() -> None:
    settings = AppSettings(layout_mode=LayoutMode.COMPACT, theme="nord")
    data = settings.to_dict()
    assert data == {
        "artwork_enabled": True,
        "layout_mode": "compact",
        "clock_visible": True,
        "theme": "nord",
    }


def test_from_dict_loads_legacy_data_without_a_theme_key() -> None:
    settings = AppSettings.from_dict(
        {"artwork_enabled": True, "layout_mode": "expanded", "clock_visible": True}
    )
    assert settings == AppSettings()


def test_from_dict_falls_back_to_default_layout_mode_when_invalid() -> None:
    settings = AppSettings.from_dict(
        {"artwork_enabled": True, "layout_mode": "not-a-mode", "clock_visible": True}
    )
    assert settings.layout_mode is LayoutMode.EXPANDED


def test_from_dict_rejects_malformed_theme_value() -> None:
    settings = AppSettings.from_dict(
        {"artwork_enabled": True, "layout_mode": "expanded", "clock_visible": True, "theme": 123}
    )
    assert settings.theme is None


def test_from_dict_malformed_theme_preserves_other_valid_fields() -> None:
    settings = AppSettings.from_dict(
        {
            "artwork_enabled": False,
            "layout_mode": "compact",
            "clock_visible": False,
            "theme": 123,
        }
    )
    assert settings.artwork_enabled is False
    assert settings.layout_mode is LayoutMode.COMPACT
    assert settings.clock_visible is False
    assert settings.theme is None


def test_from_dict_missing_optional_fields_use_defaults() -> None:
    settings = AppSettings.from_dict({})
    assert settings == AppSettings()


def test_from_dict_accepts_valid_theme_string() -> None:
    settings = AppSettings.from_dict(
        {"artwork_enabled": True, "layout_mode": "expanded", "clock_visible": True, "theme": "nord"}
    )
    assert settings.theme == "nord"
