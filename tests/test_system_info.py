"""Tests for system info gathering (dashboard.services.system_info)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.services import system_info as system_info_module
from dashboard.services.system_info import (
    gather_system_info,
    get_disk_usage,
    get_memory_usage,
    get_shell,
    get_wsl_distro_name,
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


# --- get_wsl_distro_name ---------------------------------------------------------


def test_get_wsl_distro_name_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert get_wsl_distro_name() == "Ubuntu"


def test_get_wsl_distro_name_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    assert get_wsl_distro_name() is None


# --- get_memory_usage -------------------------------------------------------------


def test_get_memory_usage_parses_meminfo(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        1000000 kB\nMemAvailable:     400000 kB\n")

    usage = get_memory_usage(meminfo)

    assert usage is not None
    assert usage.total_gb > 0
    assert usage.used_gb > 0
    assert 0 <= usage.percent_used <= 100


def test_get_memory_usage_missing_file_returns_none(tmp_path: Path) -> None:
    assert get_memory_usage(tmp_path / "does-not-exist") is None


def test_get_memory_usage_malformed_file_returns_none(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("not the format we expect at all")

    assert get_memory_usage(meminfo) is None


def test_get_memory_usage_missing_mem_available_returns_none(tmp_path: Path) -> None:
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:        1000000 kB\n")

    assert get_memory_usage(meminfo) is None


def test_gather_system_info_includes_wsl_and_memory_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system_info_module, "get_wsl_distro_name", lambda: "Ubuntu")
    monkeypatch.setattr(system_info_module, "get_memory_usage", lambda: None)

    info = gather_system_info()

    assert info.wsl_distro == "Ubuntu"
    assert info.memory_usage is None
