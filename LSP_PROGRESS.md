# CR6 language server progress

Live implementation state and session handoff. Design and acceptance criteria:
[LSP_PLAN.md](LSP_PLAN.md). Compiler state and worker policy: [PROGRESS.md](PROGRESS.md).

## Current state (2026-10-11, cloud session)

- LSP-00 done (baseline, inventory, contracts): [docs/lsp-rounds/LSP-00.md](docs/lsp-rounds/LSP-00.md).
  Review pending (the human runs `gpt-6.1-sol` manually; Codex is unavailable in the cloud
  container). Usage is checked by the human; work proceeds per round.
- Active round: LSP-01. Host-only checks pending: [LSP_TO_BE_TESTED.md](LSP_TO_BE_TESTED.md).
- Released capabilities: none. No server/extension behaviour observed yet.
- Container limits: no Wine (no oracle, difftest, live replay, probes), VS Code download host
  blocked (no VS Code integration tests). Shared-code gate here: `tools/selfdiff.py` (0 differ)
  plus full pytest; baseline `scratch/lsp/selfdiff-base.json` (local, ignored; regenerate at the
  base revision with `--save` if missing).

## Resume here

1. Read LSP_PLAN.md, this file, the active round brief under `docs/lsp-rounds/`, and its handoffs.
2. Check `git log` on `claude/laughing-franklin-08z9vo` and `git worktree list`; recover any
   worker worktree under `.claude/worktrees/` before editing.
3. If `scratch/lsp/selfdiff-base.json` is missing, recreate it at the round's base SHA.

## Round ledger

| Round/chunk | Base / worktree / owner | State | Evidence / review | Next action |
|---|---|---|---|---|
| LSP-00 | `f42e471`, main checkout, coordinator | accepted pending review | LSP-00.md gate table; selfdiff 0 differ | human review |
| LSP-01 | base `25eeced`, brief [LSP-01.md](docs/lsp-rounds/LSP-01.md) | active | - | integrate 01b, round gates |
| LSP-01a sources+facade | `codex/lsp-01-sources` -> `c7e5a6f` | integrated, gates passed | at `c7e5a6f`: selfdiff 0 differ; full pytest 21,547 passed / 507 skipped; native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` | oracle gates: T-01 |
| LSP-01c VS Code client | `codex/lsp-01-vscode` -> `b69ea37` | integrated | coordinator rerun: npm ci, compile exit 0, 5/5 tests | VS Code launch: T-02 |
| LSP-01b server | `codex/lsp-01-server`, `.claude/worktrees/lsp-01-server`, base `3c4cd44`, Sonnet | active | - | verify, cherry-pick |
| LSP-02a positions | `codex/lsp-02-positions`, `.claude/worktrees/lsp-02-positions`, base `3c4cd44`, Sonnet | active | - | verify, cherry-pick |
| LSP-03a catalog+grammar | `codex/lsp-03-catalog` -> `4822b49` | integrated | coordinator rerun: 32 passed, `gen_grammar --check` up to date; combined tests/lsp 92 passed; npm compile 0, 5/5; worker: selfdiff 0 differ, full pytest 21,579/507 | highlighting: T-03 |
| LSP-04a help provider | `codex/lsp-04-helpdocs` -> `f252d1e` | integrated | coordinator rerun 18 passed; fixtures synthetic (no 8-word overlap with extracted_chm); worker: selfdiff 0 differ, full pytest 21,565/507 | defect: VoltSE last param `Mult, Offse` -> LSP-04a-fix |
| LSP-04a-fix help param names | `codex/lsp-04-helpfix`, `.claude/worktrees/lsp-04-helpfix`, base `41c4c4a`, Haiku | active | - | verify, cherry-pick |
| LSP-05a binding index | `codex/lsp-05-bindings`, `.claude/worktrees/lsp-05-bindings`, base `41c4c4a`, Sonnet, brief [LSP-05.md](docs/lsp-rounds/LSP-05.md) | active | - | verify, cherry-pick |

Parallel dispatch of LSP-02a/03a/04a alongside 01b follows the human's 2026-10-11 instruction to use
more workers (four at once, the PROGRESS.md maximum); their files are disjoint (briefs list them).

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
