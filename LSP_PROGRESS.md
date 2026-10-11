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

## Work rules (human instructions 2026-10-11)

- Host-only checks run asynchronously on the human's machine from
  [LSP_TO_BE_TESTED.md](LSP_TO_BE_TESTED.md), which pushes its findings to this branch. Cloud
  sessions only **append** to that file (new items, or a dated amendment under an existing item);
  never rewrite or reorder existing text. Pull with `git pull --rebase` before editing it.
- Workers: up to four Claude subagents in parallel (budget allows); coordinator verifies and
  cherry-picks. Reviews by `gpt-6.1-sol` are run manually by the human.

## Open questions for the human

- Help parsing (LSP-04a-fix), display only (hover/signature labels; the instruction table stays
  authoritative for counts and checks). Coordinator recommendations after checking the help pages
  (2026-10-11), pending the human's answer:
  1. A bare `[X]` around one parameter inside a call's parameter list marks it optional (as
     `ModbusSlave ( ..., [ModbusOption] )`, whose page has an "Optional Parameter" section);
     `Name [optional]` is already handled. Brackets outside call parameter lists (statement
     blocks `[Step increment]`, alternatives `[A,B,C]`, SDM `[COMMAND 2049]`) are not parameters.
  2. A trailing `...` (`MenuPick ( Item1, Item2, Item3... )`) is one repeatable parameter.
  3. Keep quotes on quoted names (`"SDICommand"`): they mark a string-literal argument.

## Work rules (human instructions 2026-10-11)

- Host-only checks run asynchronously on the human's machine from
  [LSP_TO_BE_TESTED.md](LSP_TO_BE_TESTED.md), which pushes its findings to this branch. Cloud
  sessions only **append** to that file (new items, or a dated amendment under an existing item);
  never rewrite or reorder existing text. Pull with `git pull --rebase` before editing it.
- Workers: up to four Claude subagents in parallel (budget allows); coordinator verifies and
  cherry-picks. Reviews by `gpt-6.1-sol` are run manually by the human.

## Open questions for the human

- Help parsing (LSP-04a-fix): (1) should a trailing `...` in a help syntax list mean one repeatable
  parameter (would restore `MenuPick`'s count match)? (2) should `[ X ]` in a syntax line mark `X`
  optional? (3) keep quotes on quoted literal names (`"SDICommand"`)? Coordinator default until
  answered: no to (1) and (2), keep quotes. Where help and the instruction table disagree on the
  parameter count, the table is authoritative and help names are shown only when counts match
  (LSP_PLAN.md: help never overrides checker rules).

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
| LSP-01b server | `codex/lsp-01-server` -> `bdc8881` | integrated | combined tests/lsp 156 passed x3 (no flakes); worker: full pytest 21,566/507, selfdiff 0 differ | ranges still compiler-line based -> LSP-01d; host checks T-02/T-04 |
| LSP-02a positions | `codex/lsp-02-positions` -> `43acb40` | integrated, gates passed | at `43acb40`: selfdiff 0 differ; full pytest 21,637/507; native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass`; loader+parser ~+8% time (worker) | oracle: T-01 |
| LSP-03a catalog+grammar | `codex/lsp-03-catalog` -> `4822b49` | integrated | coordinator rerun: 32 passed, `gen_grammar --check` up to date; combined tests/lsp 92 passed; npm compile 0, 5/5; worker: selfdiff 0 differ, full pytest 21,579/507 | highlighting: T-03 |
| LSP-04a help provider | `codex/lsp-04-helpdocs` -> `f252d1e` | integrated | coordinator rerun 18 passed; fixtures synthetic (no 8-word overlap with extracted_chm); worker: selfdiff 0 differ, full pytest 21,565/507 | defect: VoltSE last param `Mult, Offse` -> LSP-04a-fix |
| LSP-04a-fix help param names | `codex/lsp-04-helpfix` -> cherry-picked | integrated | coordinator rerun 23 passed; CR6 help coverage 302 with help, matching counts 226 -> 269, 33 mismatches (categories in LSP-04a-fix-handoff.md) | open questions below |
| LSP-05a binding index | `codex/lsp-05-bindings` -> `eca1727` | integrated | coordinator rerun 37 passed; combined tests/lsp 193 passed; worker: full pytest 21,634/507, selfdiff 0 differ; crbasic/ 0 unresolved, CHM 439 unresolved / 188 ambiguous (fragments) | LSP-05b |
| LSP-03b-core editing logic | `codex/lsp-03-editing`, `.claude/worktrees/lsp-03-editing`, base `e909d96`, Sonnet | active | - | verify, cherry-pick |
| LSP-04b-core hover logic | `codex/lsp-04-hover`, `.claude/worktrees/lsp-04-hover`, base `e909d96`, Sonnet | active | - | verify, cherry-pick |

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
