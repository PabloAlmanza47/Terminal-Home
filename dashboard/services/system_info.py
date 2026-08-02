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
class MemoryUsage:
    total_gb: float
    used_gb: float
    percent_used: float


@dataclass(frozen=True, slots=True)
class SystemInfo:
    hostname: str
    operating_system: str
    python_version: str
    shell: str
    tmux_version: str
    disk_usage: DiskUsage | None
    memory_usage: MemoryUsage | None
    wsl_distro: str | None


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


def get_wsl_distro_name() -> str | None:
    """The WSL distro name (e.g. "Ubuntu") if running under WSL, else None.

    WSL sets WSL_DISTRO_NAME in every interactive shell -- far cheaper and
    more reliable than parsing /proc/version or shelling out to uname.
    """
    return os.environ.get("WSL_DISTRO_NAME") or None


def get_memory_usage(meminfo_path: Path | None = None) -> MemoryUsage | None:
    """Memory usage parsed from /proc/meminfo.

    Linux/WSL only, and only ever a best-effort read -- there's no
    portable stdlib API for this across platforms without a third-party
    dependency, so any other platform, or a missing/malformed file,
    returns None rather than guessing.
    """
    meminfo_path = meminfo_path if meminfo_path is not None else Path("/proc/meminfo")
    try:
        lines = meminfo_path.read_text().splitlines()
    except OSError:
        return None

    values: dict[str, int] = {}
    for line in lines:
        key, _, rest = line.partition(":")
        if key not in ("MemTotal", "MemAvailable"):
            continue
        digits = rest.strip().removesuffix("kB").strip()
        if digits.isdigit():
            values[key] = int(digits)

    total_kb = values.get("MemTotal")
    available_kb = values.get("MemAvailable")
    if not total_kb or available_kb is None:
        return None

    used_kb = max(total_kb - available_kb, 0)
    return MemoryUsage(
        total_gb=_bytes_to_gb(total_kb * 1024),
        used_gb=_bytes_to_gb(used_kb * 1024),
        percent_used=round((used_kb / total_kb) * 100, 1),
    )


def gather_system_info() -> SystemInfo:
    """Collect everything the System Information screen needs in one call."""
    return SystemInfo(
        hostname=get_hostname(),
        operating_system=get_operating_system(),
        python_version=platform.python_version(),
        shell=get_shell(),
        tmux_version=get_tmux_version() or "not installed",
        disk_usage=get_disk_usage(),
        memory_usage=get_memory_usage(),
        wsl_distro=get_wsl_distro_name(),
    )
