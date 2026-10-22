# opencrbasic progress

Current compiler state and handoff, updated **2026-10-22 after development round 29**.
Measurements describe the round-29 implementation. Inspect `git status` before editing;
use Git to check commit and publication state.

Design and parity criteria: [PLAN.md](PLAN.md). Previous measurements, round logs and superseded
workflow instructions: [progress history](docs/progress-history.md). Language-server work has its
own [plan](LSP_PLAN.md) and [progress ledger](LSP_PROGRESS.md).

## Current status

The CR6, CR1000X, CR300 and CR350 profiles are implemented. Complete parity remains open:
saved reproducers still contain false accepts, diagnostic differences and mode differences;
output coverage also has gaps. Exact diagnostic agreement means equal exit codes and normalized
ordered stdout, not byte parity. Reference crashes and runner failures are not compiler verdicts.

Latest observed validation, on macOS Apple Silicon with Wine 11.17 and Python 3.15:

| Check | Latest result |
|---|---|
| `pytest -q -n 4 -m 'not server'` | 44,744 passed, 311 skipped, 187 strict xfail (round-24/26/27/29 fixture gaps) |
| `pytest -q -m server` | 163 passed |
| Round-29 fixtures and audit (parameter headers, String parameters, review fixes) | 180 / 320 / 104 reference observations (four profiles): 176 / 320 / 92 match, 4 / 0 / 12 strict xfail; audit 0 differing |
| Round-28 parameter-alias fixture and audit (round-29 implementation) | 368 reference observations: all match; audit 0 differing |
| Round-27 alias-declaration fixture and audit | 152 reference observations: 144 match, eight strict xfail; audit 0 differing |
| Round-26 measurement-alias fixture and audit | 282 reference observations: 164 match, 118 strict xfail; audit 0 differing |
| Round-25 optional-output/burst fixture and audit | 444 reference observations; all match; audit 0 differing |
| Round-24 destination/settling/FieldNames fixture | 674 reference observations (CR6/CR1000X/CR300/CR350): 629 match, 45 documented gaps |
| Integer-TimeOut fixture and fresh four-profile audit (round 23) | 236 cases; 0 differing; 0 missing reference verdicts |
| Expression-source fixture and its audit (round 22) | 100 cases across four profiles; 0 differing |
| Saved fuzz replay, 3,158 inputs, round-29 implementation against round-22 reference results | 2,391 exact; 48 false accepts; 684 message differences; 16 mode differences; 19 reference/runner failures retained separately |
| Round-29 replay comparison against the round-28 checker | Six outputs changed: five message differences became exact, one CR6 message difference now also reports the reference's line-35 diagnostic. No new false accept or reject. Reference compilers were not rerun for this replay |
| Fresh bounded fuzz, seeds 417–420, 40 mutants/profile (round 28; not rerun in round 29) | 157 exact; one pre-existing false accept and one diagnostic difference; one CR1000X reference fault; six existing seed faults kept separate ([details](docs/fuzz-findings.md#round-28-bounded-refresh-2026-10-22)) |
| CR6 TDF and table-list corpus comparison (round 22) | Both 210/210 byte-identical; 308 reference rejections, six missing expected artifacts and one reference fault |

Four-profile diagnostic comparison, rerun in round 29 (unchanged from round 28) (`tools/difftest.py --group all`):

| Profile | Repository `.CR6` programs: exact / same verdict, different text | Help examples: exact / reference failures |
|---|---|---|
| CR6 | 19 / 0 | 506 / 1 |
| CR1000X | 19 / 0 | 506 / 1 |
| CR300 | 8 / 11 | 505 / 2 |
| CR350 | 9 / 10 | 505 / 2 |

No corpus verdict mismatches, implementation exceptions or lost exact cases were observed on
usable reference results. The CR300/CR350 repository rejections reflect CR6-specific programs. The help-corpus
failures are `pointervariable_2.CR6` on all four profiles and `strcomp_3.CR6` on CR300/CR350.
The differential gates exit 1 while reference verdicts are missing; this is not a clean parity gate.
The saved-reference replay script reports these failures separately and exits 0. Compare individual
valid results and keep failures separate.

Repository verification passed in round 29 both with the reference compiler and with
`CR6COMP=opencrbasic`:

```text
BUILD  host tests 142 pass | CRBasic 20/20 compiled, 6 warning(s) | repository pass
```

Expected warnings are owned by [repository build status](../STATUS.md#build). This verification
includes the `.CR1X` bench harness; the diagnostic corpus table above covers `.CR6` programs.
Compilation and host tests do not establish logger runtime behavior. No hardware tests were run.

Rounds 23–29 did not rerun full corpus TDF/table-list comparisons on any profile; round 22 ran CR6 only.
These rounds did not rerun full timing, message-file, signature and preprocessing coverage;
round 27 refreshed only the five repository signatures in `tests/test_signature.py`. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

Round 29 closes all 84 round-28 expected failures, in three steps. Two read-only Codex
(gpt-6.1-sol) research passes traced the CR6 decompilation and predicted rules and probes;
Claude workers confirmed each rule on all four profiles before implementing it; a Codex review
of the combined diff found five defects, all confirmed by reference probes and fixed.

- [Routine-parameter header dimensions](docs/checker.md#routine-parameter-header-dimensions-round-29):
  header dimensions resolve in declaration order; a nonconstant, undefined or <1 dimension is
  reported and recovers to 1 with the parameter still registered; more than three dimensions
  and nonconstant String lengths are reported; Boolean and Double parameters are alias
  sources; an earlier Const wins over a same-named parameter in alias indices; type and
  dimension diagnostics follow parameter order; CR300/CR350 stop the header at the first bad
  parameter (`Too many parameters:` two positions on) and silence later failed headers.
- [String alias storage](docs/checker.md#string-alias-storage-round-29): all String sources
  (parameter, `Public`, local `Dim`) now use word units. A slot of `String * L` takes
  ceil(max(4, L+1)/4) words; a character subscript is `Alias is illegally dimensioned.`; list
  items advance by whole slots; the explicit-count bound applies only to dimensioned RHS names.
  Nonfinite lengths convert like a C long instead of raising.
- Codex's research predictions held except for CR300/CR350 multi-parameter and multi-routine
  header stops, which the probes established.

Remaining strict xfails: four Const-shadow body assignments (`Invalid assignment in expression:
J=1.`), four `Public P As String * K` (no diagnostic; predates round 29), and eight `Out of
memory.` lines after an unresolved String parameter length. Unprobed: three-dimensional
Strings, recovered shape beyond three dimensions, and whether type or too-many-dimension errors
also silence later CR300/CR350 headers.

Probe scripts, research reports and validation logs are under `scratch/r29/` and
`scratch/codex/` (untracked). Prior state is in the
[round-28 handoff snapshot](docs/progress-history.md#round-28-handoff-snapshot-2026-10-22).
Production CRBasic was not changed. No hardware was flashed or tested.

## Next steps

1. Continue the builder-driven approach of round 24: `tools/builder_map.py FUN_xxxxxxxx` lists
   every instruction builder (compile-function thunks resolved through the PE image) that calls a
   shared decompiled helper, with the call's arguments. Open neighbours with
   strict `xfail` observations: CR1000X `Invalid PulseCount reps.`, CR6 4W/6W channel overlap,
   CSAT3 negative Reps `Out of memory.`, the timing line of an extreme settling time, and a lone
   table bounds line that should not gate pass 2. Round 25 resolved the 4W/6W doubling and Resistance
   `+1` options and the 4W/6W burst MeasPEx constraint. Round 26 resolved the seven bare-alias cases;
   round 27 resolved alias source binding and chain diagnostic precedence. Rounds 28–29 resolved
   routine-parameter alias sources, header dimensions and String storage. Start with the
   round-29 leftovers (Const-shadow body assignment, `Public` nonconstant String length,
   `Out of memory.` after an unresolved String length) or the round-27 global-name collisions.
   Round-26 dimensioned/indexed measurement aliases, scalar parameter measurement bounds and
   CR300/CR350 post-error cascades also remain; use their minimized cases before extending resolution. Not yet probed: the remaining settling-time callers (Thermistor, AM25T, Therm107-109, Resistance),
   SDMGeneric's String destination rule and `EC100` options past its table. Then extend the
   constant-folding gaps from round 23 (`KT/2`, `2^0`, `NOT 0`, `1<<31`, Float variable TimeOut,
   an earlier `IfTime("a",1,Sec)` warning) and the TCPOpen/SerialOpen/Timer/Scan constant
   arguments ([round-23 observations](docs/instructions.md#integer-timeout-expressions-and-named-negation-all-four-loggers-round-23)).
2. Resolve remaining Const diagnostics and ordering: `Variable used in CONST expression.`,
   `X = J(1)`, `Const K = Min(2,3)`, CR300/CR350 `Variable illegal in Parameter: J.`, and a Sub
   referring to a Const declared later. Evidence and completed neighboring rules are in
   [checker notes](docs/checker.md), especially the Const sections.
3. Close interval gaps: zero-interval list units when Scan and DataInterval units differ,
   fractional intervals, CR300 out-of-range Scan unit display, and integral/constant checks in
   one-line If bodies. Recheck unprobed numeric-unit auto-size weights and finite Debug values
   between 2^31 and FLT_MAX. Owners: [TDF rules](docs/tdf-rules.md) and
   [instruction rules](docs/instructions.md).
4. Finish table metadata coverage: per-profile Status definitions (including CardBytesFree),
   CR300/CR350 implicit structure-table headers/units, and zero-argument Date/PPPOpen sources.
   FFT literals and malformed one-argument IncludeSection inputs currently supply reference
   faults rather than usable verdicts.
5. Refresh all-profile output coverage, including timing, message-file bytes, signatures,
   preprocessing and TDF/table lists. Distinguish missing from empty LST artifacts in
   `tools/tdftest.py`; retain the non-ASCII diagnostic encoding and compiler-module typing leads
   from the [archived LSP findings](docs/progress-history.md#compiler-items-found-by-the-lsp-work-recorded-2026-10-11-not-executed).
6. Rank the current replay differences by rule family before choosing another semantic chunk.
   Older leads include SlowSequence argument/block combinations and allocation edge cases; their
   archived task status must be reproduced rather than assumed current. Then run fresh bounded
   fuzz seeds 421–424, with no competing heavy run. Earlier seeds are recorded in
   [fuzz findings](docs/fuzz-findings.md).

## Environment setup

Run package tools from `opencrbasic/`, using its external uv environment:

```bash
export UV_PROJECT_ENVIRONMENT="$HOME/.uv-envs/opencrbasic"
export PYTHONPATH="$PWD/src"
uv sync
```

Python 3.15+ is required. Never create a repository `.venv`. The explicit `PYTHONPATH` also keeps
worktree runs from importing the main checkout through the shared editable installation.

The current macOS Wine setup is documented in [scripts/README.md](../scripts/README.md#compiling-on-macos).
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

Follow [repository instructions](../AGENTS.md) and the current human request. Round 29 (human
instruction 2026-10-22) used Claude subagents for implementation and Codex `gpt-6.1-sol` for
read-only decompilation research and review, run with the ChatGPT app's bundled CLI
(`/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex exec
-m gpt-6.1-sol -s read-only`; `~/.local/bin/codex` 0.158 rejects the model). Workers follow
`scratch/r29/brief-worker.md`; treat Codex output as hypotheses to confirm on the reference. Commit or push only when
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
