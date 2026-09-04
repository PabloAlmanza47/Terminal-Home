from pathlib import Path

from dashboard.services.activity import (
    agent_display_name,
    agent_status,
    effective_agent_session,
)
from dashboard.services.agent_deck import AgentDeckSession, AgentStatus
from dashboard.services.projects import Project, ProjectStatus
from dashboard.cli import _status_payload, _status_text
from dashboard.screens.home import _activity_card
from dashboard.screens.project_detail import _agent_line, format_activity_block
from dashboard.services.git import GitStatus


def _session(name: str, tool: str, state: AgentStatus) -> AgentDeckSession:
    return AgentDeckSession(name, name, Path("/tmp/project"), tool, state)


def _status(*sessions: AgentDeckSession) -> ProjectStatus:
    return ProjectStatus(
        Project("project", Path("/tmp/project")), Path("/tmp/project"), True, False,
        None, None, None, "project", False, False, None, agent_sessions=sessions,
    )


def test_effective_agent_selection_is_generic_and_priority_ordered() -> None:
    selected, count = effective_agent_session(
        (_session("codex", "codex", AgentStatus.RUNNING),
         _session("claude", "claude", AgentStatus.WAITING))
    )
    assert selected is not None and selected.tool == "claude"
    assert count == 2


def test_equal_priority_selection_is_deterministic() -> None:
    selected, _ = effective_agent_session(
        (_session("z", "codex", AgentStatus.RUNNING),
         _session("a", "claude", AgentStatus.RUNNING))
    )
    assert selected is not None and selected.tool == "claude"


def test_agent_display_and_status_cover_unknown_tools_and_no_session() -> None:
    assert agent_display_name("foo-agent") == "Foo Agent"
    value, count, selected = agent_status(_status(_session("x", "foo-agent", AgentStatus.IDLE)))
    assert (value.glyph, value.label, count, selected.tool) == ("○", "Idle", 1, "foo-agent")
    value, count, selected = agent_status(_status())
    assert (value.label, count, selected) == ("No Agent", 0, None)


def test_all_presentations_use_selected_claude_session() -> None:
    status = _status(_session("claude", "claude", AgentStatus.WAITING))
    assert "Claude" in _activity_card(status)
    assert "Codex" not in _activity_card(status)
    assert "Claude" in (_agent_line(status) or "")
    assert "Claude" in format_activity_block(status)
    assert "Agent      Claude Waiting" in _status_text(status, GitStatus(False, None, False))
    assert _status_payload(status, GitStatus(False, None, False))["agent"] == {
        "count": 1, "status": "waiting", "tool": "claude"
    }
