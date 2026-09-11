# Release notes

## v0.4.0 — Daily-driver workspaces

Terminal Home v0.4.0 turns the project dashboard into a practical daily-driver
workspace: move between active projects quickly, return to familiar pane
geometry, inspect Git changes, and keep coding-agent activity visible without
losing the terminal-native workflow.

### Highlights

- Added the tmux-native `th switch` quick switcher with active/recent grouping,
  search, duplicate-name disambiguation, and `switch-client` handoff for
  running workspaces.
- Added best-effort remembered pane-layout persistence across tmux restarts,
  detach/attach cycles, and workspace recreation.
- Added optional Agent Deck integration with live agent status, project-scoped
  attach actions, the `th agent` command, and safe filtering of Agent Deck
  sessions from Terminal Home navigation.
- Added read-only Git status and per-file staged/working-tree diff views,
  including bounded previews and graceful handling of missing, binary,
  oversized, untracked, and unavailable files.
- Added the workspace `prefix + g` Lazygit popup, opened in the focused pane's
  directory with a graceful fallback when Lazygit is not installed.
- Improved project and session navigation, keyboard search, collision-safe
  selection, lifecycle rechecks, attach/switch behavior, and stopped-workspace
  recreation.

### Safety and compatibility

Git inspection and diff previews remain read-only and bounded. Agent Deck and
the Lazygit popup are optional integrations. Existing workspace stores remain
readable, with schema migration only after a successful save or forget
operation; managed sessions are never overwritten.
