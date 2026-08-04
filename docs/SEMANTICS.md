# Live-Session Semantics

This is the authoritative statement of what Terminal Home will and will not
do to a tmux session. Terminal Home decides *what* to open and *where* — it
does not manage what happens inside a pane after launch, and it never
second-guesses a tmux session that's already running.

## The three rules

1. **A running tmux session is authoritative while it exists.** If a
   session for a project is already running, Terminal Home only ever
   attaches (or, from inside an existing tmux client, switches) to it.
2. **A saved workspace configuration is authoritative when creating a new
   session.** If no session is running, Terminal Home rebuilds exactly
   what was last saved — the windows, panes, and startup commands recorded
   in that project's `WorkspaceSpec`.
3. **Resuming a running session never mutates it.** Attaching to a running
   session never adds, removes, rearranges, or recreates its windows or
   panes to match whatever the saved workspace currently says.

"Resume Session" and "Recreate Workspace" are the same request under the
hood — both build a `LaunchAction.ATTACH` `LaunchRequest`
(`dashboard.services.projects.build_launch_request`). The tmux
orchestration layer (`dashboard/services/workspace_launcher.py`) re-checks
whether the session is actually running at launch time, regardless of
which button the user saw, so a session that appeared or vanished between
scanning and launch is always handled by exactly one of rule 1 or rule 2 —
never both, and never a guess.

## What Terminal Home does not do

- **It does not restore arbitrary shell state.** A resumed or recreated
  pane gets, at most, the one startup command recorded for it (or nothing,
  for a plain shell) — not command history, environment variables, or any
  other shell state from a previous session.
- **It does not capture commands manually launched after workspace
  creation.** Anything typed into a pane after the workspace comes up is
  invisible to Terminal Home; it is never recorded, and never replayed.
- **Editing a saved workspace does not mutate a currently running
  session.** Saving from "Edit Workspace" only ever updates Terminal
  Home's own JSON metadata store. A session already running for that
  project — with its old layout — is left completely untouched.
- **Saved changes apply the next time a workspace is recreated**, not
  immediately. If a session is running when its saved workspace is edited,
  the new layout takes effect only the next time that session is torn
  down and recreated from scratch — Terminal Home never reaches into a
  live session to apply a change.
- **Terminal Home refuses to replace or overwrite an existing session.**
  Creating a new session always checks first and raises rather than
  killing or overwriting one already running under that name.
- **The live session and the saved configuration may differ without
  either being considered corrupt.** Adding panes, closing windows, or
  running arbitrary commands by hand inside a live session is never
  reported as an error or a warning. Terminal Home only ever reports a
  "Metadata Warning" when the *saved JSON itself* fails to parse — never
  because a live session doesn't match what's saved.

## Reusable template semantics

- A `WorkspaceTemplate` is reusable layout intent only: stable ID, user-visible
  name, ordered windows, and ordered pane specifications. It never contains a
  project path/name, session identity, launch action, Git or repository state,
  detected commands, tool availability, workspace-store key, or live tmux state.
- Saving a workspace as a template reads only the saved `WorkspaceSpec`. It does
  not inspect live panes, execute commands, access project source, or alter a
  running session.
- Selecting Blank, Default, or a saved template only initializes wizard draft
  state. It does not create a directory, save metadata, run Git, detect commands,
  or create/attach to tmux.
- Applying a template creates an independent destination workspace with the
  destination's path, project name, and already-determined session identity.
  Later template rename/deletion cannot affect that workspace, and editing the
  applied draft cannot mutate the template.
- Development Server and Test Terminal panes retain only their pane-kind intent.
  Their commands remain destination-specific and are detected at launch time.
  Custom pane commands remain exact reusable user-authored values.
- Template rename preserves stable identity. Delete and rename affect only
  `templates.json`; neither operation changes saved project workspaces or live
  sessions. The built-in Default Workspace is not a user template.

## Where this is enforced in code

- `dashboard/services/projects.py::build_launch_request` — builds the
  identical `LaunchAction.ATTACH` request regardless of whether the caller
  is presenting it as "Resume" or "Recreate"; what actually happens is
  decided at launch time, not here.
- `dashboard/services/workspace_launcher.py::execute_launch_request` —
  attaches if the session exists; only builds a new session (from the
  saved `WorkspaceSpec`) if it doesn't. Only ever called after the Textual
  app has fully exited (`dashboard/app.py::main`) — never from a mounted
  screen.
- `dashboard/services/tmux.py::create_workspace_session` — refuses
  outright if a session with the target name already exists.
- `dashboard/screens/new_project/step_review.py::ReviewScreen._save_existing_project`
  — in `WizardMode.EXISTING_EDIT`, only ever calls `save_workspace` and
  returns; it never builds a `LaunchRequest` and never touches tmux.
