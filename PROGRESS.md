# opencrbasic progress

Current compiler state and handoff, updated **2026-10-21 after development round 26**.
Measurements describe the round-26 implementation. Inspect `git status` before editing;
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
| `pytest -q -n 4 -m 'not server'` | 43,599 passed, 311 skipped, 186 strict xfail (round-24/25/26 fixture gaps) |
| `pytest -q -m server` | 163 passed |
| Round-26 measurement-alias fixture and audit | 282 reference observations (four profiles): 141 match, 141 strict xfail; audit 0 differing |
| Round-25 optional-output/burst fixture and audit | 444 reference observations; all match; audit 0 differing |
| Round-24 destination/settling/FieldNames fixture | 674 reference observations (CR6/CR1000X/CR300/CR350): 629 match, 45 documented gaps |
| Integer-TimeOut fixture and fresh four-profile audit (round 23) | 236 cases; 0 differing; 0 missing reference verdicts |
| Expression-source fixture and its audit (round 22) | 100 cases across four profiles; 0 differing |
| Saved fuzz replay, 3,158 inputs, round-26 implementation against round-22 reference results | 2,393 exact; 48 false accepts; 682 message differences; 16 mode differences; 19 reference/runner failures retained separately |
| Round-26 replay comparison against round 25 | All 3,139 usable-reference cases unchanged; 19 reference failures kept separate. Reference compilers were not rerun for this replay |
| CR6 TDF and table-list corpus comparison (round 22) | Both 210/210 byte-identical; 308 reference rejections, six missing expected artifacts and one reference fault |

Four-profile diagnostic comparison, rerun in round 26 (`tools/difftest.py --group all`):

| Profile | Repository `.CR6` programs: exact / same verdict, different text | Help examples: exact / reference failures |
|---|---|---|
| CR6 | 18 / 0 | 506 / 1 |
| CR1000X | 18 / 0 | 506 / 1 |
| CR300 | 7 / 11 | 505 / 2 |
| CR350 | 8 / 10 | 505 / 2 |

No verdict mismatches, implementation exceptions or lost exact cases were observed on usable
reference results. The CR300/CR350 repository rejections reflect CR6-specific programs. The help-corpus
failures are `pointervariable_2.CR6` on all four profiles and `strcomp_3.CR6` on CR300/CR350.
The differential and replay gates exit 1 while reference verdicts are missing; this is not a clean
parity gate. Compare individual valid results and keep failures separate.

Repository verification passed in round 26 both with the reference compiler and with
`CR6COMP=opencrbasic`:

```text
BUILD  host tests 140 pass | CRBasic 19/19 compiled, 5 warning(s) | repository pass
```

Expected warnings are owned by [repository build status](../STATUS.md#build). This verification
includes the `.CR1X` bench harness; the diagnostic corpus table above covers `.CR6` programs.
Compilation and host tests do not establish logger runtime behavior. No hardware tests were run.

Rounds 23–26 did not rerun full corpus TDF/table-list comparisons on any profile; round 22 ran CR6 only.
These rounds did not rerun full timing, message-file, signature and preprocessing coverage. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

Round 26 resolves bare Float alias destinations in the CR6/CR1000X measurement Reps checker.
The backing array's remaining capacity and the reference compiler's diagnostic name are now
used for normal and optional measurement outputs. All seven round-25 alias cases now pass.
Rules, decompiled-function trail and limitations are in
[measurement alias destinations](docs/checker.md#measurement-alias-destinations-round-26).

The new four-profile fixture preserves 282 reference observations: 141 match the implementation
(up from 111 before this round), while 141 strict expected failures preserve neighboring gaps.
These are existing dimensioned/indexed alias, alias-chain, declaration-shadowing and CR300/CR350
bounds/type/channel and scalar routine-parameter differences; they are not newly introduced regressions.

Local probes and validation logs are under `scratch/r26*` (untracked). Prior state is preserved
in the [round-25 handoff snapshot](docs/progress-history.md#round-25-handoff-snapshot-2026-10-20).
No hardware was flashed or tested. TDF bytes of the round-22 expression fixture still require
`tools/tdf_expression_fixture_gen.py --audit`; diagnostic audits do not check them.

## Next steps

1. Continue the builder-driven approach of round 24: `tools/builder_map.py FUN_xxxxxxxx` lists
   every instruction builder (compile-function thunks resolved through the PE image) that calls a
   shared decompiled helper, with the call's arguments. Open neighbours with
   strict `xfail` observations: CR1000X `Invalid PulseCount reps.`, CR6 4W/6W channel overlap,
   CSAT3 negative Reps `Out of memory.`, the timing line of an extreme settling time, and a lone
   table bounds line that should not gate pass 2. Round 25 resolved the 4W/6W doubling and Resistance
   `+1` options and the 4W/6W burst MeasPEx constraint. Round 26 resolves the seven bare-alias cases. The new
   alias fixture preserves dimensioned/indexed aliases, alias chains, local declaration shadowing
   and CR300/CR350 bounds/type/channel gaps; use its minimized cases before extending resolution. Not yet probed: the remaining settling-time callers (Thermistor, AM25T, Therm107-109, Resistance),
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
   fuzz seeds 413–416, with no competing heavy run. Earlier seeds are recorded in
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

Follow [repository instructions](../AGENTS.md) and the current human request. This session works
directly without subagents. Historical agent/model assignments, review arrangements and branch
integration instructions in the archive are not an active workflow. Commit or push only when
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
