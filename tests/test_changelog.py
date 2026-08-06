from pathlib import Path


def test_changelog_documents_current_release() -> None:
    changelog = Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8")
    assert "## [0.3.0] - 2026-08-05" in text
    assert "## [0.2.0] - 2026-08-04" in text
    assert "## [0.1.0]" in text
