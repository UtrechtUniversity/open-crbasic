# CR6 language server progress

Live implementation state and session handoff. Design and acceptance criteria:
[LSP_PLAN.md](LSP_PLAN.md). Compiler state and worker policy: [PROGRESS.md](PROGRESS.md).

## Current state (2026-10-11)

- Planning documents created; implementation has not started.
- Active round: none. Next proposed round: LSP-00.
- Released capabilities: none. No server/extension tests or editor behaviour observed.
- No LSP workers, implementation branches or worktrees created by this planning task.
- Compiler baseline: read its live PROGRESS.md; remeasure at LSP-00 rather than copying counts.
- Verification of this documentation change: pending until the planning task's checks finish.

## Resume here

1. On an implementation request, read LSP_PLAN.md, AGENTS.md and compiler PROGRESS.md.
2. Inventory checkout/worktree changes and record the actual base SHA and import path.
3. Start LSP-00 with a durable round brief and current baseline measurements. Inventory existing
   scope fixtures, driver reparsing/output rules, and snapshot/include API gaps before designing
   the facade. Confirm which designated worker/reviewer tools are available.

No implementation request has been executed by the planning task. There is no partial server
work to recover. This file is the owner of future LSP resume state; the templates below are
instructions for maintaining it, not observed results.

## Round ledger

| Round/chunk | Base / worktree / owner | State | Evidence / review | Next action |
|---|---|---|---|---|
| LSP-00 | Not assigned | Planned | No implementation evidence | Write round brief and establish baseline |

States: planned, active, checkpointed-unverified, verified-not-integrated, integrated-awaiting-gates,
accepted. Record exact SHAs and paths when assigned; do not substitute descriptions for identities.

## Handoff record template

Update this section at each milestone and before a session ends. Link the active round brief and
per-worker handoffs instead of copying their full histories.

- Session date/time and actual integration branch/base SHA:
- Active round, chunk IDs, owners, branches and worktree paths:
- Interpreter/package import path, optional API version and environment:
- Latest accepted capability and its protocol/VS Code evidence:
- Last passing commands, revision tested, exit codes and saved output paths:
- Known compiler-baseline failures: link to compiler state/evidence:
- Pending commands and process/session IDs; completion status:
- Review findings: fixed / pending / deferred, with evidence links:
- WIP commits or checkpoint patches/hashes, untracked-file inventories, retained ignored assets:
- Usage tool/window, available reserve or unavailable-tool fallback:
- Unresolved requirements/semantic questions and confidence:
- Exact next action, command and expected acceptance evidence:

## Gate results template

| Gate | Revision / environment | Observed result | Saved evidence | Pending or known failure |
|---|---|---|---|---|

Record exact commands and completed observations. Quote the repository verifier's final BUILD
line. A compiler report from a different revision, a started command, or a worker's assertion
does not establish an integrated LSP result.
