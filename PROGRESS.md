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

## Status (2026-10-12, end of round 13; resume in a clean session)

Measured on `claude/laughing-franklin-08z9vo` at `18f1b774` (plus the docs commit that adds this
text), on the macOS M4 machine (Wine 11.17, CPython 3.13). Exact = equal exit code and normalized ordered stdout. False reject = real compiler
accepts, opencrbasic rejects: **none observed** in any differential, replay or fixture run.

| Gate | Result |
|---|---|
| `uv run --no-sync pytest -q -n 8` (strict: checker exceptions fail tests) | 25,674 passed, 311 skipped, 0 failed |
| `tools/difftest.py --group all`, crbasic/ (16) exact | CR6 16, CR1000X 16, CR300 6, CR350 6 (the rest are matching-verdict rejections of CR6 programs) |
| help corpus (507) exact / msg differs / verdict mismatch / oracle err | CR6 505/1/0/1, CR1000X 505/1/0/1, CR300 505/0/0/2, CR350 505/0/0/2 |
| `tools/fuzz_replay.py` (2,251 reproducers) | 1,847 exact, 4 false accepts, 0 false rejects, 372 message diffs, 12 mode diffs, 16 oracle errors (`scratch/replay-r13.json`) |
| `tools/tdftest.py --group all` | 2026-10-12 unchanged: table list 207/208 (only `goestable_1`). repo 16/16; combined repo+CHM 208 compared (16 repo + 192 CHM) TDF 207/208, table list 207/208 (only `goestable_1` field name), so CHM 191/192. Of the 507 CHM inputs: 192 compared, 308 rejected by the real compiler, 6 with no non-empty LST seen by the harness (it conflates absent and empty), 1 oracle fault. Label corrected 2026-10-11 from host evidence `docs/lsp-rounds/host-tests/2026-10-11-602f6462/RESULTS.md` |
| `-t` [Times] (`scratch/timing_harness.py`, untracked, lost with the old machine) | 2026-10-11: repo 16/16, CHM 506/507 (only `pointervariable_2`, a real-compiler crash); not re-run in round 13 |
| implementation exceptions (difftest/replay class) | 0 |
| `cd ../scripts && uv run --no-sync verify.py` (env `$HOME/.uv-envs/firnmelt-scripts`) | 2026-10-12 (after round 13): `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` |

Reference faults (`pointervariable_2.CR6` on all loggers, `strcomp_3.CR6` on CR300/CR350, 16
replay reproducers) make the gates exit nonzero; they are counted separately, never as verdicts.
Every fixture under `tests/fixtures/` records real-compiler output; recheck one with
`uv run --no-sync tools/fixture_audit.py tests/fixtures/<x>.json` (must report 0 differing).

Remaining help-corpus diff: CR6/CR1000X `strcomp_3` only: `!name` on a non-pointer variable is a
false accept (docs/checker.md "Round 13 analysis"; pass placement unresolved, needs the tracer). Remaining fuzz false accepts: CR300/CR350 storage limit (2, `docs/tdf-rules.md`
"Storage limit"), Scan-interval overflow autoallocate (1), `CDM_Delay` >60 min message count (1).

| Phase | State |
|---|---|
| 0 Oracle + corpus | done: `tools/oracle.py`, `tools/difftest.py`, 507 CHM examples |
| 1 Binary RE | done and ongoing: `docs/binary.md` (functions, message tracer), Ghidra 12 dumps, CLI, type codes, messages, modes, TDF rules, signature, logger differences |
| 2 Front end | implemented; small residual gaps (`docs/frontend.md`, `docs/checker.md`) |
| 3 Checker | near parity on the help corpus; long tail of message ordering in fuzz reproducers |
| 4 Fuzzing | `tools/fuzz.py`, `tools/fuzz_replay.py`; 2,251 saved reproducers; seeds used: 0, 1, 42-54, 100, 201-208, 301-308 (`docs/fuzz-findings.md`) |
| 5 Outputs | TDF/table list exact except one name; `-t` exact except one crash; exact auto-size model; CDM binary image not attempted |
| 6 Complete parity gate | incomplete: 372 fuzz message diffs, 4 false accepts, 1 CR6/CR1000X help diff, storage limit, CDM image |

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

## Resume here (2026-10-12)

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
- Pending branch `ocb-r13-subscope` (`c6da3a4d`, WIP, unverified, NOT integrated): Dim/parameter
  local to a Sub/Function is undefined elsewhere (false accept, observed on all four loggers) and
  routine-body Public/Const in duplicate checks (review A9). Needs its fixture (cases and helper in
  `scratch/r13-helpers/subscope/`), fixture_audit, full gates on all four loggers; it adds
  rejections, so check false rejects carefully.
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

1. Finish `ocb-r13-subscope` (see Resume here), then review rounds 13 with fresh-context Opus
   reviewers (`git log e39271da..HEAD`).
2. Known false rejects found in round 13, not yet fixed (all pre-existing): logical-assignment
   readings other than a lone name before the logical word (`x > 2 And y = 1`, `(x > 2) And y = 1`,
   `1 And y = 1`, `-x And y = 1`, `Not x And y = 1`, `x And y(1) = 1`; `parser._parse_assignment_or_call`);
   `If Abs(X Then` (compiles on the real compiler).
3. Known false accepts: `!name` on a non-pointer variable (`strcomp_3`); `X AND Status.Nope = 1`
   and `X = Status.Nope`/`X.dat` (no Status/table field list); scalar Sub parameter indexed as an
   array; some divide-by-zero SplitStr forms on CR300/CR350; `RainFlowSample` source check.
4. Fuzz message diffs (372): rank by first differing line (`scratch/replay-r13.json`).
5. Remaining fuzz false accepts: storage limit (CR300/CR350), `CDM_Delay` >60 min count,
   Scan-interval overflow autoallocate; `crbc -z` traceback on the storage-limit programs
   (compiler item 2).
6. LSP cost on the CLI path: provenance built on every compile (+7% time, +49% peak memory on
   `Main.CR6`; review C F1); optional flag the CLI leaves off.
7. Fresh fuzz seeds (next unused: 401+); outputs: CDM binary image (`-b`), `goestable_1` field
   name, timing for variable arguments and Reps > 1000.
8. Publishing is not planned: internal use only (human decision 2026-10-11, `docs/legal.md`).
