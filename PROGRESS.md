# opencrbasic progress

Current compiler state and handoff, updated **2026-10-31 after development round 75; v1.0 released internally (M0-M7 done)** (round-51
fuzz triage and M3 report/configuration, round 52; include names, round 53; two false rejects, two false
accepts and three first-error families, round 54; PipeLine placement data and four false accepts/rejects,
round 55; four fuzz false accepts and a UINT1 warning, round 56; the M1 gate scope and its last five
relevant false accepts, round 57; fresh seeds 453-460, M2 measurement and first-error review, round
58; M2 fix scope decision and realistic first errors, round 59; packed-output Reps and fresh M2
candidates, round 60; missing commas in Sub arguments, round 61; user Function expression syntax, round 62; IIF omitted final-arm types, round 63; fresh seeds 465/466,
two false accepts, a false reject and four first-error families, round 64; String-typed operands,
FTPClient PutGetOption and GetRecord destination size, round 65; includes after an IncludeSection
error, round 66; Codex final-review fixes, round 67; M3 checks OCB002-OCB006; Codex follow-up blockers, round 68; numbers beyond the float range and M1/M2 sign-off, round 69; M3 exit check and `OCB007`, round 70; Constants used before their declaration and AvgRun instance counts, round 71; M4 evidence inventory and coverage notices, round 72; M6 checks in the language server, round 73; M5 artifact matrix, round 74; M7 internal release 1.0.0, round 75). Use Git to check
publication
state and inspect `git status` before editing.

Goal, priorities and the v1.0 roadmap: [PLAN.md](PLAN.md#remaining-work-strategy). The human
decision of 2026-10-24 makes verdict safety, execution mode and the first error the priorities;
later-diagnostic order and cascades no longer block a release. Previous measurements, round logs
and superseded handoffs are not kept in this repository. Language-server work has
its own [plan](LSP_PLAN.md) and [progress ledger](LSP_PROGRESS.md).

## Current status

The CR6, CR1000X, CR300 and CR350 profiles are implemented. Reference crashes and runner
failures are not compiler verdicts. Exact diagnostic agreement means equal exit codes and
normalized ordered stdout, not byte parity.

Latest observed validation, on macOS Apple Silicon with Wine 11.17 and Python 3.15:

| Check | Latest result |
|---|---|
| `pytest -q -n 8 -m 'not server'` | 62,822 passed, 311 skipped, 1,218 strict xfail (round 66) |
| `pytest -q -m server` | 163 passed (round 66) |
| Round-24–31 fixture audits (15 fixtures, cached oracle) | 0 differing, 0 without verdict |
| Round-59 fixture audit (`r59_*` and the four edited fixtures) | 0 differing, 0 without verdict |
| Round-60 fixture audit (`r60_packed_table_reps.json`) | 176 cases, 0 differing, 0 without verdict |
| Round-61 fixture audit (`r61_sub_arguments.json`) | 116 cases, 0 differing, 0 without verdict; round-62 promotion: 64 passed, 52 strict xfail |
| Round-62 fixture audit (`r62_function_syntax.json`) | 312 cases, 0 differing, 0 without verdict; focused tests: 172 passed, 140 strict xfail |
| Round-63 fixture audits (`r63_iif_missing_arm.json`, `r36_relevant_seeds.json`) | 168 + 2,414 cases, 0 differing, 0 without verdict; new fixture: 142 passed, 26 strict xfail |
| Round-65 fixture audit (`r65_*.json`, `var_types.json`) | 648 + 469 cases, 0 differing, 0 without verdict; new fixtures: 644 passed, 4 strict xfail (CR300/CR350 only) |
| Round-64 fixture audit (`r64_*.json`, `r62_function_syntax.json`) | 548 + 312 cases, 0 differing, 0 without verdict; new fixtures: 422 passed, 126 strict xfail (CR6/CR1000X: 8, all listed) |
| Saved fuzz replay, `fuzz_replay.py --recorded --baseline tests/replay_baseline.json --gate no-regressions` (the CI command; round 66) | 3,338 inputs: 2,631 exact (round 63: 2,628; round 58: 2,596); CR6/CR1000X: 13 first-error misses (19), 25 false accepts, none in the relevant area; regression gate passes; `tests/replay_baseline.json` not refreshed. |
| GEUS corpus, `difftest.py --group geus --logger cr1000x` (round 66) | 66/66 exact (round 32: 64 exact, two false rejects); no GEUS `.CR6` programs |
| Fresh fuzz, seeds 469/470 (round 65, `--relevant`, 1,000 each, run on the round-65 code) | No false accept, false reject, mode difference, crash, hang or implementation exception (one warning-only row). First-error differences are fuzz arguments, token splits, two-edit inputs or outside the set, except `6b414530e683` (a swapped `IncludeSection`), fixed in round 66 ([review](docs/first-error-review.md#round-65-fresh-inputs)). Raw output in `scratch/r65/` |
| Fresh fuzz, seeds 465/466 and 467/468 (round 64, `--relevant`, 1,000 each, CR6 odd / CR1000X even) | 465/466: 1 false accept (fixed), 31 message differences ([review](docs/first-error-review.md#round-64-fresh-inputs-and-round-60-classification)). 467/468, run after the round-64 fixes: 2 false accepts (`FTPClient` PutGetOption, `GetRecord` destination size), fixed in round 65; no mode difference (three warning-only rows). Raw output in `scratch/r64/` |
| Fresh fuzz, seeds 461/463 (round 60, `--relevant`, 1,000 each, CR6/CR1000X) | No verdict or execution-mode differences on usable results. Three packed-output first errors fixed; malformed-call candidates remain open ([review](docs/first-error-review.md#round-60-fresh-relevant-inputs)). Faults/timeouts excluded; raw summaries in `scratch/r60-m2-20261030/` |
| Fresh fuzz, seeds 453-460 (round 58, `--relevant`, 1,000 mutants each, CR6 odd / CR1000X even) | 453-456: 7 false accepts, 2 false rejects, 1 implementation exception; 457/458: none; 459/460: 1 false accept each. All fixed; the 8,000 mutants now replay with no verdict difference (`fuzz_replay.py --recorded scratch/r58/fuzz-4*/*/*/*.CR6`). Raw output in `scratch/r58/` (git-ignored) |
| First-error rate on reference-rejected replay cases (M2, `tools/first_error_rate.py`, round 65; unchanged from round 62; not rerun in round 66) | on the 3,158 reproducers shared with round 30: CR6 94.2% (round 58: 93.4%; round 30: 88.1%), CR1000X 91.8% (89.2%; 78.1%), CR300 92.7% (92.2%; 91.7%), CR350 92.0% (92.0%; 91.3%). The round-30 report is `scratch/r30_replay_candidate.json` (local only) |
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

Four-profile diagnostic comparison, rerun in round 66 and unchanged from round 28
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

Repository verification passed in round 66 both with the reference compiler and with
`CR6COMP=opencrbasic`:

```text
BUILD  Pyrefly 501 files pass | host tests 148 pass | CRBasic 20/20 compiled, 6 warning(s) | repository pass
```

Expected warnings are owned by [repository build status](../STATUS.md#build). Compilation and host tests do
not establish logger runtime behavior. No hardware tests were run.

Rounds 23–31 did not rerun full corpus TDF/table-list comparisons. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

**Round 90** (archived verdict parity). Fixed false accepts `c060e9f1402f` and
`77636b10567c` by validating `TimedControl` and `SDMSW8A` options. See the
[option probes](docs/instructions.md#timedcontrol-and-sdmsw8a-options-round-90).
The 25-case archived replay now has 21 false accepts and four exact matches.

**Round 89** (verdict parity, fuzz seed 777). Fixed CR350 false accept `6eb9b69ecb54`
by checking `SlowSequence` scan interval limits on CR300/CR350. See the
[scan probes](docs/instructions.md#slow-scan-intervals-and-the-1-day-limit-cr6cr1000x-round-9-2026-10-10).

**Round 88** (archived verdict parity). Fixed false accepts `d12785e05faf` and
`52138d7bd179` by checking the `TriggerSequence` event byte and `CDM_PulsePort` delay.
See the [range probes](docs/instructions.md#triggersequence-event-and-cdm_pulseport-delay-round-88).
Of the 25 archived false accepts below, 23 still reproduce and these two now match exactly.

**Round 87** (verdict parity, fuzz seed 777). Fixed CR1000X false accept `a0a6de4b78cc`.
The interval check resolves quoted table names and names supplied by String constants on all
four profiles. See the [interval probes](docs/instructions.md#datatable-interval-d-usec-not-integrally-divisible-by-scan-interval-d-usec-all-four-loggers).

**Round 86** (verdict parity, fuzz seed 777). Fixed CR6 false reject `07cae3ba79de` and
matched module support, channel bounds, and repetition overruns for `CDM_PanelTemp`.
See the [channel probes](docs/instructions.md#cdm--sdm--tdr-constant-channels-cr6--cr1000x).
The full local help corpus matched exactly on all 2,022 usable profile/program comparisons.
Six reference runs returned no verdict.

**Round 85** (verdict parity, fuzz seed 776). Fixed CR1000X false accept `df473bc65461`
and verified typed `RealTime` and `Move` destination bounds on all four profiles.
See the [destination probes](docs/checker.md#destination-bounds-2026-10-02).

**Round 84** (verdict parity). Rejects assignments to unknown `Status` fields on CR300/CR350,
including inline and compound assignments. See the [assignment probes](docs/checker.md#status-assignment-targets-round-84).

**Round 83** (verdict parity). Fixed String-valued `Status`, structure and single-pass `Public`
fields as `Delay` intervals. Removed the unrecognised CR300 `Status.RecipeStatus` read name.
See the [field-type probes](docs/instructions.md#delay-three-checks-split-by-option).
Seed 775 on the round 82 code agreed on all 1,276 usable verdicts, with four reference failures.

**Round 82** (verdict parity). Extended the indexed-constant checks to array dimensions,
initializers, Alias subscripts, and assignment targets. See the [declaration probes](docs/checker.md#indexed-constants-round-79).
Fresh seed 774 gave 1,197 usable reference verdicts across the four profiles on the round 81 code.
All agreed on acceptance. Three inputs had no reference verdict.

**Round 81** (verdict parity, fuzz seed 773). Fixed CR350 false accept `dfd1542832c1`
and the equivalent String-interval cases on the other profiles. See the [Delay probes](docs/instructions.md#delay-three-checks-split-by-option).
The 960 mutations in seed 773 found two false accepts. A four-profile rerun after the fixes
had zero false accepts and zero false rejects, with a reference verdict for every input.

**Round 80** (verdict parity, fuzz seed 773). Fixed CR6 false accept `df2651cd7c31`.
A declaration fragment following an interrupted continuation is parsed as a standalone expression.
See the [continuation probes](docs/frontend.md#declaration-continuations).

**Round 79** (verdict parity, fuzz seed 772). Fixed the CR6 false accept `aa708bc338ff`,
an indexed predefined constant in an assignment. See the [four-profile probes](docs/checker.md#indexed-constants-round-79).
The 640 mutations used three handwritten seeds accepted by all four compilers. One input yielded
no reference verdict.

**Round 78** (CR300/CR350 verdict parity). Closed the remaining `Sub`-as-value false accepts
on the two single-pass profiles. See the [value and call probes](docs/checker.md#constants-declared-after-their-use-round-71).

**Round 77** (verdict parity, fuzz seed 771). Fixed the two false rejects found in 700 CR6/CR1000X
FirnMelt mutations: [oversized `DataInterval` lapses](docs/tdf-rules.md#cr6cr1000x-out-of-memory)
and [six-argument `SerialOpen` buffers](docs/checker.md#serialopen-buffer-literals-round-77).
The reference yielded no verdict for four inputs. The available FirnMelt seeds did not compile
on CR300/CR350, so those profiles had no mutants in this run.

**Round 76** (verdict parity). A `Sub` name shared with another routine's parameter no longer
makes that parameter visible as a value outside its routine. See the [scope probes](docs/checker.md#constants-declared-after-their-use-round-71).

**Round 75: M7 done, opencrbasic 1.0.0** (single agent; Codex `gpt-6.1-sol` review in `scratch/m7/`,
local only). Version 1.0.0 for the package, `__version__` and the VS Code extension
(`tests/test_versions.py`; policy in README "Versions"); README rewritten for scope, the three
channels and the accepted differences; `tools/release_manifest.py` pins compilers, corpora and
baselines; the [release record](docs/release-1.0.md) holds the configuration, hashes, every gate
command with its result, the known failures and the exclusions; local annotated tag
`opencrbasic-v1.0.0`. The matrix gate now treats a cell that only loses differences as improved,
not regressed; the CR300/CR350 baselines were refreshed after the last round-74 fix (23 improvements,
11 still-differing `-i`/`-p` cells with new bytes). The one stale replay recording, `75f68395c4e8`,
cannot be refreshed: the reference crashes on it (exit 5). One relevant-area false accept is an
exception to the M1 gate (a `Sub` name that is also another routine's parameter, round 71).

**Round 74: M5 done** (single agent; Codex `gpt-6.1-sol` review in `scratch/m5/`, local only).
`tools/artifact_matrix.py` compares every output of the reference compiler and `crbc` (exit,
stdout, message file, `-z`/`-l`/`-c`/`-p`/`-i`/`-b` files, written, missing or empty) over 76
programs and 10 option sets per profile ([artifacts](docs/artifacts.md)); baseline
`tests/artifact_baseline.json` (all four profiles), gate `--gate no-regressions`, saved runs for CI
in `tests/fixtures/m5_artifacts.json` (76). The matrix found and round 74 fixed:
- `crbc` wrote `CR6cmple.xxx` for every profile (each compiler writes `<LOGGER>cmple.xxx`);
- `-z` on a failed compile writes the version byte `0x01`; `-b` prints its message before the
  result, also when a pass-1 error stops a CDM program;
- `-p` ends after a missing include and reads the `Const`s of an existing one for `#If`; `-i` keeps
  a missing include as written; a dropped pass-1 error (full message buffer) ends `-p`/`-i` output
  at its statement;
- first error: `Public 1bad` / `Sub S(1p)` is `Variable name 1bad illegal, starts with a number.`
  (all four loggers; 8 round-45 strict xfails promoted).
- Result on CR6: 757 of 760 cells exact; CR1000X 685 (the TDF Status table is CR6's: best
  effort). Remaining cases are listed in the doc.
- Codex review fixes: the gate pins output fingerprints and fails on new cells, reference
  failures and a missing baseline; the `-p`/`-i` cut follows the dropped error's own file and line
  and only for pass-1 errors (four new probes); stdout compared as bytes on both sides; one staged
  manifest for staging and the cache key.
- Gates (`scratch/m5/gates*.log`, `replay.log`): 63,948 passed, 311 skipped, 1,283 strict xfail
  (8 round-45 xfails promoted); server 174 passed; fixture audit 0 differing; CI saved replay 2,634
  exact, no regression; four-profile difftest unchanged, GEUS 66/66; matrix gate exit 0 on CR6 and
  CR1000X; both `verify.py` backends pass (21/21). No hardware tests.

**Round 73: M6 done** (single agent; Codex `gpt-6.1-sol` reviews in `scratch/m6/codex-review*.log`, local only).
The opencrbasic checks and coverage notices reach the editor:
- facade API_VERSION 2 ([LSP API](docs/lsp-api.md#analysis-facade-api_version-2)): `findings`
  (channel, code, severity, evidence link, path, span) and `config_error` when the reference accepts
  the program; `opencrbasic.toml` read through the source provider, so it is a dependency and unsaved
  text counts; request `checks` (LSP severities), `notices`, `config_root`;
- worker and session carry them; published with source `opencrbasic (check)` / `opencrbasic (coverage)`,
  `code`, `codeDescription` and the configured severity, merged and versioned per entry like compiler
  diagnostics; compiler diagnostics carry `data` `{entry, result, index}` and quick fixes match on it;
- settings `opencrbasic.checks` and `opencrbasic.coverageNotices` (default off: nearly every program
  has a notice), both in the analysis key; a `**/opencrbasic.toml` watcher; the configuration is
  searched only up to the entry's workspace folder; entries re-run when that root changes;
- Codex review fixes: a stale diagnostic picked a newer result's fix (result token), ancestor
  configurations outside the watched folder, and a race where the first analysis ran before the
  folders arrived. Not done on purpose: syncing an open toml from VS Code (it would route CRBasic
  features to the toml); an edit applies on save.
- Tests: `tests/lsp/test_m6_server.py` (11: channels, unsaved include, two entries, settings and toml
  changes, stale versions and results, programs with errors, formatting with a check error, `data`
  quick fixes, config above the folder, notices).
- Gates (`scratch/m6/gates2.log`): 63,864 passed, 311 skipped, 1,291 strict xfail; server 174 passed;
  both `verify.py` backends pass (21/21). The reference channel is unchanged. No VS Code host test
  and no hardware tests.

**Round 72: M4 done** (single agent; Codex `gpt-6.1-sol` reviewed three times, `scratch/m4/codex-review*.log`,
local only). `tools/inventory.py` generates the CR6 [evidence inventory](docs/inventory.md)
(`docs/inventory-cr6.json`) and the coverage-notice table (`src/opencrbasic/data/notices_cr6.json`),
both pinned to the SHA-256 of `CR6Comp.exe`, the decompile and `instructions.json`:
- per instruction: builder, the messages of its reporters (`FUN_005089c0`, `FUN_00508a00`,
  `FUN_0049ce10`) in the builder and its own helpers with decompile lines, helper call sites, shared
  helpers, indirect-call sites, linked `module:line` sites and observation refs, and a state
  (`tested`, `partially tested`, `unknown`); unresolved reporter arguments are kept as `unknown`;
- a message is implemented when an opencrbasic template or rule string produces it, its literal
  pieces occur in the sources, or opencrbasic prints it on a saved program where the reference did;
- `checks.notices` names, at the first use of a CR6 instruction in an accepted program, its
  messages that are not implemented; `crbc --report` writes them (schema 2); stdout, `--strict`
  stderr and exit codes are unchanged. 9 of the 17 listed CR6 replay false accepts get a notice
  naming their rule (`tests/test_inventory.py`); the other eight stay listed by identity.
- Codex review fixes: five false notices for templated messages (regression tests), reporter
  wrappers and string symbols with punctuation or commas, evidence links, the shared-helper rule,
  bare statement uses, warning prefixes, per-case fixture identity with sibling files.
- Gates (`scratch/m4/gates3.log`): 63,864 passed, 311 skipped, 1,291 strict xfail; server 163 passed;
  both `verify.py` backends pass (21/21). The reference channel is unchanged (no checker change), so
  difftest and replay were not rerun. No hardware tests.

**Round 71** (M1, single agent; Codex `gpt-6.1-sol` reviewed four times, `scratch/r71/codex-review*.log`,
local only). The round-70 false accept (a `Delay` unit parameter with a later same-name `Const`) was one
case of a wider rule, probed on all four loggers (`tests/fixtures/r71_const_order.json`, 368
observations; [checker notes](docs/checker.md#constants-declared-after-their-use-round-71)):
- **False accepts fixed (CR6/CR1000X):** a `Const` used before its declaration in a pass-1 constant
  position (`Dim`/`Public` size, `Alias` subscript, `Delay` unit, `PanelTemp` fnotch), also inside a
  `Sub`; a routine parameter named like a later `Const`, used as a constant inside its own body; an
  `AvgRun` instance count holding a variable, alias, field, function call or String `Const` anywhere
  (`V+0`, `INT(3)`), or a negative constant (`True` -> `Out of memory.`); a `Sub` name used as a value
  (`X = V`, `If V Then`, `Delay(1,V,mSec)`). All but the parameter cases predate round 71.
- **False rejects avoided** (Codex reviews, confirmed on the reference): `Public A(IEEE4)` before
  `Const IEEE4=1`; another routine's `Dim K` or local `Alias` making a global `Const K` a variable.
- Implemented by `symbols.statement_scopes` (per statement: own parameters, `Const`s still to come)
  and a `publics` option of `symbols.scoped_view` (a `Public` in another routine stays global).
- **Not modelled, listed** (strict xfails): 26 CR300/CR350 cases (assignments and `If` before a
  `Const`, Sub names as values); on CR6/CR1000X a `Sub` name that is also another routine's
  parameter (false accept, unrealistic) and two first-error-only collisions (`Alias X=V` before
  `Sub V`, `Sec()`).
- Gates (`scratch/r71/gates/gates4.log`): fixture audit 0 differing; 63,844 passed, 311 skipped, 1,291
  strict xfail; server 163 passed; CI saved replay 2,634 exact (round 70: 2,633), no regression;
  four-profile difftest unchanged, GEUS 66/66; both `verify.py` backends pass (21/21). No hardware
  tests.

**Round 70: M3 done** (single agent; Codex `gpt-6.1-sol` sign-off after two fix rounds,
`scratch/m3b/codex-review3.log`). Each shipped check was re-checked against the
[M3 exit criterion](PLAN.md#v10-milestones):
- **Evidence gap fixed:** `OCB003` also fires on a negative `ExciteV` delay, but the fixture had no
  `ExciteV` observation and PLAN said it compiles on all four loggers. It does not: on CR6 (`U1`)
  and CR1000X (`Vx1`) it is the reference error `Program too big for task memory`, which
  opencrbasic already reproduces; it compiles on CR300/CR350, so the `ExciteV` part applies there
  only. A fraction of a microsecond below zero (`ExciteV(U1,0,-0.9)` compiles on CR6 where `-1` does
  not) is no longer reported. `OCB004` now cites `datatypes.htm` (undefined mathematical results
  give NAN or INF).
- **Scope false positives fixed** (Codex review): `OCB002` and `OCB007` skip a destination or
  operand name declared more than once (a String parameter shadowing a global `Long`, a `Dim` in
  two routines) or used in an `Alias`, also nested in an operand (`IIF(1,S,S)`); `OCB003`,
  `OCB004`, `OCB006` and `OCB007` no longer fold a `Const` that a parameter or variable of the same
  name shadows; oracle controls added.
- **Reference-channel false accept found, not fixed (outside M3):** a routine parameter used as a
  `Delay` unit while a later global `Const` has the same name (`Sub P(U As Long)` / `Delay(1,-0.9,U)`
  / `Const U=Sec`) is `Variable illegal in Parameter: U.` on all four loggers; opencrbasic compiles it
  (it folds the global `Const`). Without the `Const` it matches. Probe: `scratch/m3b/scope3.txt`.
- **New check `OCB007` (warning):** a String literal longer than its destination holds
  (`S = "<36>"` on `String * 32`, `C = "ab"` on `String * 1`); accepted on all four loggers, capacity
  from the round-29 slot rule and `dim.htm`.
- **Closed without a check:** a measurement-task `PortSet` in a conditional is handled by the
  reference itself (SequentialMode, or an error under `PipelineMode`).
- **Tests added:** 99 new fixture observations (`m3_check_evidence.json`, now 295), the CHM and
  GEUS corpora (`test_corpus_findings_are_justified`: only the justified `OCB001` on
  `QAS_B_LoggerProgramV2.CR1X`), and `openloggernet.cr6comp.compile_program` under
  `CR6COMP=opencrbasic` with a firing check and an invalid `opencrbasic.toml`
  (`test_cr6comp_native_command_runs_no_checks`).
Probe inputs and recorder: `scratch/m3b/` (local only).

**Round 69: M1 and M2 done.** The Codex `gpt-6.1-sol` sign-off review of round 68
(`scratch/r68/codex-signoff.log`, local only) found every earlier finding resolved and signed off
**M2 complete**; for M1 it confirmed two remaining implementation exceptions on numbers beyond the
float range (`Delay(<309-digit integer>,1,mSec)`, `Sample(1e309,...)`), both older than round 68.
A stress sweep (`scratch/r69/huge.py`: 309-digit integers and `±1e309` in every argument of 22
instruction shapes, Dim, Const and Scan, 848 compiles on all four profiles) found 23 sites. Fixed at
the root: `consteval.value_of` treats an integer beyond the float range as not a constant, and a
shared finite-number guard makes the numeric rules skip an infinity (implemented with Codex;
`scratch/r69/codex-inf.log`). Infinities stay constant values, because `String * 1e999` has
recorded verdicts (rounds 29 and 54). The reference itself gives no usable verdict for these
literals, so `tests/test_huge_literals.py` asserts only the absence of an implementation exception;
the sweep now gives 0 of 848. With that the last M1 criterion is met, and M1 and M2 are closed
([PLAN status](PLAN.md#v10-milestones)).
- Two repository commits during this round (`df9f72fd`, `589ec5b9`, TDR acceptance) changed
  `crbasic/`: 121 replay reproducers were re-recorded against the new siblings
  (`fuzz_replay.py --rerecord-stale`), after which the CI replay is again 2,633 exact.
- Gates (`scratch/r69/gates/`): fixture audit 0 differing; 63,407 passed, 311 skipped, 1,259 strict
  xfail; server 163 passed; CI saved replay 2,633 exact with no regression; four-profile difftest
  unchanged (repository now 20/20 exact on CR6/CR1000X), GEUS 66/66; both `verify.py` backends pass
  (21/21 CRBasic programs). No hardware tests.

**Round 68** (M1/M2, implemented by Codex `gpt-6.1-sol` with workspace write access, brief
`scratch/r68/brief.md`, log `scratch/r68/codex-fix.log`; gates rerun by the coordinator). The Codex
follow-up review (`scratch/r67/codex-followup.log`) left two blockers, both fixed:
- **M1:** a huge negative integer literal (`FTPClient(...,-1` and 309 zeros`)`) raised `OverflowError`
  in `consteval._num`; such a value is now not a constant (no reference verdict exists for it).
- **M2:** `Sub S(A As String * 16!)` reports `Incomplete expression: 16!.` on CR6/CR1000X, like the
  round-67 variable declarations; the two `r67_string_size_junk` gaps now match.
- The adjacent-operand locator in user Function calls skips `NOT` (`G(Not X, Abs(X Y))`; reasoned
  from the code, no reference probe).
- Gates (`scratch/r68/gates/`): fixture audit 0 differing; 63,382 passed, 311 skipped, 1,259
  strict xfail; server 163 passed; CI saved replay 2,633 exact, no regression; four-profile
  difftest and GEUS 66/66 unchanged; both `verify.py` backends pass. No hardware tests. A further
  Codex review to sign off M1/M2 has not been run.

**Round 67** (M1/M2 Codex final review). The Codex `gpt-6.1-sol` read-only review of rounds
64-66 (`scratch/r65/codex/final-review.log`, local only) did not sign off: it found a false-reject
regression, a reachable implementation exception, realistic first-error gaps and 13 fresh
identities without a decision, and two overstated verification claims. All addressed, probed on
all four profiles (`tests/fixtures/r67_*.json`, 308 observations; CR6/CR1000X all match except the
later cascade lines after an overflowing measurement Reps), rules in
[checker notes](docs/checker.md#round-67-review-fixes):
- **False rejects fixed:** `CType(...,Long) * 2` (round 65 typed every `CType` as String);
  `FTPClient(...,-2147483649)`; `CallTable TN` with `Const TN = "T1"` naming an existing table.
- **Implementation exception fixed:** a PutGetOption literal of hundreds of digits
  (`OverflowError`; the reference itself gives no verdict there).
- **First errors fixed:** adjacent operands in a nested built-in inside a user Function call; a
  quote right after a user Function call; an overflowing measurement Reps (`301007b4ed1a`); a
  misspelled table-name `Const` (`c9199e5b744a`, `5231a9a68e91`); a duplicated `For` line, a
  deleted `If` line and a deleted `EndSub` (seeds 471/472).
- **False accept fixed:** a sized String pointer `Dim P As String * 16!` (seed 474), and junk
  after a String size in general.
- Every first-error difference of the fresh batches since round 60 now has a recorded decision
  ([review](docs/first-error-review.md#round-67-ledger-completion)). Fresh seeds 471/472 (round-66
  code) and 473/474 (round-67 code before the String-size fix) had no verdict or mode difference
  apart from that false accept, which their first-error triage exposed.
**M3 checks** (after round 66). Five opencrbasic checks join `OCB001`, each on a form the
reference accepts on all four loggers (`tests/fixtures/m3_check_evidence.json`, 196 observations
of positive forms and accepted controls), with positive tests, controls and a documented default
([checker notes](docs/checker.md#opencrbasic-checks-ocb002-ocb007-m3)):
- `OCB002` (warning): a numeric variable assigned a `+`/`&` chain mixing numeric and String
  operands (`L = 2000 + Mid(S,5,2)`; hardware lesson). Strings joined into a number's text
  (`HexStr(6) + "." + HexStr(7)`, deliberate in GEUS programs) are not reported;
- `OCB003` (warning): a constant negative `Delay` interval or `ExciteV` delay (human proposal
  2026-10-28);
- `OCB004` (warning): a division by a constant zero;
- `OCB005` (info): an unsized `String` routine parameter (23 characters; hardware lesson);
- `OCB006` (info): a constant `DataInterval` dividing neither an hour nor a day (hardware lesson).
None fires on the station programs in `tests/corpus/station/`, the accepted CHM examples or the 66 GEUS
programs (`test_station_programs_have_no_findings`). `crbc --report` now also writes a report
when the entry file is missing; `crbc --strict` with only warnings keeps exit 0
(`test_warning_checks_keep_exit_zero`). The check `OCB002` in an existing configuration test was
renamed `OCB099` (an unknown code).

**Round 66** (M2, single agent). The realistic first-error candidate left by seeds 469/470: a swapped
`IncludeSection("CPU:ShuntLib.crb","Definitions")` (`6b414530e683`). On CR6/CR1000X an
`IncludeSection` extension error in active code stops every later include, so their definitions
and their own errors are missing; a bad plain `Include` or one in an inactive `#If` stops nothing
([rule](docs/checker.md#includes-after-an-includesection-error-round-66)). Before, opencrbasic still
loaded the later sections and reported an error inside them first. The parser skips the later
include blocks as it does in an inactive `#If`. Fixture `r66_include_stop.json` (48 observations
with sibling files, all four profiles; CR6/CR1000X all match except the order of a later missing-file
warning; CR300/CR350 unchanged, their include resolution is not modelled here).
- Gates: the fixture audit ran on `r65_string_operands.json` only, by mistake; the round-66 fixture
  was checked by pytest only (36 passed, 12 strict xfail; correction from the round-67 Codex
  review). Full and server suites pass; CI saved replay 2,631 exact (+1)
  with no regression; four-profile difftest and GEUS 66/66 unchanged; the fresh replay of rounds
  60/64/65 makes `6b414530e683` exact; both `verify.py` backends pass. No hardware tests. Logs:
  `scratch/r66/gates/`.

**Round 65** (M1/M2, single agent). The three families left open by round 64, probed on all four
profiles (648 observations, `tests/fixtures/r65_*.json`, 644 match, 4 CR300/CR350 strict gaps):
- **String-typed operands** (false accepts, all four loggers): a String-returning built-in
  (`kind` 312: `Mid`, `FormatFloat`, `Chr`, `Hex`, `Trim`, ...), a `Function ... As String`, an `IIF`
  with a String arm and a `+`/`&` with a String on either side are String operands, so
  `Mid(S,1,2) * 2`, `(2000 + Mid(S,5,2)) * 2` and `Mid(S,1,1) AND 1` are rejected. A statement with
  several offending operators now reports each in source order instead of nothing (it was a false
  accept with plain String variables too: `(S * 2) + (S * 3)`).
  [Rule](docs/instructions.md#string-operands).
- **`FTPClient` PutGetOption** (`3b789efdd145`): a constant outside -29..29 is `Invalid PutGetOption`
  on CR6/CR1000X. [Rule](docs/instructions.md#ftpclient-putgetoption).
- **`GetRecord` destination size** (`ca5b1677129c`): a numeric destination smaller than the record
  reports, then `Could not find DataTable`, and ends the walk (all four loggers).
  [Rule](docs/instructions.md#getrecord-destination-size).
- Gates: fixture audit 0 differing; full and server suites pass; CI saved replay 2,630 exact with
  no regression; four-profile difftest and GEUS 66/66 unchanged; the fresh replay of rounds 60/64
  gains 2 exact cases and both seed 467/468 false accepts are fixed; both `verify.py` backends pass.
  Fresh seeds 469/470 on this code: no verdict or mode difference. No hardware tests. Commands and
  logs: `scratch/r65/gates/`.

**Round 64** (M1/M2, single agent). Fresh `--relevant` seeds 465 (CR6) and 466 (CR1000X), then the
open round-60 candidates. All rules were probed on all four profiles (548 observations,
`tests/fixtures/r64_*.json`); rules and boundaries in [checker notes](docs/checker.md#leading-plus-before-an-assignment-round-64)
and the four sections after it.
- **False accepts fixed:** an overflowing `WindVector` Reps (`9f6e9f17660e`, seed 466); an SDM
  `Delay` (option 2) after `SlowSequence` (the realistic core of round-60 `9abdc4c03311`).
- **False reject fixed:** `+ X = 1` compiles on all four loggers; `- X = 1`, `+ 1 = X` and
  `+ Abs(X) = 1` are `Invalid assignment in expression` on CR6/CR1000X.
- **First errors:** a statement starting with `+`/`-` is never `Incomplete expression`
  (`53f4ed873761`); user Function call statements of any arity, unknown names in them and
  explicit `Call F(...)`; unterminated strings in user Function calls (`LogDebug("abc)`). Ten
  round-62 strict gaps now match.
- The remaining round-60 inputs and the 31 fresh differences are classified in the
  [first-error review](docs/first-error-review.md#round-64-fresh-inputs-and-round-60-classification).
  That review's claim that none left open was realistic was too strong: the Codex review found
  realistic gaps and unrecorded identities, fixed or recorded in round 67.
- Seeds 467/468, run on the round-64 code: two new false accepts, `FTPClient` PutGetOption
  (`3b789efdd145`) and a `GetRecord` destination smaller than the record (`ca5b1677129c`). While
  probing M3 candidates, a third family was found: String-returning functions and a `+` with a
  String operand are String-typed operands (`X = Mid(S,1,2) * 2` is `Illegal String operand`).
  All three were fixed in round 65.
- Gates: fixture audit 0 differing; full and server suites pass; CI saved replay 2,630 exact with
  no regression; four-profile difftest and GEUS 66/66 unchanged; fresh replay of rounds 60/64
  improves by 16 exact. Both `verify.py` backends **failed** at gate time on one Pyrefly
  annotation (`scratch/r64/gates/verify-*.log`); after the fix both passed on a manual rerun whose
  output was not saved (correction from the round-67 Codex review). Codex review of the diff was
  cut short by its usage limit before findings. No hardware tests. Commands and logs: `scratch/r64/gates/`.

**Round 63** (M2, single agent). Round 62 was committed separately as `1aad02c9` after its seven
files matched the validated checkpoint and the focused r61/r62 suites passed again (236 passed,
192 strict xfail). The next realistic candidate, CR6 `d6479ed3f595`, now matches exactly.
The missing final arm in `IIF("1","0")` participates as numeric in the arm-type check, so the
String second arm reports `type mismatch` before the missing-argument diagnostic. The condition's
String type does not cause this error; numeric second-arm controls keep only the arity error.

The implementation extends the existing arm-type checker by three lines, preserving its
String-type classifier, once-per-line limit and earlier-error gate. The four-profile 168-case
fixture has 142 exact observations and 26 strict gaps; 96 previously differing observations
now match. Explicit empty slots, excess arguments, unparsed unclosed calls and reserved-name
cascades remain excluded. Evidence: [IIF omitted arm](docs/checker.md#iif-omitted-final-arm-round-63).

The saved CI replay stays at 2,628 exact with no regression; the round-60 fresh corpus gains
`d6479ed3f595` exactly (10/31 exact). First-error rates on the saved CI replay are unchanged.
M2 remains open for the moved-continuation case and other candidates. Raw observations, exact
gate commands and the next resume point are under `scratch/r63/RESUME.md` (local only).
Round 63 was committed as `fdac7e6b`; no push or hardware test was performed.


**Round 62** (M2, single agent). Round 61 was committed separately as `56336637` after checking
all six validated input hashes and rerunning its focused fixture (56 passed, 60 strict xfail).
The next interrupted-call candidate, CR6 `a31a74073f76`, is now exact. Its callee `LogDebug` is
actually a Function, so it follows the whole-expression grammar rather than the Sub argument
reader. A fresh CR6 reference run with current sibling files confirmed the saved output.

For unparsed calls and assignments to one-parameter user Functions, the existing expression
scanner now preserves the unmatched-parenthesis diagnostic and whole-statement echo. All four
profiles were probed; CR300/CR350 retain their newline and final unresolved-scalar diagnostic.
The 312-case round-62 fixture retains Function boundaries and unfinished Sub controls; 92
previously differing new observations match. Eight old round-61 Function observations also
match and no longer carry expected-failure marks. Evidence and exclusions:
[Function syntax notes](docs/checker.md#user-function-expression-syntax-round-62).

Saved CI replay improves by one exact case (`15c0ffa7f461`); the round-60 fresh corpus improves
by six exact cases: `1822097cb726`, `a31a74073f76`, `e816abac4b12`, `36a0db0a73f4`,
`15bc1b5cb20a` and CR1000X `dce5f0264db1`. No replay baseline was refreshed.
M2 remains open for the other realistic malformed-call candidates. Exact commands, raw probes,
initial runner timeout/retry, and the resume point are under `scratch/r62/RESUME.md` (local only).
Round 62 was committed as `1aad02c9` before round 63; no push or hardware test was performed.


**Round 61** (M2, single agent). Round 60 was committed separately as `c2809873` after all six
files matched its validated checkpoint and its focused fixture passed again (100 passed,
76 strict xfail). Round 61 continues the missing-comma candidate from `scratch/r60-m2-20261030/RESUME.md`.
Declared Sub calls such as `S("a" "b")`, `S(1 2)` and `S(X X)` now report the malformed argument,
rather than a whole-call fallback, on all four profiles. The checker reuses the expression
scanner; 36 previously differing observations now match. It keeps unresolved names, unfinished continuations, nested function syntax and
trailing tokens outside the new rule. Decompilation trail and exclusions:
[Sub argument notes](docs/checker.md#malformed-sub-arguments-round-61).

Four-profile observations and valid-call/arity controls are in
`tests/fixtures/r61_sub_arguments.json`; exact commands, probe outputs and the current handoff
are in `scratch/r61/RESUME.md` (local only). At the end of round 61, the original deleted-continuation case and other round-60 candidates
remained open; round 62 above supersedes that handoff. Round 61 was committed separately as
`56336637` before round 62. No push or hardware test was performed.


**Round 60** (M2, single agent). Fresh `--relevant` seed 461 (CR6) and seed 463 (CR1000X),
1,000 mutants each, with shrinking disabled: no verdict or execution-mode differences, crashes,
hangs or implementation exceptions on usable reference results. Reference faults/timeouts remain
operational failures; complete summaries and raw observations are in
`scratch/r60-m2-20261030/` (local only). The CR6 run reused a directory containing earlier shrunk
observations; `fuzz-461-current/selection.json` records the 19 observations from this run copied
into the separate handoff directory.

- Fixed three CR1000X first-error differences: the odd UINT1/BOOL8 Reps error precedes an
  alias-source overrun. Follow-up probes also found false accepts for decimal odd counts;
  `_odd_pair_reps` now uses the same integer conversion as the table bounds check.
- `FUN_004ec300` decompilation and four-profile boundary probes support the narrow change;
  [checker notes](docs/checker.md#packed-table-output-reps-before-alias-bounds-round-60).
  Fixture `r60_packed_table_reps.json`: 176 reference observations, including even-count controls
  and all five packed-output instruction families; 100 exact and 76 strict gaps. The WindVector
  direction-alias gap remains excluded from the alias-overrun change to preserve its first error.
- **M2 remains open:** fresh realistic malformed-call first errors are now listed in the
  [review](docs/first-error-review.md#round-60-fresh-relevant-inputs), with the minimum next probes.
  Start with `a31a74073f76` (deleted final line of a continued routine call) or the already-probed
  `S("a" "b")` missing-comma family. No hardware tests, commit or push.
- Final gates: full suite and server suite pass; fixture audit 0 differing / 0 without verdict;
  four-profile difftest unchanged (exit 1 only for the known reference faults), GEUS 66/66 exact;
  CI saved replay gate passes with the same 2,627 exact inputs and baseline first-error rates;
  fresh replay makes the three packed-output cases exact. Both `verify.py` backends pass with
  the BUILD line above. Exact commands, logs, dirty input hashes and local-review handoff:
  `scratch/r60-m2-20261030/RESUME.md` (local only).

**Round 59** (M2 under the new fix scope; coordinator only, no Codex job was needed). Human decision
2026-10-29 ([PLAN](PLAN.md#v10-milestones)): M2 fixes first-error differences only for mistakes a
programmer could realistically make. Every row of the [first-error review](docs/first-error-review.md)
was re-applied to its whole seed program (`scratch/r59/unshrink.py`): 75 of 128 match there (shrinker
artefacts), and the 53 left are all token-splitting blanks, fuzz arguments, statements inserted into a
continued line, two edits in one statement, or outside the relevant set
([list](docs/first-error-review.md#round-59-realism-triage)). Realistic single-edit variants were probed
on CR6/CR1000X and fixed ([checker notes](docs/checker.md#round-59-realistic-first-errors) and the
sections after it):
- false accepts: a `Const`/predefined name as a destination (`Battery(K)`, `SerialIn(ComRS232,...)`),
  bridge `Range` codes (`BrHalf`/`BrFull`/... on CR1000X), a nested `StructureType`, a default on a
  non-`Optional` parameter, a routine's `Const` with an unknown type, a 3-dimensional String array,
  `TDR100` Points;
- false reject: an array declaration with a junk word before an initializer (`Public X(3) Long = {...}`);
- first errors: constant destinations are pass 1 (beside every other pass-1 error), the nested
  `DataTable` error is pass 2, `!` on an undeclared name, malformed routine headers, dotted references
  with an undeclared base, structure-array `Sample` reps, the `Illegally dimension` echo after an
  initializer, an unterminated string after the last parameter, the one-line-`If` argument echo, an
  unreadable control condition echoed alone;
- harness: our text is compared as the CLI's Latin-1 bytes read back (`oracle.as_read_back`).
- Fixtures `r59_*.json` (968 observations, all four profiles; CR6/CR1000X all match except listed
  later-diagnostic and outside-the-set rows); 9 strict xfails in earlier fixtures now match. Gates:
  fixture audit 0 differing; four-profile difftest and GEUS unchanged; CI replay gate passes (2,627
  exact); `verify.py` passes with both compilers. No hardware tests.

**Round 58** (M1 confirmation and M2 measurement; coordinator, three Codex `gpt-6.1-sol` read-only jobs:
`scratch/r58/codex-{m2,dim,names}.log`). Fresh `--relevant` seeds 453-460 (8,000 mutants) found 9 false
accepts, 2 false rejects and an implementation exception on CR6/CR1000X; all are fixed, with their
families ([checker notes](docs/checker.md#orphan-endtable-in-the-table-walk-round-58) and the sections after it):
- implementation exception: a String `Range` crashed `_check_range_code`; any String range is invalid;
- declarations: a space after a parenthesised later dimension cuts the dimension list (decompiled:
  the `As` search tokenizes from the first `)`); `Dim A(2,(3), 4, 5)` was a false reject;
- `WindVector` SubInterval/output option and Reps 0; the SubInterval line gates nothing;
- `SerialOpen`: Format options 16+ on ports 1-8; the parser's comma recovery (a bare undeclared TXDelay
  was a false reject; float formats);
- `IPNetPower`/`MonitorComms`/`LogIP` never resolve their arguments (empty builders; false reject);
- tables: an orphan `EndTable` ends the stage-2a walk; `ResetTable`/`GetRecord`/`FileMark` need a table
  closed before them;
- one-line `If` statements get the tier-C value and terminal checks; String SDI-12 ports are INT_MIN.
- M2: `tools/first_error_rate.py`; [first-error review](docs/first-error-review.md) lists all 128
  differing CR6/CR1000X replay rows by family and kind (Codex classification, spot-checked).
- Fixtures `r58_*.json` (1,041 observations, all four profiles; CR6/CR1000X all match except listed
  same-verdict messages). Gates: fixture audit 0 differing; four-profile difftest and GEUS unchanged;
  CI replay gate passes (2,596 exact); `verify.py` passes with both compilers. No hardware tests.

**Round 57** (M1 under the new gate scope; coordinator, Codex `gpt-6.1-sol` decompilation research in
`scratch/r57/codex-alloc.log`). All five relevant false accepts fixed, plus the families they belong to:
- **Task-memory overflow of long delays** (`55821bbfb248`, `ExciteV(Vx1,0,-1)`): the delay generator's
  signed 16-bit loop count; `ExciteV` delays from `1409024010` us and `Delay(0,...)` from `1409024000` us
  (and their 32-bit wraps) repeat `Program too big for task memory` until the message buffer is full.
  The `Delay` interval is unsigned 32-bit (also for the 60-minute limit and scan time; the Double low
  word outside the Long range), `ExciteV`'s delay now costs scan time, and SequentialMode skips the
  option-0 checks (a false reject: `Delay(0,-1,uSec)` there). 14 round-35 `not_modelled` observations
  now match.
- **`SerialOpen`** (`c3d3adda9412`, `9be1e8aba3d8`): a non-numeric format makes the fifth argument the
  CommsMode; a sixth argument must be a constant integer.
- **Bounds** (`e6b6604b4da3`, `f76ae7c58e75`): `PRTCalc` destinations; alias sources of table output
  (`Sample`, `Average`, ...) and alias destinations of `PRTCalc`, `CalFile`, `Move` and the running
  statistics.
- Fixtures `r57_task_memory_delays.json`, `r57_serialopen_format_commsmode.json`,
  `r57_alias_bounds.json` (592 observations, all four profiles; CR6/CR1000X all match except four
  same-verdict `SerialOpen` messages). Details: [docs/checker.md](docs/checker.md#task-memory-overflow-of-long-delays-round-57)
  and the two sections after it. Human proposal recorded as a PLAN check candidate: negative or
  wrapped delays.
- Gates: fixture audit 0 differing; four-profile difftest and GEUS unchanged; CI replay gate passes;
  `verify.py` passes with both compilers. No hardware tests.

**Round 56** (M1, fresh fuzz seeds 449/450; coordinator only). Five CR1000X false accepts fixed:
bridge excitation below 20 mV, `Minimum` data type 0, spatial destination types and Long bounds, and
`Sample(...,UINT1)` of a non-UINT1 variable, `WindVector` reps bounds; plus the CR6/CR1000X UINT1 padding warning. Ten round-36/38
`not_modelled` observations of the UINT1 rule now match. Details: [docs/checker.md](docs/checker.md#round-56-fuzz-rules). Gates as in round 55.
Fresh fuzz seeds 451/452 (end of round 56): CR6 490 exact, 9 message differences; CR1000X 488 exact,
7 message differences, 4 false accepts, 1 warning-only difference. `70e8fae3a5b3` (excitation -1 mV) is
fixed; **open:** `9be1e8aba3d8` (`SerialOpen(ComC7,19200,0,0,150,"fuzz")`, an extra argument: `Parameter
must be a constant integer:` with the whole statement), `e6b6604b4da3` (`PRTCalc(RTD_C,2,...)` destination
bounds), `f76ae7c58e75` (`Sample(&HFF,GeoUnit,String)` String source bounds). Raw output in `scratch/r57/`.

**Round 55** (M1/M2; worker B's round-54 PipeLine work integrated, Codex review, coordinator fixes).
- **CI:** round 54 broke the CI replay gate (`921b96a020e3`: a `Sample` outside a table owned a
  `FieldNames`); fixed in `ce35fbd3`. Local rounds now run the replay exactly as CI does, with
  `--baseline tests/replay_baseline.json` (the gate without it missed this).
- **PipeLine placement data** (worker B, fixture `r54_pipeline_task_instructions.json`, 1,274
  observations, 1,242 matched): `SetDAC`, `ExciteI`, `Resistance`, `VibratingWire`, `EC100`, 24 `CDM_*`
  instructions, a setup group (`CDM_ExciteV`, `CDM_ExciteI`, `CDM_MuxSelect`) and the slow-scan
  `may skip scans` warning ([checker notes](docs/checker.md#task-instructions-in-pipeline-mode)).
- **False reject fixed:** a `Scan` inside `For`/`If`/`Do` in PipeLine Mode no longer makes its task
  instructions conditional (pre-existing since round 49, found by the Codex review).
- **False accepts fixed:** an unknown time unit in `TimeIntoInterval`/`IfTime` (GEUS `64dd660d49e3`);
  `SetSetting("Baudrate(C  OMC1)")` (only the first word of the index counts, `b74fe6cc73d0`); CR6
  `VibratingWire` with a numeric diagnostics argument.
- **Warnings:** no `may skip scans` for `Delay(0,0,...)` or `PortSet(...,1)`.
- Details: [docs/checker.md](docs/checker.md#unknown-time-units-and-settings-port-words-round-55) and the
  following section. Gates: fixture audits 0 differing, difftest and GEUS unchanged, CI replay gate passes,
  `verify.py` passes with both compilers.

**Round 54** (M1/M2; Sonnet workers A and C, Codex research and review, coordinator fixes).
- **False rejects fixed:** a `DataTable` named after a predefined constant (`DataTable(True,...)`)
  made that constant undefined everywhere (found by Codex research on `7d9dbda67c22`); a `FieldNames`
  with no owning output instruction was resolved against any preceding instruction.
- **False accepts fixed:** bridge MeasPEx below 1 (`BrHalf`/`BrHalf3W`/`BrFull`/`BrHalf4W`/`BrFull6W`,
  `Invalid measurements per excitation N`, GEUS fuzz `953f1271f9a9`); an initializer after empty type
  dimensions (`Public A As Float() = {...}`, `628c8b23311c`).
- **First error:** juxtaposed dimension operands (`Dim G(1  8)` -> `Incomplete expression: 1  8.`, also
  in Sub parameters and pointer types, `5d03371b1388`); `FieldNames` errors on the consuming output
  instruction's line with the two follow-up messages (`83c0a809c514`).
- Codex review of the staged diff found two new false accepts (`As Float!(1 8)`, an unclosed
  `Sub S(a(1 8)`), an `OverflowError` on a non-finite dimension and a same-line order flip; all four fixed
  and recorded (`r54_dim_review_controls.json`).
- Fixtures `r54_*.json`: 634 observations, 584 matched; details and gaps in
  [docs/checker.md](docs/checker.md#juxtaposed-dimension-operands-round-54) and the following round-54
  sections. Gates: fixture audits 0 differing, four-profile difftest and GEUS unchanged, replay gate
  passes, `verify.py` passes with both compilers.
- Worker B's PipeLine Mode work was still running at this commit (integrated in round 55).

**Round 53** (M1/M2, fresh relevant fuzz seeds 443/444; one session, coordinator only, Codex
research). The fuzz found no verdict difference by its classifier, but two of its six first-error
misses were a false accept on our side: a non-constant `Include`/`IncludeSection` file name
(`IncludeSection("Functions",Undeclared)`) was accepted; the reference rejects undeclared names and
variables, then reports a NULL file name
([checker notes](docs/checker.md#include-names-that-are-not-constants-round-53)). Fixture
`r53_include_runtime_names.json` (60 observations, 34 matched; CR300/CR350 and string-`Const` names
`not_modelled`); four round-43 `not_modelled` cases of the same rule now match. The other four
misses were open at the end of round 53 (all four fixed in round 54): `7d9dbda67c22` (CR6, `DataTable(True,FinApp_Starts,-1)`: the reference's main-file
error comes before our include-file error), `5d03371b1388` (CR1000X, `Dim GPSstring(1  8)` gives
`Incomplete expression: 1  8.`; probed on CR6/CR1000X: `Public G(8 1)` and `Dim G(2,1  8)` echo only
the bad dimension, `Dim G(x  8)` adds `Variable illegal in Parameter: x  8.`, and nothing else is
reported, where we print `Invalid symbol in expression: G(...)`; the fix belongs in
`parser._parse_vardecl`), `83c0a809c514` (CR1000X, a malformed `FieldNames` is reported on
the line of the instruction it names), `ba8d6f459994` (CR6, a continuation line).

**Round 52** (M2/M3, triage of the round-51 fuzz differences; one session, Sonnet workers A-C,
Codex `gpt-6.1-sol` research and review). Four of the 14 round-51 message differences are now exact.
- **False reject fixed:** CR1000X accepts `SW12(SW12_CSIO,...)` (port 503); its invalid-port message
  is a reference formatting bug (`Invalid port 6642484, must be NULL.` for 0), modelled as profile
  data ([instructions](docs/instructions.md#sw12-invalid-sw12-port-d)). Fixture `r52_sw12_ports.json`.
- **False accept fixed:** constant destinations (`PanelTemp(3,...)`, `Erase(5)`, `AddPrecise(3,1)`)
  inside a one-line `If` were unchecked (`e261e8e17146`); fixture `r52_inline_constant_destination.json`.
- **First error:** a negative `DataInterval` is reported before a later table `Reps`/bounds error, and
  a constant destination anywhere hides every `Reps`/bounds check (`3a4aa2c0910b`); fixture
  `r52_datainterval_first_error.json`.
- **Implementation exception hidden as `Unparsed`:** `Const K=CHR()` crashed the constant folder
  (`cfb47653ff50`); fixture `r52_const_builtin_arity.json`. `crbc` stdout now carries source bytes
  like the message file (a UTF-8 `°` was double-encoded); the in-process replay still counts
  `5a642d9a0c61` because it decodes the two sides differently.
- **M3:** `--report FILE`, `opencrbasic.toml` (`--config`, `--check CODE=SEVERITY`, suppressions with
  reasons), `CompileResult.findings`/`suppressed`; exit 4 for an invalid configuration under
  `--strict` (a round-52 decision) ([checker notes](docs/checker.md#check-configuration-and-report-file-round-52)).
  Codex review findings fixed: duplicate inline `AddPrecise(3,)` diagnostic, cross-drive `relpath`,
  non-string suppression codes, backslash include paths.
- Gates: fixture audit of the r51/r52 fixtures 0 differing; four-profile difftest and GEUS unchanged;
  replay gate passes; `verify.py` passes with both compilers.

**Round 51** (M1, fresh relevant fuzz, seeds 441 CR6 / 442 CR1000X, 500 mutants each, no
shrinking; one session).
- CR6: 492 exact, 6 message differences. CR1000X: 490 exact, 8 message differences, 1 false accept
  (`a66ad5f9beb3`), no false reject, crash, hang, implementation exception or mode difference.
  The false accept minimizes to an undeclared name as 7th `SDI12Recorder` argument inside a
  one-line `If`; `check_const_family_args` ran on top-level statements only. Fixed
  (`_with_inline`); rule in [docs/checker.md](docs/checker.md#pass-1-instruction-arguments-2026-09-27).
  Fixture `tests/fixtures/r51_inline_const_family_args.json` (14 observations). The 14 message
  differences are not triaged. Raw output: `scratch/r51/`.

Rounds 49 and 50 (PipeLine Mode placement of further task instructions, fixture
`tests/fixtures/r49_pipeline_task_instructions.json`, 868 observations) and all earlier round
logs, review fixes and local maintenance queues are not kept in this repository. Rules are in
[task instructions in PipeLine Mode](docs/checker.md#task-instructions-in-pipeline-mode).

## Next steps

Follow the [v1.0 milestones](PLAN.md#v10-milestones); M0 comes first.

1. **M0, remaining:** the CI workflow passed on GitHub on 2026-10-25 (run 40184946232, after the
   round-35 TDF fixture refresh); optionally record the parenthesized-assignment runtime on the
   bench CR6 (unknown so far).
2. Human decisions of 2026-10-24, recorded in [PLAN.md](PLAN.md#v10-milestones):
   - CR6 and CR1000X gate v1.0; CR300/CR350 are best effort;
   - the GEUS programs may be compiled as a corpus (`AGENTS.md` updated);
   - the `Status` lesson was corrected after the old observation could not be reproduced
     ([lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#status-names-are-compile-checked));
   - an `--strict` error for aliases named after CRBasic names (`OCB001`, round 33).
3. **M3: done (round 70).** Checks `OCB001`-`OCB007` ship with the framework (codes, severities,
   `opencrbasic.toml`, `--strict`, `--report`, `CompileResult.findings`). Remaining PLAN candidates
   need a reference probe first, value or flow analysis, or (the constant index past the declared
   storage) alias, flattened and String-index handling. The round-70 reference-channel false accept (a `Delay` unit parameter with a later
   same-name `Const`) is fixed in round 71. The language server does not run checks yet: that is M6
   (call `checks.analyze` with its settings, put the configuration in the cache key); done in round 73.
4. **M1: done (round 69).** Round 76 closed the routine-parameter exception recorded in the
   1.0 release record. The 25 historical replay
   false accepts outside the relevant set stay listed by identity (round 34: `072c74da6af5`,
   `51f39a5f2393`, `7b4b2177d56b`, `870ca9a9562e`, `e8c854e3493d`, `a0b2b0b059b3`, `ec6b11bc3176`,
   `022d98dd3efc`, `4156d89a014d`, `6ac24bd775f1`, `72f95eac103f`, `77636b10567c`, `7f9efa8af42c`,
   `c060e9f1402f`, `d12785e05faf`, `f2f6e5d8b8e6`; round 35: `0e23c61229f4`, `39a03364be83`,
   `52138d7bd179`, `577a59ecb08c`, `778ea45e64b7`, `9c6367a2c598`, `ecc509dd06de`, `f61f825392f2`,
   `fa923ecef6f4`) and get coverage notices with M4. CR300/CR350 remain best effort. After any
   `crbasic/` change run `fuzz_replay.py --rerecord-stale` first.
5. **M2: done (round 69).** Every first-error difference of the fresh batches since round 60 has a
   recorded decision in the [first-error review](docs/first-error-review.md); the realistic ones are
   fixed. Later-diagnostic differences are accepted under the 2026-10-24 decision.
6. **Open candidates from rounds 44-51:**
   - PipeLine Mode placement is still unprobed for `CDM_BridgeFilt`, `CDM_CalRead`, `CDM_CANOutCfg`,
     `CDM_FactCal`, `CDM_FFTFilt`, `CDM_OffsetRd`, `CDM_VoltFilt`, `CDM_VW300*`, `SDMTrigger`, `TGA`;
     same-line order with the before-`BeginProg` message is not modelled (round 55);
   - round-54 fuzz: 3 first-error misses and 1 earlier first location remain (`scratch/r54/triage.json`; `SetSetting("",...)` and an overflowing `Scan` count match in isolation, so they are ordering effects);
   - 10 round-51 message differences remain (`scratch/r51/`; all later-diagnostic or same-location text except `8f0c2a005513`:
     the reference prints 9 `Parameter will truncate` warnings before the error for an undeclared first
     `TimeIntoInterval` argument in an `If`, the round-38 cascade in `scratch/r38/tii.json`, 9/6/3 by argument;
     Codex research in `scratch/r53/codex-8f0c.log`); PipeLine probing of the instructions above was not started in round 52 (Codex cannot reach the Wine server from its sandbox, so it does research and review only); `VoltSE(...,Undecl)` in a
     one-line `If` prints the `Invalid symbol` line without the earlier `Invalid channel` line;
   - older fuzz findings: `0a9af24e1266` (malformed `Sub` parameter first error), `71107f867ad9`,
`f67f1e2a1258` (unterminated string in a call), `522ddaa3d29c`
     (warning only); numeric routine-parameter declaration diagnostics, quoted-Then condition
     boundary and malformed one-line ElseIf (strict fixture gaps).
7. **M4: done (round 72)** for CR6 ([inventory](docs/inventory.md)). Regenerate with
   `uv run python tools/inventory.py` after changing checker messages, fixtures or the replay corpus;
   `tests/test_inventory.py` fails when the binary or instruction table changes. Not done: a CR1000X inventory
   (the PLAN scopes M4 to CR6), notices in the language server (M6). Next: M5 (artifact matrix) or
   M6 (editor).
8. **M6: done (round 73).** The language server publishes the checks (`opencrbasic (check)`, code,
   evidence link) and, with `coverageNotices`, the notices; `opencrbasic.checks` and
   `opencrbasic.toml` configure them; quick fixes match by `data`.
9. **M5: done (round 74)** ([artifacts](docs/artifacts.md)). Open: per-profile Status tables for
   the CR1000X/CR300/CR350 TDF (best effort now).
10. **M7: done (round 75)**: 1.0.0, [release record](docs/release-1.0.md). Further work is
   post-1.0: per-profile TDF Status tables, the open first-error candidates (item 6), host tests of
   the M6 editor display.
11. The old next-step items (Const diagnostics, interval gaps, table metadata, artifact refresh)
   are not kept in this repository.
   Re-rank each under the fix, flag or accept rule before working on it.

## Environment setup

Run package tools from the repository root:

```bash
export PYTHONPATH="$PWD/src"
uv sync
```

Python 3.15+ is required. The explicit `PYTHONPATH` also keeps
worktree runs from importing the main checkout through the shared editable installation.

The current macOS Wine setup is documented in the open-loggernet README ("Compiling on macOS").
The oracle uses `~/.wine-cr6`, `WINEDEBUG=-all`, and defaults to disabling GUI drivers through
`WINEDLLOVERRIDES=winemac.drv,winex11.drv,winewayland.drv=d`; explicit overrides are preserved.
For a new prefix, disable crash dialogs once:

```bash
WINEPREFIX="$HOME/.wine-cr6" WINEDEBUG=-all \
  "$HOME/.local/wine/Wine Devel.app/Contents/Resources/wine/bin/wine" \
  reg add 'HKCU\Software\Wine\WineDbg' /v ShowCrashDialog /t REG_DWORD /d 0 /f
```

Runner
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

Follow [repository instructions](AGENTS.md) and the current human request. Rounds 29–31 (human
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

Keep heavy validation runs bounded and avoid overlapping them. Do not kill validation process
trees: prior forced termination exhausted macOS multiprocessing semaphores. Before another
long round, `python -c "import multiprocessing as m; m.Lock()"` checks that pools can start.
Update this file after each milestone; move superseded snapshots and run history to the archive.
