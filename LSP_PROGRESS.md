# CR6 language server progress

Live implementation state and session handoff. Design and acceptance criteria:
[LSP_PLAN.md](LSP_PLAN.md). Compiler state and worker policy: [PROGRESS.md](PROGRESS.md).

## Current state (2026-10-12, LSP work in this configuration closed)

Human instruction 2026-10-12: R5 is the last LSP work in this cloud configuration. The work below
is integrated and pushed on `claude/laughing-franklin-08z9vo`; no worker worktree or `codex/*`
branch remains (every worker commit was verified present on the branch before deletion).

- Implemented and integrated: LSP-00..09 (analysis facade and source providers, positions, catalog
  and generated grammar, help provider, binding index; stdio server with diagnostics, completion,
  hover, signature help, outline, folding, links, definition/references/highlights, semantic
  tokens, rename, formatting, quick fixes, inlay hints, "Show Compiler Output" and "Show Table
  Fields"; workspace watching, folders and limits; VS Code client and packaged VSIX; user guide,
  benchmarks, install check, test tiers, Pyrefly gate) and review/host fix rounds R1-R5.
- Final gates on the integrated tree (R5 included, coordinator, cloud container):
  `tests/lsp -n 4` 781 passed; full suite `-n 4` 22,286 passed / 507 skipped; selfdiff 4343 cases,
  0 differ; `pyrefly check` 0 errors; `npm test` 30 pass; `tools/vsix_check.py` 11/11; `verify.py`
  (`CR6COMP=opencrbasic`) `BUILD  host tests 119 FAIL | CRBasic 16/16 compiled, 6 warning(s) |
  repository pass`, the 3 failures being the stale generated power/solar outputs from `2a1d36d`
  (not LSP; fix: rerun `scripts/solar_model.py`, `power_sim.py`, `power_workbook.py`).
- Reviews: rounds 1 (`39286384`), 2 (`f3069825`), 3 ([astra](docs/lsp-rounds/review-2026-10-11-ccba3666.md),
  [Fable](docs/lsp-rounds/review-2026-10-11-ccba3666-fable.md)) and 4
  ([`743da2ac`](docs/lsp-rounds/review-2026-10-12-743da2ac.md)): every finding fixed (round-3
  F2, F12 and F15 in LSP-R7a: `Alias` junk tokens counted in the ordinals, `BeginBurstTrigger`
  pair, `TO` dropped from read-position quick fixes).
  The R5 fixes themselves are not reviewed.
- Host (macOS only; release claim narrowed to macOS by human decision 2026-10-12): T-01..T-17 and
  H-01..H-10 accepted per the host records under `docs/lsp-rounds/host-tests/`, last
  [`e18cdf52`](docs/lsp-rounds/host-tests/2026-10-11-e18cdf52/RESULTS.md). Open host item: T-18
  (bounded compiler-output command, LSP-R5a).

## Remaining work (for a later session)

1. Host T-18 on a revision at or after `1302f14`.
2. Optional review of the R5 commits (`743da2ac..1302f14`).
3. Known limits kept by design (documented in the user guide): whole-line diagnostic ranges, rename
   does not detect a file that appears after analysis, formatting refuses programs with errors,
   completion capped at 200, CR6-only help, references skip entries whose analysis is pending,
   Windows/Linux not claimed; the open review items above.
4. `scratch/lsp/selfdiff-base.json` is local to a checkout (untracked); a host or new session
   recreates it at the round's base SHA before running selfdiff ("Resume here").
5. Compiler items found by this work are recorded in PROGRESS.md (not executed here).

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
- 2026-10-12: Windows and Linux cannot be tested in this work session; the release claim is
  macOS only. Windows/Linux stay untested and unclaimed (user guide sections 1 and 8, client README).
- 2026-10-16: help stays CR6-only for the first release (other loggers need their own extracted
  help; not available). The first release is accepted on the macOS validation alone; T-19
  (Windows/WSL re-checks after LSP-R6) is not a release gate. Remaining work before merging: one
  gpt-6.1-sol review round of the unreviewed LSP commits, its findings and the open earlier review
  items fixed, then a repository cleanup.

## Review round 2

[gpt-6.1-sol review through `f3069825`](docs/lsp-rounds/review-2026-10-11-f3069825.md): all four
round-1 findings confirmed fixed. Four new P2 findings, all accepted (reproducers, oracle probes for
1 and 4): (1) rename does not verify unedited dependencies' freshness (collision introduced
later), (2) rename freshness uses prepare-time cached bytes, (4) rename capture check ignores
`Public.<name>` member uses -> LSP-R2a; (3) feature positions use replacement bytes when the
editor encoding cannot represent a character -> LSP-R2b.

## Integration fixes by the coordinator

- `tools/lsp_bench.py` (LSP-08a) removed `OPENCRBASIC_STRICT` from `os.environ` at import, so the
  smoke test switched strict mode off for every later test in the session (found as 2 failures in
  `tests/lsp/test_analysis.py` when run after it). Now only `main()` does it.

## Final gates at `679f2f7` (2026-10-11)

selfdiff 0 differ; npm 16/16; tests/lsp 596 passed; full pytest 22,101 passed / 507 skipped.
`verify.py` (both runners): `BUILD  host tests 119 FAIL | ... | repository pass` -- three
generated-output hash checks (`test_power_sim`, `test_power_workbook`, `test_solar_model`) fail
because commit `2a1d36d` (human, PDF rename) edited `references/power/solar-panel/solar_params.json`
without regenerating the power/solar outputs. Not LSP code; left to the human
(`scripts/solar_model.py`, `power_sim.py`, `power_workbook.py` regenerate them). Host confirmed
at `679f2f7`: the old parameter bytes reproduce both stored digests exactly.

## Regressions found after integration

- `tests/lsp/test_rename.py::test_unresolved_and_unmodelled_and_quoted` fails on the branch since
  LSP-07a was integrated after LSP-05c (field references now resolve, refusal reason changed from
  `unmodelled` to `kind`). Coordinator did not rerun tests/lsp after that cherry-pick (rule
  tightened: rerun tests/lsp after **every** integration). Fixed by LSP-05d, which also found that
  a variable rename left `Quoted.a` member references behind (unsafe; now refused).

## Review findings

[Second gpt-6.1-sol review through `f3069825`](docs/lsp-rounds/review-2026-10-11-f3069825.md)
(human-requested, 2026-10-11): findings and prior-fix verification are recorded in the report.
The new findings await implementation.

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

Host records: `docs/lsp-rounds/host-tests/` (`602f6462`, `8b25214a`, `7c450ed5`, `c0fd9bf1`,
`e77ee5cc`/`4d88a186`, `1b01311d`). Accepted from them: T-01 oracle gates (no regression through
`e77ee5cc`), T-02 CR6 scenarios, H-01 range 32-33 (`e77ee5cc`), T-03 visual, T-07 idle benchmark
(hover p95 0.9 ms, completion 3.9 ms, diagnostics after debounce 134 ms), T-09 provider/API checks
16/16 (`1b01311d`; visual checks pending), H-05 strict-mode leak fixed and retested (`1b01311d`).

Accepted from `7d6db3b1`/`572e54d4` (macOS, VS Code 1.141.0, API-level): T-11 install 15/15; T-04
amendment (H-02 restart race gone, H-03 associations, logger switch); T-10 steps 1-3, 5-7, 9; T-12
rename/formatting/quick fix/H-04 display. Visual checks (F2 box, Ctrl+. menu, toasts, Problems
rendering) still pending.

Open:
- H-06 / H-07 and LSP-R2a/R2b: fixed and host-retested at `679f2f7`
  (`docs/lsp-rounds/host-tests/2026-10-11-679f2f70/RESULTS.md`: all T-13 checks pass; T-04/T-10/T-11/T-12 rerun pass at API level).
- Banner stderr copy and pygls' normal "Server shutting down" line show as `[error]` in the
  output channel (cosmetic; documented).
- Undo after a multi-file rename leaves files the rename opened open and clean (VS Code
  behaviour, observed at `ccba3666`; documented in LSP-R4a).
- Host record `ccba3666` (macOS, VS Code 1.141.0, packaged VSIX in an empty profile): T-14 pass;
  T-09/T-12 visual checks observed with the qualifications in its RESULTS.md. New:
  - H-08: Peek References from entry A on a symbol declared in a library shared with B lists A and
    Lib but not B (the references counterpart of review A1, which LSP-R3a fixed for rename only)
    -> LSP-R4a.
  - H-09: after a multi-file rename and one undo, the open/dirty state of files the rename opened is
    VS Code's and varies between runs (clean at `ccba3666`; open and dirty at `e18cdf52` after
    saving A before the undo); all texts are restored in every run. Documentation only; the guide
    no longer states one outcome.
  - H-10: the refusal for a click in a comment reads "no given analysis depends on the file";
    give it a message that names the comment -> LSP-R4a.
  - First `npm ci` on the host gave tar ENOENT warnings and a missing `tsc`; a repeat was clean
    (cause not established; not an LSP defect).
- Host record `e18cdf52` (macOS, installed VSIX; R4b probes and gates on `e1769132`):
  [RESULTS.md](docs/lsp-rounds/host-tests/2026-10-11-e18cdf52/RESULTS.md). Pass: T-15 (hints and
  both commands), T-16 macOS items (cross-entry rename and collision, dimension parameter, include
  size limit, `Units` formatting, nested signature, close after Python change, integer settings),
  H-08 and H-10 re-checks, T-17 cold completion (6/6 within 500 ms on R4b; 0 items before),
  T-17 latency (after-debounce p95 149 ms; GUI squiggle upper bounds 984-1249 ms). Full suite
  22471 passed; npm 30; vsix_check 11/11; pyrefly 0; verify.py the 3 known freshness failures.
  Not run there: selfdiff (the baseline is local to the cloud container), Windows F5,
  Linux/Windows GUI, the empty-list/next-keystroke fallback in the GUI.
- Windows/Linux: not claimed (human decision 2026-10-12).

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
| LSP-wire features in server | `codex/lsp-wire` -> cherry-picked | integrated, gates passed | worker: tests/lsp 405 x3, selfdiff 0 differ, npm 10/10; option (b) pickled program from the pool (first hover p50 39 ms vs 575 ms in-thread); review finding 3 fixed (negotiated encoding for diagnostics) | at `f54eef2`: tests/lsp 459 passed, selfdiff 0 differ, npm 10/10, full pytest 21,986 passed / 507 skipped, native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass`; host T-09 provider checks 16/16 at `1b01311d` | T-09 visual |
| LSP-01e client fixes H-02/H-03 | `codex/lsp-01-client` -> cherry-picked | integrated | worker: npm 16/16, server tests 24, selfdiff 0 differ; client no longer uses `synchronize.configurationSection` (own gated notification, server pulls) | host retest: T-04 amendment |
| LSP-04e checker arity in signature help | `codex/lsp-04-arity` -> cherry-picked | integrated | worker: hover+helpdocs 85, tests/lsp 467, selfdiff 0 differ; FTPClient/HTTPPut/NetworkTimeProtocol now paired with help names; "Help: still to do"/"Help: not configured" placeholders | EmailSend/TCPOpen stay argN until the T-08 fixture enters the checker (PROGRESS.md compiler item 5) |
| LSP-04a-optmark | `codex/lsp-04-optmark` -> cherry-picked | integrated | worker: helpdocs+hover 70 passed, tests/lsp 367, selfdiff 0 differ; FileOption optional in FTPClient/EMailSend/HTTPPost/HTTPPut | gap: `coverage_report` counts instructions only; FTPClient etc. are function-table entries (coordinator check) |
| LSP-07a rename core | `codex/lsp-07-rename` -> cherry-picked | integrated (core, not wired) | worker: 21 passed, tests/lsp 390, selfdiff 0 differ; full sweep over crbasic/ 940 accepted / 171 refused (string_mention 109, unindexed_code 62), 0 compile differences | wire after LSP-06; known weakness: the sweep's per-line comparison accepts an unchanged table name |
| LSP-07b formatting core | `codex/lsp-07-format` -> cherry-picked | integrated | coordinator rerun 22 passed; worker: tests/lsp 391, selfdiff 0 differ; CHM 198/198 accepted programs identical after formatting, idempotent 507/507 | `SlowSequence` indented inside BeginProg (style, differs from Main.CR6); initializer pulls not modelled; `xmlparse_1` message changes with indentation -> T-06 |
| LSP-05c table fields | `codex/lsp-05-fields` -> `b2bdd39` | integrated | coordinator rerun 61 passed; worker: tests/lsp 376, selfdiff 0 differ; crbasic unmodelled 96 -> 6 (`Status.CardBytesFree` not in profile data), unresolved unchanged | derived-name fields have no source range |
| LSP-04c function help coverage | `codex/lsp-04-funccov` -> cherry-picked | integrated | worker: helpdocs+hover 73 passed, selfdiff 0 differ; CR6 functions: 117 with help, 91 of 111 checked match `nargs` | follow-up LSP-04d: FTPClient/EmailSend `argspec` 0x9556/0x5556 = 6 args, the most a 16-bit packed argspec holds (docs/binary.md: 4-bit count + 2 bits per argument), so `nargs` 6 is probably a packing cap, not the real count; first establish how the checker validates their argument counts (checker code + fixtures), then decide label pairing |
| LSP-08a benchmarks | `codex/lsp-08-bench` -> cherry-picked | integrated | [cloud results](docs/lsp-rounds/LSP-08a-bench-cloud.md) under load (avg 4-9 on 4 CPUs): hover p95 12 ms, completion p95 38 ms; Main.CR6 analysis warm p50 716 ms; server didChange->publish p95 931 ms after debounce (target 500 ms, inconclusive under load); 200-edit RSS flat at 34 MB | host rerun: T-07 |
| LSP-R1 review fixes 1, 2, 4 | `codex/lsp-r1-fixes` -> cherry-picked | integrated | worker: reproducers 7 failed before / 13 pass after, tests/lsp 391, selfdiff 0 differ | compiler-side same omission (`symbols.scoped_view`, `cli._hides_times`, `timing.py`) suggested as a separate compiler task |
| LSP-04d function argument counts | `codex/lsp-04-argcount` -> cherry-picked | integrated (classification only) | worker: 75 passed, selfdiff 0 differ; checker `_call_arity_problem` uses oracle-backed `_ARITY_ALLOWED` (FTPCLIENT {6,10,11}, HTTPPUT {4,8,9}) / `_ARITY_RANGE`; fixtures: EmailSend/EmailRecv min 9 | follow-up LSP-04e (after R1, edits hover.py): signature help uses the checker's accepted-count sets; host probe T-08 for the rest |
| LSP-07c quick-fix core | `codex/lsp-07-quickfix` -> cherry-picked | integrated | worker: 22 passed, selfdiff 0 differ (its 2 tests/lsp failures were the lsp_bench strict-mode leak, fixed); misspelling sweep 16/16 top suggestion exact | worker claim "no diagnostic for an undeclared name read inside a Sub" not reproduced by the coordinator for `x = Undeclared1` in a called Sub (reported, line 3); shape unknown |
| LSP-06a workspace resilience | `codex/lsp-06-workspace` -> `80a6fb8` | integrated, gates passed | at `80a6fb8`: tests/lsp 534, selfdiff 0 differ, npm 16/16, full pytest 22,044 passed / 507 skipped, native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass`; full rename sweep accepted 940 / refused 196 (unmodelled 25 new after 05d), 0 problems | host T-10 |
| LSP-05d navigation accessor | `codex/lsp-05-accessor` -> `a2a7fe7` | integrated | worker: tests/lsp 504 passed, selfdiff 0 differ; fixed the rename regression (field refs refused as `unmodelled`) and a second one (rename of `a` left `Quoted.a` behind: all table member references now refuse) | coordinator: tests/lsp + full rename sweep running |
| LSP-03d scan cache | `codex/lsp-03-scancache` -> cherry-picked | integrated | worker: tests/lsp 521, selfdiff 0 differ; completion on an unchanged buffer p50 15.6 -> 2.2 ms (cache hit); a changed buffer still costs ~16 ms | - |
| LSP-08b user guide | `codex/lsp-08-guide` -> `adf6654` | integrated | [docs/lsp-user-guide.md](docs/lsp-user-guide.md); 25 relative links checked, 0 broken; written on a base before LSP-06a/07-wire | refresh after LSP-07-wire; repo-root `.vscode/settings.json` added (human decision 2026-10-11): `entries` `crbasic/*.CR6`, `helpDirectory` `extracted_chm` |
| LSP-07-wire rename/format/quick fixes in server + H-04 display | `codex/lsp-07-wire` -> `fe33b2e` | integrated, gates passed | at `810b8f9`: tests/lsp 578, selfdiff 0 differ, npm 16/16, full pytest 22,083 passed / 507 skipped, native verify `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` | host T-12; review round 2 findings -> LSP-R2 |
| LSP-R2a rename review fixes 1, 2, 4 | `codex/lsp-r2-rename` -> `463a565` | integrated | combined tests/lsp 592 passed (coordinator); worker: reproducers failed before / pass after, full rename sweep 940/196, 0 problems, selfdiff 0 differ | server should pass open buffers as `source` -> LSP-06c; limit: a file absent at analysis that appears later is not detected |
| LSP-R2b encoding position mapping (review 3) | `codex/lsp-r2-encoding` -> `3d76e93` | integrated | combined tests/lsp 586 then 592; worker: reproducer failed before / pass after; one conversion point (`features.Ctx`, `positions.EditorBytes`) | - |
| LSP-06c host H-06/H-07 + rename source | `codex/lsp-06-hostfix` -> `a42c7b0` | integrated, gates passed | combined tests/lsp 596 passed (coordinator); worker: 596 x3, selfdiff 0 differ; info note for every open non-entry file; `**/*` delete watcher; rename gets open buffers as `source` | host T-13 |
| LSP-08b-refresh user guide | `codex/lsp-08-guide2` -> cherry-picked | integrated | guide updated to `810b8f9`; 32 links, 0 broken; R2 findings listed as known limits | update after R2 lands |
| LSP-01g contribute tuning settings | `codex/lsp-01-settings` -> cherry-picked | integrated | worker: npm 16/16, server tests 25, selfdiff 0 differ; sync test package.json vs config.py | Settings UI: host check |
| LSP-03c completion performance | `codex/lsp-03-complperf` -> cherry-picked | integrated | worker: editing+features 147 passed, selfdiff 0 differ; catalog items cached, cap 200 with `is_incomplete`; speed-up not shown under load (`editing._scan` ~15 ms per call now dominates) | server passthrough of isIncomplete -> LSP-06a; next: cache `_scan` per text |
| LSP-08c install check | `codex/lsp-08-install` -> cherry-picked | integrated | [cloud run](docs/lsp-rounds/LSP-08c-install-cloud.md) 15/15 checks: wheel 492 KiB / 105 files with all 46 profile data files; `crbc` output equals the checkout; core imports without pygls; `crbc-lsp` banner names the installed path | macOS/Windows: T-11 |
| LSP-06b other-logger server tests | `codex/lsp-06-profiles` -> cherry-picked | integrated | worker: 24 passed x3, tests/lsp 563, selfdiff 0 differ; cr1000x/cr300/cr350 through the server match `analyze`; no server defects | - |
| LSP-08d test-run speed | `codex/lsp-08-speed` -> cherry-picked | integrated | coordinator on combined tree: tests/lsp -n 4 721 passed in 96 s (serial was ~360 s), fast tier `-m "not slow"` 709 in 69 s, `-m server` 136 in 53 s, pyrefly 0, selfdiff 0 differ; `server` mark added to the LSP-09c server tests (`test_lsp09_server.py`, 4 tests in `test_features_positions.py`) that postdate the worker base; worker: full suite -n 4 359 s vs 653 s serial | - |
| LSP-R3 review round 3 fixes | `codex/lsp-r3{a..e}` -> cherry-picked | integrated | coordinator on combined tree `f6ce2b2`: tests/lsp -n 4 757 passed, full suite -n 4 22262 passed / 507 skipped, selfdiff 0 differ, pyrefly 0, npm 30/30; verify.py (CR6COMP=opencrbasic) `BUILD  host tests 119 FAIL | CRBasic 16/16 compiled, 6 warning(s) | repository pass` (the 3 known stale power/solar digests). Parser change additive only (`Param.dim_exprs`, compare=False). A2 added optional `AnalysisRequest.max_file_bytes` / `LimitedSource` (default unchanged) | host T-16 |
| LSP-R4a host findings H-08..H-10 | `codex/lsp-r4a` -> cherry-picked | integrated | coordinator: tests/lsp -n 4 763 passed, selfdiff 0 differ, pyrefly 0, npm 30/30; references united across every current entry reading the declaration file (entries still pending are skipped, not awaited); comment/inactive-branch rename click refused `not_a_name` with an accurate message; worker's in-place edit of T-12 step 5 replaced by an appended amendment (append-only rule) | host: T-09 references re-check |
| LSP-R4b member completion before the first analysis | `codex/lsp-r4b` -> cherry-picked | integrated | defect confirmed over stdio: with no finished analysis every member list (`Status.`, `Public.`) was empty with `isIncomplete: false`; host's Status/Public difference attributed to timing (inferred). Fix: completion waits up to `featureWaitMs` only while no program exists; without one it answers `isIncomplete: true`. Coordinator: tests/lsp -n 4 770 passed, selfdiff 0 differ, pyrefly 0, npm 30/30 | host T-17 item 1 |
| LSP-R5 review round 4 fixes | `codex/lsp-r5{a,b}` -> cherry-picked | integrated | coordinator on the combined tree: tests/lsp -n 4 781 passed, full suite 22,286 passed / 507 skipped, selfdiff 0 differ, pyrefly 0, npm 30, vsix_check 11/11, verify.py only the 3 known freshness failures. R5a: compiler-output command runs in the worker pool under maxFileBytes/analysisTimeoutMs, refusals `too_large`/`timeout`; R5b: link gate gets Markdown only, scheduler fixture commits unsigned, inlay `Units` match on a token boundary | host T-18 |
| LSP-R6 Windows/WSL host findings H-11..H-21 | `codex/lsp-r6{a,b}` -> cherry-picked | integrated | Codex gpt-6.1-sol workers + read-only review (1 P2 fixed: stale diagnostics when a file's URI changes; P3 skipped: output tab restored from before the upgrade keeps its old URI). Coordinator on the combined tree (macOS): tests/lsp -n 4 805 passed, pyrefly 0, npm 36/36, verify.py `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository FAIL` (pre-existing `opencrbasic/.venv` only). H-11 NUL std handles (Windows-only, unverified), H-12 taskkill, H-13 Scripts/*.exe, H-15 test path, H-16 URIs follow the folder spelling, H-18 no toast while not running, H-19 output URI without leading slash, H-20 `[crbasic]` maximumLength 0, trust declared, PanelTemp option rows in hovers; H-17/H-21/didClose documented; H-14 root `.gitattributes`: `-text` for `*.CR6`/`*.crb` and `opencrbasic/tests/**` (byte-exact checkout; CRLF CLI fixtures kept) | host T-19 |
| LSP-08e Pyrefly type checking | `codex/lsp-08-types` -> cherry-picked | integrated | coordinator on combined tree: `pyrefly check` 0 errors (was 38), tests/lsp `-n 4` 596 passed in 99 s; no new suppressions; latent bug fixed (`untitled:` URI gave a bare TypeError in `fs_path`) | - |
| LSP-08f VSIX packaging | `codex/lsp-08-vsix` -> cherry-picked | integrated | coordinator rerun: npm 16/16, `tools/vsix_check.py` 11/11 (VSIX 232 files, ~396 KB, runtime closure 9 packages, no src/tests/help) | host T-14 |
| LSP-09a inlay hints core | `codex/lsp-09-inlay` -> cherry-picked | integrated (core, not wired) | worker: 15 passed, tests/lsp -n 4 611, selfdiff 0 differ; Main.CR6 whole file 344 hints in 0.08 s, never `argN` | wire in LSP-09c |
| LSP-09b commands core (compiler output, table fields) | `codex/lsp-09-commands` -> cherry-picked | integrated (core, not wired) | worker: 58 passed, tests/lsp -n 4 654, selfdiff 0 differ; `compiler_output` equals `crbc` stdout on all crbasic/ + CHM sample, writes no files | wire in LSP-09c |
| LSP-09c wiring (inlay hints, two commands) | `codex/lsp-09-wire` -> cherry-picked | integrated | coordinator on combined tree: tests/lsp -n 4 690 passed, npm 25/25, pyrefly 0; worker: new server tests 21 x3, selfdiff 0 differ, vsix_check 11/11 | host T-15 |

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
