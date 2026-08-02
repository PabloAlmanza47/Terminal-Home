# terminal-home

Pablo's personal terminal development dashboard, built with [Textual](https://textual.textualize.io/).
Version 2 added a full New Project wizard that scaffolds a project directory and launches
a tmux workspace for it. Version 3 (this version) completes the **Open Project** workflow:
selecting an existing directory under `~/projects` now resumes its running tmux session,
recreates its previously saved workspace, or walks through configuring one -- reusing the
same `WorkspaceSpec`/tmux orchestration machinery the New Project wizard already built.

## Requirements

- Python 3.10+
- Linux/WSL with `tmux` installed (required to actually launch a workspace; the dashboard
  itself still runs and degrades gracefully without it)
- Optional, with graceful fallbacks (see "Tool requirements and fallbacks" below):
  `nvim`, `claude` (Claude Code), `lazygit`, `tree`, `git`

## Setup

A virtual environment already exists at `.venv/`. To (re)create it and install the
project in editable mode with dev tools:

```bash
cd ~/projects/terminal-home
python3 -m venv .venv                 # skip if .venv already exists
.venv/bin/pip install -e ".[dev]"
```

## Running the dashboard

```bash
.venv/bin/python -m dashboard
```

Once activated (`source .venv/bin/activate`), you can also run it as `python -m dashboard`,
or as `dev` since the editable install registers a `dev` console script inside the
virtual environment. Wiring `dev` up as a global command (e.g. via a `.bashrc` alias or
PATH entry) is intentionally left for a later version.

## Keyboard controls

- **Arrow keys** -- move through menus and lists
- **Enter** -- select the highlighted item
- **Escape** -- go back one step (or cancel, on the first step of a flow)
- **F5** -- refresh (Open Project's list and Project Detail's status)
- **q** -- quit, from the home screen
- Full shortcut list is always visible in the footer at the bottom of the screen

## Workspace, window, and pane terminology

- A **workspace** is one tmux **session** for a project: a project name, its directory,
  a generated session name, and one or more windows. Modeled by `WorkspaceSpec`.
- A **window** is one tmux window: a name, and 1-4 ordered panes. Modeled by `WindowSpec`.
- A **pane** is one tmux pane: a type (see the catalogue below), a display name, and --
  for custom panes only -- a literal shell command. Modeled by `PaneSpec`. Pane *order*
  within a window is preserved exactly as configured, since it determines left-to-right,
  top-to-bottom placement once a layout is applied.

These models (`dashboard/models/`) have no Textual imports and no subprocess calls --
they're plain, validated dataclasses so they can be unit tested in isolation, and so the
same models can eventually back a "launch an existing project's workspace" flow, not just
the New Project wizard.

## New Project wizard

"Create New Project" on the home menu opens a 5-step wizard. Nothing is written to disk,
and no tmux session is created, until the final step is confirmed.

1. **Project Info** -- project display name, folder name (defaulted from the project name
   via a filesystem-safe slug, e.g. "My Cool Project" -> `my-cool-project`), and whether to
   `git init` the new directory (on by default). The destination (`~/projects/<folder>`) is
   shown live. Validation (empty names, path separators in the folder name, an existing
   directory at the destination, escaping `~/projects`) is reported inline without crashing.
2. **Configure Window** -- a window name (defaults to `main` for the very first window), and
   a checkable list of 1-4 pane types (see below), with Move Up/Move Down controls to set
   their final order. Trying to select a 5th pane is blocked with an inline explanation.
   Choosing "Custom Command" reveals a name + command field and a warning that the command
   will run automatically when the workspace starts (it is never run during the wizard).
3. **Layout Preview** -- a compact ASCII preview of the pane layout that Step 2 will
   produce, with a way back to Step 2 to change the selection or order.
4. **Windows** -- a summary of every configured window so far, with Add Another Window,
   Edit Selected Window, Remove Selected Window (blocked if it's the only one left), Finish
   Workspace, and Cancel. Window names must be unique within a workspace; there's no fixed
   limit on the number of windows.
5. **Review** -- project name, full destination path, git-init choice, the generated tmux
   session name, every window with its ordered panes, and a compact layout preview per
   window. "Create and Open" is the only action that touches the filesystem, git, or tmux.

### Pane types

| Pane            | Preferred command | Fallback                                   |
|-----------------|--------------------|--------------------------------------------|
| Code Editor     | `nvim .`           | interactive shell (Neovim not found)        |
| Claude Code     | `claude`           | interactive shell (Claude Code not found)   |
| Git             | `lazygit`          | `git status` then a shell; or, if the project isn't a git repo yet, a plain shell |
| File Tree       | `tree -C .`        | `find`-based listing, then a shell          |
| Test Terminal   | (none -- a plain shell titled "tests")                                          |
| Development Server | (none -- a plain shell titled "server")                                     |
| Blank Terminal  | (none -- a plain shell)                                                         |
| Custom Command  | the exact command you enter                                                     |

Tool availability is checked at *launch* time (`dashboard/services/pane_commands.py`), not
wizard time, so it always reflects what's actually installed. Every pane's shell starts in
the project's directory regardless of which command (if any) runs in it. Falling back to a
shell is always nonfatal: a short note is printed before the tmux session is attached.

### Pane layout rules

| Panes | Layout                                                              | tmux layout name |
|-------|----------------------------------------------------------------------|------------------|
| 1     | fills the window                                                     | (none)           |
| 2     | equal, side by side (pane 1 left, pane 2 right)                      | `even-horizontal`|
| 3     | pane 1 full-height left; panes 2/3 stacked upper-right/lower-right    | `main-vertical`  |
| 4     | balanced 2x2 grid (upper-left, upper-right, lower-left, lower-right)  | `tiled`          |

`dashboard/models/layout.py` is the single source of truth for these rules -- the Step 3
preview, the Step 5 review, and the real tmux `select-layout` call all derive from it.

## Open Project workflow

"Open Project" on the home menu lists immediate subdirectories of `~/projects` (excluding
`terminal-home` itself, hidden/dot directories, and anything inaccessible), each annotated
with its git branch, whether it has a saved workspace, and whether its tmux session is
currently running. The list is searchable (type to filter), arrow keys move through it,
**F5** rescans everything (projects, saved workspaces, and tmux sessions) without
restarting the dashboard, and Enter opens **Project Detail** for the highlighted project --
nothing is ever launched directly from the list.

Project Detail shows the project's full status and offers only the actions that are safe
given that status:

- **Running** -- a tmux session matching this project is currently up: **Resume Session**
  attaches (or, if run from inside an existing tmux client, switches) to it. No panes or
  windows are recreated, and no duplicate session is ever created.
- **Saved Workspace** -- no session is running, but a `WorkspaceSpec` was saved previously:
  **Recreate Workspace** rebuilds the exact saved session (windows, panes, layouts, startup
  commands) via the same tmux orchestration the New Project wizard uses, then attaches.
- **Not Configured** -- nothing saved yet: **Open Default Workspace** creates and saves a
  simple one-window workspace (`code`: a Code Editor pane and a Blank Terminal pane, side by
  side) and launches it; **Configure Workspace** opens the same window/pane builder the New
  Project wizard uses (window naming, pane selection/ordering, layout preview, review), just
  starting from window configuration instead of project naming, and never creating, renaming,
  or deleting the project directory or running `git init` -- it only saves a `WorkspaceSpec`
  for the directory that's already there.

**Resume** and **Recreate** are actually the same request under the hood
(`LaunchAction.ATTACH`): the tmux orchestration layer re-checks what's actually running right
before acting, regardless of which button was shown. If a session disappears between opening
Project Detail and pressing the button, it's safely recreated from the saved workspace instead
of failing; if a session unexpectedly already exists where a fresh one was about to be
created, the dashboard leaves it alone and reports a clear error rather than ever killing or
overwriting it.

For a project that already has a saved workspace, Project Detail also offers:

- **Edit Workspace** -- reopens the same window/pane builder pre-filled with the saved
  windows and panes. Saving replaces only that project's saved definition; it never touches a
  currently running session for that project directly -- if one is running, a note explains
  that the updated layout takes effect the next time the session is recreated, and Resume
  Session for the live session keeps working exactly as before.
- **Reset to Default Workspace** (confirmation required) -- replaces the saved definition
  with the same simple `code` window described above, keeping the same session name. Doesn't
  touch a running session.
- **Forget Saved Workspace** (confirmation required) -- removes only terminal-home's own
  saved metadata for that project's canonical path. Never touches the project directory, its
  files, its git history, or any tmux session -- afterwards the project is simply treated as
  "Not Configured" again.

### Session identity

Every workspace is keyed by its project's *canonical* (resolved) path, not just its folder
name, since two different parents could otherwise contain identically-named folders. A saved
workspace's tmux session name is decided once (via `dashboard.services.tmux.generate_session_name`'s
collision rules) and then persisted -- it is never re-derived or fuzzy-matched later. A
project with no saved workspace is only ever considered "running" if a session with its exact
deterministic slug (`sanitize_session_name(project_name)`) exists; a similarly-named but
unrelated session is never attached to by mistake.

### After a WSL or tmux restart

Saved workspaces live in the JSON store described below, independent of any tmux server, so
after a restart every project that had a saved workspace still shows "Saved Workspace" and
offers **Recreate Workspace** exactly as before -- nothing needs to be reconfigured.

### Safety guarantees

- Only `dashboard/services/workspace_launcher.py` ever runs a real tmux command, and only
  after Textual has fully exited (`app.run()` returned) -- no screen calls tmux directly.
- A tmux session is never killed or overwritten. Creating a new session always refuses if one
  with that name already exists; attaching always re-checks first.
- Reset and Forget only ever modify terminal-home's own JSON metadata store -- never the
  project directory, its git data, or any tmux session.
- Malformed or corrupt saved-workspace JSON, a saved project path that no longer exists, a
  vanished project directory, missing tmux, or a pane's preferred tool (e.g. `nvim`) no longer
  being installed are all handled with a friendly message or graceful fallback -- never a
  Python traceback.

## Workspace persistence

Confirmed workspaces are saved as JSON under `$XDG_DATA_HOME/terminal-home/workspaces.json`
(falling back to `~/.local/share/terminal-home/workspaces.json` when `XDG_DATA_HOME` isn't
set) -- never inside the project directory itself. Entries are keyed by each project's
canonical (resolved) path, so a workspace can be recreated after its tmux session disappears
or WSL restarts. A missing, corrupt, or partially invalid store is handled without crashing;
`dashboard.services.workspace_store.load_workspace_result` distinguishes "nothing saved" from
"something was saved but it's corrupt" so Project Detail can offer a friendly warning and a
way to forget the bad entry, rather than silently pretending nothing was ever configured.

## Safety and validation behavior

- The wizard never overwrites or merges into an existing directory, and never touches a
  tmux session that already exists under the generated name.
- Nothing is created until "Create and Open" is confirmed on the final review step;
  cancelling at any point (Escape, or a Cancel button) leaves no trace on disk.
- If project/git creation fails partway through, the error is shown inline (no Python
  traceback), and whatever was already created (e.g. the directory, if `git init` then
  failed) is reported rather than silently deleted.
- tmux session/window/pane construction (`dashboard/services/tmux.py`) separates building
  the exact list of tmux commands from running them, so it's unit tested without a real
  tmux server. If a step fails partway through building a *new* session, only that
  in-progress session is cleaned up -- pre-existing sessions and directories are never
  touched.
- The dashboard attaches to the new session with `tmux attach-session` when run outside
  tmux, or `tmux switch-client` when already inside tmux -- and only after Textual has
  fully exited, since Textual and tmux can't both own the terminal at once.

## Project layout

```
dashboard/
    app.py                     Textual App subclass; entry point; runs the tmux
                                 orchestration layer after Textual exits
    app.tcss                    Theme and layout (dark, bordered panels)
    art.py                      ASCII art + the fullwidth-text trick used for the title
    models/                     Plain dataclasses, no Textual imports, no subprocess calls
        workspace.py               PaneKind, PaneSpec, WindowSpec, WorkspaceSpec, LaunchAction, LaunchRequest
        layout.py                   Pane layout rules + ASCII preview rendering
    services/                  Plain Python, no Textual imports -- easy to unit test
        projects.py                Scans ~/projects; ProjectStatus + primary/secondary action matrix
        git_info.py                  Cheap, tolerant git branch/repo lookups
        tmux.py                     Session listing + workspace command construction/execution
        system_info.py              Hostname, OS, Python version, shell, disk usage
        slug.py                      Filesystem-safe slug generation
        project_creation.py          Validation, directory creation, git init (New Project only)
        pane_commands.py             Resolves each pane kind into a launch plan at launch time
        workspace_defaults.py         The simple default WorkspaceSpec (Open Default/Reset)
        workspace_store.py           JSON persistence under XDG_DATA_HOME; load/forget by canonical path
        workspace_launcher.py         Non-Textual orchestration: LaunchRequest -> running tmux
    screens/                    One module per screen
        home.py
        projects.py                Open Project: searchable, status-annotated project list
        project_detail.py           Resume/Recreate/Default/Configure/Edit/Reset/Forget
        confirm.py                   Reusable Yes/No modal for destructive metadata actions
        tmux_sessions.py            Resume tmux Session (read-only session list)
        system_info.py               System Information
        settings.py                   Settings (placeholder)
        new_project/                Shared window/pane configuration flow (WizardMode: NEW_PROJECT,
                                     EXISTING_CREATE, EXISTING_EDIT -- see state.py)
            state.py                    WizardState, WindowDraft, WizardMode, step-numbering/factories
            step_project_info.py         Step 1 (NEW_PROJECT only)
            step_window_config.py        Configure Window (entry point for EXISTING_CREATE)
            step_layout_preview.py       Layout Preview
            step_window_summary.py       Windows summary (entry point for EXISTING_EDIT)
            step_review.py               Review -- branches on WizardMode at the final save/launch step
tests/                       Unit tests for models/ and services/, plus Pilot tests for screens/wizards
    test_project_detail.py              Project Detail rendering + Resume/Recreate/Default/Edit/Reset/Forget
    test_projects_screen.py             Open Project list: scan, search, refresh, navigation
    test_confirm_screen.py               ConfirmScreen Pilot tests
    test_git_info.py / test_workspace_defaults.py   New small services
    wizard/test_new_project_wizard.py   Textual Pilot tests driving the New Project wizard
    wizard/test_existing_project_wizard.py   Configure Workspace for an existing project
```

## Tests and static checks

```bash
.venv/bin/pytest
.venv/bin/mypy dashboard
.venv/bin/ruff check dashboard tests
```

Tests mock every filesystem-writing and subprocess-executing call -- no test creates a real
tmux session, attaches to tmux, or touches the real `~/projects` or XDG data directory.

## Notes on this version's scope

- **Create New Project** (version 2) and **Open Project** (this version) are both full
  flows now; see above for each.
- **Resume tmux Session** (the home-menu item, distinct from Open Project) still only
  *lists* sessions rather than attaching -- it's a general, project-independent tmux session
  browser, and attaching from there is left for a later version.
- **Settings** is still a placeholder screen.
