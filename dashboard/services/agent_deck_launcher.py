"""Post-Textual Agent Deck attach execution."""

from __future__ import annotations

import subprocess

from dashboard.services.agent_deck import attach_argv


class AgentDeckLaunchError(Exception):
    pass


def execute_agent_deck_attach(session_id: str) -> None:
    try:
        result = subprocess.run(attach_argv(session_id), stdin=None, stdout=None, stderr=None)
    except OSError as exc:
        raise AgentDeckLaunchError(f"Could not start Agent Deck: {exc}") from exc
    if result.returncode != 0:
        raise AgentDeckLaunchError(
            f"Agent Deck attach exited with status {result.returncode}."
        )
