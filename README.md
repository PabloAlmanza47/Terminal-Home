# terminal-home

Pablo's personal terminal development dashboard, built with [Textual](https://textual.textualize.io/).
Version 2 adds a full New Project wizard that scaffolds a project directory and launches
a tmux workspace for it, on top of the version 1 home screen and read-only Projects/tmux/
System Info screens.

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

## Workspace persistence

Confirmed workspaces are saved as JSON under `$XDG_DATA_HOME/terminal-home/workspaces.json`
(falling back to `~/.local/share/terminal-home/workspaces.json` when `XDG_DATA_HOME` isn't
set) -- never inside the project directory itself. Entries are keyed by each project's
canonical (resolved) path, so a workspace can be recreated after its tmux session disappears
or WSL restarts, even though this version's wizard only *writes* that file (a "relaunch an
existing project's saved workspace" flow is future work; see `dashboard/services/workspace_store.py`).
A missing, corrupt, or partially invalid store is handled without crashing -- at worst, the
unreadable entries are silently dropped. Project discovery for **Open Project** still just
scans directories under `~/projects`, so a freshly created project shows up there immediately
even if its workspace metadata were ever lost.

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
        workspace.py               PaneKind, PaneSpec, WindowSpec, WorkspaceSpec, LaunchRequest
        layout.py                   Pane layout rules + ASCII preview rendering
    services/                  Plain Python, no Textual imports -- easy to unit test
        projects.py                Scans ~/projects for project directories
        tmux.py                     Session listing + workspace command construction/execution
        system_info.py              Hostname, OS, Python version, shell, disk usage
        slug.py                      Filesystem-safe slug generation
        project_creation.py          Validation, directory creation, git init
        pane_commands.py             Resolves each pane kind into a launch plan at launch time
        workspace_store.py           JSON persistence under XDG_DATA_HOME
        workspace_launcher.py         Non-Textual orchestration: LaunchRequest -> running tmux
    screens/                    One module per screen
        home.py
        projects.py                Open Project
        tmux_sessions.py            Resume tmux Session
        system_info.py               System Information
        settings.py                   Settings (placeholder)
        new_project/                The New Project wizard
            state.py                    Mutable in-progress wizard state (WizardState, WindowDraft)
            step_project_info.py         Step 1
            step_window_config.py        Step 2
            step_layout_preview.py       Step 3
            step_window_summary.py       Step 4
            step_review.py               Step 5
tests/                       Unit tests for models/ and services/, plus Pilot tests for the wizard
    wizard/test_new_project_wizard.py   Textual Pilot tests driving the full wizard
```

## Tests and static checks

```bash
.venv/bin/pytest
.venv/bin/mypy dashboard
.venv/bin/ruff check dashboard tests
```

Tests mock every filesystem-writing and subprocess-executing call -- no test creates a real
tmux session, attaches to tmux, or touches the real `~/projects` or XDG data directory.

## Notes on version 2 scope

- **Create New Project** is now a full wizard (this version's main addition): see above.
- **Open Project** still just lists immediate subdirectories of `~/projects` and shows the
  selected one's path -- launching its saved workspace (if any) via the same tmux
  orchestration layer used by the wizard is planned for a later version.
- **Resume tmux Session** still only *lists* sessions; attaching from here is planned
  alongside the above.
- **Settings** is still a placeholder screen.
