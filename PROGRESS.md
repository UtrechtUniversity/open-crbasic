# opencrbasic progress

Current compiler state and handoff, updated **2026-10-27 during development round 47**
(repository-wide Pyrefly gate). Round 46 is committed at `c5877c5c`; round 47 is in progress.
The live Codex usage tool reported the five-hour window **100% used** at this handoff;
stop at the human-requested usage boundary, with round 47 incomplete and uncommitted.
Inspect `git status` before editing; use Git to check publication state.

Goal, priorities and the v1.0 roadmap: [PLAN.md](PLAN.md#remaining-work-strategy). The human
decision of 2026-10-24 makes verdict safety, execution mode and the first error the priorities;
later-diagnostic order and cascades no longer block a release. Previous measurements, round logs
and superseded handoffs: [progress history](docs/progress-history.md). Language-server work has
its own [plan](LSP_PLAN.md) and [progress ledger](LSP_PROGRESS.md).

## Current status

The CR6, CR1000X, CR300 and CR350 profiles are implemented. Reference crashes and runner
failures are not compiler verdicts. Exact diagnostic agreement means equal exit codes and
normalized ordered stdout, not byte parity.

Latest observed validation, on macOS Apple Silicon with Wine 11.17 and Python 3.15:

| Check | Latest result |
|---|---|
| `pytest -q -n 4 -m 'not server'` | 55,324 passed, 311 skipped, 440 strict xfail (round 46) |
| `pytest -q -m server` | 163 passed (round 46) |
| Round-24–31 fixture audits (15 fixtures, cached oracle) | 0 differing, 0 without verdict |
| Round-31 fixtures (four profiles): measurement spans / settling and overlap / alias cascades / alias collisions / review fixes | 1,014 / 801 / 300 / 514 / 420 reference observations; 960 / 749 / 288 / 512 / 406 match; 54 / 52 / 12 / 2 / 14 strict xfail |
| Earlier alias fixtures (r26–r30) | r26 282/282, r27 152/152, r28 368/368, r29 headers 180/180, r29 Strings 320/320, r29 review 104/104, r30 dimensioned 180/180 match; r30 review 92 match, 8 strict xfail |
| Round-24 destination/settling/FieldNames fixture | 674 observations: 672 match, 2 strict xfail |
| Saved fuzz replay, `fuzz_replay.py --recorded` (round 46) | Round 46 retains all round-45 impact counts; regression gate passes and baseline is unchanged. Round 45, 3,338 inputs: 2,569 exact (four more); CR6/CR1000X first-error misses 23, earlier first locations 5, same-location messages 107; verdict/mode counts unchanged; regression gate passes and baseline records five improvements. Round 44, 3,338 inputs: 2,565 exact; two more CR1000X same-location messages become exact, other impact counts unchanged from round 43; no regression, baseline refreshed for those rows. Round 43, 3,338 inputs: 2,563 exact; one same-location message becomes exact, all other impact counts unchanged from round 41; no regression, baseline refreshed for that row. Round 41, 3,338 inputs: 2,562 exact; CR6/CR1000X same-location messages 112, first-error misses 24, earlier first locations 6; 43 false accepts, no false reject; gate against the round-40 baseline passes, baseline refreshed. Round 40, 3,338 inputs after re-recording the 121 sibling reproducers: 2,552 exact; CR6/CR1000X first-error misses 24, earlier first locations 6, same-location messages 133; 43 false accepts, no false reject; one stale recording (operational); gate against the round-39 baseline passes, baseline refreshed. Round 39, 3,338 inputs: 2,512 exact; CR6/CR1000X first-error misses 32 and earlier first locations 6 (58 and 13 before); 43 false accepts, no false reject; gate against the round-38 baseline passes, baseline refreshed. Round 38, 3,338 inputs (43 new from seeds 429-432): 2,455 exact, 43 false accepts (CR6 18, CR1000X 9, CR300 9, CR350 7), no false reject; the gate against the round-37 baseline passes with no regression, and the baseline is refreshed. Round 37, 3,295 inputs: 2,439 exact, 43 false accepts (CR6 18, CR1000X 9, CR300 9, CR350 7), no false reject; the gate against the round-36 baseline passes with no regression, and `tests/replay_baseline.json` is refreshed. Round 36, 3,295 inputs (74 new from seeds 425-428): 2,436 exact, 44 false accepts (CR6 18, CR1000X 10, CR300 9, CR350 7), no false reject; the gate against the refreshed `tests/replay_baseline.json` passes, and no round-35 row regressed (one CR350 false accept became exact). Round 35, 3,221 inputs (63 new from seeds 421-424): 2,423 exact, 44 false accepts (CR6 18, CR1000X 9, CR300 9, CR350 8), no false reject, gate passes. Round 34, 3,158 inputs: 2,410 exact and 35 false accepts (CR6 11, CR1000X 7, CR300 9, CR350 8), no false reject; the gate against `tests/replay_baseline.json` passes. Round 33, whose output was unchanged from round 31 on every input: 2,390 exact; 48 false accepts; no false reject; one mode and 15 warning differences; 60 missing the first error, 35 earlier first location, 220 same location with a different message, 370 later-diagnostic or order differences; 19 operational (one recorded result is a Wine page fault). The two extra first-error misses against the round-31 table are the `hang/dd9cc650482d` reproducers, whose recorded reference result differs from the round-31 rerun |
| GEUS corpus, `difftest.py --group geus --logger cr1000x` (round 46) | 66/66 exact (round 32: 64 exact, two false rejects); no GEUS `.CR6` programs |
| Round-31 replay against the round-30 checker | Two outputs changed: one message difference became exact, one changed text but remains a message difference. Reference compilers were not rerun |
| Fresh fuzz, seeds 439/440 (round 46, `--relevant`, 100 mutants each, CR6/CR1000X, no shrinking) | 194 exact, 5 diagnostic differences and 1 genuine CR1000X execution-mode difference (`00b7a2ff9df4`, unresolved); no false accept/reject, crash, hang or implementation exception. Two known reference seed faults excluded |
| Fresh fuzz, seeds 437/438 (round 45, `--relevant`, 100 mutants each, CR6/CR1000X, no shrinking) | 196 exact, 3 message differences, 1 mutant without a reference verdict; no false accept/reject, crash, hang or implementation exception. Both seed loaders also exclude the known `pointervariable_2.CR6` reference fault |
| Fresh fuzz, seeds 429-432 (round 38, `--relevant`, 500 mutants each, CR6 429/430 and CR1000X 431/432) | 1,942 exact, no false reject, 2 false accepts (both fixed), 44 message differences, 3 warning differences, 17 runner failures |
| Fresh fuzz, seeds 425-428 (round 36, `--relevant`, 500 mutants each, CR6 425/426 and CR1000X 427/428) | 1,913 exact, no false reject, 13 false accepts, 64 message differences, one warning difference, 15 runner failures; 12 false accepts fixed in round 36 |
| Fresh fuzz, seeds 421-424 (round 35, 500 mutants each, CR6 421/422 and CR1000X 423/424 with GEUS seeds and the new operators) | 1,923 exact, 3 false rejects, 17 false accepts, 48 message differences, 18 runner failures; all three false rejects and 8 false accepts fixed in round 35 |
| `tools/timingtest.py` (round 31, by the integration worker) | repository 19/19 on all four profiles; help examples CR6 506/507, CR1000X 505 + one documented mismatch (`covspa_1`), CR300/CR350 505/507 |
| CR6 TDF and table-list corpus comparison (round 22) | Both 210/210 byte-identical; 308 reference rejections, six missing expected artifacts and one reference fault |

The impact classes are now produced by `tools/impact.py` (`fuzz_replay.py` and `difftest.py`
print the per-profile table); [PLAN.md](PLAN.md#where-v10-starts-round-30-measurement) keeps the
round-30 starting table.

The four 2026-10-24 probe false accepts outside the replay (unknown table field, unknown
`Settings.` name, assignment to an unknown `Status.` field, `Status.` initializer) are fixed on
CR6/CR1000X since round 34.

GEUS corpus: the reference CR1000X compiler accepts all 66 GEUS `.CR1X` programs; since round 33
opencrbasic matches all 66 exactly ([details](PLAN.md#where-v10-starts-round-30-measurement)).

Four-profile diagnostic comparison, rerun in round 46 and unchanged from round 28
(`tools/difftest.py --group all`):

| Profile | Repository `.CR6` programs: exact / same verdict, different text | Help examples: exact / reference failures |
|---|---|---|
| CR6 | 19 / 0 | 506 / 1 |
| CR1000X | 19 / 0 | 506 / 1 |
| CR300 | 8 / 11 | 505 / 2 |
| CR350 | 9 / 10 | 505 / 2 |

No corpus verdict mismatches, implementation exceptions or lost exact cases were observed on
usable reference results. The CR300/CR350 repository rejections reflect CR6-specific programs. The
help-corpus failures are `pointervariable_2.CR6` on all four profiles and `strcomp_3.CR6` on
CR300/CR350. The differential gates exit 1 while reference verdicts are missing; compare individual
valid results and keep failures separate.

Repository verification passed in round 46 both with the reference compiler and with
`CR6COMP=opencrbasic`:

```text
BUILD  host tests 146 pass | CRBasic 20/20 compiled, 6 warning(s) | repository pass
```

Expected warnings are owned by [repository build status](../STATUS.md#build). Compilation and host tests do
not establish logger runtime behavior. No hardware tests were run.

Rounds 23–31 did not rerun full corpus TDF/table-list comparisons. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

**Round 47** (repository-wide Python type checking, current human request; in progress).
- Add Pyrefly to `scripts/verify.py` for every tracked or new, non-ignored Python file, including
  tests, tools, field scripts and drawing helpers. The initial tracked inventory has 461 files.
  Ignored environments, caches and scratch probes are outside the source inventory.
- Initial default-preset run: 1,030 errors; after import roots and the development dependencies
  were configured, 1,014 errors remained. The current check is recorded in `scratch/r47/checkpoint.json` and
  `checkpoint.log`; the inventory now includes the two new shared helper modules. Baseline JSON,
  exact inventory and commands are in `scratch/r47/`. No error baseline or blanket suppression is being introduced.
- `pyrefly.toml` at the repository root owns the common configuration; the old LSP-only include
  list is removed. `scripts/` pins Pyrefly 1.3.1 and installs the type-checking import dependencies
  with uv. `verify.py` now propagates type-check failures into its BUILD summary and exit status;
  inventory and failure-propagation tests are added (6/6 pass). Corrections and validation remain
  before commit; this round is intentionally uncommitted while the type gate fails.
- Corrected profile/AST/provenance contracts, a structural help-lookup interface, explicit numeric
  optional guards, CLI option types and positive test assertions. The parser/constant/hover focused
  suites pass 134 tests. An intermediate indentation regression in a pointer-type annotation edit
  was caught by the focused tests and corrected; retain both logs in `scratch/r47/`.
- No error baseline, broad ignores or Any replacement is being used to silence remaining issues.
  In particular, scalar conversions not established by the existing code remain type errors.
  Current default-preset result: **402 errors across 463 files** (`scratch/r47/imports-corrected.json`).
  Full non-server validation first reported 55,001 passed, 311 skipped, 440 xfail and 11
  collection errors caused by the new helper import path. The package-relative import correction
  collects all 824 LSP tests successfully. The corrected full run passes **55,324 tests,
  311 skipped, 440 strict xfail**, with no errors (`scratch/r47/pytest-final.log`).
  `scratch/r47/validate-rest.py` then runs server tests, corpus comparisons, replay, fixture
  audits and both verify backends sequentially. Final results: server **163 passed**; saved
  replay regression gate passed; both fixtures have zero differences and zero missing verdicts;
  four-profile corpus results are unchanged, including known reference faults, and GEUS is 66/66.
  Both reference and opencrbasic verify backends exit 1 solely at the new type gate:

  ```text
  BUILD  Pyrefly 463 files FAIL | host tests 148 pass | CRBasic 20/20 compiled, 6 warning(s) | repository pass
  ```

  Warnings are the existing uncalled-table warnings. Read `validation-exits.json`,
  `corpus-exits.json` and individual logs; the corpus wrapper exits zero even when a child
  comparison exits one for the known missing reference verdicts. No hardware tests ran.
  Resume details and exact commands: `scratch/r47/RESUME.md`.
- The new round-46 CR1000X mode candidate is deferred until this requested round is complete.
  Do not execute local maintenance. Continue the user's committed-round workflow after validation.

**Round 46** (M1/M2, Timer TimNo and nonfinite integral arguments; one session, no subagents).
- Fresh seed-438 `cr1000x/message_diff/71107f867ad9` minimized to `Timer(1.5,Sec,2)`,
  a false accept on all four profiles. TimNo now uses the shared integral-argument rule in
  pass 1. The same walk rejects bare/signed `NAN`/`INF` at its integer-typed positions;
  the existing HTTP/FTP timeout classifier is unchanged.
- CR6 trace: `FUN_004945c0` -> `FUN_0049efb0` -> `FUN_0049edf0`, integer-conversion mode
  enabled for TimNo and reset before Units/Option. Evidence:
  `tests/fixtures/r46_timer_integral.json`, 392 observations, 380 exact, 12 strict xfail, binary
  hashes included. Final audit: 0 differing, 0 without verdict.
  Focused suites: 2,931 passed, 63 strict xfail. Rules, accepted controls and boundaries:
  [Timer number](docs/checker.md#timer-number-and-nonfinite-integral-arguments-round-46).
- Context probes prevented new false rejects for CR6/CR1000X storage named `Timer` and retained
  earlier unknown-name/table-placement diagnostics. Inline output instructions now reach the
  existing table-structure check through the flattened statement view, fixing a pre-existing
  false accept. `Const NAN = 1`/`Const INF = 1` controls retain their values.
- Initial validation of the smaller implementation is preserved in `scratch/r46/`; its full suite
  passed 55,240 tests with 311 skips and 428 strict xfail, and its remaining gates completed.
  The final implementation in `scratch/r46/final/` passed 55,324 non-server tests with 311 skips
  and 440 strict xfail; server suite 163 passed. Recorded replay retains all round-45 impact
  counts (2,569 exact of 3,338); its no-regressions gate passes and the baseline is unchanged.
  The motivating fresh reproducer is exact. Four-profile repository/help and GEUS results are
  unchanged, including known reference faults. Both final repository verification backends pass
  with the BUILD line above. Commands and exit states: `scratch/r46/final/validation-exits.json`.
  `git diff --check` passes. No hardware tests, push or local maintenance.
- Fresh relevant fuzz, CR6 seed 439 and CR1000X seed 440, 100 mutants each: 194 exact, five
  diagnostic differences and one genuine execution-mode difference (`00b7a2ff9df4`, CR1000X,
  reference SequentialMode versus our PipelineMode). No false accept/reject, crash, hang or
  implementation exception; each seed loader excludes the known `pointervariable_2.CR6` fault.
  The mode gap is unresolved and is not in the saved replay. Raw results and six reproducers
  are preserved in `scratch/r46/final/fuzz/`.
- Next round, human instruction 2026-10-27: add Pyrefly checking to `scripts/verify.py` for all
  repository Python files and resolve the issues found. Prioritize that round before the new
  mode candidate or other parity work.

**Round 45** (M1/M2, quoted routine-parameter names; one session, no subagents).
- Fixed fresh round-42 `cr6/message_diff/0a9af24e1266`: a quoted parameter token in a `Sub`
  header is a literal declaration name, including its quotes. It does not declare the ordinary
  identifier inside the quotes. An undefined body name is now diagnosed in the body, and a
  header whose quoted parameter is unused compiles. The same rule applies to `Function`.
- CR6 trace: `Sub`/`Function` thunk `0x403ff8` -> `FUN_00451f70`; pass-1 resolver
  `FUN_004baf00` cases 12/35 -> `FUN_004b9700` -> `FUN_004d3560`. Evidence:
  `tests/fixtures/r45_quoted_parameters.json`, 172 observations, 132 exact and 40 strict xfail;
  independent audit: 0 differing, 0 without verdict. Focused suites: 688 passed, 40 xfail.
  Rules, accepted controls and gaps: [quoted parameters](docs/checker.md#quoted-routine-parameter-names-round-45).
- Recorded replay: 3,338 rows, 2,569 exact (four more); one CR1000X earlier first location now
  has the correct first error, retaining later-diagnostic differences. CR6/CR1000X first-error
  misses 23, earlier first locations 5 and same-location messages 107. No impact regression
  against round 44; the compact baseline records these five improvements. The fresh motivating
  reproducer is exact too. Verdict and execution-mode counts are unchanged.
- Fresh relevant fuzz: CR6 seed 437 and CR1000X seed 438, 100 mutants each, no shrinking:
  196 exact, three diagnostic differences, one mutant with no reference verdict; no false
  accept/reject, crash, hang or implementation exception. Each seed loader also excludes the
  known `pointervariable_2.CR6` reference fault. Logs, summaries and three saved reproducers:
  `scratch/r45/fuzz/`; these are not added to the saved corpus.
- Validation: non-server suite 54,944 passed, 311 skipped, 428 strict xfail; server suite
  163 passed. Four-profile repository/help results unchanged, including known reference faults;
  GEUS CR1000X 66/66 exact. Both repository verification backends pass with the BUILD line above.
  Commands and exit states: `scratch/r45/validation-exits.json`; individual logs stay alongside it.
  `git diff --check` passes. No hardware tests or push.
- Next bounded candidates: numeric routine-parameter declaration diagnostics (round-45 fixture
  gaps), or fresh seed-438 first-error `71107f867ad9` (earlier `Parameter must be integral.`
  versus our later unmatched-block error). The other fresh findings are `cf7630a9abd7` (SW12
  port diagnostic) and `f67f1e2a1258` (unterminated string in a call). These need minimization
  and fresh probes before implementation.
- Human instruction, 2026-10-27: finish, verify and commit each round, then continue further
  rounds until the current five-hour Codex usage window is consumed. Do not execute local
  maintenance. No hardware test or push is requested.

**Round 44** (M2, unreadable one-line If conditions; one session, no subagents).
- Fixed fresh GEUS/CR1000X seed-434 `0818efcb5653`: a split condition name in
  `If BattVolt <= Batt  Threshold Then SW12(...)` gets the condition-only symbol diagnostic.
  CR6/CR1000X now reuse the existing block-condition scan for parser failures before `Then`;
  syntax errors before an unknown name stay pass 1, unknown first operands stay pass 2.
  Parsed inline-body diagnostics and the CR300/CR350 path are retained.
- Traced CR6 `If` thunk `0x40625d` -> `FUN_00450b20` -> `FUN_0045a9e0`, plus the read loop's
  `Then` separation and type-7 expression scan. Evidence: `tests/fixtures/r44_inline_conditions.json`,
  132 observations, 114 exact and 18 strict xfail; independent audit 0 differing, 0 without verdict.
  Focused condition suites: 1,345 passed, 18 xfail. Rules and known gaps:
  [one-line If conditions](docs/checker.md#unreadable-one-line-if-conditions-round-44).
- Recorded replay: 3,338 rows, 2,565 exact (two more); `87115edb8712` and `f21f0323bbb8` on
  CR1000X are now exact. All other impact counts unchanged; no regression against round 43,
  and the compact baseline records those two improvements. The fresh motivating reproducer is
  exact too. No acceptance, mode or first-location regression was observed.
- Fresh relevant fuzz: CR6 seed 435 and CR1000X seed 436, 100 mutants each, no shrinking.
  194 exact, three message differences, one warning-only difference (the fuzz tool labels it
  `mode_diff`, but both results say PipelineMode), and two mutants with no reference verdict.
  No false accept/reject, implementation exception, crash or hang of opencrbasic. Each seed
  loader also excludes the known `pointervariable_2.CR6` reference fault. Raw summaries and the
  four saved reproducers stay in ignored `scratch/r44/fuzz/`; they are not in the saved corpus.
- Validation: final non-server suite 54,812 passed, 311 skipped, 388 strict xfail; server suite
  163 passed. Four-profile repository/help results unchanged, including known reference faults;
  GEUS CR1000X 66/66 exact. Both repository verification backends pass with the BUILD line above.
  `git diff --check` passes. No hardware tests, commit or push for round 44.
- Next bounded candidates: the malformed `Sub` parameter first error (`0a9af24e1266`), the
  quoted-Then condition boundary and malformed one-line ElseIf (strict fixture gaps), or M3's
  report/configuration framework. For new fuzz findings inspect `scratch/r44/fuzz/` and separate
  the warning-only row `522ddaa3d29c` from execution-mode differences.
- End-of-round maintenance: `scratch/local-maintenance/r44-2026-10-26/HANDOFF.md` owns its actual
  startup status, queue hashes, logs, model/settings and exact resume command. Supervisor PID
  83012 exited after the enabled swap-growth guard stopped model loading; **not running**, zero
  completed packets. Round 43's equivalent failed startup remains preserved. Do not retry based
  only on the free-memory percentage; diagnose the loading/resource condition first. Prepare a
  fresh queue after any reviewed input or HEAD change.

**Round 43** (M1/M2, bounded include filename constants; one session, no subagents).
- Fixed fresh seed-433 `d1cd86d16934`: `IncludeSection("Definitions",(1+2))` is an include
  filename/extension diagnostic during parsing, not a silent omission of the include followed by
  missing-definition errors. `Include` has the same constant-name behavior. CR6/CR1000X reject
  invalid extensions; CR300/CR350 usually warn, retaining their existing extension rule.
- Traced CR6 `FUN_004b6840` -> `FUN_004ba850` -> `FUN_004b66f0`; the parser uses prior constants
  and active branches. Shared extension validation avoids diverging from the literal loader.
  Loading files named by expressions with valid extensions remains outside this bounded rule.
- Evidence: `tests/fixtures/r43_include_names.json`, 148 observations, 138 exact and 10 strict
  xfail; independent audit: 0 differing, 0 without verdict. Focused existing include/source tests:
  1,938 passed, 10 strict xfail. Rules and the ten known gaps:
  [folded include filenames](docs/checker.md#folded-include-filenames-round-43).
- Raw probes: 156 observations. Four corrupted replacement-character filename echoes and four
  control-character filename echoes are preserved in `scratch/r43/` and excluded from deterministic
  fixtures; their verdicts were usable, their filename text is not a stable rule.
- Recorded replay: 3,338 rows, 2,563 exact (one more than rounds 41/42); `d3af581efdd6` is now
  exact. Verdict, mode and first-location counts are unchanged; `no-regressions` passes against
  the round-41 baseline. The baseline now records that one improvement. Of the six new round-42
  message reproducers,
  `d1cd86d16934` now matches exactly, five remain diagnostic differences; no verdict change.
  No new generic fuzz seeds in round 43.
- Validation: non-server suite 54,698 passed, 311 skipped, 370 strict xfail; server suite
  163 passed. Corpus comparisons preserve the round-42 results, including the known reference
  faults; GEUS CR1000X remains 66/66 exact. Both repository verification backends pass with the
  BUILD line above. `git diff --check` passes. No hardware tests, commit or push for round 43.
- Resume with the existing M1/M2/M3 priorities below. Fresh candidate first-error families are
  in `scratch/r42/fuzz/`: malformed `Sub` parameters (`0a9af24e1266`), the broken `En  dProg`
  cascade (`0a16dd0bb9df`), and malformed one-line `If` text (`0818efcb5653`, CR1000X).
  These are hypotheses awaiting minimization and fresh probes, not accepted rules.
- End-of-round local review queue: `scratch/local-maintenance/r43-2026-10-26/`; inspect its
  `HANDOFF.md`, PID, startup log and checkpointed results before editing its hashed inputs.
  The queue reviews source/document agreement and fixture distinctions with controls. Startup
  supervisor PID 48111 exited after the enabled memory guard stopped model loading for swap
  growth; **not running**, zero completed packets. Logs and exact resume command are in
  `HANDOFF.md`. No source changes, commits or hardware operations are authorized to it.

**Round 42** (M1, bounded SerialBrk gap from the handoff; one session, no subagents).
- Traced CR6 instruction thunk `0x4019d3` to builder `FUN_004731f0`, then probed four profiles.
  Numeric constant ports are 9..16 on CR6 and 9..12 on CR1000X. Preserve their distinct messages,
  signed/truncated long conversion, and accepted string/runtime-variable arguments. CR300/CR350
  accept the tested constants without this check. Only statement calls reach this builder.
- Evidence: `tests/fixtures/r42_serialbrk.json`, 172 observations, all exact, no xfail; independent
  reference audit: 0 differing, 0 without verdict. Rules:
  [SerialBrk ports](docs/checker.md#serialbrk-ports-round-42).
- Recorded replay: all 3,338 rows retain the round-41 impact counts; 2,562 exact, 43 false accepts,
  no false reject, 19 operational rows (18 recorded runner failures and one stale recording).
  The `no-regressions` gate against `tests/replay_baseline.json` passes; baseline unchanged.
- Fresh relevant fuzz, CR6 seed 433 and CR1000X seed 434, 100 mutants each, no shrinking:
  194 exact, 6 message differences, no false accept/reject, crash, hang or implementation exception.
  Each seed-loading stage excludes the known `pointervariable_2.CR6` reference page fault.
  The six message reproducers and complete command logs remain in ignored `scratch/r42/`;
  they have not been added to the saved replay corpus.
- Four-profile repository/help corpus results remain unchanged; no lost exact case or verdict
  mismatch on usable reference results. Known reference faults still make the differential gates
  exit 1. GEUS CR1000X remains 66/66 exact.
- Initial non-server suite: 8 filesystem-snapshot failures caused by concurrent reference
  `verify.py` writing `crbasic/CR6cmple.xxx`; 54,552 passed, 311 skipped, 360 xfail. Keep the raw
  `scratch/r42/pytest.log`. Clean rerun: 54,560 passed, 311 skipped, 360 strict xfail; server
  suite: 163 passed. The optional
  `CR6COMP=opencrbasic` verification in the sandbox compiled all 20 entries but two existing
  LibreOffice workbook subprocesses failed. Both the required reference verification and the
  `CR6COMP=opencrbasic` host retry passed with the BUILD line above.
- Previous local maintenance (`r36-2026-10-25`) completed 8/8, with no invalid jobs. Its claimed
  aggregate count contradiction and claimed complete-fix contradiction are proposals only:
  historical round scopes and explicitly documented gaps do not establish defects. No local
  proposal was applied. Maintenance kickoff was deferred to round 43, per the human request to commit round 42 and
  continue immediately.

**Round 41** (M2, messages; one session, no subagents or Codex). From the CR6/CR1000X
same-location message rows of the replay, each probed:
- `#If` conditions echo up to `Then` with their spacing (corrects round 39);
- `Const A B = 1` takes `B = 1` as the value;
- the left-to-right arity rule inside `Const` values (all four loggers);
- `CType` types: only `Long`/`Float`/`Double`/`String` and the `UINT1`/`IEEE4`/`IEEE8` codes; a
  variable or string type was a false accept.
- Replay: exact 2,552 -> 2,562; CR6/CR1000X same-location messages 133 -> 112, first-error misses
  24, earlier first locations 6; gate against the round-40 baseline passes.
- Evidence: `tests/fixtures/r41_messages.json` (189 observations, 42 strict xfail). Rules:
  [message families](docs/checker.md#message-families-round-41).

**Round 40** (M2; one session, no subagents or Codex).
- **Stale recordings.** 121 reproducers compile against the live `crbasic/` files; recordings now
  carry a sibling digest, `fuzz_replay.py --recorded` reports a mismatch as `stale_recording`
  (operational), and `--rerecord-stale` re-records. All 121 were re-recorded: five reference results
  changed (the SBD schema v5 `GPS_Lat` rows); `75f68395c4e8` stays stale (reference Wine fault).
- **First error and messages**, each probed: pass-2 symbol errors in included files carry no
  `<file>` prefix; more than three subscripts is a pass-1 error; a constant destination is reported
  with the constant-only argument errors; a stray `#Else`/`#ElseIf`/`#EndIf` is `Unknown
  instruction` (all four loggers); scan-only placement follows its statement's symbol error.
- Replay: exact 2,512 -> 2,552; CR6/CR1000X first-error misses 32 -> 24, same-location messages
  163 -> 133, earlier first locations 6; no verdict change; gate against the round-39 baseline passes.
- Evidence: `tests/fixtures/r40_messages.json` (122 observations, 4 strict xfail). Rules:
  [messages and stale recordings](docs/checker.md#messages-and-stale-recordings-round-40).

**Round 39** (M2, first error; one session, no subagents or Codex). Families from the saved
replay's CR6/CR1000X first-error rows, each probed against the reference:
- an unresolved assignment or bare call turns off the pass-1 `EndSelect w/o Select` (the orphan
  is then `END IF without IF or END SELECT without SELECT.` in pass 2);
- malformed `#If` conditions use the reference messages instead of the parser's wording (all four
  loggers);
- table output outside a table reports only the placement message;
- `Units` targets are checked last;
- the arity deferral reads left to right;
- a nested `DataTable` header loses to an unresolved table name met first.
- Replay: exact 2,455 -> 2,512; CR6/CR1000X first-error misses 58 -> 32, earlier first locations
  13 -> 6; no false accept or false reject change; no regression against the round-38 baseline.
  The difftest caught a lost exact help example (`realtime_1.CR6`): the first deferral code reused
  pass 1's lazily computed `known` set, which gates later checks; fixed with a separate set.
- Evidence: `tests/fixtures/r39_first_errors.json` (224 observations, 12 strict xfail). Rules:
  [first-error families](docs/checker.md#first-error-families-round-39).
- No fuzz seeds were run.

**Round 38** (M1/M2; one session, no subagents or Codex). Fresh `--relevant` seeds 429-432 (CR6
429/430, CR1000X 431/432, 500 mutants each): 1,942 exact, 2 false accepts, no false reject, 44
message and 3 warning differences, 17 runner failures. Fixed, each probed against the reference:
- **False accepts:** `SDI12Recorder` NAN fill outside -1..room (aliases included), `SDI12Recorder`
  destination index bounds, a single index on a multidimensional array checked against the first
  dimension (all instructions, all four loggers; `Battery(M(4))` of `M(3,4)` was accepted), dotted
  fields (`Status.`, `Settings.`, `Public.`, table) at constant-only positions, and a String in the
  middle of a logical chain (`If X AND S AND Y`).
- **First error (M2):** the odd UINT1/BOOL8 Reps message first on its line and ahead of a source
  overrun; an undefined table source ends the stage-2a table checks; a wrong-arity call whose read
  arguments name something undefined defers to `Invalid symbol in expression` on CR6/CR1000X.
- Evidence: `tests/fixtures/r38_fresh_seeds.json` (688 observations, 17 strict xfail). Rules:
  [fresh-seed findings](docs/checker.md#fresh-seed-findings-round-38). The replay gate caught 8 CR6
  first-error regressions of a first arity rule (names in surplus arguments); fixed before the
  baseline refresh.
- Flagged: the `TimeIntoInterval` truncate-warning cascade (accepted), orphan-`Case Else` fragments
  `114315fdb7cf`/`e406ca2fdda2`, and three warning rows (`Call`/`EndIf never reached`, `CommsMode
  Option 3 not supported on ComUSB`).
- The 43 new reproducers are in `tests/corpus/fuzz/` and the replay baseline.

**Round 37** (M1; one session, no subagents or Codex). The flagged round-36 false accept
`7b14df1ed0f8` led to two rules, both probed on all four loggers and modelled on CR6/CR1000X:
- **Terminal ownership.** A constant `SerialOpen` claims both terminals of its pair for the whole
  program; `SDI12Recorder`/`SDI12SensorSetup`, `PortSet`/`WriteIO` (per mask bit) and `PulseCount`
  claim in textual order, and a claim in another role is `<Instr>: channel <T> already in use or
  unavailable.` An SDI-12 conflict prints only with no earlier error; the others always. A recorder
  and a sensor on one terminal is `SDI Recorder and Sensor cannot use the same port.`
- **`SerialOpen` ComPort codes.** Constant ComPorts outside 1..18/31..99 (CR6) or 1..14/31..99
  (CR1000X) are `ComPort N illegal.` plus `No Matching`; non-integers `ComPort 0 illegal.` plus
  `Parameter must be a constant integer`. The error ends the statement, also suppressing a short
  argument list's `Expression expects` after the older variable ComPort errors.
- Evidence: `tests/fixtures/r37_terminal_ownership.json` (640 observations, 3 strict xfail, all
  CR300/CR350 cascades). Rules: [terminal ownership](docs/checker.md#terminal-ownership-round-37).
- Replay: `7b14df1ed0f8` and two CR6 `SerialOpen(9600,...)`-shaped rows (`bb6d192052e3`,
  `25498b9865a7`) are now exact; no row regressed.
- No new fuzz seeds were run this round.

**Round 36** (M1; one session, no subagents or Codex):
- **Relevant seeds.** `tools/fuzz.py --relevant` keeps only help examples whose every instruction
  and function a FirnMelt or GEUS program also uses (`relevant_names`, computed from the programs,
  `Archive/` left out), beside the repository and GEUS seeds. Seeds 425-428 found 13 false accepts
  and no false reject.
- **M1 fixes**, all probed against the reference (all four profiles unless noted):
  - `Timer` Units and Option must be integer-typed constants; Option outside 0..4 is `Legal
    options are 0..4` (FirnMelt's `Timer(..., TimerResetAndStart)` shape);
  - `IIF` with one String and one numeric arm is `type mismatch`;
  - a `SetSetting`/`SetStatus` name that is not a constant string is `Status Field name must be
    constant.`;
  - `INTDV` and `\` reject String operands like `*`, `/`, `MOD`;
  - CR6/CR1000X `SerialIn` constant ComPort codes;
  - `EmailRelay` takes 4, 5, 9 or 10 arguments;
  - `If(`/`ElseIf(` with no blank before the condition was never name-checked;
  - CR6/CR1000X output data types 0..255 (swept), fieldless codes and `declared with no output
    fields`, odd Reps with `BOOL8`/`UINT1`, and `WindVector`'s data type;
  - CR1000X `ExciteV` takes only `Vx1..Vx4`.
- Evidence: `tests/fixtures/r36_relevant_seeds.json` (2,414 observations, 51 strict xfail: data
  types past 255, the UINT1 variable-type line, NSEC with `WindVector`, a CR6 `SetSetting` cascade
  and a CR300/CR350 line after `is not defined`). Rules:
  [relevant-instruction seeds](docs/checker.md#relevant-instruction-seeds-round-36).
- Flagged: `7b14df1ed0f8` (CR1000X, `SerialOpen(10,...)` claiming `SDI12Recorder`'s `C3`) and the
  warning difference `e7e12f2d1011` (`TimeIntoInterval` with a string interval).
- The 74 reproducers of seeds 425-428 are in `tests/corpus/fuzz/` and the replay baseline.

**Round 35** (one session, no subagents or Codex):
- **M0, fuzzer realism.** `tools/fuzz.py` gains three operators for plausible mistakes (a typo in a
  declared name, a neighbouring number, two swapped arguments; `tests/test_fuzz_operators.py`) and,
  for CR1000X, the 50 GEUS programs without `Include` as seeds. The configuration format is recorded
  in [PLAN.md](PLAN.md#three-diagnostic-channels).
- **Fresh seeds 421-424** found three false rejects. One was a round-34 regression (a second
  `AVW200`'s ComPort is not resolved by the reference); the other two were 32-bit conversions
  (CR1000X differential channel `999999999999`, `String * 999999999999`). All are fixed.
- **M1 fixes** from the same run, each probed on CR6/CR1000X: aliases at constant-only positions,
  a string Reps as INT_MIN, `Delay(2,0,...)` and a negative option-0 delay, running-statistic
  destination bounds (also scalar Sub parameters), the `SerialOpen` baud check on every port (it
  was `ComRS232` only; a mutated `Main.CR6` showed it), and CR1000X single-ended channels outside
  int32. Evidence: `tests/fixtures/r35_fresh_seeds.json` (449 observations, 402 match; the 47
  others are message-level, plus the four negative-`Delay` cascades). Rules:
  [fresh-seed findings](docs/checker.md#fresh-seed-findings-round-35).
- The 63 reproducers of seeds 421-424 are in `tests/corpus/fuzz/` and the replay baseline.

**Round 34** (M1, verdict safety; one session, no subagents or Codex). The 48 replay false accepts
were grouped by the reference's first error: 31 are on CR6/CR1000X, mostly singletons. Fixed:
- **Undeclared names outside `_MULTI_REPORT_ORDER`.** Round 14 listed only the positions reported
  when every argument was undeclared, and the checker checked only those. A help-example sweep (57
  single-position mutants, CR6/CR1000X) now backs checking the rest; it also found `StdDevRun`
  position 8 never resolved, `TimedControl` Size/Interval must be constants (a variable there was a
  false accept), and `SDMSIO4` resolving its destination unless the value count is 0, with a
  count-fits-destination bounds rule.
- **The four probe false accepts**: unknown DataTable fields (field names from the TDF builder),
  unknown `Settings.` names (all 259 `SetSetting` names read clean on CR6/CR1000X), assignments to
  unknown `Status.`/`Settings.` fields, and declaration initializers that read a variable or a
  `Status.`/`Settings.` member (`Variable illegal in Parameter`).
- Evidence: `tests/fixtures/r34_undeclared_positions.json` (76 observations, 75 match),
  `r34_multi_order_sweep.json` (112, 95 match; 17 differ only in later diagnostics) and
  `r34_table_settings.json` (606, 594 match; the 12 others are CR300/CR350, not modelled). Rules:
  [positions outside the multi-report order](docs/checker.md#positions-outside-the-multi-report-order-round-34),
  [table fields, Settings and Status](docs/checker.md#datatable-fields-settings-names-and-status-targets-round-34).
- Second batch, from the replay triage, all CR6/CR1000X with the probes in
  `tests/fixtures/r34_false_accepts.json` (688 observations, all match; 22 more were dropped after
  repeated Wine timeouts):
  - `SerialInRecord`: the input-buffer rule (0 without a `SerialOpen` of the port, else
    max(256, BufferSize), NumberBytes compared unsigned) and the StartWord/NumberBytes/EndWord rule
    (fitted, 16-bit words); `SerialOpen` CommsMode per port (errors on `ComC*`, warnings on RS232,
    ME, U and SDC ports) and a variable BufferSize;
  - code between `EndSequence` and the next `SlowSequence`; `SubMenu` is no deferred body;
  - instance slots of `MinRun`/`MaxRun`/`TotalRun`/`StdDevRun`; CR1000X `PulseCount` on `P1`/`P2`;
    `As Long2,` with a trailing comma; a blank after a last empty `()` in a declaration.
  Rules: [code after EndSequence](docs/checker.md#code-after-endsequence-submenu-bodies-round-34),
  [SerialInRecord and SerialOpen](docs/checker.md#serialinrecord-serialopen-and-running-statistic-instances-round-34).
- Replay false accepts 48 to 35, exact 2,390 to 2,410; repository, help and GEUS corpora unchanged.
- `tables.TABLE_PROPERTIES` is now shared by the checker and `bindings.py`.

**Round 33** (one session, no subagents or Codex):
- **M1, GEUS false rejects fixed.** A statement in one pair of parentheses (`(w = 0)`,
  `If c Then (w = 0)`, `(Delay(0,1,mSec))`) compiles as that statement on all four loggers; an
  alias named `Status`, `Public` or `Settings` makes the name a value. Probes also found and fixed
  a CR6/CR1000X false reject of `Double` as a declared or alias name, and of `Public.X` after
  `Alias D=Public`. Evidence: `tests/fixtures/r33_geus_false_rejects.json`, 256 reference
  observations on four profiles, 246 match, 10 strict xfail (CR6/CR1000X first-error text for an
  unclosed `(w = 0` and for `(w = 0)(w=1)`; CR300/CR350 accept both and miss
  `Dimensions not declared` for `Status(1)` on a scalar alias). Rules:
  [parenthesized statements](docs/checker.md#parenthesized-statements-round-33),
  [aliases named Status](docs/checker.md#aliases-named-status-public-or-settings-round-33),
  [alias names that are CRBasic names](docs/checker.md#alias-names-that-are-crbasic-names-round-33).
  Whether the logger executes a parenthesized assignment is not observed on hardware.
- **M0, impact measure and gates.** `tools/impact.py` classifies every row on verdict, mode, first
  error, warnings and later diagnostics, with operational failures apart. `fuzz_replay.py` gains
  `--recorded` (no Wine), `--baseline`/`--save-baseline` and a `no-regressions` gate for new
  CR6/CR1000X false accepts, false rejects, mode differences and first-error regressions;
  `tests/replay_baseline.json` is the committed baseline. A recorded reference result that is a
  runner failure now counts as operational, not as a verdict. `difftest.py` reports the impact
  table and has `--group geus`.
- **M0, CI.** `.github/workflows/opencrbasic.yml` runs the Wine-free suites and the recorded replay
  gate. It has not run on GitHub yet; the replay command passed locally.
- **M3 start, human request.** `crbc --strict` and `src/opencrbasic/checks.py` with check `OCB001`:
  an `Alias` named after an instruction, function, system table, keyword or special value, which
  the reference accepts (probed on all four profiles). Output contract: findings on stderr, stdout
  and `CR6cmple.xxx` unchanged, exit 3 for an accepted program with a strict error
  (`tests/test_checks.py`, [PLAN](PLAN.md#three-diagnostic-channels)). Controls: no FirnMelt
  program fires; one GEUS program does (`Alias D2out(3)= Status`, the motivating case).
- `tools/probe.py` now compares the result line too, so mode differences show.

**Round 31** closed the remaining strict xfails of rounds 24, 26, 27 and 29 and extended
measurement spans. Four workers ran in parallel; three had Codex (gpt-6.1-sol) research. They
were stopped at the weekly usage limit on 2026-10-23. A fifth worker integrated the four diffs on
2026-10-24. The integration's 91 failures were overlapping rules from two workers (double-reported
CR1000X PulseCount and CR6 bridge channel lines, a missing `Reps > 1` gate) and two unfinished
pieces (the CR300/CR350 `-t` section dropped after a settling error, a destination-overrun guard
that hid a duplicate bounds line). Rules and evidence are in four `docs/checker.md` sections:
- [measurement destination and channel spans](docs/checker.md#measurement-destination-and-channel-spans-round-31);
- [alias cascades](docs/checker.md#alias-cascades-non-float-alias-destinations-and-parameter-capacity-round-31);
- [alias name collisions](docs/checker.md#alias-name-collisions-const-first-assignment-and-string-lengths-round-31);
- [bridge spans and settling timing](docs/checker.md#bridge-channel-spans-and-settling-time-timing-round-31).

A Codex review of the integrated round-31 diff (`scratch/codex/review-r31.md`) predicted nine
defects:
- routine-scoped alias destination types leaking between routines on CR300/CR350;
- wrapped PulseCount port codes;
- fractional CR1000X CurrentSE channels;
- alias-named String lengths;
- an indexed Const assignment in a one-line `If`;
- an `OverflowError` for extreme bridge fN1;
- the CSAT3 OOM cutoff;
- alias-collision cutoff across includes;
- empty-array parameter capacity.

A fix worker confirmed each against the reference before changing code.
[Round-31 review fixes](#round-31-review-fixes) records the outcome.

### Round-31 review fixes

All nine findings were confirmed on the reference compilers and fixed. None was refuted. The
probes also exposed and fixed neighbouring gaps:
- CR300/CR350 PulseCount channel families and an invalid start (false accepts and false
  rejects);
- CR1000X PulseCount span families and fractional channel truncation;
- negative and float-zero fN1 (false rejects and a ZeroDivisionError);
- infinite settling times reporting the maximum.

Evidence is in `tests/fixtures/r31_review_fixes.json`: 420 observations, 406 match and 14 are
strict xfail. The remaining gaps are CR300/CR350 dimensionless-parameter indexing and the
`Q() As Long` header. Rules are in the round-31 `docs/checker.md` sections and the fN1 rule in
`docs/instructions.md`. Unverified generalizations:
- the infinite-settling rule (`inf_max`) was observed on VoltSe only;
- channel truncation was observed on CR1000X CurrentSE/PulseCount and CR300/CR350 PulseCount only.

**Round 32 (planning).** Under the 2026-10-24 decision, [PLAN.md](PLAN.md) now defines v1.0:
- three diagnostic channels: reference diagnostics, opencrbasic checks, coverage notices;
- a fix, flag or accept rule for remaining differences;
- probed check candidates;
- milestones M0–M7, about 12–22 rounds.

A Codex review of the draft plan (`scratch/codex/review-r32-plan.md`) corrected:
- the mode-difference count (one, not 16);
- the corpus provenance (2,983 of 3,158 reproducers are help-example mutants);
- the classifier's first-error ordering;
- the claim that a notice can waive verdict safety.

All four corrections are in the plan. Prior state: the
[round-30 handoff snapshot](docs/progress-history.md#round-30-handoff-snapshot-2026-10-23).
Production CRBasic was not changed. No hardware was flashed or tested.

### Local maintenance queue (round 36)

Prepared on 2026-10-25 at HEAD `0fab9b15` with the uncommitted round-36 changes (dirty inputs
hashed), following [LocalMaintenance.md](../verification/LocalMaintenance.md#quality-first-kickoff),
in `scratch/local-maintenance/r36-2026-10-25/` (`packets.json`, `HANDOFF.md`). Eight packets: two
round-36 handoff-versus-`docs/checker.md` checks, the PLAN-versus-PROGRESS false-accept tally,
three code-versus-doc rule checks (Timer, output data types, SerialIn), a historical-counts scope
control and a test-structure control.

**Completed.** Supervisor PID 5901, Qwen3.8-27B UD-Q4_K_M, `xhigh`, 2,048-token reasoning budget,
4,096 output, 8K context, 1,200 s timeout: 8/8 packets valid, no errors, 2,806 s of generation;
the memory guard did not stop it (swap 3.6-4.1 GiB). Both controls answered as expected
(consistent; insufficient_evidence). Hosted review of the proposals:
- 01 (contradiction: Settings fix without its CR6 exception) rejected: the round-36 entry lists the
  CR6 `SetSetting` cascade among the strict xfails;
- 03 (contradiction: PLAN tally) rejected: PLAN counts fixed false accepts and PROGRESS the
  remaining ones; they reconcile;
- 04 consistent, with a valid nit: the `_TIMER_CONTEXT` comment in `checker/expressions.py`
  states the INT_MAX rule without the CR300/CR350 qualifier its code and docs carry; not edited;
- 02, 05, 06 consistent.

No change was accepted. Round 36 was committed after the worker finished.

### Local maintenance queue (round 32)

A local review queue was prepared on 2026-10-24 at HEAD `356c2258`, following
[LocalMaintenance.md](../verification/LocalMaintenance.md#quality-first-kickoff). It is in
`scratch/local-maintenance/r32-2026-10-24/`, with nine packets in `packets.json`:
- plan versus handoff counts;
- the README purpose versus the 2026-10-24 decision;
- the `Status` lesson conflict (a positive control);
- two scope controls;
- round-31 handoff versus `docs/checker.md`;
- the LSP plan scope;
- two test-overlap candidates.

**Not completed.** The quality-first worker started (supervisor PID 8852, Qwen3.8-27B
UD-Q4_K_M, `xhigh`, 2,048-token reasoning budget, 4,096 output, 8K context, 1,200 s timeout). The
runner's memory guard stopped the model server during loading: swap grew from 951 to 5,937 MiB
(`memory-*.json`, `stopped_for_swap_growth: true`; `kickoff.log`, `server.log`). No packet was
attempted, and the guard and other applications were left intact.

To resume with more free memory, check that no worker is live, then rerun the launcher from
`verification/LocalMaintenance.md` with `queue` set to that directory. It processes only
unattempted packets. Local output is a proposal; check it against the cited sources at the same
revision before acting on it.

## Next steps

Follow the [v1.0 milestones](PLAN.md#v10-milestones); M0 comes first.

1. **M0, remaining:** the CI workflow passed on GitHub on 2026-10-25 (run 40184946232, after the
   round-35 TDF fixture refresh); optionally record the parenthesized-assignment runtime on the
   bench CR6 (unknown so far).
2. Human decisions of 2026-10-24, recorded in [PLAN.md](PLAN.md#v10-milestones):
   - CR6 and CR1000X gate v1.0; CR300/CR350 are best effort;
   - the GEUS programs may be compiled as a corpus (`AGENTS.md` updated);
   - the `Status` lesson was corrected after the old observation could not be reproduced
     ([lessons](../knowledge/crbasic/lessons.md#status-names-are-compile-checked));
   - an `--strict` error for aliases named after CRBasic names (`OCB001`, round 33).
3. **M3, remaining framework:** `--report FILE`, configuration and suppressions, the separate
   result field for the language server, then the next checks from the PLAN table.
4. **M1, remaining false accepts.** The 27 left on CR6/CR1000X fall under the PLAN's flag rule:
   instructions neither FirnMelt nor GEUS uses, or allocation and 32-bit clamping models. Round 34's
   18: `072c74da6af5` (`&H0  10a0101`), `51f39a5f2393` and `7b4b2177d56b` (`CDM_FFTFilt`),
   `55821bbfb248` (`Program too big for task memory` cascade), `870ca9a9562e`/`e8c854e3493d` (I2C/SPI
   port clamp), `a0b2b0b059b3` (CDM excitation clamp), `ec6b11bc3176`/`022d98dd3efc` (`MenuPick`),
   `4156d89a014d` (`EC100`), `6ac24bd775f1` (`CDM_MuxSelect`), `72f95eac103f` (`DNPVariable`),
   `77636b10567c` (`SDMSW8A`), `7f9efa8af42c` (`SDMCD16AC`), `c060e9f1402f` (`TimedControl`),
   `c3d3adda9412` (`SerialOpen` string format), `d12785e05faf` (`TriggerSequence`), `f2f6e5d8b8e6`
   (`SNMPVariable`). Round 35's 9: `0e23c61229f4` (`CS7500`), `39a03364be83` (`FieldCalStrain`),
   `52138d7bd179` (`CDM_PulsePort`), `577a59ecb08c` (`AVW200` destination), `778ea45e64b7`
   (`CDM_BrFull6W` settling clamp), `9c6367a2c598` (`MenuRecompile`), `ecc509dd06de`/`f61f825392f2`
   (`TableFile`), `fa923ecef6f4` (`MenuPick`). Round 36's `7b14df1ed0f8` was fixed in round 37
   (terminal ownership). Coverage notices for them wait for M4. The 16
   CR300/CR350 false accepts are best effort. Next: more `--relevant` seeds (round 38's 429-432 found only two false accepts; also rules found in passing: data types past 255, `Sample` with UINT1,
   CR300/CR350 terminal ownership and `SerialOpen` ports), and the message-level first-error differences (M2): after round 41, 24 CR6/CR1000X
   first-error misses, 6 earlier first locations and 112 same-location messages remain in the
   replay; after any `crbasic/` change run `fuzz_replay.py --rerecord-stale` first (`docs/checker.md`
   "First-error families (round 39)" lists the modelled ones).
5. **M3/M4** can start after M0, in parallel with M1/M2: the check framework, and the CR6 evidence
   inventory from `tools/builder_map.py` and `tools/message_catalog.py`.
6. The old next-step items (Const diagnostics, interval gaps, table metadata, artifact refresh)
   are kept in the [round-30 snapshot](docs/progress-history.md#round-30-handoff-snapshot-2026-10-23).
   Re-rank each under the fix, flag or accept rule before working on it.

## Environment setup

Run package tools from `opencrbasic/`, using its external uv environment:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"
export PYTHONPATH="$PWD/src"
uv sync
```

Python 3.15+ is required. The explicit `PYTHONPATH` also keeps
worktree runs from importing the main checkout through the shared editable installation.

The current macOS Wine setup is documented in [openloggernet/README.md](../openloggernet/README.md#compiling-on-macos).
The oracle uses `~/.wine-cr6`, `WINEDEBUG=-all`, and defaults to disabling GUI drivers through
`WINEDLLOVERRIDES=winemac.drv,winex11.drv,winewayland.drv=d`; explicit overrides are preserved.
For a new prefix, disable crash dialogs as recorded in the
[archived environment setup](docs/progress-history.md#environment-setup-needed-to-resume). Runner
failures and timeouts must never be cached or counted as accepted programs.

Current reverse-engineering artifacts: `scratch/re/CR6Comp.decomp.c` and
`scratch/re/fields.decomp.c`, regenerated with Ghidra 12.1.4. The installed headless launcher is
`/opt/homebrew/Cellar/ghidra/12.1.4/libexec/support/analyzeHeadless`; Java is
`/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home`. Use a project directory under
`/private/tmp/`: Ghidra rejects the comma in this checkout's path. Restricted runs used
`JAVA_TOOL_OPTIONS=-Duser.home=/private/tmp/opencrbasic-ghidra-home` for writable settings.
Regeneration script: `tools/ghidra/DumpAll.java`; tracing method: [binary notes](docs/binary.md).
Old scratch briefs, research notes and timing helpers mentioned in history may be absent.

## Work method

Follow [repository instructions](../AGENTS.md) and the current human request. Rounds 29–31 (human
instruction 2026-10-22) used Claude subagents for implementation and Codex `gpt-6.1-sol` for
read-only decompilation research and review, run with the ChatGPT app's bundled CLI
(`/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex exec
-m gpt-6.1-sol -s read-only`; `~/.local/bin/codex` 0.158 rejects the model). Workers follow
`scratch/r31/brief-worker.md`; treat Codex output as hypotheses to confirm on the reference. Commit or push only when
authorized under the repository instructions.

Work on one observed rule family at a time: minimize a reproducer, inspect decompiled behavior,
probe accepted boundaries and failures on all four profiles, implement the smallest change,
and preserve reference observations in a fixture and the owning behavior document. Decompiled
code informs the rules and is never pasted into `src/`.

Run focused checks immediately, then the full pytest and server suites, four-profile differential
comparison, saved replay and relevant artifact/fixture audits. Compare against a baseline;
never prune reproducers just to improve agreement. Retry transient runner failures separately.
Finish with `cd ../scripts && uv run verify.py` using the scripts environment
`UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/firnmelt-scripts`.

Keep heavy validation runs bounded and avoid overlapping them. Do not kill validation process
trees: prior forced termination exhausted macOS multiprocessing semaphores. Before another
long round, `python -c "import multiprocessing as m; m.Lock()"` checks that pools can start.
Update this file after each milestone; move superseded snapshots and run history to the archive.
