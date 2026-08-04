"""Focused distribution checks for the installable Terminal Home package."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_has_runtime_metadata_and_console_scripts() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    project = data["project"]
    assert project["dynamic"] == ["version"]
    assert project["readme"] == "README.md"
    assert project["license"]["file"] == "LICENSE"
    assert project["requires-python"] == ">=3.10"
    assert project["scripts"]["th"] == "dashboard.cli:main"
    assert project["scripts"]["terminal-home"] == "dashboard.cli:main"
    assert project["scripts"]["dev"] == "dashboard.cli:main"


@pytest.mark.skipif(
    importlib.util.find_spec("build.__main__") is None,
    reason="build is provided by the development extra",
)
def test_build_artifacts_and_isolated_wheel_install(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(dist)],
        cwd=ROOT,
        check=True,
    )
    wheels = list(dist.glob("*.whl"))
    sdists = list(dist.glob("*.tar.gz"))
    assert len(wheels) == 1
    assert len(sdists) == 1

    with zipfile.ZipFile(wheels[0]) as wheel:
        names = set(wheel.namelist())
    assert "dashboard/cli.py" in names
    assert "dashboard/app.tcss" in names
    assert any(name.endswith("entry_points.txt") for name in names)

    venv_dir = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    python = venv_dir / "bin" / "python"
    scripts = venv_dir / "bin"
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheels[0])],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert (scripts / "th").exists()
    assert (scripts / "terminal-home").exists()
    env = dict(os.environ)
    env["HOME"] = str(tmp_path / "home")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    env["XDG_DATA_HOME"] = str(tmp_path / "data")
    help_result = subprocess.run(
        [str(scripts / "th"), "--help"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Terminal Home" in help_result.stdout
