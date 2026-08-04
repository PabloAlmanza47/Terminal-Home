from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceTemplate
from dashboard.services.atomic_file import backup_path_for
from dashboard.services.load_result import LoadSource
from dashboard.services.template_store import (
    TEMPLATE_STORE_SCHEMA_VERSION,
    DuplicateTemplateNameError,
    TemplateStoreVersionError,
    create_template,
    default_template_store_path,
    delete_template,
    find_template_by_name,
    get_template,
    load_all_templates,
    load_templates_result,
    rename_template,
    replace_template_contents,
)


def _template(name: str, *, identity: str | None = None) -> WorkspaceTemplate:
    return WorkspaceTemplate(
        identity or str(uuid4()),
        name,
        (WindowSpec("code", (PaneSpec(PaneKind.BLANK_TERMINAL, "Blank Terminal"),)),),
    )


def test_missing_store_and_xdg_path_with_spaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "data with spaces"
    monkeypatch.setenv("XDG_DATA_HOME", str(root))
    assert default_template_store_path() == root / "terminal-home" / "templates.json"
    assert load_all_templates() == ()
    assert not default_template_store_path().exists()


def test_create_reload_lookup_order_duplicates_and_rename(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    zulu = create_template(_template("zulu"), path)
    alpha = create_template(_template("Alpha"), path)
    assert [item.name for item in load_all_templates(path)] == ["Alpha", "zulu"]
    assert get_template(zulu.id, path) == zulu
    assert find_template_by_name("ALPHA", path) == alpha
    with pytest.raises(DuplicateTemplateNameError):
        create_template(_template("alpha"), path)
    renamed = rename_template(alpha.id, "aLPHA", path)
    assert renamed is not None
    assert renamed.id == alpha.id
    assert renamed.name == "aLPHA"
    assert renamed.windows == alpha.windows


def test_delete_missing_and_replace_contents(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    template = create_template(_template("One"), path)
    replacement = (WindowSpec("shell", (PaneSpec(PaneKind.GIT, "Git"),)),)
    updated = replace_template_contents(template.id, replacement, path)
    assert updated is not None and updated.windows == replacement
    assert delete_template("00000000-0000-0000-0000-000000000000", path) is False
    assert delete_template(template.id, path) is True
    assert load_all_templates(path) == ()


def test_atomic_backup_and_corrupt_primary_recovery(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    template = create_template(_template("One"), path)
    rename_template(template.id, "Two", path)
    backup = backup_path_for(path)
    assert backup.exists()
    path.write_text("not json")
    result = load_templates_result(path)
    assert result.source is LoadSource.BACKUP
    assert result.templates[0].name == "One"
    assert result.warning


def test_corrupt_primary_and_backup_reports_error(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    path.write_text("bad")
    backup_path_for(path).write_text("also bad")
    result = load_templates_result(path)
    assert result.templates == ()
    assert result.error


def test_future_version_is_never_overwritten_or_recovered_from_backup(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    original = json.dumps({"schema_version": TEMPLATE_STORE_SCHEMA_VERSION + 1, "templates": []})
    path.write_text(original)
    backup_path_for(path).write_text(
        json.dumps({"schema_version": TEMPLATE_STORE_SCHEMA_VERSION, "templates": []})
    )
    result = load_templates_result(path)
    assert result.error and result.source is LoadSource.PRIMARY
    with pytest.raises(TemplateStoreVersionError):
        create_template(_template("Nope"), path)
    assert path.read_text() == original


def test_invalid_individual_records_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "templates.json"
    valid = _template("Valid")
    path.write_text(
        json.dumps(
            {
                "schema_version": TEMPLATE_STORE_SCHEMA_VERSION,
                "templates": [{"bad": True}, valid.to_dict()],
            }
        )
    )
    result = load_templates_result(path)
    assert result.templates == (valid,)
    assert "Skipped 1" in (result.warning or "")
