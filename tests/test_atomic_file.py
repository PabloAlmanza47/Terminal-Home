from __future__ import annotations

from pathlib import Path

import pytest

from dashboard.services import atomic_file
from dashboard.services.atomic_file import atomic_write_text, backup_path_for


def _temps(path: Path) -> list[Path]:
    return list(path.parent.glob(".terminal-home-*.tmp"))


def test_new_write_is_atomic_and_has_no_backup_or_temp(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    atomic_write_text(path, "one", preserve_existing=False)
    assert path.read_text() == "one"
    assert not backup_path_for(path).exists()
    assert _temps(path) == []


def test_temp_is_created_in_destination_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "nested" / "settings.json"
    seen: list[object] = []
    original = atomic_file.tempfile.mkstemp

    def recording_mkstemp(*args: object, **kwargs: object) -> tuple[int, str]:
        seen.append(kwargs.get("dir"))
        return original(**kwargs)

    monkeypatch.setattr(atomic_file.tempfile, "mkstemp", recording_mkstemp)
    atomic_write_text(path, "one", preserve_existing=False)
    assert seen == [path.parent]


def test_three_saves_rotate_exact_previous_generation(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    atomic_write_text(path, "one", preserve_existing=False)
    atomic_write_text(path, "two", preserve_existing=True)
    assert backup_path_for(path).read_text() == "one"
    atomic_write_text(path, "three", preserve_existing=True)
    assert path.read_text() == "three"
    assert backup_path_for(path).read_text() == "two"
    assert not backup_path_for(backup_path_for(path)).exists()


def test_temp_write_failure_preserves_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "settings.json"
    path.write_text("primary")
    backup_path_for(path).write_text("backup")
    monkeypatch.setattr(
        atomic_file, "_write_temp", lambda parent, data: (_ for _ in ()).throw(OSError("write"))
    )
    with pytest.raises(OSError, match="write"):
        atomic_write_text(path, "new", preserve_existing=True)
    assert path.read_text() == "primary"
    assert backup_path_for(path).read_text() == "backup"
    assert _temps(path) == []


@pytest.mark.parametrize("failed_destination", ["backup", "primary"])
def test_replace_failure_preserves_primary_and_leaves_usable_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_destination: str
) -> None:
    path = tmp_path / "settings.json"
    backup = backup_path_for(path)
    path.write_text("primary")
    backup.write_text("backup")
    original = atomic_file.os.replace

    def failing_replace(source: Path, destination: Path) -> None:
        target = backup if failed_destination == "backup" else path
        if Path(destination) == target:
            raise OSError("replace")
        original(source, destination)

    monkeypatch.setattr(atomic_file.os, "replace", failing_replace)
    with pytest.raises(OSError, match="replace"):
        atomic_write_text(path, "new", preserve_existing=True)
    assert path.read_text() == "primary"
    assert backup.read_text() in {"backup", "primary"}
    assert _temps(path) == []


def test_invalid_primary_can_be_replaced_without_overwriting_backup(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("invalid")
    backup_path_for(path).write_text("valid backup")
    atomic_write_text(path, "new", preserve_existing=False)
    assert path.read_text() == "new"
    assert backup_path_for(path).read_text() == "valid backup"
