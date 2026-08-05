# Changelog

## [0.2.1] - 2026-08-05

### Added

- Resume tmux Session now exits Textual and attaches or switches to the selected
  session, with a final existence check and clear failure messages.
- Persisted CLI table-header color preference in Settings, including No color
  and automatic plain output for pipes, redirected output, and `NO_COLOR`.

### Fixed

- Shared option-list prompt markers no longer accumulate indentation or flatten
  Rich styling.
- Open Project rows align status and branch columns on wide terminals and use
  deliberate ellipsis/compact layouts on narrow terminals.
- Scrollbars now use one-cell, transparent-track, restrained theme-aware styling.
- Restored responsive Terminal Home artwork, aligned Recent Projects badges, and
  tightened Open Project status-to-branch spacing.
- System Information now uses a wider screen-specific panel and circular
  indicators consistently across independent settings and pane selection.
- Continue Project is grouped by configured state, while Recent Projects keeps
  aligned columns in compact and expanded layouts.
- Appearance settings are now one keyboard-toggleable multi-select group, with
  the same purple focus treatment as the other Settings choice groups.
- Full artwork now places its title beside the icon row and its subtitle below.

## [0.2.0] - 2026-08-04

### Added

- SSH host registration and management, plus remote project registration and management.
- Remote tmux workspace launching with remote-aware `th list`, `th plan`, `th up`, and diagnostics.
- Reusable local workspace templates with import, export, review, rename, and delete flows.
- `th new <project-name>` with interactive and noninteractive creation.
- `--path`, `--root`, local workspace-template selection, Git, and launch controls.
- Bash and Zsh completion for commands, projects, remotes, and templates.
- Project-aware development-server and test-command detection.
- Keyboard shortcut help, keyboard-first terminal action lists, and accessibility tests.

### Changed

- Added the Coding Agent setting with None, Codex, and Claude Code choices.
- Redesigned the TUI with transparent terminal-native styling and restrained focus states.
- Standardized keyboard navigation and shared TUI/CLI project creation behavior.
- Arrow-key navigation now works immediately without requiring Tab in essential workflows.
- Refined the responsive Home layout with compact, content-sized sections.
- Removed the unreliable ASCII artwork in favor of a dependable compact title.
- Updated setuptools license metadata to the SPDX `MIT` form while preserving
  `LICENSE` in distributions.

### Fixed

- Mouse-only interaction paths and inconsistent initial focus.
- Destructive dialogs defaulting to unsafe actions.
- Global shortcuts firing while typing and missing Space activation in option lists.

## [0.1.0]

Initial public release with the Textual dashboard, local and SSH workspace
management, project discovery, templates, and tmux launch orchestration.
