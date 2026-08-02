"""Gathers the facts shown on the System Information screen."""

from __future__ import annotations

import os
import platform
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from dashboard.services.tmux import get_tmux_version


@dataclass(frozen=True, slots=True)
class DiskUsage:
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float


@dataclass(frozen=True, slots=True)
class SystemInfo:
    hostname: str
    operating_system: str
    python_version: str
    shell: str
    tmux_version: str
    disk_usage: DiskUsage | None


def _bytes_to_gb(num_bytes: int) -> float:
    return round(num_bytes / (1024**3), 2)


def get_disk_usage(path: Path | None = None) -> DiskUsage | None:
    """Disk usage for the filesystem containing *path* (default: home dir).

    Returns None if the path doesn't exist or usage can't be determined,
    instead of raising.
    """
    path = path if path is not None else Path.home()
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent_used = round((usage.used / usage.total) * 100, 1) if usage.total else 0.0
    return DiskUsage(
        total_gb=_bytes_to_gb(usage.total),
        used_gb=_bytes_to_gb(usage.used),
        free_gb=_bytes_to_gb(usage.free),
        percent_used=percent_used,
    )


def get_hostname() -> str:
    try:
        return socket.gethostname() or "unknown"
    except OSError:
        return "unknown"


def get_operating_system() -> str:
    return platform.platform() or sys.platform


def get_shell() -> str:
    return os.environ.get("SHELL", "unknown")


def gather_system_info() -> SystemInfo:
    """Collect everything the System Information screen needs in one call."""
    return SystemInfo(
        hostname=get_hostname(),
        operating_system=get_operating_system(),
        python_version=platform.python_version(),
        shell=get_shell(),
        tmux_version=get_tmux_version() or "not installed",
        disk_usage=get_disk_usage(),
    )
