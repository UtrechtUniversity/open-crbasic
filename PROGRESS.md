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

## Next steps

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
