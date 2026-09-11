# Changelog

## [Unreleased]

## [0.4.0] - 2026-09-11

### Added

- Added the tmux-native `th switch` quick switcher with active/recent grouping,
  project/path/session search, duplicate-name disambiguation, and
  `switch-client` handoff for running workspaces.
- Added independent persistence for learned tmux pane geometry, with lifecycle
  checkpoints and reuse when recreating saved workspaces.
- Added optional Agent Deck integration: live agent status on project screens,
  project-scoped attach actions, the `th agent` command, and safe filtering of
  Agent Deck-owned tmux sessions from Terminal Home navigation.
- Added read-only Git status and per-file staged/working-tree diff views,
  including safe previews for untracked text files and graceful handling of
  missing, binary, oversized, or unavailable data.
- Added the workspace `prefix + g` Lazygit popup, launched in the focused
  pane's directory with a graceful missing-tool fallback.

### Changed

- Home and Project Detail navigation now support fast keyboard search,
  collision-safe project selection, live activity summaries, and clearer Git,
  workspace, and session state.
- Workspace launch now rechecks session state after the TUI exits: running
  sessions are attached or switched to without recreation, stopped saved
  workspaces are recreated, and existing sessions are never overwritten.
- Workspace persistence now uses location-aware schema 2 records, while
  legacy flat and schema 1 stores remain readable and migrate only after a
  successful save or forget operation.
- CLI lifecycle commands now share the dashboard's project-selection and
  workspace-launch rules, including `th switch`, `th status`, `th agent`, and
  noninteractive project creation paths.

### Fixed

- Fixed layout capture across attach, detach, and in-tmux client-switch flows,
  including safe behavior when sessions disappear or tmux reports malformed
  data.
- Fixed Lazygit popup installation for existing Terminal Home sessions by
  migrating older managed sessions that lack the popup marker; failed optional
  binding installation no longer blocks workspace launch.
- Fixed Git status and diff parsing for staged/unstaged changes, renames,
  conflicts, deleted files, untracked files, detached HEADs, and command
  failures without making Git state load-bearing.
- Fixed project and session navigation so Agent Deck sessions, orphan tmux
  sessions, duplicate project names, and disappearing sessions are handled
  without attaching to or recreating the wrong workspace.

### Security

- Kept Git inspection and diff previews read-only, bounded, timeout-limited,
  and safe for untracked/binary content; Agent Deck and optional tmux popup
  integrations remain best-effort and do not make external state changes
  beyond their explicit attach or popup actions.

## [0.3.1] - 2026-08-05

### Fixed

- Fixed Python 3.10 clean-wheel CLI startup by importing project intelligence
  only when project-specific doctor or setup functionality is used.
- Lightweight help and version commands no longer eagerly import optional
  runtime modules.

## [0.3.0] - 2026-08-05

### Added

- Read-only local project intelligence and project-specific `th doctor <project>` diagnostics.
- Typed, evidence-backed setup plans and safe guided execution through `th setup <project>`.
- `th setup <project> --dry-run` with exact argv, working-directory, evidence, and risk reporting.
- Node.js, Next.js, Prisma, Python, .NET, environment-example, malformed-file,
  bounded-file, and unknown-project analysis.
- Bash and Zsh project-selector completion for `doctor` and `setup`.

### Changed

- Setup now follows Detect → Explain → Approve → Execute with default-No
  per-action and final approvals.
- Package-manager setup output clearly warns that lifecycle/build hooks may run.
- Project intelligence, setup planning, and setup execution remain local-only;
  remote selectors are rejected without SSH activity.

### Fixed

- Environment-file copies revalidate project-root containment and reject
  symlinked sources and destinations, including broken destination symlinks.
- Manifest inspection uses bounded reads and deterministic, malformed-input-safe
  parsing without speculative actions.

### Security

- Setup never automates migrations, seeds, database pushes, global or runtime
  installs, Git changes, secret writing, environment-file overwrites, or
  arbitrary project scripts.

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
