# opencrbasic progress

Current compiler state and handoff, updated **2026-10-19 after development round 22**.
Measurements describe the round-22 implementation. Inspect `git status` before editing.

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
| `pytest -q -n 4 -m 'not server'` | 42,149 passed, 311 skipped |
| `pytest -q -m server` | 163 passed |
| New expression-source fixture and its audit | 100 cases across four profiles; 0 differing |
| Saved fuzz replay, 3,158 inputs, after serial retries | 2,376 exact; 62 false accepts; 685 message differences; 16 mode differences; 19 reference/runner failures |
| Replay regressions against the pre-change snapshot | No changed compiler output on any input with a usable reference verdict; no false rejects, implementation exceptions, crashes or hangs observed |
| CR6 TDF and table-list corpus comparison | Both 210/210 byte-identical; 308 reference rejections, six missing expected artifacts and one reference fault |

Four-profile diagnostic comparison (`tools/difftest.py --group all`):

| Profile | Repository `.CR6` programs: exact / same verdict, different text | Help examples: exact / reference failures |
|---|---|---|
| CR6 | 18 / 0 | 506 / 1 |
| CR1000X | 18 / 0 | 506 / 1 |
| CR300 | 7 / 11 | 505 / 2 |
| CR350 | 7 / 11 | 505 / 2 |

No verdict mismatches, implementation exceptions or lost exact cases were observed in this
comparison. The CR300/CR350 repository rejections reflect CR6-specific programs. The help-corpus
failures are `pointervariable_2.CR6` on all four profiles and `strcomp_3.CR6` on CR300/CR350.
The differential and replay gates exit 1 while reference verdicts are missing; this is not a clean
parity gate. Compare individual valid results and keep failures separate.

Repository verification passed with both the reference compiler and `CR6COMP=opencrbasic`:

```text
BUILD  host tests 136 pass | CRBasic 19/19 compiled, 6 warning(s) | repository pass
```

Expected warnings are owned by [repository build status](../STATUS.md#build). This verification
includes the `.CR1X` bench harness; the diagnostic corpus table above covers `.CR6` programs.
Compilation and host tests do not establish logger runtime behavior. No hardware tests were run.

The latest round did not rerun full corpus TDF/table-list comparisons on the other three profiles,
or full timing, message-file, signature and preprocessing coverage. CDM binary images (`-b`) are
[explicitly excluded from parity](PLAN.md#explicit-exclusions-from-parity). Distribution policy is
in [legal and provenance notes](docs/legal.md).

## Latest changes and evidence

String-function temporary-buffer widths, member field metadata and duplicate checks now match
the new reference cases. A zero-byte `Replace` temporary reports the reference allocation error
instead of reaching a division by zero during table rendering. Rules and remaining limits are in
[TDF expression buffers and member sources](docs/tdf-rules.md#expression-buffers-and-member-sources-round-22-2026-10-19).

The fixture is `tests/fixtures/tdf_expressions.json`; tests check ordered diagnostics and exact
bytes of explicitly declared DataTables, including an auto-sized table. Regenerate or audit with
`tools/tdf_expression_fixture_gen.py`; the standard diagnostic fixture auditor does not cover its
TDF bytes. Implicit structure-table header/units differences on CR300/CR350 remain open.

Latest local run evidence is under `scratch/r22_*`: the differential baseline/final reports,
pytest/server logs, fixture audit, TDF comparison and both repository builds. Replay reports are
`scratch/r22_replay_fresh.json`, `scratch/r22_replay_retries.json` and the combined
`scratch/r22_replay_final_refreshed.json`; `scratch/r22_replay_comparison.json` compares observed
pre-change compiler output with the refreshed reference results. These are untracked local files;
tracked fixtures and behavior notes remain the durable evidence.

## Next steps

1. Extend the error-count-dependent constant-folding investigation beyond negative TimeOut
   literals: TCPOpen/SerialOpen constant arguments, Timer's third argument, Scan/DataInterval,
   Const values, `1-1`, `-KT`, `KT+1`, signed hex/binary forms and unresolved names. Probe all four
   compilers before generalizing; start from the existing TimeOut fixtures and
   [instruction behavior notes](docs/instructions.md).
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
