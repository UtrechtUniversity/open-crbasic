# opencrbasic progress history

Archived snapshots and development logs. [Current state and next steps](../PROGRESS.md)
live in `PROGRESS.md`. Branches, pending work, machine state and workflow policies below
describe their recorded dates; they are not current instructions.

## Round 24 latest-changes snapshot (2026-10-19)

Superseded by round 25 in `PROGRESS.md`. Validation: 43,014 non-server tests passed,
311 skipped, 45 strict expected failures; 163 server tests passed. Saved replay: 2,393 exact,
48 false accepts, 682 message differences, 16 mode differences, 19 reference/runner failures.
Four-profile corpus diagnostics and repository verification were unchanged from round 23.

### Latest changes and evidence (round 24)

Round 24 used the decompiled CR6 instruction builders to replace guessed per-instruction rules
with the counts the compiler itself passes to its shared destination resolver, then confirmed every
boundary on the reference compilers. Fixture `tests/fixtures/r24_dest_counts.json` holds 674
observations (629 asserted, 45 strict `xfail` cases for documented neighbouring gaps):

- Destination element counts for SDMGeneric, SDMSW8A, CSAT3/CSAT3B/CSAT3BMonitor, the CS7500/
  LI7200/LI7700 command tables, TGA, EC100, SDMCD16AC, PeakValley, HydraProbe, GOESGPS and RectPolar
  ([builder destination counts](checker.md#builder-destination-counts-round-24-2026-10-19));
  the previous LI7700 rule was wrong for most commands.
- Measurement Reps destination size for BrHalf3W, BrHalf4W, BrFull, BrFull6W, PulseCount, CS616
  (CR6/CR1000X) and Resistance (CR6), from the shared `FUN_005163b0` validator family.
- Table-output sources written `X()`, multi-dimensional sources and expression subscripts
  ([table output source](checker.md#table-output-source-with-x-a-multi-dimensional-source-or-an-expression-subscript-round-24)).
- Analog settling time limits 20..6000000 with C `(long)` conversion
  ([analog settling time](instructions.md#analog-settling-time-cr6cr1000x-round-24)).
- `FieldNames` argument validation on all four loggers
  ([FieldNames argument](checker.md#fieldnames-argument-round-24-all-four-loggers)).

TDF bytes of the round-22 expression fixture still require `tools/tdf_expression_fixture_gen.py
--audit`; the diagnostic fixture auditor does not check them.

Latest local evidence is under `scratch/r24*` (probe logs, builder extraction scripts, replay and
differential runs); these files are untracked. Round-23 evidence and its change notes are archived
in the [progress history](progress-history.md#round-23-latest-changes-snapshot-2026-10-19).

## Round 23 latest-changes snapshot (2026-10-19)

Superseded by round 24 in `PROGRESS.md`. Validation counts of this round: 42,385 non-server tests
passed (311 skipped), 163 server tests; saved replay 2,376 exact, 62 false accepts, 685 message
differences, 16 mode differences, 19 reference/runner failures.

### Latest changes and evidence (round 23)

Round 23 extends the error-dependent TimeOut fold to integer `+`, `-`, `*`, unary signs on
constants/expressions and signed hex/binary forms, with 32-bit wrapping. Clean and error-gated
HTTPPut/FTPClient behavior now matches the 236-case fixture. Mechanism, evidence and known limits:
[integer TimeOut expressions](instructions.md#integer-timeout-expressions-and-named-negation-all-four-loggers-round-23).
The wider 320-observation probe also found remaining float, runtime, `NOT`, shift and warning
gaps; these are documented explicitly and are outside the implemented fixture's scope.

Round-22 table-buffer and member fixes remain covered by `tests/fixtures/tdf_expressions.json`:
[rules and remaining limits](tdf-rules.md#expression-buffers-and-member-sources-round-22-2026-10-19).
Its TDF bytes require `tools/tdf_expression_fixture_gen.py --audit`; the diagnostic fixture auditor
does not check them.

Latest local evidence is under `scratch/r23_*`: probes, fixture audit, tests, differential runs,
repository verification and replay reports. `r23_baseline.json` and `r23_replay_candidate.json`
compare compiler output with the round-22 refreshed reference verdicts; the 19 missing reference
verdicts are excluded from output comparisons. Earlier fresh reference sweeps remain under
`scratch/r22_*`. These files are untracked; fixtures and behavior notes are the durable evidence.

## Handoff snapshot through development round 22 (2026-10-19)

Preserved from the pre-cleanup progress ledger, including its older status tables and
superseded resume lists. Heading levels and relative Markdown links have been adjusted.
Other paths in this snapshot are relative to `opencrbasic/`.

### opencrbasic progress

Living state. Update after every milestone. Plan: `PLAN.md`.

Proposed CR6 language-server work has a separate [plan](../LSP_PLAN.md) and
[resume ledger](../LSP_PROGRESS.md); compiler measurements and worker policy remain here.

#### Environment setup (needed to resume)

- macOS (Apple Silicon, set up 2026-10-12): Rosetta 2, then Wine 11.17 (Gcenx build) unpacked to
  `~/.local/wine` per `scripts/README.md` "Compiling on macOS"; prefix `~/.wine-cr6` (created on
  first run; then add the ShowCrashDialog key below). `uv sync` with the env variables of each
  `.envrc`. Codex reviews are not available on this machine (see "Reviews").

- Python: uv (`uv sync`, `uv run pytest -q`, `uv run tools/<tool>.py`), with
  `UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/opencrbasic` (`.envrc`); never a `.venv` in the
  repository (scripts/verify.py rejects it).

- Oracle: `apt-get install wine32:i386 wine64 wine` (on Ubuntu 24.04 with the ondrej PPA,
  first `apt-get install --allow-downgrades libgd3=2.3.3-9ubuntu5 libgd3:i386=2.3.3-9ubuntu5`),
  `dpkg --add-architecture i386` first. Run with `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all`.
  First run takes ~15 s (prefix creation), later runs ~2 s.
- Ghidra 11.4.2 headless (optional, RE only): `/opt/re/ghidra_11.4.2_PUBLIC`, script
  `tools/ghidra/DumpAll.java` writes all decompiled functions to one C file.

- Wine crash popups: disabled in the macOS compiler prefix `~/.wine-cr6` on 2026-10-01 at the
  human's request. `HKCU\Software\Wine\WineDbg\ShowCrashDialog` is DWORD `0` (confirmed by
  `wine reg query`). The known `pointervariable_2.CR6` crash subsequently returned exit 5 with
  console crash details, without the previous dialog wait. For a new prefix, run once:

  ```bash
  WINEPREFIX="$HOME/.wine-cr6" WINEDEBUG=-all \
    "$HOME/.local/wine/Wine Devel.app/Contents/Resources/wine/bin/wine" \
    reg add 'HKCU\Software\Wine\WineDbg' /v ShowCrashDialog /t REG_DWORD /d 0 /f
  ```

  The built-in oracle runner also defaults `WINEDLLOVERRIDES` to
  `winemac.drv,winex11.drv,winewayland.drv=d`, disabling GUI drivers for compiler subprocesses.
  This avoids runtime assertion dialogs, which the registry key alone did not suppress. All six
  previous timeout cases returned promptly with this invocation: four reference faults and two
  usable verdicts. Explicit environment overrides and `CR6COMP_RUNNER` are preserved.

  This preserves Wine's console crash report. It applies to runners using this prefix, including
  the oracle, differential tools, and `scripts/cr6comp.py`. It does not close existing dialogs.

#### Status (2026-10-12, end of round 15; resume in a clean session)

Measured on `claude/laughing-franklin-08z9vo` at the round-15 head (plus the docs commit that adds this
text), on the macOS M4 machine (Wine 11.17, CPython 3.13). Exact = equal exit code and normalized ordered stdout. False reject = real compiler
accepts, opencrbasic rejects: **none observed** in any differential, replay or fixture run.

| Gate | Result |
|---|---|
| `uv run --no-sync pytest -q -n 8` (full suite) and `-m server` | 37,418 passed, 311 skipped; server 161 passed (round 15, after reboot) |
| `tools/difftest.py --group all`, crbasic/ (16) exact | CR6 16, CR1000X 16, CR300 6, CR350 6 (the rest are matching-verdict rejections of CR6 programs) |
| help corpus (507) exact / msg differs / verdict mismatch / oracle err | CR6 506/0/0/1, CR1000X 506/0/0/1, CR300 505/0/0/2, CR350 505/0/0/2 |
| `tools/fuzz_replay.py` (2,923 reproducers incl. seeds 401-408) | 2,272 exact, 52 false accepts, 0 false rejects, 569 message diffs, 14 mode diffs, 16 oracle errors (`scratch/replay-r15.json`) |
| `tools/tdftest.py --group all` | 2026-10-12 unchanged: table list 207/208 (only `goestable_1`). repo 16/16; combined repo+CHM 208 compared (16 repo + 192 CHM) TDF 207/208, table list 207/208 (only `goestable_1` field name), so CHM 191/192. Of the 507 CHM inputs: 192 compared, 308 rejected by the real compiler, 6 with no non-empty LST seen by the harness (it conflates absent and empty), 1 oracle fault. Label corrected 2026-10-11 from host evidence `docs/lsp-rounds/host-tests/2026-10-11-602f6462/RESULTS.md` |
| `-t` [Times] (`scratch/timing_harness.py`, untracked, lost with the old machine) | 2026-10-11: repo 16/16, CHM 506/507 (only `pointervariable_2`, a real-compiler crash); not re-run in round 13 |
| implementation exceptions (difftest/replay class) | 0 |
| `cd ../scripts && uv run --no-sync verify.py` (env `$HOME/.uv-envs/firnmelt-scripts`) | 2026-10-12 (after round 13): `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` |

Reference faults (`pointervariable_2.CR6` on all loggers, `strcomp_3.CR6` on CR300/CR350, 16
replay reproducers) make the gates exit nonzero; they are counted separately, never as verdicts.
Every fixture under `tests/fixtures/` records real-compiler output; recheck one with
`uv run --no-sync tools/fixture_audit.py tests/fixtures/<x>.json` (must report 0 differing).

Help corpus: no remaining verdict or message diffs (only reference faults). Remaining fuzz false accepts: CR300/CR350 storage limit (2, `docs/tdf-rules.md`
"Storage limit"), Scan-interval overflow autoallocate (1), `CDM_Delay` >60 min message count (1).

| Phase | State |
|---|---|
| 0 Oracle + corpus | done: `tools/oracle.py`, `tools/difftest.py`, 507 CHM examples |
| 1 Binary RE | done and ongoing: `docs/binary.md` (functions, message tracer), Ghidra 12 dumps, CLI, type codes, messages, modes, TDF rules, signature, logger differences |
| 2 Front end | implemented; small residual gaps (`docs/frontend.md`, `docs/checker.md`) |
| 3 Checker | near parity on the help corpus; long tail of message ordering in fuzz reproducers |
| 4 Fuzzing | `tools/fuzz.py`, `tools/fuzz_replay.py`; 2,251 saved reproducers; seeds used: 0, 1, 42-54, 100, 201-208, 301-308 (`docs/fuzz-findings.md`) |
| 5 Outputs | TDF/table list exact except one name; `-t` exact except one crash; exact auto-size model; CDM binary image excluded from parity (`PLAN.md` "Explicit exclusions from parity") |
| 6 Complete parity gate | incomplete: 539 fuzz message diffs, 67 false accepts, storage limit |

History, older measurements and the per-chunk log: `docs/progress-history.md`.

#### Work method

- **One chunk = one observed rule family.** Minimise, probe all four loggers (incl. accepted
  boundaries) with `tools/probe.py` (template with `$BODY$` + cases file; prints `==`/`!=` real
  vs ours, cached oracle), implement minimally and data-driven in the existing passes, add a
  fixture from the real compiler (`scratch/mkfixture.py <name> <template> <cases> <loggers>`,
  untracked helper) with a small test, document in `docs/checker.md` or `docs/instructions.md`,
  then full pytest + four-logger difftest + replay + fixture audit. One commit per chunk. Never
  generalise beyond probed evidence: a false reject is the worst outcome.
- **Who does the work (human instructions 2026-10-03 and 2026-10-11).** The coordinating session
  plans, integrates and verifies; it does not implement chunks itself. Workers are Claude
  subagents (Agent tool), one family each, in their own worktree:
  - `model: "haiku"` (Haiku 5.5) for easy/medium work: integrating clean branches, review findings
    that come with a reproducer, small well-scoped families, fixture/docs chores. Verdict after
    rounds 11-12 (6 Haiku workers): reliable; it stops on real design conflicts, reverts unsafe
    attempts, and its fixtures re-audit clean. Do not let it amend cherry-picked commits.
  - `model: "sonnet"` for rule discovery, reverse engineering, hard families, design conflicts.
  - At most 4 workers at once: heavier rounds exhausted the 5-hour window in about an hour.
- **Reverse engineering (human decision 2026-10-10: decompilation and debugger tracing approved,
  internal project).** `tools/trace_errors.py <cr6|cr1000x|cr300|cr350> prog.CR6 [--json]`
  records the caller of every emitted message using a patched exe copy in `scratch/re/` (never
  modify `compiler/`); `docs/binary.md` "Message tracer". Ghidra dumps
  `scratch/re/CR6Comp.decomp.c`, `scratch/re/cr300comp.decomp.c` (untracked); regenerate with
  `tools/ghidra/DumpAll.java` and `/opt/homebrew/Cellar/ghidra/<ver>/libexec/support/analyzeHeadless`
  (`JAVA_HOME=/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home`; Ghidra refuses
  project paths under dot-directories such as `.claude`, so use e.g. `/private/tmp/.../ghproj`).
  `winedbg` disables a breakpoint after its first hit on macOS Wine, hence the patched exe. Every
  rule is still verified by oracle probes and fixtures; decompiled code never goes into `src/`.
- **Worktrees.** `git worktree add -b <br> .claude/worktrees/<br> claude/laughing-franklin-08z9vo`,
  then `ln -s <main>/opencrbasic/.oracle-cache <wt>/opencrbasic/.oracle-cache` (and
  `scratch/re` for RE workers). Inside a worktree always
  `export UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/opencrbasic WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all PYTHONPATH=$PWD/src`
  from `opencrbasic/` (the shared env's editable install imports the MAIN checkout otherwise).
  Never create `.venv` in the repository (`verify.py` fails on it). Remove the cache symlink
  with `unlink` before `git worktree remove --force`.
- **Integration.** `git cherry-pick` worker commits onto `claude/laughing-franklin-08z9vo` (or
  fast-forward when a worker branched from current main); never merge commits; keep cherry-picked
  messages and trailers. Docs conflicts from parallel appends: keep both sides. Code conflicts:
  delegate to a worker with "keep both sides' behaviour, both fixture sets green, probe the
  real compiler where they disagree". Verify each worker branch independently before trusting
  its report (pytest + `fixture_audit` on its new fixtures); in zsh, avoid `set -- $pair` loops
  (no word splitting).
- **Worker rules (`scratch/codex/brief-claude.md`, untracked; recreate from here if missing).**
  Work only in your worktree; export the env above; never `git add -A`, never commit
  `.oracle-cache`; NEVER use `git stash` (shared across worktrees; a worker once popped another's
  changes; use a WIP commit); record a baseline before editing; never claim an observation not
  made; a real-compiler page fault (exit 5) is no verdict; do not edit PROGRESS.md/PLAN.md; final
  report with commits, rules, counts, uncertainty.
- **Reviews.** After each batch, one read-only Codex review with `gpt-6.1-sol` (human instruction
  2026-10-04), using the ChatGPT app's bundled CLI (0.159.2+; `~/.local/bin/codex` 0.158 rejects
  the model): `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex exec -m gpt-6.1-sol -s read-only -C <repo> -o opencrbasic/scratch/codex/review-<n>.md "<prompt>" </dev/null`.
  Codex is for reviews and research summaries only, never implementation. Every review so far
  found real defects; act on them with a worker that confirms each against the oracle first.
  Human statement 2026-10-12: the Codex reviewer is not accessible on the current (macOS, M4)
  machine. There, reviews are done by fresh-context Opus agents (`deep-reasoner`, read-only, no
  builder transcript), output in `scratch/reviews/`.
  Human instruction 2026-10-12: the cloud Claude session `firnmelt-datalogger-reviewer` (Fable
  level) is available for reviews. The coordinator may push commits to `origin` and ask it to
  review the pushed range; it cannot message back, so ask it to push its report to a branch
  `reviews/<name>` (file `opencrbasic/docs/reviews/<name>.md` or similar) and fetch that.
- **Usage pacing.** Check `mcp__ccd_session_mgmt__get_usage`; tell workers to wrap up at ~85% of
  the 5-hour window (they otherwise get cut off mid-task; a cut-off branch is recovered by saving
  uncommitted work as a WIP commit and verifying the last real commit).
- **CRBasic Field Notes artifact** (https://claude.ai/artifact/FngiuvrYeHto3WivhRouX4; source
  `scratch/crbasic-field-notes.html`, Codex summary `scratch/crbasic-lessons-source.md`): update
  ONLY when the human asks; publish with `url` to keep the link.

#### Round 21 (2026-10-19; resume here)

- Integration branch `ocb-r21` from `main` 316aeccc. Weekly usage was 61% at the start: one
  worker at a time.
- TimeOut negative literals: Codex's model (`scratch/codex/re-r20-timeout-cr3xx.md`) confirmed on the
  real compilers, every TimeOut verdict of 56 results as predicted (`scratch/r21/timeout/c.txt` +
  `run.txt`; we match 16/56): `-N` folds into an integer constant only while the compiler-wide
  reported-error count is zero (never reset by `:`/newline), so after any reported error a
  negative TimeOut literal is `Parameter must be a constant integer`; positive/`+N`/hex keep NEG;
  CR6/CR1000X defer expressions with unresolved names (they do not count). Worker W1 implements it
  (branch `ocb-r21-w1`, brief `scratch/r21/W1-negfold.md`).
- Codex RE on `goestable_1` finished 2026-10-19 13:23 (`scratch/codex/re-r21-goestable.md`); root
  cause: a String-literal Sample is stored at 4*(floor(n/4)+1) bytes, we use 1. Coordinator oracle
  check (`scratch/r21/goes/`): 8/10 CR300/CR350 count predictions exact (original, no GOESField,
  no cross-table sources, short literal; the RT_DATA-DataInterval variant was predicted wrong);
  CR6/CR300 `Anonymous<n>` naming (one program-wide counter from 1, FieldNames still consumes a
  number) 3/3. Brief for the next worker: `scratch/r21/W2-literal-fields.md` (start after W1).
- Integrated and gated on `ocb-r21`:
  - W1 (24a2f132) negative TimeOut literals stop folding after a reported error; String-operand
    errors moved to pass 1 (`var_types.check_string_operands`); `order_timeout_negative` only
    moves NEG after undefined names from its own or earlier statements. Coordinator 56-case probe
    56/56 (was 16/56); fixture `r21_negfold.json` 204 cases 0 differing.
  - W2 (cefee4ea) non-variable Sample sources: String width 4*(floor(n/4)+1), concatenations sum,
    numbers as String 24; `Anonymous<n>` from one program-wide counter (all output instructions,
    uncalled tables count, FieldNames still consumes); `FFT(<expression>)` field
    `Anonymous<n>_FFT` (was dropped). Fixture `tdf_anonymous.json` (user-table summaries, audited
    by the worker uncached on the real compilers, 112/112; `fixture_audit.py` cannot read this
    format).
- Gates at cefee4ea: pytest 42,212 passed / 311 skipped, `-m server` 163 passed; difftest
  unchanged; `fuzz_replay` 2,377 exact, 62 false accepts, 0 false rejects (unchanged: the corpus
  has none of these shapes); tdftest CR6 TDF byte-identical 210/210 (was 209/210), CR300 sizes
  114/114, CR350 118/118, CR1000X sizes 272/274 (Status-table gap unchanged); `scripts/verify.py`:
  `BUILD  host tests 136 pass | CRBasic 19/19 compiled, 6 warning(s) | repository pass`.

#### Round 22 (2026-10-19)

- Worked directly in the main checkout, without subagents, per the current human request. No
  commits or hardware operations. Regenerated the CR6 decompilation with Ghidra 12.1.4 into
  `scratch/re/CR6Comp.decomp.c`; targeted field-builder dump `scratch/re/fields.decomp.c`.
- Closed round-21 W2's expression-field leftovers: String-function temporary-buffer widths,
  canonical member names and String member widths, and duplicate checks for resolved member
  sources. Boundary probes also exposed a zero-byte `Replace` temporary; the existing allocation
  stage now reports the real compiler's error instead of reaching a division by zero in table
  rendering. Rules, addresses, confidence and remaining limits:
  [tdf-rules.md, expression buffers and member sources](tdf-rules.md#expression-buffers-and-member-sources-round-22-2026-10-19).
- New reference fixture `tdf_expressions.json`: 100 observations across all four compilers;
  `tests/test_tdf_expressions.py` checks ordered diagnostics and exact bytes for explicitly
  declared DataTables, including an auto-sized table. Generator/auditor
  `tools/tdf_expression_fixture_gen.py --audit`: 100 cases, 0 differing (content-keyed reference
  cache). Implicit structure-table header/units gaps on CR300/CR350 were observed and documented,
  not changed by this round.
- Validation of the final implementation:
  - `uv run --no-sync pytest -q -n 4 -m 'not server'`: 42,149 passed, 311 skipped;
    `uv run --no-sync pytest -q -m server`: 163 passed.
  - Four-profile `tools/difftest.py --group all --gate no-regressions`: same results as the
    pre-change baseline, zero lost exact cases, zero verdict mismatches or implementation
    exceptions. Repository CR6 programs: 18/18 exact on CR6/CR1000X; CR300/CR350 7 exact and 11
    matching-verdict rejections. CHM: 506/506/505/505 exact, with 1/1/2/2 reference faults;
    those faults make the gate commands exit 1 and are not compiler verdicts.
  - `tools/tdftest.py --logger cr6 --group all`: TDF and table list 210/210 byte-identical;
    308 reference rejections, six missing expected artifacts and one reference fault remain.
  - `tools/fuzz_replay.py --workers 4 --gate no-regressions --json ...`: all 3,158 saved inputs
    refreshed; three runner timeouts retried serially, two then exact and one still without a
    usable reference verdict. Combined result: 2,376 exact, 62 false accepts, 685 message diffs,
    16 mode diffs, 19 reference/runner failures; no false rejects, implementation exceptions,
    crashes or hangs. For every usable reference result, compiler output is identical to the
    pre-change snapshot, so exact agreement has not regressed. Gate exit 1 is due to missing
    reference verdicts. Raw run, retries and combined report are kept separately in
    `scratch/r22_replay_{fresh,retries,final_refreshed}.json`; comparison in
    `scratch/r22_replay_comparison.json`. This round's new shapes are covered by the fixture.
  - `scripts/verify.py`, with both the reference compiler and `CR6COMP=opencrbasic`:
    `BUILD  host tests 136 pass | CRBasic 19/19 compiled, 6 warning(s) | repository pass`.
    The native run's first restricted invocation failed two LibreOffice workbook tests; rerunning
    with the same external-app access as the reference build passed. No source change was needed.

#### Next steps (round 23)

1. Suspected same folding rule in other constant-integer checks (W1): TCPOpen/SerialOpen constant
   arguments, Timer's third argument, unary minus in Scan/DataInterval, Const values, `1-1`/`-KT`.
   Probe on the real compilers first. The old note `scratch/codex/re-r20-timeout-cr3xx.md` is
   absent in this checkout; use the regenerated decompilation rather than assuming it exists.
2. TDF: CR300/CR350 implicit structure-table header/units gaps and per-profile Status-table
   definitions; zero-argument `Date`/`PPPOpen` source forms. Round-21 W2's named leftovers are
   fixed above. FFT of a literal still supplies no reference verdict (compiler fault).
3. W1 leftovers: `"a" * "a"` prints two errors; `+&H…`; error attribution across later `:`
   segments on one line.
4. Fuzz seeds 413-416, alone on the machine (the old `scratch/r20/W1/run.sh` needs recreating).
5. Round-20 next-steps items 3-5 (Const and units leftovers).


#### Round 20 (2026-10-18/19)

- Integration branch `ocb-r20`, cut from `main` at ef873362 ("Require Python 3.15 ...", not by
  this workstream). Clean baseline after the reboot (2026-10-18): pytest 41,625 passed / 311
  skipped, `-m server` 163 passed; difftest and `fuzz_replay` identical to round 19.
- Semaphore exhaustion (2026-10-18): the macOS POSIX semaphore table ran out
  (`multiprocessing.Lock()` -> `OSError(28)`) after killed pytest/xdist trees under load averages of
  90-170 (four workers + coordinator checks). Every multiprocessing pool then fails or hangs (the
  LSP server pool: `tests/lsp/test_lsp_bench.py::test_smoke_report` times out with ENOSPC in the
  server's stderr). Fixed by a reboot. Check before a round:
  `~/.uv-envs/opencrbasic/bin/python -c "import multiprocessing as m; m.Lock()"`.
  Claude Code moves long commands to the background automatically, so "stay in the foreground"
  cannot be followed; the worker brief now says: never kill a backgrounded run, no second heavy run
  while one is going. Run at most two workers plus the coordinator's gates.
- Integrated, each gated by the coordinator (fixture audits, full pytest, difftest x4, replay,
  probes of valid neighbours):
  - W3 Const leftovers (b48b6872 + fix e36154ef on main): unmatched paren / out-of-place / adjacent-operand
    values, `Long[5]`, `Const "abc`, `As String * n = ...` false accept, use before declaration
    (`r20_const.json`). Its first commit lost two real-compiler fixture cases
    (`r14_routine_local_scope` dim_in_const, `r7_cr3xx_top-13`), caught by the full pytest the
    worker had not run; the fix restores both. Coordinator probe of 10 valid shapes: 38/40 match,
    the 2 misses are a pre-existing CR300/CR350 false accept (a Sub using a Const declared after
    it is `K is not defined.`).
  - W5 DataInterval/Scan units by value (65e960f3 on main): 0..5 truncated, 6 = MON (TDF interval 0,
    tinto dropped, auto-size like MON), anything else invalid (1 SEC), DataInterval divisibility
    check with truncated units (`r20_interval_units.json`). Coordinator probe of 10 valid numeric
    DataInterval shapes: 40/40 match.
- Gates at 80af5685 (65e960f3 after the rebase onto main): pytest 41,896 passed / 311 skipped; all r20 fixtures and the older Const
  fixtures audit 0 differing; difftest unchanged; `fuzz_replay` 2,377 exact (+2), 62 false accepts,
  0 false rejects, 687 message diffs (`scratch/r20/replay-w5.json`); tdftest unchanged
  (`goestable_1` only; the reference-fault lines are not mismatches).
- Not done in round 20: W1 fuzz seeds 413-416 (machine saturated; `scratch/r20/W1/run.sh`), W2/W6
  TimeOut CR300/CR350 (no commit; observations `scratch/r20/W6/cond.out`, `ungated.out`; candidate
  rule: after an earlier error on the same line a '-'-signed decimal TimeOut prints `Parameter must
  be a constant integer: <stmt>.` on CR300/CR350, positive/hex keep NEG; a second '-' call after a
  NEG call gives NEG then constant-integer on all four loggers). Codex RE research on it finished
  2026-10-19 09:09 (`scratch/codex/re-r20-timeout-cr3xx.md`; an earlier note here said it was still
  running, which was wrong); round 21 oracle-checked it.

#### Next steps (round 21)

1. Read `scratch/codex/re-r20-timeout-cr3xx.md`, oracle-check its predictions, then one worker for
   the CR300/CR350 TimeOut messages (start from `scratch/r20/W6/`).
2. Fuzz seeds 413-416, alone on the machine (`scratch/r20/W1/run.sh`).
3. W3 leftovers (docs/checker.md "Round 20"): `Variable used in CONST expression.` (per-logger
   cascade), `X = J(1)` and `Const K = Min(2,3)` (`Incomplete expression`), CR300/CR350
   `Variable illegal in Parameter: J.`, a Sub using a later Const on CR300/CR350.
4. W5 leftovers (docs/tdf-rules.md "Numeric and Const units (round 20)"): zero-interval list unit
   name when Scan and DataInterval units differ, fractional Interval values, CR300 Scan list
   `(null)`/`MON` for out-of-range units.
5. Round-19 "Next steps (round 20)" items 3-5.

#### Round 19 (2026-10-17)

- Integrated on `ocb-r18` (pushed to the mirror after this commit), each verified by the
  coordinator (fixture audit, full pytest, oracle probe of the worker's weakest claim):
  - Codex review of rounds 16-18 (`scratch/codex/review-r18.md`): W6 R18X-2 the IfTime/Timer timeout
    mode advances in source order inside a one-line If (`intervals.gate_text`;
    `r19_inline_if_timeout.json`); W5 R18X-1 Scan units folded by value (`tables._scan_unit_name`;
    also fixed CR350 boundaries and CR300/CR350 units 0/6 false rejects) and R18X-3 Debug float
    literal past float32 (`r19_review_r18x.json`).
  - W8 TimeOut literal values: signed32(10*v) < 0 -> `Parameter TimeOut cannot be negative`, per-logger
    literal classification (`r19_timeout_values.json`, docs/instructions.md). Rule from Codex RE
    (`scratch/codex/re-r19-timeout.md`), coordinator-confirmed 80/80 before any code
    (`scratch/r19/timeout/`). W8's first version lost an exact mixed-error case; its follow-up
    fixed it. Coordinator probe `scratch/r19/w8c.txt` + `w8c.out`: 10/16 match vs 4/16 before W8.
  - W7 Const false rejects: empty/spaced subscripts, `As <word> unknown Const type`, dangling-operator
    subscripts, unterminated strings, CR300/CR350 `CONST needs assignment.` taint
    (`r19_const_leftovers.json`); coordinator probe of 14 valid typed-Const shapes: 56/56 match.
- Gates at 761df945: pytest 41,625 passed / 311 skipped, `-m server` 163 passed; all r19 fixtures
  plus r18_const_array and r17_preserve_pool audit 0 differing; difftest unchanged (cr6/cr1000x
  repo 18/18, chm 506; cr300/cr350 repo 7 exact + 11 verdict, chm 505; mismatch 0,
  implementation_exception 0); `fuzz_replay` identical to round 18 (2,375 exact, 62 false
  accepts, 0 false rejects, 689 message diffs, no class change; `scratch/r19/replay-r19-final.json`;
  none of the round-19 shapes occur in the corpus); tdftest unchanged; `scripts/verify.py`:
  `BUILD  host tests 136 pass | CRBasic 19/19 compiled, 6 warning(s) | repository pass`.
- Codex RE research for a hard family, then a coordinator oracle check of its predictions, then a
  Haiku worker, worked well (TimeOut). Oracle runs time out under 4 workers; re-run (cached).
  `tools/probe.py` imports its own checkout's `src` regardless of PYTHONPATH: compare old code
  in a detached worktree (it also needs `scratch/` to exist).

#### Next steps (round 20)

1. TimeOut leftovers on CR300/CR350 (`scratch/r19/w8c.out`): an undefined name inside the same call
   gives `Parameter must be a constant integer:` (not NEG); an undefined name in a LATER statement on
   the same line prints NEG first (our order pass puts it second). Not modelled: expressions in the
   TimeOut slot (-KT, KT+1), sign on hex, &O/&B, an undefined name in the TimeOut slot itself.
2. Const leftovers (W7, docs/checker.md "Round 19"): `Const J (+) = 7`, `J As Long (5 = 7`, `Long[5]`
   (lexer error), `Const "abc`, `J(a+)`, `J(3 4)`; false accepts `Const J As String * 10 = "a"`,
   `Const K = Y`/`J(Y)` (`Variable used in CONST expression.`), `J("a")`, `J(J)`, `X = J(1)`.
3. W5 leftovers: DataInterval numeric units (`tables._interval_from_args` reads by name); CR6/CR1000X
   auto-size weights with numeric units unprobed; Debug finite values in [2^31, FLT_MAX].
4. W6 leftover: `intervals.check` never visits one-line If bodies for the integral/constant checks.
5. Round-18 items 4-6 (SlowSequence, OOM leftovers, older lists, fuzz seeds 413+). A fresh fuzz
   seed batch would give the replay gate new evidence; the corpus does not cover rounds 18-19.

#### Round 18 (2026-10-16)

- `claude/laughing-franklin-08z9vo` was merged into `main` (mirror PR #1, 73e423ca) and pushed to
  the primary remote. Round 18 works on `ocb-r18`, cut from `origin/main` (local, NOT pushed);
  use it as the integration branch and worktree base from now on (replaces the old branch name in
  "Work method").
- Integrated, each verified by the coordinator (fixture audit, full pytest, and a probe of the
  worker's weakest claim): round-17 `ocb-r17-preserve` (PreserveVariables pool, no conflict);
  W1 `Const J(n) = v` declares scalar J = first subscript, rest ignored (false reject on all four
  loggers, not just CR300; fixture `r18_const_array.json`); W3 `SlowSequence(")`/`(A,")` messages and
  pre-BeginProg `Multiple BeginProgs.`, CR300/CR350 gate by "argument resolves" (coordinator probe
  overturned the worker's "literal only" guess; `r18_slowseq_timeout.json`); W4 a clean SlowSequence
  before any BeginProg opens the program, so `SlowSequence`/`EndProg` compiles (false reject), incl.
  CR300/CR350 expression arguments over declared names (coordinator probe found the first version
  still false-rejected them; `r18_slowseq_endprog.json`); W2 CR300/CR350 inline `If c Then CallTable`
  counts as a call in the storage allocator (false accept; `r18_inline_calltable.json`) and
  CR6/CR1000X `Out of memory.` for auto/negative sizes via the Lapses*16 wrap plus the zero-request
  `Out of Memory; Unable to allocate table <Name>.` (`r18_oom_auto.json`; `docs/tdf-rules.md`).
- Gates at b130ab83 (macOS M4): pytest 40,603 passed / 311 skipped, `-m server` 163 passed; all six
  new/changed fixtures audit 0 differing; difftest repo/chm exact cr6 18/506, cr1000x 18/506,
  cr300 7/505 (11 verdict), cr350 7/505 (11 verdict), mismatch 0, implementation_exception 0 (repo
  count is 18 since the merge into main); `fuzz_replay` 3,158: 2,375 exact (2,368), 62 false
  accepts, 0 false rejects, 689 message diffs, 16 mode diffs, 16 oracle errors, no file got worse
  (`scratch/r18/replay-r18-final.json`); tdftest unchanged (`goestable_1` only on CR6/CR300/CR350,
  the pre-existing CR1000X Status gap); `scripts/verify.py`: `BUILD  host tests 136 pass | CRBasic
  19/19 compiled, 6 warning(s) | repository pass`.
- `fuzz_replay --gate no-regressions` always exits 1 while the corpus has oracle errors
  (`gate_failed`); compare class counts instead.
- Worktree `scratch` symlinks are not covered by `.gitignore` (`scratch/` matches directories
  only); briefs must forbid `git add -A`/`git add .`. Briefs: `scratch/r18/W*.md`.

#### Next steps (round 19, done or carried into round 20)

1. Fix the Codex review of rounds 16-18 (human instruction 2026-10-17: gpt-6.1-sol via the
   ChatGPT app CLI, not the cloud session; report `scratch/codex/review-r18.md`, range
   `438ad6ee..ocb-r18`). All three confirmed by coordinator oracle probes on 2026-10-17
   (`scratch/r18/review/t.CR6` + `c.txt`, all four loggers):
   - R18X-1 FALSE REJECT, CR300 only: `PreserveVariables` + `Scan(2,3,0,0)` (numeric units, 3 = Min)
     and `DataTable(Test,1,579584)` with `Sample(1,V,FP2)` of `Public V(60)` compiles on all four;
     ours fails storage on CR300 (`Scan(2,Min,0,0)` matches). `tables._preserve_deduction` reads
     units by name and defaults to seconds; fold the units argument.
   - R18X-2 FALSE REJECT, all four: after `X = IfTime(0,1,Min)`, `If 1 Then X = FTPClient("s","u",
     "p","a","b",2,0,0,Sec,0,"x") Else X = Timer(1,Sec,0)` gives only `line 5: Warning: Parameter will
     truncate to the nearest integer.`; ours adds `Parameter must be a constant integer`. The inline
     If text and its bodies both pass through `timeout_gate` (`checker/expressions.py`
     `check_function_arity` on `_with_inline(program)`), so the Else `Timer` resets the mode first.
   - R18X-3 internal error, CR6/CR1000X: `Public C As Long, L As Long, T As String * 100` /
     `Debug(-1,1e39,C,L,T)` before BeginProg is `line 2: Out of memory.` on the real compilers
     (Codex predicted a clean compile: wrong); ours raises `OverflowError` in the float32 pack in
     `checker/instr_rules.py` (`Internal error: unable to compile program.`). CR300/CR350 match.
   `ocb-r18` is pushed to the mirror (2026-10-17).
2. TimeOut (HTTPPut 9th / FTPClient 11th) values: W3 probes `scratch/r18/W3/run_timeout1.txt`
   show `-1`, `TRUE`, `&HFFFFFFFF`, 2147483647 -> `Parameter TimeOut cannot be negative` on all four
   loggers but -2147483648 compiles; looks like a per-logger value*1000 overflow. Pre-existing
   false accepts. Candidate for Codex research over `scratch/re/*.decomp.c` before a worker.
3. Const leftovers (W1): false rejects `Const J()`, `J(,3)`, `J (3)`, `J(3 = 5`, `Const J As
   Long(3)` echo; false accepts `Const K = Y`/`J(Y)` (`Variable used in CONST expression.`),
   `Const K = "a"`, `X = J(1)` on a Const (`Incomplete expression`).
4. SlowSequence leftovers: declared-array argument `SlowSequence(B(1))`; Scan/NextScan between a
   pre-BeginProg SlowSequence and a real BeginProg (`Multiple` + `BEGINPROG never reached`
   warning); CR300/CR350 NextScan there; `SubScan (1,Sec,0)` in such a block.
5. OOM leftovers (W2): auto tables with CardOut/TableFile, records forced to 2, counts 0/1, N
   outside int32; `_scan_weights` ignores inline calls (no observed effect).
6. Round-17 list item 4 leftovers and the round-16 lists below (false accepts, message diffs,
   fuzz seeds 413+).

#### Round 17 (2026-10-16)

- Integrated on `claude/laughing-franklin-08z9vo` (local, NOT pushed), each gated: `ocb-r16-pending-fuzz`;
  Lapses `cvttss2si` + `*16` wrap in the CR300/CR350 allocator (false reject 9354d3641c2a); the
  non-WIP commits of `ocb-r16-falseacc2` and `ocb-r16-msgdiff2`; W1 `:` splits a statement even
  inside an open parenthesis (false reject `Call S(A,Z,3 : Z = 1`); W2 unterminated string in an
  optional-first-parameter instruction (false reject `Calibrate(")`; SlowSequence excluded by the
  coordinator, eec3f491); W4 CR6/CR1000X `Out of memory.` (128 MiB per allocation: fixed tables,
  Public table, Dim/Public segments; `docs/tdf-rules.md`); W3 HTTPPut/FTPClient TimeOut rule with
  its IfTime parser-mode gate (round-16 revert undone), both round-16 WIP commits completed.
- Gates at f1ab5cae (macOS M4): pytest 39,778 passed / 311 skipped, `-m server` 163 passed;
  difftest repo/chm exact cr6 16/506, cr1000x 16/506, cr300 6/505, cr350 6/505 (unchanged);
  every new fixture audits 0 differing uncached; `fuzz_replay` 3,158: 2,368 exact (2,315 at the
  start), 62 false accepts (78), 0 false rejects, 696 message diffs, 16 mode diffs, 16 oracle errors
  (`scratch/replay-r17h.json`); `scripts/verify.py`: `BUILD  host tests 119 pass | CRBasic 16/16
  compiled, 6 warning(s) | repository pass` (after deleting a stray `opencrbasic/.venv`).
  tdftest CR6/CR300/CR350 unchanged; CR1000X tdftest has 274 Status-table field-name mismatches,
  identical before and after W4 (pre-existing CR1000X TDF gap, never claimed).
- Codex works on this machine again (ChatGPT app CLI 0.162 alpha). Human instruction 2026-10-16:
  run read-only gpt-6.1-sol research over `scratch/re/*.decomp.c` for hard families. Report
  `scratch/codex/re-r17.md` (CR6 OOM, CR300 Preserve pool, IfTime parser mode); its predictions
  were oracle-checked before workers used them (CR6 OOM boundaries exact; PreserveVariables off).
  Pass `-o` an absolute path.
- Worker incidents: W4 ran `pkill -f fuzz_replay` (forbidden); W3 reports `fuzz_replay.py --help`
  starts a run and touched a corpus file (restored). Repeat both warnings in briefs.

#### Next steps (round 18, done or carried into round 19)

1. Gate and integrate `ocb-r17-preserve` (f20301b1, W5: CR300/CR350 PreserveVariables /
   PreserveOneVariable pool deductions, fixture `r17_preserve_pool.json` audited 0 differing,
   pytest/difftest green in the worktree): NOT yet run there: `fuzz_replay`, tdftest cr300/cr350.
   Expect a small conflict in `checker/storage.py` with W4. Worktree still at
   `.claude/worktrees/ocb-r17-preserve`; `ocb-r17-ftp-wips` worktree can be removed (integrated).
2. FALSE REJECT found by W5, not fixed: `Const J(300) = 1` (array constant) on CR300 gives
   `Const is not defined`; the real compiler accepts. Probe all four loggers first.
3. Push this branch, then ask the cloud reviewer for a review of rounds 16-17.
4. Open from round 17: `SlowSequence(")` / `SlowSequence(A,")` messages; W1 leftovers
   (`docs/checker.md` "Colon inside parentheses"); TimeOut negative/overflow messages; Timer/
   SerialOpen mode reset unprobed on CR1000X/CR300; inline `If ... CallTable` on CR300/CR350
   still a false accept; CR6 OOM for auto/negative table sizes.
5. Then round-16 lists below (false accepts, message diffs, seeds 413+).

#### Resume here (2026-10-13, round 16 interrupted by machine shutdown)

- Verified and pushed on `claude/laughing-franklin-08z9vo` (full pytest 38,202+ passed, difftest
  unchanged, replay 2,300 exact / 48 false accepts / 0 false rejects): cloud review R15C-1..3 (no
  defects, fixtures), Debug args + GZip arity, table names / GOES fields / ApplyAndRestartSequence
  messages, CR300/CR350 26-pass allocator port (auto sizes, squeeze rejects; tdftest CR300 113/114,
  CR350 117/118 user-table sizes), `tools/timingtest.py` + CR1000X/CR300/CR350 timing models
  (repo 16/16, CHM 505/507 each), `fixture_audit --no-cache --jobs` + atomic oracle cache writes
  (uncached re-audit of all r14-r16 fixtures: 12,381 cases, 0 differing), rNN glob tests skip only
  non-empty `siblings`. CDM excluded (human decision, PLAN.md).
- Pushed but NOT fully verified: branch `ocb-r16-pending-fuzz` = this branch's previous head +
  4 commits from fuzz seeds 409-412 (Delay interval >= 2^31, String * N(dims) initializer, Units
  extra words, 235 reproducers). Its 3 fixtures pass the uncached re-audit (0 differing); the full
  pytest on the combined tree did not finish before shutdown.
- Stopped mid-task, pushed as-is (verify every commit before integrating):
  - `ocb-r16-falseacc2` (W7, remaining non-CDM false accepts): 546e8e77 HTTPPut/FTPClient TimeOut
    type, 84c70b74 unclosed Sub-call undefined names, 49c6f23c over-indexed array reads, 8afe88f8
    Function-in-Sub-call and SerialOpen arity, 202ca4bb WIP (unverified: no docs, fixture
    r16_function_statement_arity.json unaudited). Worker replay: false accepts 48 -> 39.
  - `ocb-r16-msgdiff2` (W8, message diffs): db2c32f0 ESSInitialize cut-off string, bae8bab0 Alias of
    a later-declared variable, 60eea626 unmatched EndDialSequence/EndModemHangup/EndVoice, 808fc8e8
    table-only instructions before a stop, 4ab1b610 WIP (unverified parser change, fixture not
    regenerated).

#### Next steps (round 17)

1. `git checkout ocb-r16-pending-fuzz`; run the full gates (full pytest incl. `-m server`,
   `difftest --group all` x4, `fuzz_replay`); if green, fast-forward this branch to it.
2. Fix the known FALSE REJECTS first (probe all four loggers):
   - `tests/corpus/fuzz/cr300/false_reject/9354d3641c2a.CR6`: 9999-record table, 15 s DataInterval;
     we print "Unable to allocate enough data table space to manage flash write cycle limits.", the
     real CR300 compiles. Still reproduces with the allocator port (`checker/storage.py`).
   - `Call S(A,Z,3 : Z = 1` compiles on all four loggers; we reject (W7).
   - `Calibrate(")` inside BeginProg compiles on the real compilers; we reject (W8).
3. Integrate `ocb-r16-falseacc2` and `ocb-r16-msgdiff2` commit by commit (cherry-pick, gate each;
   finish or drop the two WIP commits).
4. Push and ask the cloud reviewer `firnmelt-datalogger-reviewer` to review round 16
   (`ca091574..HEAD` minus round 15; it may need the human to approve the request).
5. Remaining false accepts (non-CDM): docs/fuzz-findings.md "Round 15"/"Round 16" lists; seeds
   405-412 value families; DebugBreak before BeginProg on CR6; nested DialSequence/ModemHangup.
6. Message diffs: docs/fuzz-findings.md "Round 16 message-diff ranking" open items.
7. Fresh fuzz seeds 413+.
8. CDM is excluded (human decision 2026-10-12, PLAN.md). Publishing not planned (`docs/legal.md`).

#### Resume here (2026-10-12, after round 15)

- Round 15 (2026-10-12, Mac, after reboot) integrated and pushed: ~45 commits on top of the cloud
  hand-off `ca091574`. Done: held-back inline-If arity commit (13e07c21), one-line ElseIf body,
  unclosed Abs-like groups (nested, n+1 side-by-side, LineNum/Signature), block keywords and
  SubScan/NextScan in one-line If bodies, While/Wend blocks, stray statements in one-line If
  bodies, CR300/CR350 storage limit from the decompiled allocator (`checker/storage.py`), DataTable
  size argument (no `-z` traceback), Scan intervals wrapping to zero, CDM_Delay double message,
  fuzz seeds 405-408 (MoveBytes option, AddPrecise string arg, Call name spaces, unreachable
  warnings), nine message-diff families (adjacent operands, EOF continuation, Matrix, ...),
  RoutersNeighbors/DNPVariable/SNMPVariable/FieldCal/CDM port and channel value rules, CLI skips
  LSP provenance (-25% peak memory). Fixtures `tests/fixtures/r15_*.json` (tests/test_r15_fixtures.py,
  tests/test_r15_table_sizes.py). Decompiled dumps in `scratch/re/` (untracked).
- No pending branches. Not reviewed yet: round 15 (cloud review requested; report expected on
  `origin/reviews/r15-cloud`).

#### Round 14 notes (2026-10-12)

- Cloud session (Linux container, Wine 9.0, 2026-10-12, after round 14): `3835326..235db55`, each
  rule oracle-probed, recorded in a fixture and gated (full pytest, `difftest --group all` on all
  four loggers vs a fresh baseline, `fuzz_replay` vs a baseline taken in the same container).
  Measured there at `235db55`: pytest `-m "not server"` 30,205 passed / 311 skipped, `-m server`
  161 passed (on 14072c8, so the LSP server tests do pass off the Mac); help corpus unchanged
  (506/506/505/505 exact, only reference faults); `fuzz_replay` 2,015 exact (1,971 at the start
  of the session in the same container; the Mac counted 1,972), 34 false accepts, 0 false rejects.
  Done: R14C-1 (structure-typed routine parameters, regression), R14C-2 (probed; DataTable name
  slot `T ;`/`T;` modelled), R14C-3..6, R13C-3..8 (four fixed, rest not defects), CR300/CR350 `;`
  in instruction arguments, trailing `;` after a function statement (false reject), string
  literal ports (INT_MIN), numeric DataType codes, bounds in conditions/code 24/SplitStr element
  reps/Calibrate size, SlowSequence with an open Scan, SerialOpen TX delay, ArgosError,
  MenuPick after a plain Const, MoveBytes option, CDM settling time, InstructionTimes,
  MQTTPublishConstTable placement, GOESData/ArgosData arity, CR6/CR1000X TCPOpen handle type.
  Details: `docs/checker.md` sections from "Round 14 cloud review follow-up" on; fixtures
  `tests/fixtures/r14_review_r13c.json`, `r14_string_ports.json`, `r14_output_datatype.json`,
  `r14_bounds_conditions.json`, `r14_slowsequence_open_scan.json`,
  `r14_serialopen_txdelay_argos.json`, `r14_menupick_plain_const.json`,
  `r14_movebytes_cdm_insttimes_mqtt.json`, `r14_goes_argos_tcpopen.json` (and additions to
  `r14_semicolon.json`, `r14_trailing_dot_members.json`, `r14_abs_unclosed.json`).
- Done after the hand-off: unknown `Status.X` / `Public.X` member reads (`docs/checker.md`
  "Unknown `Status.X` / `Public.X` field reads", per-logger names `data/**/status_members.json`
  from an oracle sweep; fixture `r14_status_public_reads.json`); `fuzz_replay` 2,016 exact, 33
  false accepts. Probed, not implemented: `RoutersNeighbors(D("fuzz"))` is `Destination variable
  cannot be indexed` on all four loggers.
- Round 14 (2026-10-12) is integrated and pushed (`eaebfddd..6ca00592`). Reviews: local Opus
  `scratch/reviews/review-r14.md` (all HIGH findings fixed) and cloud reviewer
  `origin/reviews/r13-cloud` = `scratch/reviews/review-r13-cloud.md` (R13C-1..8 open, see Next
  steps). Workers fixed: routine-local scope (ex-WIP branch), strcomp_3 pointer `!name`, R14-1/2/5/7/10,
  logical-assign left operands and `Public.X` targets, unclosed `Abs(`-like calls, fuzz message-diff
  top clusters (368 -> 296 on the old set), fuzz seeds 401-404 families, trailing-dot members,
  CR300/CR350 For-header echo, `;` family (R14-4/R14-6) and R13C-1 (one-line If with a
  logical-prefixed assignment body); R13C-2 probed: not a defect. Fixtures `tests/fixtures/r14_*.json` (tests/test_r14_fixtures.py).
- Pending, NOT integrated:
  - branch `ocb-r14-followups` commit `2330aa24` (parameter counts in one-line If bodies): its
    `cr3xx.check_too_many_cascade` change conflicts with the W8 inline-If cascade commit; a naive
    combination fails `r14_inline_if_cascade` case `inlinecond_two_inline_ifs` (CR300/CR350).
    Needs a worker. Pointer `!x` probe evidence for more statement forms: `scratch/r14-helpers/followups/`.
- MACHINE STATE: the macOS POSIX semaphore table is exhausted (leaked by killed process trees;
  `multiprocessing.Lock()` -> ENOSPC). Reboot before the next round; until then LSP server tests
  (`-m server`, test_lsp_bench) cannot run. Workers must never use pkill/killall.

- Round 13 (2026-10-12, first session on the macOS M4 machine) is integrated: 40 commits
  `e39271da..18f1b774`. Three fresh-context Opus reviews replaced the lost round-11 Codex review
  and covered rounds 10-12 and the LSP commits (`scratch/reviews/review-r13-{A,B,C}.md`; LSP work
  changed no `crbc` output over 8,590 runs). Ten Sonnet workers fixed: scope-blind array bounds
  (false rejects, all loggers), include paths with directories (false reject), nested and self
  includes, two-word `End Sub`, `X.5`-style digit members (regression), logical-assign targets,
  trailing empty arguments, TCPOpen handle types/connection count, `;` after statements (false
  reject), ETsz/ETClearSky in tables (false reject), `#If` without `#Endif` with warnings, builtin
  arity tables (compiler item 5), three of four help diffs, include `<file>` prefix by message
  kind, CR300/CR350 cascade after an include error, the float32 `struct.pack` platform difference
  in `check_sdm_address`, and smaller message families. Fixtures: `tests/fixtures/r13_*.json`,
  tested by `tests/test_r13_fixtures.py` (multi-file ones with `siblings` by
  `tests/test_include_validation.py`); all audit 0 differing.
- Branch `ocb-r13-subscope` was finished and integrated in round 14 (fixture `r14_routine_local_scope.json`).
- Untracked state lost with the old machine: `scratch/re/` dumps, `scratch/tdf-sweep/`,
  `scratch/timing_harness.py`, `scratch/mkfixture.py`, `scratch/codex/`, `ocb-ranges` branch.
  Recreated: worker brief `scratch/codex/brief-claude.md`; fixture helpers in
  `scratch/r13-helpers/` (`mkfix.py` for multi-file fixtures).
- Machine notes: background `uv run` jobs started with `&` or nohup die silently in this harness;
  use the Bash tool's `run_in_background` or the foreground. Ghidra: `brew install ghidra`
  (formula, openjdk@21, no sudo) was still installing at the end of the session.

#### Compiler items found by the LSP work (recorded 2026-10-11, not executed)

Round 13 status (2026-10-12): done 1 (two-word closers), 5 (arity tables), 11 (self/nested
include), 12 (include paths with directories); closed as not a defect 7 (`Static`), 9 (labels),
13 (duplicate declaration in an include matches on all loggers). Open: 2, 4, 6, 8, 10, 14.

Human instruction 2026-10-11: compiler work found while building the language server is recorded
here and left for the compiler workstream; the LSP session does not execute it. Evidence links
are in `docs/lsp-rounds/`.

1. **Two-word `End Sub`/`End Function`** (parsed as `Instruction("END", [Name("Sub")])`) do not end
   a routine in `checker/symbols.scoped_view`, `cli._hides_times` and `timing.py` (they test only
   `ENDSUB`/`ENDFUNCTION`). No fixture uses the two-word closer alone; needs oracle probes
   (scoping after `End Sub`, `-t`). Source: gpt-6.1-sol review finding 1
   (`docs/lsp-rounds/review-2026-10-11-39286384.md`), `LSP-R1-handoff.md`.
2. **`crbc -z` traceback** (`struct.error` in `tdf.serialize`) on the CR300/CR350 storage-limit
   false accepts `tests/corpus/fuzz/cr300/false_accept/80cdb831bd9a.CR6` and
   `cr350/false_accept/4b318b036fd1.CR6` (table size beyond 32 bits). Part of the storage-limit
   family above; at minimum no traceback. Source: `docs/lsp-rounds/LSP-00.md`.
3. ~~Indentation changes a message~~: not reproduced on the host (T-06, `docs/lsp-rounds/host-tests/2026-10-11-e77ee5cc/RESULTS.md`): real CR6Comp and
   `crbc` both print the same pair at line 78 with and without indentation. Closed unless the LSP-07b
   formatter observation is reproduced.
4. **Non-ASCII include names** (T-05, `docs/lsp-rounds/host-tests/2026-10-11-e77ee5cc/RESULTS.md`): resolution matches the real compiler (UTF-8 source
   bytes: both warn "cannot open include"; Latin-1 source: both resolve). Remaining difference:
   the real compiler writes the message's raw source bytes (shows `É`), `crbc` prints the Latin-1
   decoded text re-encoded as UTF-8 (shows `Ã\x89`). CLI output encoding item.
5. **Builtin argument counts** (T-08 done on the host, audited fixture with 0 differing:
   `docs/lsp-rounds/host-tests/2026-10-11-e77ee5cc/function-arity-fixture.json`): accepted counts
   EmailSend 9/13/14, EmailRecv 9-13, UDPDatagram 6-8, XMLParse 8, HTTPGet 3-4, UDPOpen 3-4,
   TCPOpen 3/4/6, HTTPPost 4/8/9, CheckSum 3/4/6. Integrate the fixture into `tests/fixtures/`
   and the checker's `_ARITY_ALLOWED`; signature help follows the checker tables automatically.
6. **`Status.CardBytesFree`** (used by `crbasic/Main.CR6`, which the real compiler accepts) is in
   neither `data/status_table.json` nor `settings_fields.json`; check whether the Status field
   list is incomplete (`LSP-05c-handoff.md`).
7. **`Static` declarations** parse as `Unparsed` (`LSP-05a-handoff.md`); check against the oracle.
10. **Malformed one-argument `IncludeSection "X"`**: CR6Comp crashes (exit 5), `crbc` exits 0
   (T-05 side observation, `docs/lsp-rounds/host-tests/2026-10-11-e77ee5cc/RESULTS.md`); a reference fault, no verdict to match.
11. **Self-including program**: `IncludeSection("A","CPU:<itself>.CR6")` ends in
   `parser.parse_file: RecursionError` (reported as `Internal error: unable to parse program.`,
   ~0.8 s) instead of a diagnostic. Real-compiler message not yet known (`LSP-06a-handoff.md`).
12. **Include paths with a directory** (`CPU:sub/Lib.crb`) resolve only against the including file's
   directory and give "Cannot open include file"; real-compiler behaviour not yet probed
   (`LSP-06a-handoff.md`).
13. **CR300/CR350 duplicate declaration in an include**: a library declaring a variable twice makes
   `analyze` add "Too many parameters" and a duplicate "x is not defined." to the entry
   (`LSP-06b-handoff.md`); real-compiler output not checked.
14. **Typing in compiler modules** (from the LSP Pyrefly work, `LSP-08e-handoff.md`): `tables.py`
   sets `TdfTable._dup_fields`/`_interval_display` with `# type: ignore` (declare them on `TdfTable`);
   `source.LogicalLine.phys` is typed Optional although `load_text` always sets it.
9. **Statement labels**: in one LSP probe `Label1:` parsed as an unresolved call, not a `Label`
   (`LSP-07a-handoff.md`; not investigated, may depend on the statement shape).
8. **`tools/tdftest.py`** conflates an absent and an empty LST (six CHM cases report no LST);
   host evidence `docs/lsp-rounds/host-tests/2026-10-11-602f6462/RESULTS.md`.

#### Next steps (round 16, superseded by "Next steps (round 17)" above)

1. Act on the cloud review of round 15 (`origin/reviews/r15-cloud`; confirm each finding against
   the oracle first).
2. Remaining false accepts (52 in the replay): Debug arguments (rules probed, see
   docs/instructions.md / round-15 notes), GZip/HTTPPut/FTPClient constant-integer and arity,
   MinRun with a user Function, FFT scan interval, auto-table storage sharing (CR300/CR350),
   DataEvent/AcceptDataRecords storage, CR300/CR350 auto-table size constants for `-z`, and the
   46 from seeds 405-408 (docs/fuzz-findings.md "Round 15").
3. Message diffs (569): docs/fuzz-findings.md "Round 15 message-diff ranking" open items.
4. Fresh fuzz seeds 409+.
5. Outputs: `goestable_1` field name, timing for variable arguments;
   re-create the `-t` timing harness (lost with the old machine).
6. Publishing is not planned: internal use only (human decision 2026-10-11, `docs/legal.md`).

## Earlier archive (through 2026-10-03)

- 2026-10-03 (morning/midday): merged Codex `cx-names` (routine-name errors, DataTable
  placement, include-aware Call cutoff) and `cx-review2` (failed compile on checker exception,
  pipeline same-line order, pipeline task data with per-member probes, Sub placement), Codex
  special-function diagnostics, value ranges and table allocation; Sonnet review-3 fixes
  (expanded-source stop model, folded `Delay` option, shared long conversion), declarations and
  block ends, small gaps (fixed the `ReadOnly A,` false reject), instruction resources/pairing.
  Codex hit its usage window twice; human then restricted Codex to gpt-6-sol reviews. Replay
  1,200 -> 1,291 exact, 45 -> 6 false accepts; pytest 5,868 -> 6,922.

## Status snapshots before 2026-10-03

Measured 2026-10-02 (evening) on `claude/laughing-franklin-08z9vo` at `94b64c6`, after the
statement-form Function arity, SetSetting/SetStatus field names, fN1 minimum, unresolved
`Call` link-step, and duplicate field name chunks (see Log). Exact means equal exit codes and normalized ordered stdout; it does not mean byte
parity. "False reject" means the real compiler accepts and opencrbasic rejects.
No false rejects were observed in these valid differential or replay results.

`tools/difftest.py --logger <logger> --group all --show 0` (historical `examples/` excluded):

| Logger | crbasic/ (16) exact | help (507) exact | help verdict/message differs | help verdict mismatch | help oracle errors |
|---|---|---|---|---|---|
| CR6 | 16 | 436 | 68 | 2 | 1 |
| CR1000X | 16 | 443 | 61 | 2 | 1 |
| CR300 | 6 | 405 | 93 | 7 | 2 |
| CR350 | 5 | 406 | 93 | 6 | 2 |

Reference failures are now separated from compiler verdicts: `pointervariable_2.CR6` crashes
the supplied compiler on all four profiles; `strcomp_3.CR6` also crashes CR300/CR350. These make
the full differential gate exit nonzero, even though no false rejects were observed. Invalid
empty-output cache entries from sandboxed Wine attempts are ignored and were refreshed with
permitted Wine runs. CR6 baseline before the GPS chunk: 429 exact / 71 verdict / 6 mismatch /
1 oracle error; GPS closes two false accepts. Source-buffer limits add three exact help cases on
each profile; no prior exact cases were lost.

`tools/fuzz_replay.py --workers 4 --gate no-regressions --json scratch/replay-predefined-final.json`
(2026-10-02): 1,949 reproducers, 1,108 exact, 115 false accepts, 693 message differences,
14 mode/warning differences, 19 oracle errors; no false rejects, implementation crashes, or
hangs. No previous exact case was lost. The gate exits nonzero for reference compiler faults.
The earlier measured baseline had 1,100 exact and 21 oracle errors. SerialIn adds five exact
reproducer matches; predefined-name checks add two exact matches. Worker lifecycle faults are fixed using a pipe and measured deadline;
see `docs/fuzz-findings.md#worker-isolation` for evidence and regression tests.

Previous fuzz seeds 49 and 50 (800 mutants each): seed 49 no false rejects, 10 false accepts;
seed 50 one false reject (fixed: a DataTable/ConstTable name is read up to whitespace),
11 false accepts, 734 of 800 exact. Fresh bounded seeds 51 and 52 after the current chunks
(100 mutants per profile, no shrinking)
observed zero false rejects, implementation crashes, or hangs. Both exit nonzero for separate
reference failures (one CR6, two each CR1000X/CR300/CR350, including seed failures). Seed 52
results are recorded below; counts include a mutant oracle error where present.

| Seed 52 profile | exact | false accept | message difference | mode difference | mutant oracle error |
|---|---|---|---|---|---|
| CR6 | 92 | 2 | 6 | 0 | 0 |
| CR1000X | 90 | 1 | 7 | 1 | 1 |
| CR300 | 85 | 1 | 14 | 0 | 0 |
| CR350 | 92 | 0 | 8 | 0 | 0 |

The crbasic/ programs are CR6 programs; the CR300/CR350 compilers reject them (different
terminals and instructions), which opencrbasic now reproduces as a verdict for all 16 of them on
both loggers (0 mismatch, up from 8/8 and 10/6 respectively -- `docs/loggers.md`'s "CR300/CR350
agreement round").

`cd scripts && uv run --no-sync verify.py` using the documented external uv environment and
the reference compiler: `BUILD  host tests 119 pass | CRBasic 16/16 compiled, 6 warning(s) |
repository pass`. The older host-import failure report came from running outside that uv
environment and is superseded. The six expected warnings are owned by `../STATUS.md#build`.
The same BUILD line was also observed with `CR6COMP=opencrbasic` on 2026-10-02.
Latest completed full package test run: `uv run --no-sync pytest -q`: 3,213 passed,
311 skipped. Fresh popup-free CR6 reference compilation retained byte-identical TDF and table
lists for all 16 repository programs.


## Log

- 2026-10-02 (evening, Opus orchestrating Sonnet/Haiku agents in per-chunk git worktrees):
  - 9dba35f bare-statement user Function arity; a5124fa SetSetting/SetStatus field names
    (`docs/instructions.md`); 1a111ff CR6/CR1000X fN1 minimum + pass-1 Range code + variable in
    fN1; 574eb12 unresolved `Call` (`docs/checker.md` "Unresolved `Call`s"); 94b64c6 duplicate
    field names (pairwise, FFT/CH400Status, 4095-byte buffer cap).
  - After all five: pytest 4,270 passed / 311 skipped; difftest table above; repo TDF and table
    list 16/16 byte-identical. Replay on the subcall branch alone: 1,121 exact / 107 false
    accepts (from 1,108/115); no full replay after all merges yet. No false rejects observed.
  - Worktree gotcha: the shared uv env's editable install imports the MAIN checkout; inside a
    worktree export `PYTHONPATH=$PWD/src` (difftest/tdftest insert their own path).
  - Open: branch `ocb-argvals` (worktree `.claude/worktrees/ocb-argvals`) holds unverified
    commit 6cd36d5 (MenuPick boolean picklist) from an agent cut off by a network error.
  - CHM `tdftest --group chm` lists size/order mismatches (e.g. structuretype_2, tablefile_1,
    tga_1, trim_1); not yet compared against a pre-change baseline.

- 2026-10-02 (predefined-name namespace chunk): 276 fresh observations preserved with executable
  identities in `tests/fixtures/predefined_name_collision.json`. Global/routine declarations and
  parameters reserve extracted constants, with explicit numeric-name exceptions on CR6/CR1000X;
  structure members remain accepted. Mixed function/constant collisions and redeclarations now
  emit in source order inside the existing declaration scan. Evidence:
  `docs/checker.md#predefined-constant-name-collisions`.
  - Four-profile help/repo differential counts are unchanged; saved replay adds two exact matches,
    and no prior exact case is lost. Gates still exit nonzero for reference faults.
  - Fresh fuzz seed 54, 100 mutants/profile: CR6 92 exact/3 false accepts/5 message differences;
    CR1000X 92/3/5; CR300 89/3/8; CR350 91/0/9. No false rejects, implementation crashes, or hangs.
    Separate seed reference failures: 1/1/2/2 respectively, causing exit 1.
  - Full package tests: 3,213 passed, 311 skipped. Native repository verification reports
    `BUILD  host tests 119 pass | CRBasic 16/16 compiled, 6 warning(s) | repository pass`.
  - Human requested small commits with single-line messages; recorded in the plan. No hardware
    tests or flashing performed.

- 2026-10-01 (Codex continuation, first chunks, no commits): reviewed Claude's implementation
  and replaced stale planning assumptions with the four-profile parity strategy in
  `PLAN.md#remaining-work-strategy`. Centralised interpretation and kept agents on audits,
  harness changes, documentation cleanup, and regression fixtures. GPT-5.6-terra is not exposed
  by the current agent API; short agents use GPT-5.6-sol.
  - Harness: exact requires matching exit codes; `--gate report|no-regressions|exact` and replay
    JSON summaries; invalid Wine/cache results and reference crashes are oracle errors, never
    compiler verdicts; empty differential inputs fail; oracle timeout and stderr capture added.
    Historical `examples/` removed from differential compilation and active parser tests.
  - At the human's request, disabled crash dialogs in the compiler Wine prefix; see environment
    setup above. Reference crashes still produce console diagnostics and nonzero gates.
  - GPS: one shared handler with CR6/CR1000X configuration, verified against 88 fresh oracle
    cases. 22 representative observed cases saved with compiler SHA256 identities in
    `tests/fixtures/gps_ports.json`; focused tests pass. Evidence and boundaries:
    `docs/instructions.md#gps-ports`. CR300/CR350 GPS signatures remain separate and unchanged.
  - Source buffer: 142 usable fresh reference observations, 47 representative fixtures,
    terminated/unterminated line limits, continuation whitespace, and conditional include
    boundaries. Three help cases improve to exact on each profile; no prior exact match lost.
    Evidence: `docs/frontend.md#source-line-buffer`.
  - Fuzz workers: use spawn instead of threaded fork, with parallel compilation and hard-deadline
    cleanup tests. Reference failures are separately counted during seed selection, mutation,
    shrinking, and replay, rather than turning them into false accepts. Artifact gates reject
    unusable cached results and empty comparisons.
  - SerialIn: pass-1 String destination rule and CR300/CR350 argument-abort/cascade recovery,
    verified against 100 fresh observations. Fixture provenance includes all four executable
    hashes. Five saved cases improve to exact, no prior exact match lost; four-profile help/repo
    differential counts stay unchanged. Evidence: `docs/instructions.md#serialin-destination`.
  - Worker lifecycle: concurrent `join(8)` returned in 0.064 seconds while its Queue already held
    the answer. Replaced Queue/join completion with a one-way pipe and an actual monotonic
    deadline. Four-worker full replay completes with no implementation hangs. Parallel stress,
    large-result delivery, missing-result exit, and hard-deadline cleanup tests pass.
  - Built-in-name declarations: a shared symbol helper called inside the existing pass-1 scope
    scan rejects global function-name collisions, including Public/Const inside routines. Local
    Dim/parameter declarations remain accepted. 78 observations match all four profiles,
    including CR300/CR350 declaration-list and following-statement recovery. The full help/repo
    and saved replay counts are unchanged; no prior exact match is lost. Corrected an older test
    that labelled a top-level `Dim exp` as local shadowing. Evidence:
    `docs/checker.md#built-in-function-name-collisions`.
  - Seed 53 after that rule: 100 mutants/profile; zero implementation crashes, hangs, or false
    rejects. Exact/false-accept/message-difference counts: CR6 85/3/11 plus 1 mutant oracle error;
    CR1000X 86/3/11; CR300 89/3/8; CR350 91/3/6. Seed or mutant reference failures still make the
    run exit nonzero (CR6 2, CR1000X 1, CR300 2, CR350 2).
  - No hardware tests, flashing, or runtime claims. No commits requested or created.

- 2026-10-01 (third round, statement and argument checks and CR300/CR350 prose rules,
  `docs/checker.md` "Statement and argument checks", `docs/loggers.md` "CR300/CR350 cascade and
  prose rules"): twelve commits, metrics above.
  - dcaafaa: background calibration warning when delays leave under 3 mSec
  - 97c2e9d: CR300 missing-parameter leftovers; Invalid or missing parameter and the generic
    fallback start the cascade
  - f34f191: CR300 parenthesised lines echo their content
  - cc172d3: measurement Reps and destination size
  - 3fba6f9: SerialOpen string ComPort, code-29 literals, table member arity, EventCount without
    DataEvent, Const empty built-in call
  - 084aa4b: CR300 prose: keyword operators join the word chain, numbers start it
  - 3a4b904: CR300 Data lines with juxtaposed words; instruction keywords in prose; Data never
    unreachable
  - 99606b1: false reject fixed: table names read up to whitespace
  - 183e543: recursive Function calls
  - 64e8fcb: CR300 Variable illegal ends the argument list; compound targets reported once
  - d9dab3e: CR300 unknown-call argument scan ends at a table member
  - 3ff1ddf: CR300 instruction keyword followed by prose; Too many after argument names

- 2026-10-01 (second round, statement and argument checks, `docs/checker.md` "Statement and
  argument checks"): twelve commits, metrics above.
  - 2727ff6: CR300/CR350 prose lines starting `word.` read as table references
  - aa90650: CR300/CR350 `Variable not named.` starts the cascade
  - ef0b9fd: CR300/CR350 unclosed-paren assignments echo the line end, report an undefined target
  - 48b7ea9: For headers checked (loop variable, bounds, step) on all loggers; For does not declare
    its variable
  - 08da3b0: ElseIf conditions and Case values checked; Select Case echo spacing
  - 1ade32a: CR300/CR350 undefined name in a runtime parameter ends the argument list
  - 50749e3: built-in functions written as statements checked for undefined arguments
  - d0f1ffc: Const built-in call arity, parenthesized Alias indexes, expression errors start the
    CR300 cascade
  - d11b152: ESSVariables checked against the ESS table extracted from each exe
    (`data/**/ess_variables.json`)
  - 55e74ea: constant array indexes out of bounds in instruction arguments
  - 116fbe9: MenuPick rules for ConstTable constants; unknown Scan units count as 0 uSec
  - a86a63e: CR6/CR1000X scan interval too short for its measurement delays

- 2026-10-01 (CR300/CR350 argument rules, `docs/loggers.md` "CR300/CR350 argument rules"): four
  commits, metrics above.
  - 75dd03f: CR300/CR350 unknown-call argument scan covers four arguments; bare DataTable names
    are not values
  - 51c1217: inline If echoes keep trailing spaces; tainted-call names not deduplicated
  - d18eac4: cascade-rewritten instruction still checks its first argument
  - 99d707d: Float destination check (code 8); argument errors end a Dest parameter list

- 2026-09-26 (checker round 6a: fuzz-driven false-accept reduction, continued): `checker/
  structure.py`, `checker/symbols.py`, `checker/instr_rules.py`, `checker/instr_data.py`,
  `checker/warnings.py`, `checker/__init__.py` (owned this round). Full rules and oracle evidence:
  `docs/checker.md` "Round 6". Seven items, same method as round 5 (group saved fuzz reproducers
  by the real compiler's own message, oracle-confirm the exact shape, re-verify the whole corpus +
  all four loggers + `fuzz_replay.py` after every change): (1) `Scan %duSec invalid must be at
  least %dmSec.`/`Scans must be a multiple of %dmSec.` (`docs/instructions.md`'s own "boundary
  this session could not pin down" gap, resolved: the declared `Interval` truncates toward zero
  *in its own declared unit* before multiplying by that unit's microsecond factor, no float-
  precision subtlety at all, one rule with no per-unit branch); (2) a **pre-existing** bug found as
  a side effect of (1), not caused by anything this round changed -- the background-calibration
  warning is not actually ungated on CR6/CR1000X the way this project's own docs previously claimed
  for all of `check_early`; moved it to `checker/__init__.py`'s final "no errors so far" tier;
  (3) another **pre-existing** bug, found the same way -- `structure.py`'s three end-of-file block-
  balance summary checks (`No BeginProg in Program.`/`Scan without NextScan.`/`IF or SELECT without
  EndIf or EndSelect.`) needed a one-line guard, since the real compiler suppresses all three once
  any other pass-1 declaration error already fired; (4) `Illegally dimensioned aliased name`/`Alias
  is illegally dimensioned.` (an `Alias` target's own out-of-range or empty-parens index, new
  `symbols.declared_dim0_sizes`); (5) `Multiple BeginProgs.` (a genuine nesting-depth check, not
  "seen more than once"); (6) `IF or SELECT statement illegally has SCAN without NEXT SCAN.` (a
  `Scan` opened inside an `If`/`Select` still open, no matching `NextScan`, when that block's own
  `EndIf`/`EndSelect` is reached); (7) `DataTable ... EndTable not matched.`/`Could not find
  DataTable %s.` (an unclosed `DataTable`, split by whether anything later `CallTable`s it, new
  `instr_data.calltable_targets`/`check_unclosed_calltable`). Cross-checked the two `DataTable`
  messages directly against `cr300comp.exe` (byte-identical) since `tools/oracle.py`'s CLI always
  drives `CR6Comp.exe`; implemented logger-unconditionally.

  `tools/fuzz.py --logger cr6/cr1000x --count 300` false-accept count, this round's own fresh
  start/end measurements (`false_reject = 0` throughout except one long-standing, pre-existing,
  independently-root-caused `AddPrecise` gap found during baseline re-measurement, unattempted):

  | Logger | seed | before | after |
  |---|---|---|---|
  | cr6 | 42 | 44 | 36 |
  | cr6 | 43 | 32 | 29 |
  | cr6 | 44 | 36 | 27 |
  | cr1000x | 42 | 39 | 31 |

  `tools/difftest.py --logger <l> --show 0`: exact counts never lowered (cr6 336, cr1000x 352,
  unchanged), zero false rejects on all four loggers throughout. `python3 -m pytest -q`: 963 passed
  throughout (0 failed). `tools/fuzz_replay.py` (930 reproducers across cr6+cr1000x): several
  hundred fixed outright, dozens changed class, zero new `false_reject` (five older, independently
  root-caused pre-existing `false_reject` entries were themselves fixed outright as a side effect).
  This round ran on a repository shared live with the concurrent "Round 6b" session below, whose
  commits landed in this same working tree mid-round; the numbers above are this round's own
  fresh, unadjusted start/end points, not attributed to that other session's unrelated changes.
  Remaining groups: `Duplicate Alias`, further `Need closing )` shapes beyond item 4's own, general
  expression-tree gaps outside `expressions.py`'s deliberately narrow scope, `DataTable without
  EndTable.` (item 7's own not-attempted nested-table shape), and function/`Sub`/`Function`
  argument-count/type checks from `data/functions.json` (none attempted this round).

- 2026-09-26 (checker round 6b: CR300/CR350 undefined bare calls, `SerialOpen` `ComPort`): new
  `checker/cr3xx.py` (CR300/CR350-only checks, wired into `checker/__init__.py` with one additive
  call), one additive `Profile.undefined_call_message` field (`profiles.py`) and its one gated
  branch in the shared `checker/structure.py::_check_call_pass2`, `data/cr300/`/`data/cr350/`
  unchanged (no new data files needed this round), `docs/loggers.md` "Round 6b". Two fixes, found
  by grouping every CR300/CR350 `chm` `verdict`/`mismatch` case by `(their first message, our
  first message)`: (1) a bare call to a name CR300/CR350 don't recognise (most often an
  instruction absent from their smaller table, e.g. `DisplayMenu`/`I2COpen`/`CardOut`) now gets its
  own `{name} is not defined.` line before the existing generic fallback line, and (oracle-
  confirmed the opposite of CR6/CR1000X for this one branch) never the before-`BeginProg`
  follow-up/stop -- the single largest CR300/CR350 `chm` `verdict` group, 187/190 files, cut to
  81/81 (the residual is CHM prose-as-code artifacts, a different, `Unparsed`-shaped case, not
  chased); (2) `SerialOpen`'s own `ComPort` argument (a *function* call's argument, never checked
  by this project's `expressions.py` at all before now) needs a genuine compile-time constant on
  CR300/CR350 -- an undefined name or live variable there now gets `{name} is not defined.` (if
  undefined) plus `ComPort 0 illegal.`/`Parameter must be a constant integer: %s`, closing 2 real
  corpus `mismatch`es (silent false accepts) per logger. `tools/difftest.py --logger <l> --show 0`
  (`chm`, before this round's start / after both fixes): cr300 135/359/13 -> 138/358/11; cr350
  139/356/12 -> 142/355/10; cr6/cr1000x unaffected (both fixes are cr300/cr350-only, either by
  `cr3xx.check`'s own logger guard or `undefined_call_message`'s default). Zero false rejects on
  all four loggers throughout (`their_exit==0 and our_exit!=0`, empty every time).
  `python3 -m pytest -q`: 963 passed, 311 skipped throughout (0 failed). Fixed-seed fuzz
  (`--logger cr300/cr350 --count 300 --seed 42/43`): false-accept counts barely move (this round's
  two fixes are corpus-representative but each individually low-frequency in any one random
  300-mutant sample) -- `false_reject = 0` throughout except the one already-documented,
  pre-existing cr350 seed-43 flake (`docs/fuzz-findings.md` "4."), unchanged. Remaining gaps
  (evidence only, not attempted): the `Units`/`Const`-truncation echo's own trailing-newline quirk
  in the *shared* `structure.py` fallback line (implemented in this round's own new
  `checker/cr3xx.py` message, left alone in the higher-risk shared one); CHM prose-as-code
  `Unparsed` statements' own per-word echoing; `SerialOpen`'s valid `ComPort` numeric range. Full
  evidence and oracle transcripts: `docs/loggers.md` "Round 6b".

- 2026-09-26 (checker round 5: fuzz-driven false-accept reduction): `checker/structure.py`,
  `checker/expressions.py`, `checker/symbols.py`, `checker/instr_rules.py`, `parser.py` (owned this
  round, plus `checker/__init__.py`, one wiring line). Full rules, oracle transcripts and the two
  "tried broader, reverted" accounts: `docs/checker.md` "Round 5". Six items: (1) `Assignment`
  placement before `BeginProg` (a three-way split mirroring the existing `Call` check -- target
  doesn't resolve / target resolves+RHS undefined / target resolves+RHS clean -- was the largest
  single false-accept group, ~70 of ~300 mutants); (2) `ESSVariables`-implied variable names
  (`symbols.ess_variable_names`, a false reject item 1 exposed, fixed conservatively: only names the
  program itself references); (3) a **pre-existing** false reject found while re-measuring a clean
  baseline, not caused by this round -- `DataInterval`/`Scan`'s divisibility check needs a
  whole-number-value exemption (`DataInterval(0,1.5,Sec,10)` inside `Scan(1,Sec,...)` compiles
  clean despite 1.5 not being an integer multiple of 1); (4) a malformed `As` clause is exempt for
  **array** declarations (`Public Result(4) as_Fz Boolean` compiles clean, `parser.py`); (5) `Too
  many parameters:  %s.` (new `check_too_many`, restricted by a 740-accepted-file corpus safety
  scan to type-code `kind`s proven safe -- `Alias`/`Units`/`MenuPick`/`ConstTable`/instructions with
  an empty declared `params` list are excluded, all found to misfire otherwise); (6) `Scan`/`Delay`
  illegally occur before `BeginProg` -- tried as a general "any recognised `Instruction`" rule and
  reverted (a 740-file corpus scan found dozens of legitimate counter-examples, no safe
  discriminator found even in the extracted instruction table's own `runtime_fn` field); kept as a
  narrow, two-instruction set instead. Three genuine false rejects were found and fixed this round
  (items 2/3/4's own root causes), each caught by the mandatory full-corpus + `fuzz_replay.py`
  re-run before being called done -- this round's own repeat of earlier sessions' methodological
  finding (a hand-written probe is not a substitute for the full corpus before trusting a
  generalisation), twice over (see item 6's own two "tried, reverted" attempts in `docs/checker.md`).

  `tools/fuzz.py --logger cr6/cr1000x --count 300` false-accept count, before this round's start /
  after, `false_reject = 0` throughout every intermediate and final measurement:

  | Logger | seed | before | after |
  |---|---|---|---|
  | cr6 | 42 | 67 | 44 |
  | cr6 | 43 | 59 | 32 |
  | cr6 | 44 | 62 | 36 |
  | cr1000x | 42 | 57 | 39 |

  (The task brief's own stated baseline, 74/300 on cr6 seed 42, predates the "CR300/CR350 agreement
  round" below, which had already lowered it to 67/300 before this round started -- not this
  round's own work.)

  `tools/difftest.py --logger <l> --show 0`: exact counts never lowered (cr6 336, cr1000x 352, cr300
  135, cr350 139, all unchanged), zero false rejects on all four loggers throughout (`their_exit==0
  and our_exit!=0` check, empty every time); cr300/cr350 chm mismatch improved (16->13, 15->12, item
  5's own effect) with no exact-count change. `python3 -m pytest -q`: 963 passed throughout (0
  failed). `tools/fuzz_replay.py --workers 2`: 193 reproducers fixed outright by this round's end
  (708 total by then, up from 644 at the round's start as fresh false-accept saves accumulated), 60
  changed class, zero new `false_reject` at every checkpoint; the two `cr350` `false_reject` entries
  present throughout are the pre-existing, already-documented `ESSInitialize`/`SNMPVariable`-shaped
  gap (`docs/fuzz-findings.md` "4."), unchanged. Top remaining `false_accept` groups (`docs/
  checker.md` "Round 5" has the full list): `Scan %duSec invalid must be at least %dmSec.`
  (sub-millisecond granularity, boundary not pinned down); general expression-tree gaps outside
  `expressions.py`'s deliberately narrow scope; `Illegally dimensioned aliased name`; `Multiple
  BeginProgs.`; `Need closing )`; `DataTable ... EndTable not matched.`; `IF or SELECT statement
  illegally has SCAN without NEXT SCAN.` (a new placement shape along item 6's own lines, not
  chased this round).

- 2026-09-26 (`-z` TDF auto-size exact, `-u` implemented): two independent pieces, `tables.py`
  (owned this round, no `checker/*` changes needed) and `checker/unused.py` (new file) plus small
  wiring in `compiler.py`/`cli.py`.
  1. **TDF auto-size, mixed regime, now exact** (`tables.py`'s `_resolve_auto_sizes`, docs/tdf-
     rules.md §10/§12/§13): decompiled `FUN_004eab30` (table finalisation loop) and `FUN_004f4400`/
     `thunk_FUN_004f4400` (per-table probe/commit allocator) in `/opt/re/out/decomp.c`, corrected
     two earlier misreadings along the way (the mechanism is a **single pass**, not iterative
     water-filling; and the flag `thunk_FUN_004f4400` branches on to pick a table's own default
     record count is "has a real `DataInterval`", not "has CardOut/TableFile" as an earlier session
     guessed from its later reuse in the CardOut-bonus term). Pinned the one remaining fitted
     constant (`_MIXED_POOL=4,194,139`, the compiler's own free-memory query at this point) two
     independent ways -- a 4-point synthetic `tdf_probe.py` grid (exact `Fraction` arithmetic, not
     curve-fitting) and all 8 real `crbasic/` corpus "mixed regime" programs -- both agreeing
     exactly. Result: `tools/tdftest.py --group repo` **16/16 TDF byte-identical** (up from 8/16);
     `tests/test_tables.py`'s 8 xfails now pass outright. CHM sample agreement unaffected by this
     change (before/after, same 100-file slice: 22/36 both times -- the sample's own remaining
     gaps are all CPI/CDM system tables (`CPIStatus`/`CH400Status`), out of scope, not auto-sizing).
     Full derivation and evidence: docs/tdf-rules.md §10.
  2. **`-u` implemented** (`checker/unused.py`, a new, standalone module -- deliberately *not*
     routed through `checker.check`, so `checker/__init__.py` (concurrently owned) needed no
     changes at all; wired via a new `warn_unused` parameter on `compiler.compile_file` and `-u` on
     `cli.py`). Oracle-confirmed (`tools/tdf_probe.py <snippet> --extra=-u` against `CR6Comp.exe`,
     cross-checked identical on `cr1000xcomp.exe`/`cr300comp.exe`/`cr350comp.exe`, no per-logger
     differences): flags an unreferenced `Public`/`Dim` (whole array, any element's use counts) and
     an unreferenced `Sub`/`Function` parameter, each `line N: Warning: %s declared but not used.`;
     also an unreferenced `Sub` **or** `Function` itself (corrected an earlier false inference from
     one probe, that only `Sub` is checked -- a real corpus case, `ShuntLib.crb`'s `SampleShunt`,
     uncalled in three testers because its only call site sits behind a false `#If`, showed a
     `Function` *is* flagged too; the original probe's `Function` happened to self-assign its
     return value, `MyFunc = value`, itself a reference that suppressed the warning), as a bare
     `Warning: %s declared but not used.` positioned after every numbered-line warning but *before*
     the pre-existing "table declared but never called" warning (decompiled: `FUN_004eab30` calls
     the `-u` check once, early, well before the later per-table loop that detects an uncalled
     table). `Const`/`ConstTable` fields never flagged; `Alias` merges the alias name and the
     original into one usage identity, but `Alias`/`Units`/`ReadOnly`'s own mention of a name is
     never itself a use; a variable used only as a `DataTable` output source counts as used.
     Suppressed entirely by any compile error anywhere in the file. Along the way, fixed a real,
     previously-latent bug in `compiler.CompileResult.result_lines`'s diagnostic sort: it sorted
     every diagnostic by raw line number globally, which silently scrambles output once a program
     spans multiple `Include`d files (each restarts its own line numbering at 1) -- exposed at scale
     by `-u`, since unused-declaration warnings are the first diagnostic class dense enough to span
     many included library files in one program (`SbdPayload_tester.CR6`'s real corpus fixture);
     added a `file_order` primary sort key (expansion order, from `program.statements`), a no-op for
     every single-file program (i.e. every previously-verified case). Verified byte-identical
     against the real exe on all 16 `crbasic/` programs with `-u` (siblings dir, for `Include`s),
     plus a synthetic multi-`Include` fixture isolating the ordering fix
     (`tests/test_unused.py`, fixtures in `tests/fixtures/cli/`). `python3 -m pytest -q`: 963
     passed (957 + 6 new), 0 failed. `difftest.py`: `cr6 repo` 16/16 exact, 0 mismatch; `cr1000x`/
     `cr300`/`cr350 repo`: 0 mismatch (false rejects) on all three, unchanged from this session's
     starting point (the CR300/CR350 agreement round below, not touched by this entry's own files).

- 2026-09-26 (CR300/CR350 agreement round): `checker/expressions.py`, `checker/__init__.py`,
  `profiles.py` (owned this round), plus two small additive fields needed by them (`syntax.py`
  `ConstDecl.value_span`, `parser.py`'s one-line computation of it). Full rules and oracle evidence:
  `docs/loggers.md` "CR300/CR350 agreement round". Three findings, all oracle-confirmed: (1) CR300/
  CR350 do not share CR6/CR1000X's undefined-name/`Variable illegal in Parameter` mutual
  exclusivity at a const-family argument position -- they report *both* (accounts for this round's
  largest `verdict` groups, `mv200C`/`mv5000C` range-constant names CR300/CR350 simply don't have);
  (2) neither a `Const`'s initializer nor a plain `Assignment`'s RHS was ever checked for an
  undefined name on *any* logger (new `expressions.check_consts`, pass 1/ungated, and a second loop
  in `expressions.check` for assignments, pass 2/gated like the rest of that function) -- closes
  CR300's and CR350's repo-program `mismatch` count to 0 (was 8 and 10) and fixes one of CR1000X's
  three pre-existing repo mismatches too; (3) CR300/CR350's `warnings.check_early`
  (reachability/fnotch) is not unconditional the way CR6's is -- an undefined-symbol-class error
  anywhere in the file suppresses every `check_early` warning in the whole file, even on an earlier,
  otherwise-clean line (new `Profile.early_warnings_gated`). Full-corpus `tools/difftest.py`
  (`repo`+`examples`+`chm`): cr300 chm exact 120->135, mismatch 18->16, repo 0/8/8->0/16/0; cr350
  chm exact 124->139, mismatch 17->15, repo 0/6/10->0/16/0; cr6/cr1000x chm exact unchanged (336,
  352); cr1000x repo 8/5/3->9/7/0. False rejects 0->0 on all four loggers throughout (`--show 0` +
  the `their_exit==0 and our_exit!=0` check). `python3 -m pytest -q`: 949 passed both before and
  after (a later, unrelated 957/0-xfailed run in the same shared tree was the concurrent `tables.py`
  workstream's own progress, confirmed by `git diff --stat` touching only this round's five files).
  `tools/fuzz_replay.py --workers 2` (357 reproducers): 41 fixed, 48 changed class, 0 new
  `false_reject`. Remaining CR300/CR350 gaps (evidence only, not attempted): a `SerialOpen`-style
  `ComPort` argument's own three-message diagnostic; several instructions absent from CR300/CR350
  whose bare-call syntax falls into the generic "Expr" fallback rather than a per-name `is not
  defined.` (`DisplayMenu`/`UDPSocketOpen`/`I2COpen`/`I2CWrite`/`CardOut`/`PortPairConfig`); a
  handful of single-file `Units`/`Const`-truncation message differences on malformed CHM examples.

- 2026-09-26 (checker round 4: general expression-tree resolution, pass model correction):
  `checker/expressions.py`/`checker/symbols.py` (owned), plus a coordinator-directed extension into
  `checker/structure.py`/`checker/__init__.py` (pass ordering only; other modules' own checks
  untouched). Full rules, oracle transcripts and evidence: `docs/checker.md` "Round 4".
  1. **General expression-tree name resolution**, re-attempted and landed after round 3 backed it
     out: `NAN`/`INF` (a separate resolution path from the extracted `symbols.json` named-constant
     table, oracle-confirmed accepted on all four loggers) and `Status`/`Public`/`Settings`/
     (program-conditional) `DataTableInfo` (system tables, `Table.Field`) close the two gaps that
     caused round 3's false rejects; the walk now recurses into compound expressions, not just a
     bare argument. Per-logger echo/ordering rules found this round: CR6/CR1000X check every
     constant-only-family argument position before any general-family one regardless of textual
     order and report one whole-expression echo; CR300/CR350 check positions in plain textual order
     and echo every undefined name in a compound expression as its own line. The constant-only-
     family "rejects a variable" check (`Variable illegal in Parameter: %s.`) is now implemented,
     narrowed to avoid round 3's `Sample`/`DataType` regression. Variable-required stays
     unimplemented: a corpus scan found a third confirmed per-instruction override
     (`MQTTPublishTable`) beyond round 3's two, reinforcing that a blanket rule is unsafe.
  2. **Pass model correction**: the generic "Expr"/implicit-`Call` fallback (`Invalid, or out of
     place expression: %s.`/`Sub %s not defined.`) is itself pass 2 (oracle-confirmed: an earlier,
     unrelated `Variable not named.` suppresses it, same as it already suppressed
     `expressions.py`'s checks and `instructions.py`'s channel check) -- previously implemented as
     unconditional. `structure.check` split into `check_pass1` (unconditional) and `check_pass2`
     (gated, and now also implementing the before-`BeginProg` placement follow-up and stop this
     checker had previously deliberately not attempted). Two regressions caught by re-running the
     full corpus + `pytest` immediately after the first version and fixed before landing: the
     placement check does not apply inside a `Sub`/`Function` body (`crbasic/Main.CR6` false-
     rejected otherwise) or inside several other named, `EndXxx`-terminated pre-`BeginProg` body
     blocks (`ModemHangup`/`DialSequence`, corpus-evidenced; four more on the documented pattern).
     `checker/__init__.py` now has three tiers: pass 1 (unconditional), pass 2 tier B
     (`structure.check_pass2` + `expressions.check`, freely accumulating with each other, cut off
     together at the first placement error), pass 2 tier C (`instructions.check`/`instr_data`/
     `instr_measure`, gated behind tiers A *and* B both being clean, oracle-confirmed non-
     accumulation across this specific boundary).

  `tools/difftest.py --logger <l>` (`repo`+`examples`+`chm` summed), before this round (round 3's
  end state, re-measured) / after both pieces:

  | Logger | before | after | false rejects |
  |---|---|---|---|
  | cr6 | 327/189/11 | 354/164/9 | 0 -> 0 |
  | cr1000x | 338/178/11 | 363/155/9 | 1 (pre-existing `structuretype_3.CR6`, unrelated) -> 1 (unchanged) |
  | cr300 | 122/379/26 | 122/379/26 | 0 -> 0 (unaffected) |
  | cr350 | 126/374/27 | 126/374/27 | 0 -> 0 (unaffected) |

  Fixed-seed fuzz false-accept rate (`tools/fuzz.py --logger cr6 --count 300 --seed 42`): 77/300
  (25.7%) -> 74/300 (24.7%), `false_reject=0` throughout. `python3 -m pytest -q`: 949 passed, 0
  failed (943 -> 949, mostly concurrent work's own new tests, not this round's). `tools/
  fuzz_replay.py --workers 2`: no new `false_reject`; the only class regressions seen
  (`message_diff -> false_accept`, 3 reproducers) were independently confirmed pre-existing (a
  `Variable Type %s not supported` type-name-validation gap and the pre-existing `structuretype_3.
  CR6`-family parsing gap, neither in this round's own files) by replaying them against this
  round's *starting* `expressions.py`/`symbols.py`, where they were already broken the same way.

- 2026-09-26 (CLI outputs: `-p`/`-i`/`-t`/`-b`/`-m`/`-u`): new `src/opencrbasic/preprocess.py`
  (`-p`/`-i`) and `src/opencrbasic/timing.py` (`-t`), plus `cli.py` wiring for both and for `-b`;
  full account and evidence in `docs/cli.md`. `-p`/`-i`: byte-identical on all 16 repository
  programs and 98/100 of a random 100-file `tests/corpus/chm/` sample (the other 2 hit an exe-side
  truncation this session did not reproduce, see `docs/cli.md`); the exe turned out to *not* strip
  comments or expand includes under `-p` (an earlier draft of `docs/cli.md` had both wrong,
  corrected this session by direct oracle probing, not decompilation). `-t`: byte-identical on all
  16 repository programs and 91/100 of the same CHM sample, for a narrow, oracle-verified
  instruction set (`Scan`/`PanelTemp`/`PortSet`/`Sw12`/`Delay`(`mSec`)/`TCDiff`(`Reps=1`));
  discovered along the way that `[Times]` entries are gated on the checker's own pass-1/pass-2
  boundary, not the overall compile result (`dhcprenew_1.CR6` fails yet still gets a full `[Times]`
  section). `-b`: the real "No CDM devices used in this program " message, correct for every
  program in this repository (none use CPI/CDM hardware); the binary image format itself was not
  attempted (out of scope for this project). `-m`: decompiled (`FUN_00508be0`'s memory-allocation
  trace) and oracle-confirmed to have no observable CLI effect at all -- the buffer it builds is
  never written to any file or stream in the reachable code. `-u`: decompiled and oracle-confirmed
  to gate a real, previously-unexplained warning, `%s declared but not used.` (`FUN_004ba180`,
  `Public`/`Dim` only, not `Const`/`Alias`/`Sub`/`Function`) -- resolves this project's own
  "Investigated, not implemented: no trigger found" note from checker round 3 (it was never
  triggered because `-u` was never passed); not implemented here since it needs the checker's own
  symbol/usage table (`checker/symbols.py`, a concurrently-owned module) to do without duplicating
  and risking drift from that logic. Also fixed, in scope as message-file content: the message
  file's blank line straight after a `-- Compile Failed!` header is oracle-confirmed to be a bare
  LF, not CRLF like every other line (`cli.py`'s `_message_bytes`; previously assumed uniform
  CRLF, wrong on every failing compile with a message file, not just this session's own new
  outputs). New tests: `tests/test_cli.py` (5 tests) against `tests/fixtures/cli/` (a small
  scratch program exercising comments, a continuation, `#If`, and a bare `Include()` inside a
  `Scan`, captured from the real exe). `python3 -m pytest -q`: 948 passed (previously 943 + 5 new),
  0 failed from this session's own work; one unrelated failure in `test_checker.py` was present at
  the time of writing from concurrent, in-progress checker work on a shared front-end/library file
  (`BenchLib.crb` briefly failed to parse), not from anything in this entry -- confirmed by
  re-running the same `-p`/`-i` byte-identical checks, unaffected since they do not touch
  `checker/*`, while the affected `-t`/`-b` combined-message-file check briefly regressed and
  recovered as that concurrent work progressed.

- 2026-09-25/26 (checker round 3, warnings + value-range checks): implemented from
  `docs/fuzz-findings.md`'s highest-leverage items, all oracle-verified, rules and evidence in the
  new `docs/instructions.md`: four warnings (`%s never reached at run time.`, all four loggers;
  `When Main Sequence Scan %d is running, background calibration will be disabled.`, CR6 threshold
  <8ms / CR1000X <2ms, absent on CR300/CR350; `Invalid fnotch value, must be 50,60 400 or 4000`,
  CR300/CR350 only, 16 instructions' `fN1` argument) and three `Scan`/`DataInterval` value-range
  errors (`Scan Interval/BufferOption/Count parameter must be a non-negative integer` and
  `DataInterval TintoInt and Interval parameters must be non-negative`, both CR6/CR1000X only --
  found absent on CR300/CR350 as a false-reject regression, `hex_2.CR6`, and gated off there;
  `DataTable interval %d uSec not integrally divisible by scan interval %d uSec.`, project
  knowledge from `knowledge/crbasic/lessons.md`, identical on all four loggers). Closes the
  fuzz-found crash (`tables.py`'s `_scan_weights`, a non-numeric `Scan` interval) with a real
  diagnostic; the fuzz-found hang (`tables.py`'s `render_table_list`) was independently already
  fixed by the concurrent `structure.py` array-dimension check before this round started (re-
  verified fixed here too). New: `checker/warnings.py`'s `check_early` (ungated -- all of the above
  are oracle-confirmed to fire even when the file has other errors elsewhere, a second
  architectural finding this round alongside the pre-existing pass-1/pass-2 split), new functions in
  `checker/instr_rules.py`, one additive line in `checker/__init__.py`, `Profile.fnotch`/
  `Profile.background_calibration` in `profiles.py`, `data/<logger>/{fnotch,
  background_calibration}.json`. Also fixed, as shared/unowned glue (`diagnostics.py`,
  `compiler.py`, matching a precedent set by the checker-round-2 session): a warning with a real
  line now prints `line N: Warning: ...` (previously always bare `Warning: ...`, which happened to
  be right for the one warning that existed before this round); errors and warnings are now
  rendered as one line-ordered list (a line-less warning sorts last), on both the failure and
  success paths. One regression caught and fixed during development, not left in: the reachability
  check's first version falsely flagged `EndSequence` (a `SlowSequence`'s own terminator) as
  unreached on 10 real, oracle-accepted corpus programs; fixed by stopping the analysis entirely at
  the first `SlowSequence` rather than guessing at slow-sequence-internal reachability (`docs/
  instructions.md`). `python3 -m pytest -q`: 939-943 passed (small run-to-run count changes are
  from the CHM-corpus-driven test collecting differently as unrelated concurrent `structure.py`/
  `parser.py` work landed, not this round's own tests), 0 failed throughout. Investigated, not
  implemented (evidence and reasoning in `docs/instructions.md` "Investigated, not implemented"):
  `%s declared but not used.` (no trigger found); CR6's own `Invalid fnotch, minimum allowed is 2
  Hz.` error and three more `Scan`-family messages (`...must be at least...`/`...multiple of...`/
  `Maximum Scan interval is 1 Day.`) -- all real, but each has a boundary this round could not pin
  down cleanly enough to implement without a false-reject risk.

  `tools/difftest.py --logger <l>` (`chm` group; `repo`/`examples` unchanged throughout),
  before (`git worktree` at the last commit before this round, `5b4692a`) / after (this round plus
  the concurrent `structure.py`/`parser.py`/`tables.py` work landing in the same window -- both
  contributed to the same shared files, so the two are not separable from `git diff` alone;
  `docs/instructions.md`/this entry describe only this round's own rules):

  | Logger | chm exact/verdict/mismatch before | after | false rejects before -> after |
  |---|---|---|---|
  | cr6 | 310/188/9 | 311/188/8 | 0 -> 0 |
  | cr1000x | 327/172/8 | 328/172/7 | 0 -> 1 (pre-existing, `structuretype_3.CR6`, unrelated parser gap) |
  | cr300 | 93/396/18 | 120/369/18 | 0 -> 0 |
  | cr350 | 96/394/17 | 124/366/17 | 0 -> 0 |

  `tools/fuzz_replay.py --workers 2` (248 reproducers): 23-26 fixed, 0 new false rejects (run twice;
  the exact fixed/changed counts wobble a little between runs on this box -- worker contention
  re-classifying a fast case, `docs/fuzz-findings.md`'s own documented flakiness note -- but no run
  ever showed a `false_reject`). Confirmed fixed: the `_scan_weights` crash
  (`cr6/crash/ce0f811dd89e.CR6`, `cr300/crash/{237270eb6999,f3505c8f84ae}.CR6`), both `mode_diff`
  reachability/background-calibration reproducers, both `fnotch` `mode_diff` reproducers
  (`cr300`/`cr350`), and several `false_accept`/`message_diff` reproducers that were the same root
  causes. A `hang` classification seen once on a `false_accept` reproducer during this round's own
  fuzz_replay runs did not reproduce on direct, isolated re-invocation (20 back-to-back calls, 71ms
  total) and is judged transient (worker contention / the mid-round container restart), not a real
  regression -- flagged here rather than silently discarded, per AGENTS.md's evidence discipline.

- 2026-09-25/26 (checker round 3, the other half -- fuzz bugs, error-reporting internals, array
  dimension limits, expression checks): `source.py`, `lexer.py`, `parser.py`, `consteval.py`,
  `tables.py` (robustness only), `checker/__init__.py`, `checker/structure.py`,
  `checker/expressions.py`. Full rules and oracle evidence: `docs/checker.md` (new "round 3"
  sections throughout) and `docs/frontend.md`.
  1. **Fixed the three fuzz-found bugs** (`docs/fuzz-findings.md`): the hang
     (`Public X(999999999999) As Long`, `tables.py`'s `-l` renderer enumerating ~10^12 indices) is
     now rejected at declaration time with the real `Illegally dimension: %s`/`Variable %s
     dimension exceeds max of %d.` messages (`structure.py`'s new array-dimension-limit check,
     oracle-confirmed boundary at `INT32_MAX`, at most 3 dimensions) *and* `tables.py`'s renderer
     itself got a defensive element-count cap as a second line of defence; the crash (`tables.py`'s
     `_scan_weights` calling `float()` on a non-numeric `Scan` interval) is now type-checked first;
     the false reject (`IncludeSection` with a harmless third argument silently not expanding) is
     fixed in `source.py`'s regex. Added `checker/__init__.py`'s `_run_pass` (every checker pass
     wrapped so one module's bug can't take down the whole compile) and `compiler.py`'s own
     try/except around parsing/checking/signature -- belt-and-suspenders so `compile_file` never
     raises, matching what a fuzzer or any other caller needs.
  2. **Error-reporting internals**, both oracle-confirmed by construction (scratch probes, not
     saved to the corpus): a 4096-byte running buffer for error messages -- once a message no
     longer fits, it is dropped and pass 1 reports nothing further (`compiler.py`'s
     `_bounded_by_message_buffer`, verified byte-for-byte against a 150-line reproducer that
     stops after its 69th message); and an included file's own name in angle brackets,
     `<file> line N: message`, for most error classes but *not* `Sub %s not defined.`/`Invalid, or
     out of place expression: %s.` (both structure.py's generic bare-statement fallback), modelled
     as `Diagnostic.show_file` (`diagnostics.py`, default on, opted out only at those two call
     sites). The task's third claimed fact, "warnings capped at 1000", could not be verified: the
     only implemented warning kind is itself capped far earlier by a different, oracle-confirmed
     real limit, `Maximum number of tables of 128 exceeded.` (found while trying to construct the
     probe; not implemented, noted in `docs/checker.md` for whoever next touches `structure.py`'s
     declaration-count checks or `tables.py`).
  3. **`Invalid Variable declaration %s.`** for a malformed `Public`/`Dim` `As` clause (fuzz
     finding 4, well evidenced by 6 fuzz mutants): `parser.py`'s `_parse_vardecl` now distinguishes
     "no `As`/`=`/comma/EOL token here at all" (this error, abandoning the whole declaration
     statement) from an oracle-confirmed quirk this fix also had to get right to avoid two *new*
     false rejects -- once a variable's own `As Type` clause has fully parsed, anything before the
     next comma is silently discarded, not rejected.
  4. **Type-code-driven argument checks (task's 3rd item) were attempted and mostly reverted.**
     Implemented, verified against hand-written oracle probes, wired in, and then found to cause
     real false rejects once checked against the *full* corpus on all four loggers (not just the
     probes): a full expression-tree undefined-name walk rejected this repository's own
     `crbasic/Main.CR6` and siblings (`NAN` and the system `Status` table are both missing from
     `known` in a way only a nested-expression lookup reaches); constant-only (codes 1/39, 5/38,
     20) and variable-required (codes 8, 27, 29, 33, 41, 47, 48) rejection rules are real for
     specific instructions but not uniform across every instruction sharing a code (`Sample`'s
     `DataType`, `Move`'s third argument, `ModBusClient`'s last argument each override their own
     code's generic rule). All three are backed out of `expressions.check`'s active path (the
     helper functions are written and documented, not deleted, for a session with time to verify
     per-instruction); `docs/checker.md`'s `expressions.py` "Round 3" section has the full account.
     This is the session's main methodological finding: verify a generalisation against the whole
     corpus and all four loggers before trusting a hand-written probe, not just the probe.

  `python3 -m pytest -q`: 943 passed, 0 failed. `tools/difftest.py --logger <l> --group all`,
  before this round (2026-09-25 checker-round-2 end state, `PROGRESS.md`'s own prior numbers) /
  after (this entry plus the concurrent warnings/value-range round above -- both landed in the same
  window and are not separable from a plain `git diff`):

  | Logger | repo | chm | false rejects |
  |---|---|---|---|
  | cr6 | 16/0/0 unchanged | 310/188/9 -> 311/188/8 | 0 -> 0 |
  | cr1000x | 8/5/3 unchanged | 327/172/8 -> 328/172/7 | 1 (pre-existing `structuretype_3.CR6`) -> 1 (unchanged, not fixed -- see below) |
  | cr300 | unchanged | 93/396/18 -> 120/369/18 | 0 -> 0 |
  | cr350 | unchanged | 96/394/17 -> 124/366/17 | 0 -> 0 |

  `structuretype_3.CR6`'s false reject (a `StructureType` body's bare, keyword-less member
  declarations, e.g. `LatitudeDegrees` or `Speed,Course,...` alone on a line -- CR6's parser has no
  notion of this shape at all) was looked at again this round per an explicit ask to fix it "if
  quick"; it is not quick -- it needs the parser to recognise `StructureType`/`EndStructureType`
  block context and parse a bare comma-separated name list (optionally `As Type`) specially there,
  without accidentally swallowing an ordinary bare statement elsewhere -- so it stays open,
  unchanged from prior sessions' own assessment.

- 2026-09-25 (fuzzing): phase 4 first pass. `tools/fuzz.py`: seeds from `crbasic/*.CR6` (with
  `.crb` siblings) and oracle-accepted `tests/corpus/chm/` files; 16 text-level mutation operators
  (delete/duplicate/swap line, rename identifier, replace numeric literal, add/drop/replace
  argument, keyword case, whitespace/comment/line-continuation insertion, insert a foreign
  statement, toggle `EndIf`/`End If`-style two-word forms, change a declared type, change a
  DataTable/DataInterval/Scan interval value), 1-2 per mutant, seeded RNG (`--seed`). Classifies
  `false_reject`/`false_accept`/`message_diff`/`mode_diff`/`crash`, plus a `hang` class added after
  a mutant (an oversized array dimension) wedged the tool's own worker pool indefinitely --
  `run_ours` now runs `compile_file` in a forked child under an 8s timeout. Deduplicates by a
  coarse signature and shrinks (bounded delta-debugging) each kept exemplar before saving it to
  `tests/corpus/fuzz/<logger>/<class>/<hash>.CR6` + a `.json` of both verdicts. `tools/fuzz_replay.py`
  re-checks saved reproducers standalone. Ran `--logger all` (cr6 1500 mutants, cr1000x/cr300/cr350
  300 each, seed 1); 248 reproducers saved, all 248 re-confirmed with `fuzz_replay.py` just before
  writing up. Full counts, methodology caveats (dedup granularity, a fuzzer-side comma-splitting
  gap in the argument operators, `--workers 4` contention occasionally misclassifying a fast case
  as `hang`), and findings grouped by root cause: `docs/fuzz-findings.md`. Did not touch
  `src/opencrbasic/**` (checker workstream owns it; this session only builds/runs the fuzzer and
  records findings for that workstream to act on).

- 2026-09-25 (checker round 2): resumed the phase-3 checker (previous session's own summary is the
  "later still" entry below). Grouped the CR6 corpus's remaining `verdict`/`mismatch` cases by the
  real compiler's own first message and worked the largest groups. Two architectural findings drove
  most of it, both written up in `docs/checker.md`: (1) `CR6Comp.exe` compiles in two passes --
  pass 2 (semantic/value checks, e.g. `instructions.py`'s channel-range check) only runs when
  pass 1 (parsing + symbol resolution) found nothing wrong *anywhere in the file*, oracle-confirmed
  by placing an unrelated pass-1 error both before and after an otherwise-invalid channel argument
  in a scratch probe; `checker/__init__.py` now gates `instructions.check` accordingly (a
  wiring-only change, `instructions.py` itself untouched) -- this alone fixed the largest single
  chunk of remaining diffs and also surfaced a likely latent bug in `instructions.py`'s own
  `VoltSe`/`VoltDiff` channel-range entries, documented but left for the owning workstream.
  (2) The real compiler's "echo the statement's own text" messages are the *raw* source line,
  comment-stripped only -- leading indentation and trailing whitespace both preserved, not trimmed
  as this checker previously assumed; fixed in `parser.py`'s `_segment_text`. New rules: a
  required-parameter-count check (`checker/instr_rules.py`, new file, not
  `checker/instructions.py`); `Public`/`Dim`/`Units`/`Const`/`Alias` each truncated mid-declaration
  across a CHM line-wrap now get their own correct message instead of the generic fallback
  (`structure.py`, `parser.py`). `tools/difftest.py --logger cr6`: `chm` 268/229/10 ->
  **310/188/9** (exact/verdict/mismatch); `crbasic/` stayed 16/16 exact throughout; re-verified
  after every change that every program the real compiler accepts (`their_exit == 0`) is `exact`
  for us, zero exceptions. Remaining top categories, all lower-value/higher-risk and deliberately
  not chased further this round (see `docs/checker.md` "Open questions"): the same "one diagnostic,
  then silence for the rest of the file" behaviour now confirmed broader than "before `BeginProg`"
  with still no discriminator found; a built-in-function-name/prose collision
  (`Expression expects ( : %s.`) whose naive fix would misfire because `data/functions.json`
  contains non-function entries (`AND`, `IS`, `TO`); a handful of single-instance
  instruction-specific checks. `python3 -m pytest -q`: 868 -> 915 passed (17 new unit tests for the
  rules above in `test_checker.py`/`test_parser.py`, the rest from corpus-driven cases that now
  pass), 0 failed, throughout.

- 2026-09-25 (multi-logger): CR1000X/CR300/CR350 support (`PLAN.md` "Target loggers"): per-logger
  checker lookups (`checker/terminals.py` + `data/<logger>/terminals.json` replace the CR6-only
  hard-coded `U1..U12` channel check `instructions.py` used to apply to every logger regardless of
  `profile`); `mode.py`'s Sequential/PipelineMode trigger lists moved to `data/<logger>/mode.json`
  (`Profile.mode_rules`), CR1000X's re-derived from a fresh Ghidra decompile of `cr1000xcomp.exe`
  (`docs/loggers.md`: structurally identical to CR6's mode-decision function, minus the 2-3
  instructions CR1000X doesn't have at all); per-logger unresolved-symbol message text
  (`Profile.unresolved_symbol_message`, CR300/CR350's `"{name} is not defined."` vs CR6/CR1000X's
  `"Invalid symbol in expression: {name}."`); `tests/test_profiles.py` (extension/`--logger`
  mapping for all four loggers). Found and fixed a shared-infrastructure bug along the way:
  `parser.parse_file`/`parse_text` always folded `Const`s against the **CR6** profile regardless of
  `--logger` (derived from the file extension only), which was silently feeding wrong symbol values
  into every non-CR6 checker run. Full evidence, per-logger channel/terminal ranges, and remaining
  gaps: `docs/loggers.md`.

  `tools/difftest.py --logger <l>` (exact/verdict/mismatch), before this session's start / after:

  | Logger | repo | chm |
  |---|---|---|
  | cr6 | 16/0/0 -> 16/0/0 (unchanged; chm 268/229/10 -> 310/188/9 is the concurrent phase-3 checker work, not this row) | |
  | cr1000x | 8/1/7 -> 8/5/3 | 224/213/70 -> 327/172/8 |
  | cr300 | 0/3/13 -> 0/8/8 | 92/335/80 -> 93/396/18 |
  | cr350 | 0/1/15 -> 0/6/10 | 95/330/82 -> 96/394/17 |

  False-reject count (we reject, they accept -- the worse failure mode) stayed 0 throughout for
  cr6/cr300/cr350; cr1000x has one, a pre-existing general parser gap unrelated to logger
  differences (`StructureType` bare-name members), `docs/loggers.md` for detail and follow-up.

- 2026-09-25 (later still): checker phase 3 first pass: `checker.py` split into a `checker/`
  package; `structure.py` (`BeginProg`/`Scan`/`If`/`Select` block matching and placement,
  undefined `Call` targets), `expressions.py` (type-code-driven undefined-symbol check, narrow by
  design), `instructions.py` (`U1..U12` channel range check), `warnings.py` (table declared but
  never called, independent of the concurrent `tables.py` work), `symbols.py` (declared-name
  collection); wired `tables_hook.attach` in. `tools/difftest.py --logger cr6`: `crbasic/` 14->16
  exact; `chm` 198/0/309 (exact/verdict/mismatch) -> 268/229/10. Full rules and oracle evidence:
  `docs/checker.md`. Two regressions caught and fixed during development (both documented in
  `docs/checker.md`): built-in *functions* (`Timer`, `SerialOpen`, ...) called as bare statements
  in `crbasic/Main.CR6`, and `StructureType` names used as a `Sample`/`Average` `DataType`
  argument.

- 2026-09-25 (later): background agents interrupted by a user interruption at 15:56 and relaunched;
  front end, type codes, TDF rules/generator completed; checker in progress.

- 2026-09-25: Wine oracle installed; all 16 `crbasic/*.CR6` compile under Wine with the
  warnings `STATUS.md` expects. Instruction table found at VA 0x60c320 (16-byte records).

- 2026-09-25: Ghidra decompile of all 10,137 functions; CLI options, compile driver, error
  reporter, and three data tables mapped. `-z` output is the PakBus TDF; `tdf.py` parses and
  re-serialises it byte-identically. Status table fields depend on the program (slow sequences);
  auto-allocated table sizes (`-1`) come from a memory-allocation rule not yet decoded.

- 2026-09-25: branch rebased onto `add-compilers` (CR1000X, CR300, CR350 and other compilers).
  All three stretch targets run under Wine. `extract_tables.py` generalised (symbol record stride
  52 or 64 bytes) and per-logger data extracted to `data/cr1000x|cr300|cr350/`.

- 2026-09-25: Front end (phase 2) written: `lexer.py` (tokens; leading-dot numbers, `&H`/`&B`,
  pointer sigils `!`/`@`), `source.py` (comment/continuation handling, `Include`/`IncludeSection`
  expansion with correct per-file line numbers and `Section` binding), `consteval.py` (constant
  folding with CRBasic's real operator precedence, including a non-textbook `AND`/`OR` rule and
  left-associative `^`, both oracle-verified), `parser.py` (full statement/expression grammar).
  `checker.py` added as the minimal placeholder the plan calls for (`mode.py` only). Verified
  quirks and the oracle evidence for each: `docs/frontend.md`. Test results and open gaps: see the
  phase-2 row above.

## Small-task queue (work method since 2026-09-27)

Rounds are now one small, verifiable item per agent (quota cut-offs lost work in long rounds).
Each item: oracle-probe the rule, implement with a unit test, then the acceptance gate — pytest
green, difftest zero false rejects on all four loggers, exact counts not lower, and no
`false_reject` line in `fuzz_replay.py` output other than `FIXED`/`changed: false_reject -> ...`
(grep for `still false_reject` too, not only `-> false_reject`) — and the coordinator commits it.
Tick items here as they land.

- [x] Prune fixed fuzz reproducers (`fuzz_replay.py --prune-fixed`)
- [x] CR6/CR1000X: `Duplicate Alias` / `Duplicate Alias Name: %s.` (already implemented in
      `structure.py`'s `_check_alias_dims` from the prior "Round 7 (partial)" session; this item
      re-confirmed it with fresh oracle probes on all four loggers, added the two missing unit
      tests, and wrote up `docs/checker.md`'s "Duplicate Alias" section that round never landed)
- [x] CR6/CR1000X: `Need closing )` shapes (oracle-confirmed `docs/checker.md` "Need closing )
      shapes": `Need closing )` itself was already fully covered by the prior Alias empty-parens
      item; this item implements the three sibling messages -- `No Matching, or out of place ) in
      expression: %s.` for an unclosed paren in the general expression grammar, `%s is missing the
      ) character.` for an unclosed `Public`/`Dim`/`Static` dimension list, `Incomplete expression:
      %s.` for an extra/unmatched `)` -- all identical on CR6/CR1000X, `parser.py`/`structure.py`)
- [x] CR6/CR1000X: `DataTable without EndTable.` (nested tables) -- oracle-confirmed identical on
      CR6/CR1000X/CR300 (`docs/checker.md` "DataTable/EndTable pairing"); already implemented from
      an earlier undocumented round, this item re-confirmed it with fresh probes and added the
      missing unit tests. `EndTable without DataTable or nested DataTables.` (`docs/messages.tsv`'s
      third string from the same function) was probed for but not reproduced by any shape tried.
- [x] CR6/CR1000X: `AddPrecise` argument rules -- `PrecisionVariable` rejects with `Invalid
  Variable or Array: %s.` when it has no genuine variable reference in it (literal/paren-constant/
  `Const`/`NAN`); shared by `MovePrecise`; absent on CR300/CR350 (`docs/instructions.md`).
- [x] All loggers: `Sub` call argument counts (`Call argument required.`, `Call to %s with %d
      parameters, requires %d arguments.`); `If`-condition names and arity; table sources; string
      `Alias` indices (`docs/checker.md` "If conditions, Sub arguments, table sources").
      `Function` call arity inside expressions is not checked yet.
- [x] All loggers: builtin function argument counts/types (`data/functions.json` argtypes) --
      oracle-confirmed `docs/checker.md` "Built-in function call arity": too few/too many arguments
      to a built-in function nested in an expression (`Expression expects , : %s.`/`No Matching, or
      out of place ) in expression: %s.`, `checker/expressions.py`'s `check_function_arity`, an
      oracle-verified per-function allow-list rather than a blanket `nargs` rule since several
      functions' real min/max diverges from `nargs`, and diverges again per logger for a few names).
      Argument-*kind* checks are not implemented: the oracle accepts every swapped-kind literal
      tried, so there is no "wrong kind" rejection to reproduce.
- [x] CR6 help-corpus wording: pass-1 specific messages vs generic out-of-place, `Invalid or
      missing parameter.` slice only (`docs/checker.md` "A required argument cut off by an
      unterminated string"): a required `expr_or_name` (type code 26) argument whose opening `"`
      never closes before the physical line ends -- `HTTPOut`'s own shape, oracle-confirmed
      (`httpout_1.CR6`/`webpagebeginwebpageend_1.CR6`, CHM corpus, plus a scratch probe) --
      `instr_rules._check_unterminated_string_param`, deliberately keyed to the type-code `kind`
      (`bool_expr`, code 7, e.g. `IF`'s condition, gets a *different* real message, `String without
      end quote`, unimplemented, out of scope). CR6 CHM exact 336 -> 338 (+2 on CR1000X too,
      CR300/CR350 unaffected). The other four message kinds in this item's title (`Expression
      expects (`, `Need '=' in units definition`, `Incomplete expression`, `No Matching, or out of
      place ) in expression`) are not attempted this round -- re-queued below.
- [x] `Expression expects (` for a bare function name in an assignment, call statement or
      instruction argument, all four loggers (`expressions.py`, `docs/checker.md` "Expression
      expects ( (bare function name)"): 27/33 help-corpus lines now match; `Incomplete
      expression` and `No Matching )` are covered by the pass-1 scan items below.
- [x] CR6/CR1000X: constant-family instruction arguments resolve in pass 1; failed `Const`s are
      undeclared (all loggers); diagnostics print in expanded-source order (`docs/checker.md`
      "Pass-1 instruction arguments"). CR1000X `crbasic/` 9 -> 16/16 exact; CR6 chm 408 -> 422.
- [x] Declaration expressions: undefined names / variables in array dimensions, `Alias` indices and
      initializers; `Variable Type %s not supported` (`docs/checker.md` "Declaration
      expressions"). Fuzz replay: 105 saved false accepts now rejected.
- [x] Unmatched `EndIf`/`EndSelect`/`Next`/`Loop` (all loggers, `docs/checker.md` "Unmatched block
      ends").
- [x] `Units` targets, unknown `CallTable` targets, global redeclarations; `NextScan` block-stack fix
      (fuzz-found false reject) (`docs/checker.md` "Units targets, CallTable targets,
      redeclarations"). chm exact cr6 425, cr1000x 433.
- [x] CR300/CR350: `<CPU:file>` include prefix; declaration errors start the `Too many
      parameters` cascade (`docs/loggers.md` "Declaration errors taint"). chm exact cr300 254,
      cr350 264.
- [x] One-off rules: empty-parentheses calls, `DataTable` too many parameters, duplicate
      `DataInterval`, menu placement, stray `NextSubScan` (`docs/checker.md` "One-off rules").
- [x] `No BeginProg in Program.` after pass 2, on the last physical line; `Alias` trailing text
      (fuzz-found false reject); CR300/CR350 single-pass reporting and undeclared targets.
- [x] Fuzz re-measurement at `1b4d4fd` (300 mutants each): cr6 seed 42 false_accept 15 (30 this
      morning), seed 46 17; cr1000x seed 46 26 with 1 false reject (`Alias` trailing text, fixed
      next); cr300 22; cr350 16. Reproducer replay after the fixes: 619 of 1469 exact, no false
      rejects.
- [x] CR300/CR350 `Dimensions not declared`, cascade token echo, late table checks; control
      statements before `BeginProg` (all loggers). CR300/CR350 `crbasic/` 0 -> 5/16 exact, chm
      exact cr300 268, cr350 278; replay 732 of 1469 exact, no false rejects.
- [ ] CR6 help-corpus wording, remaining: `Need '=' in units definition`
- [x] Fuzz re-measurement at `e0a92cc` (300 mutants each): cr6 seed 42 false_accept 25 (was 30),
      seed 45 31; cr1000x seed 42 21 (was 27) with 1 false reject, `If`/`NextScan`/`EndIf`, fixed
      in the next commit; cr300 17 (was 18); cr350 17 (was 22). Reproducer replay after the fix:
      215 of 1469 now exact, 154 former false accepts now rejected, no false rejects.
- [x] Reachability warning: `Do`/`Loop`/`FOR` names; an unreachable `If`/`Select` silences it.
- [x] Unclosed blocks at end of program move after pass 2; `FOR without NEXT.`/`DO without LOOP.`
      added (`docs/checker.md` "Unclosed blocks"). chm exact cr6 426, cr1000x 435, cr300 251,
      cr350 261; replay 239 of 1469 exact, no false rejects.
- [ ] All loggers: `%s already defined as an internal function.` for a `Public`/global `Dim`
      named like a built-in function (oracle: `Public X, exp` on CR6)
- [x] Echo quirk: trailing newline in echoed statements (closing `.` on the next line) --
      `Alias`/`Units`'s own "target parsed, `=` runs off the end of the line" sub-case
      (`docs/checker.md` "Echo quirk", oracle-confirmed with two fresh Wine probes plus
      `realtime_1.CR6`): the echo is the target's own source text plus a literal embedded `\n`
      before the closing `.` (`Need '=' in alias definition: rTime(1)\n.`), distinguished in
      `parser.py` from the fully-empty-target case by a separate `Unparsed` reason.
      `realtime_1.CR6`'s own first diagnostic now matches the oracle exactly, but the file stays
      `verdict` (later lines depend on unimplemented, unrelated messages) -- this fix alone moves
      no `chm` file `verdict -> exact` by itself (cr6/cr1000x's 336 -> 338 / 352 -> 354 this round
      is the concurrent `instr_rules.py` item above, measured on the same shared tree). Other
      "ran off the end of the line" quirks (`Data`-keyword/prose collision, `docs/checker.md`
      "Open questions") remain unreproduced. New tests: `tests/test_echo_quirk.py`.
- [ ] CR300/CR350: next largest fuzz false-accept group (repeat)
- [x] CR6/CR1000X 16-file "keyword-resync" group: investigated, root cause is the pass model, not
      resynchronisation (`docs/checker.md` "Pass-1 fallback: statements that start with a known
      word"). Implementation split into the four items below.
- [x] CR6/CR1000X: fallback for a statement whose first token is known is pass 1 and suppresses
      pass 2 -- implemented as a left-to-right pass-1 scan (`structure.py` `_pass1_scan`,
      `docs/checker.md` "The scan", `tests/test_pass1_scan.py`). chm exact cr6 381 -> 397,
      cr1000x 403 -> 419 (measured alone in a clean worktree), zero false rejects.
- [x] Pass-1 scan, second step: commas outside a call, statements starting with `(`, function
      names without `(` (chm exact cr6 397 -> 408, cr1000x 419 -> 430).
- [ ] CR6/CR1000X: remaining pass-1 scan shapes (`docs/checker.md` "The scan", "Not done"):
      index parentheses at statement start, unclosed call statement (`Sin(1`), `Scan bar`,
      `Exit bar`, `Could not find %s.`
- [x] All loggers: a line of only `(`, `)`, `,` compiles silently (was a false reject; oracle-
      confirmed on all four; `parser.py`, `tests/test_paren_only_line.py`). chm exact cr6 378 ->
      380, cr1000x 400 -> 402, cr300/cr350 unchanged.
- [x] CR6/CR1000X: `No Matching, or out of place ) in expression` is pass 1 when the first token
      is known and every name resolves; otherwise pass 2 as the generic fallback (unknown first
      token) or `Invalid symbol in expression` (later unknown name) (`structure.py`,
      `profiles.known_first_word_pass1`, `tests/test_unclosed_paren_pass.py`). chm exact cr6 381,
      cr1000x 403. Not covered: an unclosed call statement (`Sin(1`, `foo(1`), which our parser
      accepts as a `Call`; CR300/CR350 (report it on every line, with `x is not defined.` lines).
- [ ] CR6/CR1000X: `Could not find %s.` for `name.`/`name.x` (pass 2, before the fallback)
- [x] Re-measure fuzz false-accept rates (2026-09-27, tree at `69b05f3`, `tools/fuzz.py --count
      300`, false_reject 0 in every run): cr6 seed 42 false_accept 30 (exact 235, message_diff
      35), seed 43 26 (241/33), seed 44 23 (250/27); cr1000x seed 42 27 (239/33, 1 hang); cr300
      seed 42 18 (227/53, 2 hangs); cr350 seed 42 22 (223/55). Seeds 45-46 not run. `fuzz.py
      --out` outside the project now works (it crashed saving reproducers before).

- [x] Scan placement (all loggers, `docs/checker.md` "Scan placement",
      `tests/test_scan_placement.py`): `CallTable` outside `Scan..NextScan` (`Table with
      DataInterval must be called within Scan..NextScan.`, `Cannot autoallocate memory for
      DataTable %s called outside of Scan, e.g., called by Sub.`); `NextScan with no Scan.` moved
      out of pass 1. CR6/CR1000X: pass 2 (a stray `NextScan` ends it). CR300/CR350: a late stage
      after the table `Reps` checks, where the early warnings now also run (`DO without LOOP.`
      keeps an fnotch warning). File-less diagnostics now sort as main-file lines. difftest
      unchanged, zero false rejects; replay 732 -> 773 of 1469 exact, no false rejects.
- [x] Fuzz seed 47 (tree at `b36d911` plus the pass-2 half of the item above, `--count 300`,
      false_reject 0): cr6 false_accept 13 (exact 250, message_diff 34), cr1000x 12 (265/22).
      cr300/cr350 in the row below.

- [x] Instructions before `BeginProg` (all loggers, `docs/checker.md` "Instructions before
      BeginProg", `tests/test_before_beginprog.py`): per-logger lists measured with
      `tools/probe_before_beginprog.py` (from the decompiled placement loop); explicit `Call`;
      statement order instead of line numbers (an included file inside `Scan` was at risk); CR300/
      CR350 report all of it in the late walk. Replay 773 -> 790 of 1469 exact, chm exact cr300
      268 -> 269, cr350 278 -> 279, zero false rejects.
- [x] Fuzz seed 47, cr300 and cr350 (same tree as the cr6/cr1000x row above): cr300
      false_accept 12 (exact 250, message_diff 38); cr350 12 (238/49) and 1 false reject
      (`Scan(999999999999,Sec)` against a daily table), fixed in the row below.
- [x] Scan and table interval limits (all loggers, `docs/instructions.md` "Scan and table interval
      limits", `tests/test_scan_interval_limits.py`): the divisibility message switches to whole
      seconds for tables of 3601 s or more (decompiled `FUN_004f6540`); `Maximum Scan interval is
      1 Day.`; intervals past `INT32_MAX` are not checked. Replay 792 of 1469 exact.

- [x] Stray control statements (all loggers, `docs/checker.md` "Stray control statements",
      `tests/test_stray_controls.py`): `Else`/`ElseIf`/`Case`/duplicate `Else`, `Exit Do/For/Sub`,
      `ExitScan`/`ContinueScan` outside their blocks; `EndSelect w/o Select` moved to pass 1
      (parser, per the decompilation). Replay 803 of 1469 exact, zero false rejects.

- [x] `PulseCount`/`Quadrature`/`TimerInput` outside a `Scan` (all loggers, `docs/checker.md`
      "Measurements that must be inside a Scan"): ends pass 2 / the CR300 late walk, no
      reachability warning. The SDM and PipeLine-mode variants are conditional on the mode in the
      decompilation and not done.

- [x] CR300/CR350 undefined table-output source, late (`docs/loggers.md` "Undefined table
      sources"); late-stage errors no longer start the cascade. Replay 812 of 1469 exact.

- [x] One-line `If` statements checked like ordinary statements (`docs/checker.md` "One-line
      If statements"). Replay 818 of 1469 exact, chm exact cr6 428, cr1000x 437.

- [x] Typed dimensions, member index names, initializer-bounds warning, `CallTable` excess
      arguments, CR300 `Sub` cascade rewrite (`docs/checker.md` "Declaration details and
      CallTable arguments"). Replay 822 of 1469 exact.

- [x] `TimeIntoInterval`/`IfTime` argument checks (`checker/intervals.py`, `docs/checker.md`
      "TimeIntoInterval arguments"). Replay 825 of 1469 exact.

- [x] SDM address range (`docs/instructions.md` "SDM addresses"). Replay 826 of 1469 exact.

- [x] Undefined names in `Sub` call arguments (`docs/checker.md` "Sub call arguments").
      Replay 829 of 1469 exact.

- [x] Code-4 (`const_int_kw`) instruction arguments (`docs/type-codes.md` "Code 4 arguments in
      instructions"). Replay 833 of 1469 exact.

- [x] Duplicate `Units`, missing-include drive prefix, code-4 integer-token rule
      (`docs/checker.md` "Duplicate Units, missing includes"). Replay 836 of 1469 exact.
- [x] Fuzz seed 48 (tree at `420e351`, `--count 300`, false_reject 0): cr6 false_accept 16
      (exact 264), cr1000x 12 (270), cr300 7 (253).

- [x] Display menu structure (`docs/checker.md` "Display menus"). Replay 841 of 1469 exact.

- [x] Unterminated strings by line shape; `ESSInitialize` raw argument (fixes the seed-48 CR350
      false reject) (`docs/checker.md` "Unterminated strings"). Replay 847 of 1469 exact.
- [x] Fuzz seed 48, cr350 (tree at `420e351`): false_accept 17 (exact 247), 1 false reject
      (`ESSInitialize(public")`), fixed in the row above.

- [x] Seed-47/48 reproducers added to `tests/corpus/fuzz` (316 files, 1785 reproducers in all).
      The replay had shown 6 `still false_reject` reproducers from the original corpus, missed
      because the gate only grepped `-> false_reject`; `ESSInitialize`'s raw argument fixed 5 and
      `SNMPVariable(MyTemp, ...)` declaring `MyTemp` the last (`checker/symbols.py`). Replay 885
      of 1785 exact, no false rejects.

- [x] Subscript counts beyond the declared dimensions (`docs/checker.md` "Subscript counts").
      Replay 893 of 1785 exact.

- [x] `Too many parameters` for table output (`Sample`, `Average`, `Maximum`, `Minimum`,
      `Totalize`, `StdDev`, `Median`) and `SendTableDef` (`instr_rules._TOO_MANY_EXTRA`,
      `tests/test_table_too_many.py`). Replay 900 of 1785 exact.

- [x] User `Function` argument counts, `Do`/`Loop` conditions, CR300 cascade on `Function`
      headers (`docs/checker.md` "Function argument counts"). Replay 904 of 1785 exact.

- [x] CR300/CR350 tainted declarations are unregistered (`docs/loggers.md` "Tainted
      declarations"). chm exact cr300 273, cr350 283; replay 915 of 1785 exact.

