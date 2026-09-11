# Terminal Home Agent Hub / Agent Workspaces

Design spike for post-v0.4 development. This document proposes a small first
implementation for finding and attaching to existing agent sessions. It does
not propose an agent runtime, orchestration layer, or automatic worktree
manager.

## Executive recommendation

Build Agent Hub as a read-only, Agent Deck-backed view on Home. Terminal Home
should own the navigation surface and the mapping from an agent's path to a
known project/workspace; Agent Deck should remain the source of truth for agent
session identity, title, lifecycle state, and attachment.

The MVP should:

1. refresh the existing Agent Deck snapshot as part of Home's existing scan;
2. show a compact `Active Agents` section only when Agent Deck returns sessions;
3. show title, project/path context, tool, and a normalized status;
4. filter with the existing `/` search action;
5. attach on Enter by handing the Agent Deck session ID to the existing
   post-TUI launcher;
6. degrade to an explanatory empty/unavailable state without affecting the
   project and tmux panels.

No new persisted Agent Hub database is needed for this version.

## 1. Current architecture findings

### Agent Deck integration already present

`dashboard.services.agent_deck` is an optional, best-effort adapter. It:

- checks for the `agent-deck` executable;
- invokes `agent-deck list --json` with a short timeout;
- accepts either a top-level JSON array or a `{ "sessions": [...] }` payload;
- retains only entries with a non-empty `id` and string `path`;
- normalizes paths with `realpath` and `expanduser`;
- parses `id`, `title`, `path`, `tool`, `status`, optional `tmux_session`,
  optional `profile`, and the raw status string;
- returns `AgentDeckSnapshot(available, sessions, warning)` rather than
  raising into the UI;
- exposes `agent-deck session attach <id>` through `attach_argv`.

The current model is already close to the Agent Hub row model. Its title is a
friendly task/session name when Agent Deck provides one, with the session ID as
the fallback. There is no separate Terminal Home task-name model or local
friendly-name registry today.

The public Agent Deck documentation describes the same useful concepts: paths,
titles, project-based organization, searchable sessions, Enter-to-attach, and
smart busy-versus-input status. It also documents `agent-deck list --json` as a
machine-readable interface. These are the capabilities Terminal Home should
reuse rather than duplicate:

- [Agent Deck README: features, status, CLI, organization, and import](https://github.com/tonyle9/agent-deck/blob/main/README.md)
- [Agent Deck CLI reference: session show/current/attach and JSON fields](https://github.com/asheshgoplani/agent-deck/blob/main/skills/agent-deck/references/cli-reference.md)

There are multiple projects using the `Agent Deck` name. The local adapter's
command shape and payload fields are the compatibility contract for Terminal
Home; the upstream links above are supporting research, not a reason to change
that contract during the spike.

### How Terminal Home identifies Agent Deck sessions today

`scan_all_projects` performs one discovery pass, one tmux session listing, one
tmux pane listing, and one Agent Deck snapshot. For each discovered `Project`,
it associates Agent Deck sessions when:

```text
normalize(agent_session.path) == project.path.resolve()
```

That tuple is placed on `ProjectStatus.agent_sessions`. The Project Detail
screen uses it to show an agent summary and to offer `Open Agent`. Home uses it
to show a per-project agent summary and to attach the deterministically selected
session. Agent Deck-owned tmux names are excluded from Home's generic Active
Sessions list when `AgentDeckSession.tmux_session` is present.

This means project-scoped Agent Deck status already exists; Agent Hub mainly
needs a session-scoped projection so multiple sessions for one project are
visible independently.

### Projects, workspaces, paths, and tmux

Terminal Home's identity hierarchy is currently:

```text
discovered Project
  -> canonical filesystem path
  -> ProjectStatus
  -> saved WorkspaceSpec (optional)
  -> expected/running tmux session name
```

`Project` contains a display name and path. `ProjectStatus` contains the
canonical path, saved workspace, expected session name, running-session state,
and Agent Deck sessions. `WorkspaceSpec` contains a project location and one
tmux session name with windows and panes. Local workspaces use
`LocalProjectLocation`; SSH workspaces use `SshProjectLocation`.

Terminal Home has no first-class Git worktree object. A worktree is therefore
currently just another directory if it is discovered or manually registered.
The Agent Deck path is the only reliable association available for an agent
session. A project root and a worktree should not be merged merely because they
share a Git repository.

The tmux layer reports local sessions by name and can attach or switch to a
session. Agent Deck attachment is deliberately separate: Terminal Home exits
the TUI with `AgentDeckAttachRequest`, and `dashboard.app` invokes Agent Deck's
attach command after Textual releases the terminal.

### Home screen architecture and clutter budget

Home currently has four concepts: primary actions, recent projects, Active
Sessions, and system status. The scan runs in a worker on mount/F5, and the
clock does not trigger rescans. `/` is already a global search action that
focuses Home's project search. This makes Home a good host for Agent Hub if the
new section is compact and does not become another full management screen.

The first Home design should use one selectable list with a bounded number of
rows, adjacent to or immediately below Active Sessions depending on the
responsive layout. It should not add a second always-visible detail pane.

## 2. Proposed minimal data model

Do not persist this model. Build it from the current scan result.

### Existing source model

Keep `AgentDeckSession` as the source record:

```text
id: str                  stable attach key
title: str               friendly session/task label or ID fallback
path: Path               normalized working directory
tool: str                Codex, Claude, etc.
status: AgentStatus      raw normalized lifecycle state
tmux_session: str | None optional Agent Deck tmux target
profile: str | None      optional Agent Deck profile
raw_status: str | None   compatibility/debug detail
```

### Proposed derived view model

Introduce a UI-facing, immutable derived row only if the existing
`AgentDeckSession` becomes awkward to render. A likely shape is:

```text
AgentHubEntry:
  session_id: str
  title: str
  tool_label: str
  status: AgentHubStatus
  path: Path
  project_name: str | None
  project_path: Path | None
  workspace_session_name: str | None
  tmux_session: str | None
  association: AgentAssociation
```

`AgentAssociation` should be one of:

- `known_project`: exact canonical path matched a discovered project;
- `known_worktree`: reserved for a future explicit worktree model, not inferred
  in MVP;
- `unregistered_path`: path is valid but not in project discovery;
- `missing_path`: Agent Deck record was rejected by the current parser or the
  directory no longer exists;
- `remote_or_unsupported`: reserved for paths Terminal Home cannot safely map.

For MVP, `workspace_session_name` is populated only when the exact matched
project has a saved/running Terminal Home workspace. It is display context, not
the attach target. `session_id` remains the attach target.

### Identity and deduplication

Use `session_id` as the unique key. Do not key by title, project name, or tmux
session name: multiple agents may share a project, titles may be renamed, and
Agent Deck may use a separate tmux server/socket. Preserve Agent Deck's order if
it is meaningful; otherwise sort by status priority, project name/path, title,
and ID for stable output.

## 3. Session/project/worktree association strategy

Association should be deliberately conservative:

1. Normalize the Agent Deck path with the existing `normalize_project_path`.
2. Normalize each discovered project's path with `Path.resolve()`.
3. Exact-match those canonical paths.
4. If matched, attach the entry to that `ProjectStatus` and expose its
   `expected_session_name` only as context.
5. If not matched, show the absolute/contracted Agent Deck path and label it
   `unregistered path`; do not silently attach it to a same-named project.

For worktrees, exact path matching is especially important. A Git worktree has
its own working directory and may have a different branch, uncommitted state,
and Terminal Home workspace. In MVP, the worktree can appear as an unregistered
path or as a discovered project if configured. Later, a dedicated Git/worktree
inspection service may identify the common repository and branch, but that
should not be guessed from basename or parent-directory relationships.

For tmux:

- Agent Deck's `tmux_session` is metadata/context and may point at Agent Deck's
  own tmux server or socket.
- The Agent Hub attach action always uses `AgentDeckAttachRequest(session_id)`.
- Only when Agent Deck is unavailable and the user has explicitly selected a
  known Terminal Home workspace should the generic Terminal Home attach flow be
  used.
- Never convert an Agent Deck session into a Terminal Home `LaunchRequest` just
  because both happen to use tmux.

## 4. Status model

Agent Deck currently exposes `running`, `waiting`, `idle`, `stopped`, `error`,
and `unknown`, plus an unmodified `raw_status`. The existing adapter maps
`working`/`active` to running, `pending`/`paused` to waiting, `complete` to
idle, `dead` to stopped, and `failed` to error.

The requested Agent Hub labels should be a presentation policy, not a new
runtime state machine:

| Agent Hub label | Source states | Meaning | MVP glyph |
|---|---|---|---|
| Working | `RUNNING` | Agent Deck reports active work | `●` |
| Waiting | `WAITING` | Prompt/input/approval is likely needed | `◐` |
| Completed | `IDLE` | No active work; session remains resumable/visible | `✓` |
| Unknown | `UNKNOWN`, `ERROR`, `STOPPED`, unavailable detail | Do not claim a stronger state | `?` |

The existing UI distinguishes Error, Idle, and Stopped. For MVP, it is safer to
retain that fidelity where space allows and use `Completed` only as the Agent
Hub product label for a clean idle/completed session. Do not infer completed
from a missing tmux process, a stale record, or a shell prompt. If the desired
meaning of `completed` is “agent explicitly finished the task,” Agent Deck must
provide a distinct terminal/completed state; otherwise the UI should say
`Idle` or `Unknown`.

Status is a snapshot. Show `last refreshed` only if useful; do not promise
real-time notifications or event delivery in v1.

## 5. Proposed Home UI

### Compact section

On wide Home layouts, add a bounded panel titled `Active Agents`:

```text
Active Agents
● v0.4 Mypy Fix       terminal-home  Codex · Working
◐ Wizard Failures     terminal-home  Codex · Waiting
● Packaging Validation terminal-home Codex · Working
✓ Release Docs        terminal-home  Codex · Completed
```

The path/project should be the secondary line or a shortened suffix when the
terminal is narrow. The target information is more valuable than the full
absolute path:

```text
● v0.4 Mypy Fix
  terminal-home · Codex · Working
```

Recommended rules:

- show at most 4 rows in the compact Home panel;
- add `View All Agents` only when there are more rows;
- show a count in the heading, e.g. `Active Agents (4)`;
- order waiting first, then working, then completed/idle, then unknown;
- use title first, path/project second, and tool/status last;
- keep completed/idle rows visible until Agent Deck stops returning them, but
  do not let historical sessions crowd out active ones;
- reserve `/` for filtering the existing Home project list in MVP; a later
  Agent Hub screen can make `/` filter the focused list or offer a scoped search.

### Empty and unavailable states

Do not show a scary error panel:

- Agent Deck absent: `Agent Hub unavailable — install Agent Deck to track agents`
- Agent Deck present but no sessions: `No Agent Deck sessions found`
- snapshot warning with usable sessions: show sessions and a subtle `status may
  be stale` indicator;
- malformed/failed snapshot: preserve the last in-memory rows for the current
  screen only if the scan contract supports that safely; otherwise show the
  unavailable state and keep Recent Projects/Active Sessions functional.

### Navigation

Home selection should be explicit and independent from recent-project selection.
Enter on an Agent Hub row exits the TUI with `AgentDeckAttachRequest`. `Esc`
and existing section navigation remain unchanged. A full Agent Hub screen is
not required for the first slice; `View All Agents` can be a later task.

## 6. Attach/open flow

The MVP flow is:

```text
Home scan
  -> Agent Deck snapshot
  -> Agent Hub row selected
  -> AgentDeckAttachRequest(session_id)
  -> Textual exits
  -> dashboard.app calls agent-deck session attach <id>
  -> user detaches according to Agent Deck behavior
```

This follows the current Project Detail and Home Agent Deck path. It preserves
Agent Deck's own lookup, tmux server/socket handling, session validation, and
detach semantics. The public Agent Deck material documents Enter-to-attach and
the `session attach` command, which supports this ownership boundary.

Before handing off, Terminal Home should only validate that the selected row
still has a non-empty session ID. It should not run a second speculative tmux
attach or inspect pane output. The launcher remains responsible for reporting a
non-zero exit status.

## 7. Failure and fallback behavior

Agent Hub is optional and must never block normal Terminal Home workflows.

| Failure | UI behavior | Action behavior |
|---|---|---|
| `agent-deck` not installed | explanatory unavailable row | no Agent Hub attach action |
| command timeout | warning/stale indicator | existing rows may remain; attach selected ID only if still known |
| non-zero exit | unavailable/warning | project and tmux panels still work |
| malformed JSON | unavailable/warning | ignore malformed records; do not guess |
| session disappeared after scan | launcher error notification/CLI error | do not fall back to a same-name tmux session automatically |
| Agent Deck attach executable missing at handoff | existing `AgentDeckLaunchError` path | return to app loop/error path; no Terminal Home session mutation |
| path not discovered | show unregistered path | Agent Deck attach remains available |
| tmux unavailable but Agent Deck usable | show Agent Deck status if supplied | let Agent Deck own attach; do not replace with Terminal Home tmux attach |

The distinction between an unavailable provider and an empty provider matters:
an empty list means “Agent Deck answered and has no sessions”; unavailable means
“Terminal Home cannot make a trustworthy claim.”

## 8. Persistence decision

Do not persist Agent Hub metadata in Terminal Home v1.

Reasons:

- Agent Deck already owns session identity and friendly titles.
- Persisting a duplicate title/status/path record creates stale-state and rename
  reconciliation problems.
- Terminal Home's project/workspace store is intentionally about workspace
  configuration, not agent transcripts or lifecycle.
- The existing snapshot is cheap, bounded, and already part of the scan.

Terminal Home may persist a user preference later, such as “show Agent Hub on
Home,” but that is UI configuration rather than agent metadata. A local cache is
also deferred; if introduced, it must be explicitly marked stale and never be
used as authority for attachment.

## 9. Files/modules likely to change in a future implementation

Likely first slice:

- `dashboard/services/agent_deck.py`: only if the current parser needs a small
  compatibility adjustment or a public helper for all-session ordering;
- `dashboard/services/projects.py`: expose a path-to-project association helper
  or derived Agent Hub entries without changing ProjectStatus semantics;
- `dashboard/screens/home.py`: render an Agent Hub list, lookup map, selection,
  and bounded responsive layout;
- `dashboard/app.tcss`: add compact/wide Agent Hub panel styling;
- `dashboard/app.py`: probably no change; reuse the existing
  `AgentDeckAttachRequest` execution path;
- `tests/test_agent_deck.py`: parser, availability, status, title, and attach
  contract tests;
- `tests/test_projects.py`: exact path association and unregistered/worktree
  behavior;
- `tests/test_home.py`: rendering, ordering, selection, fallback, and refresh
  tests.

Avoid changing `dashboard.models.workspace`, the wizard screens, or the tmux
workspace launcher for the MVP.

## 10. Required tests

### Pure service tests

- two sessions with the same project path remain two Agent Hub entries;
- two sessions with the same title remain two entries keyed by ID;
- title falls back to ID when absent;
- path normalization matches equivalent symlink/`~`/absolute spellings as the
  current adapter intends;
- a worktree path is not attached to its parent project by basename;
- an unregistered but valid path remains selectable and displays its path;
- ordering is deterministic and waiting sessions are not hidden;
- all current status aliases map to the documented presentation category;
- unknown/error/stopped statuses do not become “working” or “completed”;
- Agent Deck unavailable, timeout, non-zero, malformed JSON, and empty results
  produce distinct snapshot states;
- attach argv remains exactly `agent-deck session attach <id>`.

### Home interaction tests

- no Agent Deck sessions: Home still mounts and project/tmux panels work;
- Agent Deck unavailable: no crash and no misleading active-agent rows;
- four-or-fewer rows render within the wide and narrow layouts;
- five-plus rows show a bounded list and a View All affordance if that affordance
  is included in the chosen slice;
- Enter emits `AgentDeckAttachRequest` with the selected ID;
- project selection and Agent Hub selection do not share lookup IDs;
- F5 refresh replaces rows from the new snapshot;
- a failed later snapshot does not corrupt the project list;
- an Agent Deck-owned tmux session is not duplicated in generic Active Sessions.

### Regression tests

- Project Detail's existing Open Agent flow remains unchanged;
- Terminal Home's normal workspace attach still emits `LaunchRequest`;
- direct tmux sessions remain selectable through Active Sessions;
- remote project behavior remains unaffected;
- no Agent Hub code writes the workspace store or changes tmux sessions.

## 11. Staged implementation plan

Each stage is intentionally small enough for one focused Codex task.

### Stage 0 — compatibility contract

Document and test the existing Agent Deck payload and failure matrix. Confirm
which installed Agent Deck version(s) are in scope and whether `title`,
`tmux_session`, and `status=complete` are stable fields. No UI change.

### Stage 1 — pure Agent Hub projection

Add a small service function that converts one `AgentDeckSnapshot` plus the
current `ProjectStatus` collection into immutable Agent Hub entries. Implement
exact canonical-path association, conservative worktree handling, stable
ordering, status presentation, and pure tests.

### Stage 2 — Home panel

Add a bounded `Active Agents` panel to Home's existing responsive layout. Keep
the scan and worker architecture unchanged. Add rendering and empty/unavailable
tests.

### Stage 3 — attach handoff

Add a distinct Agent Hub lookup and selection branch. Emit the existing
`AgentDeckAttachRequest`; do not change the launcher. Add interaction tests and
verify generic tmux rows are not duplicated.

### Stage 4 — focused Agent Hub screen, only if needed

If four rows are insufficient in practice, add a read-only full-screen list with
search and path/project context. Reuse the same projection service and attach
handoff. Do not add spawning or metadata writes.

### Stage 5 — real-world compatibility pass

Test Agent Deck absent, stale, upgraded, separate tmux socket, multiple sessions
per project, missing worktrees, and very long titles/paths. Adjust only the
adapter/projection boundary, not Terminal Home's workspace ownership.

## 12. Risks and open questions

### Risks

- Agent Deck's JSON payload may evolve or differ across similarly named
  projects/releases; the adapter must retain defensive parsing.
- “Completed” may not be a reliable distinct lifecycle state. Treating idle as
  completed could mislead users.
- Agent Deck may own a separate tmux server/socket, so direct Terminal Home tmux
  inspection cannot be the source of truth for Agent Deck attachment.
- Canonical path matching can still fail across containers, SSH hosts, mounts,
  or differing namespace views.
- Large session histories can crowd Home unless the panel is bounded and active
  statuses are prioritized.
- A stale snapshot can look authoritative unless the UI communicates refresh or
  unavailable state.

### Open questions

- Which exact Agent Deck distribution and version does Terminal Home support as
  its compatibility target?
- Does the supported Agent Deck expose a distinct completed/finished state, or
  should Terminal Home label `IDLE` as `Idle` permanently?
- Should the user be able to hide completed/idle sessions from the Home panel?
- Should `/` search projects only, or search the focused Home section after the
  Agent Hub panel exists?
- Should an Agent Deck path outside configured project roots be offered as a
  one-time “Open path” action, or remain attach-only?
- Are remote Agent Deck sessions in scope, and if so, what host identity is
  available for safe association?
- Can Agent Deck guarantee that `id` is stable across restarts and profiles?
- Is `tmux_session` a display/debug field only, or a supported direct fallback
  target in any release?

## 13. Explicit non-goals for Agent Hub v1

These are later ideas, not part of the MVP:

- spawning agents from Terminal Home;
- automatically creating or deleting Git worktrees;
- saved prompts, prompt templates, or prompt delivery;
- notifications, desktop integrations, or background daemons;
- merge queues, PR automation, or review orchestration;
- cleanup, archival, or deletion workflows;
- multi-agent orchestration, dependencies, fan-out, or supervisor agents;
- transcript viewing, output capture, approvals, or message sending;
- replacing Agent Deck's TUI, daemon, session database, or tmux management;
- making Terminal Home responsible for agent lifecycle state;
- persisting a second copy of Agent Deck session metadata.

## MVP versus later ideas

### MVP

Read-only Agent Deck snapshot, exact path association, compact Home list,
normalized status display, search/selection appropriate to the existing Home
layout, existing Agent Deck attach handoff, bounded rendering, and robust
unavailable/empty behavior.

### Later

Spawning, automatic worktrees, saved prompts, notifications, merge queues,
cleanup, orchestration, transcript/output previews, approvals, and richer
project/worktree relationships should each be separate design and safety
decisions. None should be smuggled into the first Agent Hub implementation.

## Research notes

Local findings are based on the current Terminal Home source, especially:
`dashboard/services/agent_deck.py`, `dashboard/services/projects.py`,
`dashboard/services/activity.py`, `dashboard/screens/home.py`,
`dashboard/screens/project_detail.py`, `dashboard/services/tmux.py`,
`dashboard/models/workspace.py`, and `dashboard/app.py`.

External references were checked on 2026-09-11:

- [tonyle9/agent-deck README](https://github.com/tonyle9/agent-deck/blob/main/README.md)
- [asheshgoplani/agent-deck CLI reference](https://github.com/asheshgoplani/agent-deck/blob/main/skills/agent-deck/references/cli-reference.md)

The external references support the general capabilities and terminology. The
Terminal Home adapter remains the authoritative compatibility boundary because
the ecosystem contains multiple projects with similar names.
