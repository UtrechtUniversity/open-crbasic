# opencrbasic progress

Living state. Update after every milestone. Plan: `PLAN.md`.

Proposed CR6 language-server work has a separate [plan](LSP_PLAN.md) and
[resume ledger](LSP_PROGRESS.md); compiler measurements and worker policy remain here.

## Environment setup (needed to resume)

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

## Status (2026-10-12, end of round 15; resume in a clean session)

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

## Work method

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

## Round 19 (2026-10-17; resume here)

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

## Next steps (round 20)

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

## Round 18 (2026-10-16)

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

## Next steps (round 19, done or carried into round 20)

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

## Round 17 (2026-10-16)

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

## Next steps (round 18, done or carried into round 19)

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

## Resume here (2026-10-13, round 16 interrupted by machine shutdown)

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

## Next steps (round 17)

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

## Resume here (2026-10-12, after round 15)

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

## Round 14 notes (2026-10-12)

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

## Compiler items found by the LSP work (recorded 2026-10-11, not executed)

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

## Next steps (round 16, superseded by "Next steps (round 17)" above)

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
