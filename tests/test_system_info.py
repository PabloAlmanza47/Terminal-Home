"""Tests for system info gathering (dashboard.services.system_info)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.services import system_info as system_info_module
from dashboard.services.system_info import (
    gather_system_info,
    get_disk_usage,
    get_shell,
)


def test_get_disk_usage_for_existing_path(tmp_path: Path) -> None:
    usage = get_disk_usage(tmp_path)

    assert usage is not None
    assert usage.total_gb > 0
    assert usage.free_gb >= 0
    assert 0 <= usage.percent_used <= 100


def test_get_disk_usage_for_missing_path_returns_none(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist" / "nested"

    assert get_disk_usage(missing) is None


def test_get_shell_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SHELL", "/usr/bin/zsh")
    assert get_shell() == "/usr/bin/zsh"


def test_get_shell_defaults_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SHELL", raising=False)
    assert get_shell() == "unknown"


def test_gather_system_info_populates_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_info_module, "get_tmux_version", lambda: "tmux 3.4")

    info = gather_system_info()

    assert info.hostname
    assert info.operating_system
    assert info.python_version
    assert info.shell
    assert info.tmux_version == "tmux 3.4"


def test_gather_system_info_reports_missing_tmux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(system_info_module, "get_tmux_version", lambda: None)

    info = gather_system_info()

    assert info.tmux_version == "not installed"
