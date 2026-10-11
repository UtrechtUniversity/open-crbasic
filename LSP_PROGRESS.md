# CR6 language server progress

Live implementation state and session handoff. Design and acceptance criteria:
[LSP_PLAN.md](LSP_PLAN.md). Compiler state and worker policy: [PROGRESS.md](PROGRESS.md).

## Current state (2026-10-11, cloud session)

- LSP-00 done (baseline, inventory, contracts): [docs/lsp-rounds/LSP-00.md](docs/lsp-rounds/LSP-00.md).
  The human-requested `gpt-6.1-sol` review through `39286384` is recorded in
  [review-2026-10-11-39286384.md](docs/lsp-rounds/review-2026-10-11-39286384.md);
  findings await implementation. Usage is checked by the human; work proceeds per round.
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
- Scope: this LSP session changes only LSP code (facade, editor modules, server, client, LSP
  tests/docs). Compiler work items it finds are recorded in [PROGRESS.md](PROGRESS.md) "Compiler
  items found by the LSP work" and not executed. Where the compiler or help lacks something
  (e.g. no help page, unimplemented instruction), editor features show an explicit placeholder
  ("Help: still to do") instead of driving a compiler change.
- Workers: up to six Claude subagents in parallel (human, 2026-10-11: budget allows more than the
  compiler workstream's four); coordinator verifies and cherry-picks. Reviews by `gpt-6.1-sol` are run manually by the human.

## Human decisions (2026-10-11)

- Help parsing (display only; the instruction table stays authoritative): a bare `[X]` around one
  parameter in a call's parameter list marks it optional (`Name [optional]` already did); brackets
  outside call parameter lists are not parameters; a trailing `...` is one repeatable parameter
  (`MenuPick ( Item1, Item2, Item3... )`); quotes on quoted names are kept. Implemented in
  LSP-04a-rules.

## Integration fixes by the coordinator

- `tools/lsp_bench.py` (LSP-08a) removed `OPENCRBASIC_STRICT` from `os.environ` at import, so the
  smoke test switched strict mode off for every later test in the session (found as 2 failures in
  `tests/lsp/test_analysis.py` when run after it). Now only `main()` does it.

## Review findings

[gpt-6.1-sol review through `39286384`](docs/lsp-rounds/review-2026-10-11-39286384.md) (human-run,
2026-10-11), four P2 findings, triaged by the coordinator:

1. Two-word `End Sub`/`End Function` do not close routine scope (bindings, hover): accepted,
   reproducer confirmed by the reviewer; LSP-R1 (Haiku). The compiler's `symbols.scoped_view` has
   the same omission (compiler workstream; LSP-R1 reports whether verdicts are affected).
2. Instruction token resolves to a same-name user declaration: accepted; LSP-R1.
3. Diagnostic columns always UTF-16 regardless of negotiated encoding: accepted; fixed in LSP-wire
   (utf-8/utf-16/utf-32 test).
4. Quoted `DataTable("Hist")` missing from outline/declared names/completion: accepted; LSP-R1.

## Host findings to act on

Host records: `docs/lsp-rounds/host-tests/` (`602f6462`, `8b25214a`, `7c450ed5`, `c0fd9bf1`). T-01
(oracle) passed at `8b25214a`; T-02 CR6 scenarios and T-03 visual passed at `7c450ed5` (macOS,
VS Code 1.141.0); T-04 partial. Open findings from `7c450ed5`:

- H-01 ShuntLib edit on physical line 32 shown on 33: explained, fixed by LSP-01d (`b21c1e1`).
  `Public =` ends in `=`, so the loader pulls line 33 into the declaration (the compiler's own
  rule; the message echoes `Public = Public Shunt_Current_A`) and reports it at 33. Coordinator
  reproduction on the current tree: span (32, 33), so the range now covers both lines. Host retest:
  T-02 amendment.
- H-02 Python-path restart error toast (`didChangeConfiguration failed` / `Starting server failed`):
  client race between the configuration-sync notification and client replacement -> LSP-01e
  after LSP-wire (vscode/ is owned by the wiring worker until then).
- H-03 `.CR300` opens as Plain Text: the client language contributes only `.CR6`/`.crb` -> LSP-01e
  adds `.CR1X`, `.CR300`, `.CR350` (both cases).
- H-04 UTF-8 `É` include name unresolved (mojibake `Ã\x89`): the compiler decodes source bytes as
  Latin-1 (parity behaviour assumed, not observed for non-ASCII names). Needs real-compiler
  evidence first (T-05); no code change until then.

## Work rules (human instructions 2026-10-11)

- Host-only checks run asynchronously on the human's machine from
  [LSP_TO_BE_TESTED.md](LSP_TO_BE_TESTED.md), which pushes its findings to this branch. Cloud
  sessions only **append** to that file (new items, or a dated amendment under an existing item);
  never rewrite or reorder existing text. Pull with `git pull --rebase` before editing it.
- Workers: up to six Claude subagents in parallel (human, 2026-10-11: budget allows more than the
  compiler workstream's four); coordinator verifies and cherry-picks. Reviews by `gpt-6.1-sol` are run manually by the human.

## Human decisions (2026-10-11)

- Help parsing (display only; the instruction table stays authoritative): a bare `[X]` around one
  parameter in a call's parameter list marks it optional (`Name [optional]` already did); brackets
  outside call parameter lists are not parameters; a trailing `...` is one repeatable parameter
  (`MenuPick ( Item1, Item2, Item3... )`); quotes on quoted names are kept. Implemented in
  LSP-04a-rules.

## Host findings to act on

- 2026-10-11 host run `602f6462` (T-02): the VS Code client logged `Client is not running and
  can't be stopped` while the server failed to start -> LSP-01d client fix.
- T-02/T-04 are runnable from `a15d2b3` (server integrated); T-03 visual check pending (tokenizer
  checks passed on host).

## Work rules (human instructions 2026-10-11)

- Host-only checks run asynchronously on the human's machine from
  [LSP_TO_BE_TESTED.md](LSP_TO_BE_TESTED.md), which pushes its findings to this branch. Cloud
  sessions only **append** to that file (new items, or a dated amendment under an existing item);
  never rewrite or reorder existing text. Pull with `git pull --rebase` before editing it.
- Workers: up to six Claude subagents in parallel (human, 2026-10-11: budget allows more than the
  compiler workstream's four); coordinator verifies and cherry-picks. Reviews by `gpt-6.1-sol` are run manually by the human.

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
- Workers: up to six Claude subagents in parallel (human, 2026-10-11: budget allows more than the
  compiler workstream's four); coordinator verifies and cherry-picks. Reviews by `gpt-6.1-sol` are run manually by the human.

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
| LSP-00 | `f42e471`, main checkout, coordinator | reviewed; findings pending | LSP-00.md gate table; selfdiff 0 differ; [review through 39286384](docs/lsp-rounds/review-2026-10-11-39286384.md) | resolve review findings |
| LSP-01 | base `25eeced`, brief [LSP-01.md](docs/lsp-rounds/LSP-01.md) | active | - | integrate 01b, round gates |
| LSP-01a sources+facade | `codex/lsp-01-sources` -> `c7e5a6f` | integrated, gates passed | at `c7e5a6f`: selfdiff 0 differ; full pytest 21,547 passed / 507 skipped; native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` | oracle gates passed on host (T-01, `8b25214a`, macOS: difftest/replay/tdftest unchanged vs baseline, real-exe verify BUILD pass) |
| LSP-01c VS Code client | `codex/lsp-01-vscode` -> `b69ea37` | integrated | coordinator rerun: npm ci, compile exit 0, 5/5 tests | VS Code launch: T-02 |
| LSP-01b server | `codex/lsp-01-server` -> `bdc8881` | integrated | combined tests/lsp 156 passed x3 (no flakes); worker: full pytest 21,566/507, selfdiff 0 differ | ranges still compiler-line based -> LSP-01d; host checks T-02/T-04 |
| LSP-02a positions | `codex/lsp-02-positions` -> `43acb40` | integrated, gates passed | at `43acb40`: selfdiff 0 differ; full pytest 21,637/507; native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass`; loader+parser ~+8% time (worker) | oracle gates passed on host (T-01, `8b25214a`) |
| LSP-03a catalog+grammar | `codex/lsp-03-catalog` -> `4822b49` | integrated | coordinator rerun: 32 passed, `gen_grammar --check` up to date; combined tests/lsp 92 passed; npm compile 0, 5/5; worker: selfdiff 0 differ, full pytest 21,579/507 | highlighting: T-03 |
| LSP-04a help provider | `codex/lsp-04-helpdocs` -> `f252d1e` | integrated | coordinator rerun 18 passed; fixtures synthetic (no 8-word overlap with extracted_chm); worker: selfdiff 0 differ, full pytest 21,565/507 | defect: VoltSE last param `Mult, Offse` -> LSP-04a-fix |
| LSP-04a-fix help param names | `codex/lsp-04-helpfix` -> cherry-picked | integrated | coordinator rerun 23 passed; CR6 help coverage 302 with help, matching counts 226 -> 269, 33 mismatches (categories in LSP-04a-fix-handoff.md) | open questions below |
| LSP-05a binding index | `codex/lsp-05-bindings` -> `eca1727` | integrated | coordinator rerun 37 passed; combined tests/lsp 193 passed; worker: full pytest 21,634/507, selfdiff 0 differ; crbasic/ 0 unresolved, CHM 439 unresolved / 188 ambiguous (fragments) | LSP-05b |
| LSP-03b-core editing logic | `codex/lsp-03-editing` -> `6c39974` | integrated | coordinator rerun 110 passed; combined tests/lsp 341 passed; worker: selfdiff 0 differ | server wiring after 01d |
| LSP-04b-core hover logic | `codex/lsp-04-hover` -> `cd943ff` | integrated | coordinator rerun 38 passed; worker: tests/lsp 175, selfdiff 0 differ | server wiring |
| LSP-01d spans + client stop | `codex/lsp-01-spans` -> `b21c1e1` | integrated | worker: tests/lsp 233 passed x3, selfdiff 0 differ, npm 10/10; coordinator: npm 10/10 on combined tree | host: T-02 log must not show `Client is not running and can't be stopped` |
| LSP-04a-rules help syntax decisions | `codex/lsp-04-rules` -> cherry-picked | integrated | coordinator: helpdocs+hover+editing tests pass, spot checks MenuPick/ModbusSlave/PortSet; coverage matching 269 -> 270 (MenuPick) | leftover: `FileOption, [optional]` (FTPClient, EMailSend, HTTPPost) not marked optional -> fold into wiring chunk |
| LSP-05b-core navigation logic | `codex/lsp-05-navigation` -> `3769504` | integrated, gates passed | at `3769504`: selfdiff 0 differ, full pytest 21,869/507, native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass`; additive `stmt.origin` in parser.py | oracle: T-01 amendment |
| LSP-wire features in server | `codex/lsp-wire` -> cherry-picked | integrated-awaiting-gates | worker: tests/lsp 405 x3, selfdiff 0 differ, npm 10/10; option (b) pickled program from the pool (first hover p50 39 ms vs 575 ms in-thread); review finding 3 fixed (negotiated encoding for diagnostics) | combined gates running; host T-09 |
| LSP-01e client fixes H-02/H-03 | `codex/lsp-01-client`, base `e57c7f8`, Haiku | active | - | verify, cherry-pick |
| LSP-04e checker arity in signature help | `codex/lsp-04-arity`, base `e57c7f8`, Haiku | active | - | verify, cherry-pick |
| LSP-04a-optmark | `codex/lsp-04-optmark` -> cherry-picked | integrated | worker: helpdocs+hover 70 passed, tests/lsp 367, selfdiff 0 differ; FileOption optional in FTPClient/EMailSend/HTTPPost/HTTPPut | gap: `coverage_report` counts instructions only; FTPClient etc. are function-table entries (coordinator check) |
| LSP-07a rename core | `codex/lsp-07-rename`, base `6ca749d`, Sonnet, brief [LSP-07.md](docs/lsp-rounds/LSP-07.md) | active | - | verify, cherry-pick |
| LSP-07b formatting core | `codex/lsp-07-format` -> cherry-picked | integrated | coordinator rerun 22 passed; worker: tests/lsp 391, selfdiff 0 differ; CHM 198/198 accepted programs identical after formatting, idempotent 507/507 | `SlowSequence` indented inside BeginProg (style, differs from Main.CR6); initializer pulls not modelled; `xmlparse_1` message changes with indentation -> T-06 |
| LSP-05c table fields | `codex/lsp-05-fields` -> `b2bdd39` | integrated | coordinator rerun 61 passed; worker: tests/lsp 376, selfdiff 0 differ; crbasic unmodelled 96 -> 6 (`Status.CardBytesFree` not in profile data), unresolved unchanged | derived-name fields have no source range |
| LSP-04c function help coverage | `codex/lsp-04-funccov` -> cherry-picked | integrated | worker: helpdocs+hover 73 passed, selfdiff 0 differ; CR6 functions: 117 with help, 91 of 111 checked match `nargs` | follow-up LSP-04d: FTPClient/EmailSend `argspec` 0x9556/0x5556 = 6 args, the most a 16-bit packed argspec holds (docs/binary.md: 4-bit count + 2 bits per argument), so `nargs` 6 is probably a packing cap, not the real count; first establish how the checker validates their argument counts (checker code + fixtures), then decide label pairing |
| LSP-08a benchmarks | `codex/lsp-08-bench` -> cherry-picked | integrated | [cloud results](docs/lsp-rounds/LSP-08a-bench-cloud.md) under load (avg 4-9 on 4 CPUs): hover p95 12 ms, completion p95 38 ms; Main.CR6 analysis warm p50 716 ms; server didChange->publish p95 931 ms after debounce (target 500 ms, inconclusive under load); 200-edit RSS flat at 34 MB | host rerun: T-07 |
| LSP-R1 review fixes 1, 2, 4 | `codex/lsp-r1-fixes` -> cherry-picked | integrated | worker: reproducers 7 failed before / 13 pass after, tests/lsp 391, selfdiff 0 differ | compiler-side same omission (`symbols.scoped_view`, `cli._hides_times`, `timing.py`) suggested as a separate compiler task |
| LSP-04d function argument counts | `codex/lsp-04-argcount` -> cherry-picked | integrated (classification only) | worker: 75 passed, selfdiff 0 differ; checker `_call_arity_problem` uses oracle-backed `_ARITY_ALLOWED` (FTPCLIENT {6,10,11}, HTTPPUT {4,8,9}) / `_ARITY_RANGE`; fixtures: EmailSend/EmailRecv min 9 | follow-up LSP-04e (after R1, edits hover.py): signature help uses the checker's accepted-count sets; host probe T-08 for the rest |
| LSP-07c quick-fix core | `codex/lsp-07-quickfix` -> cherry-picked | integrated | worker: 22 passed, selfdiff 0 differ (its 2 tests/lsp failures were the lsp_bench strict-mode leak, fixed); misspelling sweep 16/16 top suggestion exact | worker claim "no diagnostic for an undeclared name read inside a Sub" not reproduced by the coordinator for `x = Undeclared1` in a called Sub (reported, line 3); shape unknown |

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
