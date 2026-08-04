from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import pytest

from dashboard.models import (
    MAX_TEMPLATE_NAME_LENGTH,
    LocalProjectLocation,
    PaneKind,
    PaneSpec,
    SshProjectLocation,
    TemplateValidationError,
    WindowSpec,
    WorkspaceSpec,
    WorkspaceTemplate,
    template_from_workspace,
    workspace_from_template,
)


def _windows() -> tuple[WindowSpec, ...]:
    return (
        WindowSpec(
            "code",
            (
                PaneSpec(PaneKind.DEV_SERVER, "Development Server"),
                PaneSpec(PaneKind.TEST_TERMINAL, "Test Terminal"),
                PaneSpec(PaneKind.CUSTOM_COMMAND, "Docs", "  mkdocs serve  "),
            ),
        ),
    )


def test_template_is_valid_trimmed_and_immutable() -> None:
    template = WorkspaceTemplate(str(uuid4()), "  Full Stack  ", _windows())
    assert template.name == "Full Stack"
    with pytest.raises(FrozenInstanceError):
        template.name = "Other"  # type: ignore[misc]


@pytest.mark.parametrize("name", ["", "   ", "bad\nname", "bad\tname", "bad\x00name"])
def test_template_rejects_invalid_names(name: str) -> None:
    with pytest.raises(TemplateValidationError):
        WorkspaceTemplate(str(uuid4()), name, _windows())


def test_template_name_maximum() -> None:
    assert WorkspaceTemplate(str(uuid4()), "x" * MAX_TEMPLATE_NAME_LENGTH, _windows())
    with pytest.raises(TemplateValidationError):
        WorkspaceTemplate(str(uuid4()), "x" * (MAX_TEMPLATE_NAME_LENGTH + 1), _windows())


def test_template_rejects_empty_windows_and_invalid_id() -> None:
    with pytest.raises(TemplateValidationError):
        WorkspaceTemplate(str(uuid4()), "Empty", ())
    with pytest.raises(TemplateValidationError):
        WorkspaceTemplate("name-derived-id", "Name", _windows())


def test_workspace_template_conversions_drop_source_identity_and_preserve_intent(
    tmp_path: Path,
) -> None:
    source = WorkspaceSpec(
        "source",
        SshProjectLocation("c27c7b67-8e3f-4ebc-8dce-d66be8fd1ea3", "/srv/source"),
        "source-session",
        _windows(),
    )
    template = template_from_workspace(source, "Full Stack")
    first = workspace_from_template(
        template,
        project_name="destination",
        project_location=LocalProjectLocation((tmp_path / "destination").resolve()),
        session_name="destination-a1b2c3",
    )
    second = workspace_from_template(
        template,
        project_name="another",
        project_location=SshProjectLocation("d84aeefb-7c29-4c63-b39c-766d559df977", "/srv/another"),
        session_name="another",
    )
    assert template.to_dict().keys() == {"id", "name", "windows"}
    assert first.project_location != source.project_location
    assert "project_location" not in template.to_dict()
    assert first.session_name != source.session_name
    assert first.windows == source.windows == second.windows
    assert first.windows is not template.windows
    assert first.windows is not second.windows
    assert first.windows[0].panes[-1].custom_command == "  mkdocs serve  "
    assert [pane.kind for pane in first.windows[0].panes[:2]] == [
        PaneKind.DEV_SERVER,
        PaneKind.TEST_TERMINAL,
    ]
    (SshProjectLocation,)
