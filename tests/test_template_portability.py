from __future__ import annotations

import json
from pathlib import Path

import pytest

import dashboard.services.template_portability as portability_module
from dashboard.models import PaneKind, PaneSpec, WindowSpec, WorkspaceTemplate
from dashboard.services.atomic_file import backup_path_for
from dashboard.services.template_portability import (
    MAX_IMPORT_BYTES,
    PORTABLE_TEMPLATE_FORMAT,
    PORTABLE_TEMPLATE_SCHEMA_VERSION,
    ExportDestinationExistsError,
    ExportPathError,
    ImportPathError,
    ImportSourceChangedError,
    ImportTooLargeError,
    PortableEncodingError,
    PortableFormatError,
    PortableJsonError,
    PortableSchemaError,
    PortableTemplateValidationError,
    UnsupportedPortableSchemaError,
    build_portable_envelope,
    construct_imported_template,
    export_template,
    load_portable_template,
    parse_portable_template,
    resolve_user_path,
    safe_default_export_filename,
    serialize_portable_template,
    verify_import_source_unchanged,
)
from dashboard.services.template_store import create_template, default_template_store_path


def _template(name: str = "Full Stack") -> WorkspaceTemplate:
    return WorkspaceTemplate(
        "00000000-0000-0000-0000-000000000001",
        name,
        (
            WindowSpec(
                "code",
                (
                    PaneSpec(PaneKind.DEV_SERVER, "Development Server"),
                    PaneSpec(PaneKind.CUSTOM_COMMAND, "Docs", "  printf '$HOME;$(whoami)'  "),
                ),
            ),
            WindowSpec("tests", (PaneSpec(PaneKind.TEST_TERMINAL, "Test Terminal"),)),
        ),
    )


def _serialized_data() -> dict[str, object]:
    return json.loads(serialize_portable_template(_template()))


def test_deterministic_envelope_excludes_local_and_runtime_identity() -> None:
    template = _template()
    first = serialize_portable_template(template)
    second = serialize_portable_template(template)
    envelope = build_portable_envelope(template)
    assert first == second
    assert first.endswith("\n")
    assert envelope["format"] == PORTABLE_TEMPLATE_FORMAT
    assert envelope["schema_version"] == PORTABLE_TEMPLATE_SCHEMA_VERSION
    text = json.dumps(envelope)
    for excluded in (
        template.id,
        "project_name",
        "project_path",
        "session_name",
        "launch_action",
        "git_branch",
        "detected_command",
    ):
        assert excluded not in text


def test_parse_preserves_order_intent_and_exact_custom_command() -> None:
    portable = parse_portable_template(serialize_portable_template(_template()))
    assert [window.window_name for window in portable.windows] == ["code", "tests"]
    assert [pane.kind for pane in portable.windows[0].panes] == [
        PaneKind.DEV_SERVER,
        PaneKind.CUSTOM_COMMAND,
    ]
    assert portable.windows[0].panes[1].custom_command == "  printf '$HOME;$(whoami)'  "
    assert portable.windows[1].panes[0].kind is PaneKind.TEST_TERMINAL
    assert portable.windows[0].panes[0].custom_command is None


def test_each_import_construction_gets_fresh_local_uuid() -> None:
    portable = parse_portable_template(serialize_portable_template(_template()))
    first = construct_imported_template(portable)
    second = construct_imported_template(portable)
    assert first.id != second.id
    assert first.windows == second.windows == portable.windows


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda data: data.pop("format"), PortableFormatError),
        (lambda data: data.update(format="other"), PortableFormatError),
        (lambda data: data.pop("schema_version"), PortableFormatError),
        (lambda data: data.update(schema_version=True), PortableSchemaError),
        (lambda data: data.update(schema_version="1"), PortableSchemaError),
        (lambda data: data.update(schema_version=0), PortableSchemaError),
        (
            lambda data: data.update(schema_version=PORTABLE_TEMPLATE_SCHEMA_VERSION + 1),
            UnsupportedPortableSchemaError,
        ),
        (lambda data: data.pop("template"), PortableFormatError),
        (lambda data: data.update(template=[]), PortableFormatError),
    ],
)
def test_invalid_envelopes_are_rejected(mutation, error: type[Exception]) -> None:
    data = _serialized_data()
    mutation(data)
    with pytest.raises(error):
        parse_portable_template(json.dumps(data))


@pytest.mark.parametrize("value", [[], None, "template"])
def test_non_object_envelope_is_rejected(value: object) -> None:
    with pytest.raises(PortableFormatError):
        parse_portable_template(json.dumps(value))


def test_invalid_json_is_rejected() -> None:
    with pytest.raises(PortableJsonError):
        parse_portable_template("not json")


def test_unknown_kind_empty_windows_and_duplicate_windows_are_rejected() -> None:
    unknown = _serialized_data()
    unknown["template"]["windows"][0]["panes"][0]["kind"] = "unknown"  # type: ignore[index]
    with pytest.raises(PortableTemplateValidationError):
        parse_portable_template(json.dumps(unknown))

    empty = _serialized_data()
    empty["template"]["windows"] = []  # type: ignore[index]
    with pytest.raises(PortableTemplateValidationError):
        parse_portable_template(json.dumps(empty))

    duplicate = _serialized_data()
    duplicate["template"]["windows"][1]["window_name"] = "code"  # type: ignore[index]
    with pytest.raises(PortableTemplateValidationError):
        parse_portable_template(json.dumps(duplicate))


def test_non_custom_panes_cannot_store_commands() -> None:
    data = _serialized_data()
    data["template"]["windows"][0]["panes"][0]["custom_command"] = "npm run dev"  # type: ignore[index]
    with pytest.raises(PortableTemplateValidationError, match="may not store"):
        parse_portable_template(json.dumps(data))


def test_payload_rejects_uuid_and_runtime_fields() -> None:
    for field in ("id", "project_path", "session_name"):
        data = _serialized_data()
        data["template"][field] = "untrusted"  # type: ignore[index]
        with pytest.raises(PortableFormatError, match="unsupported"):
            parse_portable_template(json.dumps(data))


def test_path_resolution_expands_home_and_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert resolve_user_path("~/one.json") == (tmp_path / "home" / "one.json").resolve()
    assert (
        resolve_user_path("nested/two.json", cwd=tmp_path)
        == (tmp_path / "nested" / "two.json").resolve()
    )


def test_import_file_checks_encoding_size_directory_and_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ImportPathError, match="does not exist"):
        load_portable_template(missing)
    with pytest.raises(ImportPathError, match="regular file"):
        load_portable_template(tmp_path)

    invalid_utf8 = tmp_path / "invalid.json"
    invalid_utf8.write_bytes(b"\xff")
    with pytest.raises(PortableEncodingError):
        load_portable_template(invalid_utf8)

    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"x" * (MAX_IMPORT_BYTES + 1))
    with pytest.raises(ImportTooLargeError):
        load_portable_template(oversized)


def test_load_does_not_modify_source_and_detects_change(tmp_path: Path) -> None:
    source = tmp_path / "source.th-template.json"
    source.write_text(serialize_portable_template(_template()), encoding="utf-8")
    before = source.read_bytes()
    loaded = load_portable_template(source)
    assert source.read_bytes() == before
    verify_import_source_unchanged(loaded)
    source.write_text(serialize_portable_template(_template("Changed")), encoding="utf-8")
    with pytest.raises(ImportSourceChangedError):
        verify_import_source_unchanged(loaded)


def test_export_requires_parent_refuses_directory_symlink_and_existing(tmp_path: Path) -> None:
    template = _template()
    with pytest.raises(ExportPathError, match="parent"):
        export_template(template, tmp_path / "missing" / "out.json")
    with pytest.raises(ExportPathError, match="directory"):
        export_template(template, tmp_path)
    target = tmp_path / "existing.json"
    target.write_text("keep")
    with pytest.raises(ExportDestinationExistsError):
        export_template(template, target)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(ExportPathError, match="symbolic link"):
        export_template(template, link)


def test_export_is_atomic_newline_terminated_and_preserves_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    template = create_template(_template())
    store_path = default_template_store_path()
    store_before = store_path.read_bytes()
    target = tmp_path / "portable.th-template.json"
    resolved = export_template(template, target)
    assert resolved == target.resolve()
    assert target.read_bytes().endswith(b"\n")
    assert store_path.read_bytes() == store_before
    target.write_text("previous")
    export_template(template, target, overwrite=True)
    assert backup_path_for(target).read_text() == "previous"
    assert store_path.read_bytes() == store_before


def test_safe_default_filename() -> None:
    assert safe_default_export_filename("Full Stack Development") == (
        "full-stack-development.th-template.json"
    )


def test_export_write_error_is_user_facing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_write(*args, **kwargs) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(portability_module, "atomic_write_text", fail_write)
    with pytest.raises(ExportPathError, match="denied"):
        export_template(_template(), tmp_path / "out.json")
