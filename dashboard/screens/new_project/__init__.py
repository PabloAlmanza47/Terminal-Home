"""The New Project wizard: a 5-step Textual flow that gathers a project
name, folder name, and one or more tmux windows (each with 1-4 ordered
panes), then creates the project directory and hands a structured
LaunchRequest to the non-Textual tmux orchestration layer.

Steps: Project Info -> Configure Window -> Layout Preview -> Windows
Summary -> Review. Steps 2 and 3 repeat for each additional window. All
five steps share one WizardState instance, so navigating back and forth
never loses data, and nothing is written to disk until Review's
"Create and Open" is confirmed.
"""

from __future__ import annotations

from dashboard.screens.new_project.step_project_info import NewProjectScreen

__all__ = ["NewProjectScreen"]
