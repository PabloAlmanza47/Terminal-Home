# terminal-home

Pablo's personal terminal development dashboard, built with [Textual](https://textual.textualize.io/).
Version 1: a home screen plus five screens for jumping into projects, checking tmux
sessions, and viewing system info.

## Requirements

- Python 3.10+
- Linux/WSL with `tmux` installed (optional -- the app degrades gracefully without it)

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
- **Escape** -- go back to the previous screen
- **q** -- quit, from the home screen
- Full shortcut list is always visible in the footer at the bottom of the screen

## Project layout

```
dashboard/
    app.py               Textual App subclass; entry point
    app.tcss              Theme and layout (dark, bordered panels)
    art.py                 ASCII art + the fullwidth-text trick used for the title
    services/              Plain Python, no Textual imports -- easy to unit test
        projects.py           Scans ~/projects for project directories
        tmux.py                Lists tmux sessions via subprocess
        system_info.py          Hostname, OS, Python version, shell, disk usage
    screens/                One module per screen
        home.py
        projects.py            Open Project
        tmux_sessions.py        Resume tmux Session
        system_info.py           System Information
        new_project.py            Create New Project (placeholder)
        settings.py                Settings (placeholder)
tests/                    Unit tests for the services/ layer (no UI tests yet)
```

## Tests and static checks

```bash
.venv/bin/pytest
.venv/bin/mypy dashboard
.venv/bin/ruff check dashboard tests
```

## Notes on version 1 scope

- **Open Project** lists immediate subdirectories of `~/projects`, excluding
  `terminal-home` itself. Selecting one just shows its path -- it doesn't launch
  anything yet.
- **Resume tmux Session** only *lists* sessions; it does not attach to one, since
  attaching needs the dashboard to hand off the terminal, which is planned for a
  later version.
- **Create New Project** and **Settings** are placeholder screens.
