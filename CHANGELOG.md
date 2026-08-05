# Changelog

## [0.2.0] - 2026-08-04

### Added

- `th new <project-name>` with interactive and noninteractive creation.
- `--path`, `--root`, local workspace-template selection, Git, and launch controls.
- Keyboard shortcut help and keyboard-only accessibility tests.

### Changed

- Redesigned the TUI with a transparent terminal-native visual system and restrained focus states.
- Standardized keyboard navigation and shared TUI/CLI project creation behavior.
- Improved the home screen at small terminal sizes and added contextual shortcuts.
- Refined split-screen layout with a compact Terminal Home title and content-sized sections.
- Standardized arrow-key navigation, added the Coding Agent preference, and stabilized CI.
- Updated setuptools license metadata while preserving LICENSE in distributions.

### Fixed

- Mouse-only interaction paths and inconsistent initial focus.
- Destructive dialogs defaulting to unsafe actions.
- Global shortcuts firing while typing and missing Space activation in option lists.

## [0.1.0]

Initial public release with the Textual dashboard, local and SSH workspace
management, project discovery, templates, and tmux launch orchestration.
