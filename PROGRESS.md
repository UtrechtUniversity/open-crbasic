# opencrbasic progress

Living state. Update after every milestone. Plan: `PLAN.md`.

Proposed CR6 language-server work has a separate [plan](LSP_PLAN.md) and
[resume ledger](LSP_PROGRESS.md); compiler measurements and worker policy remain here.

## Environment setup (needed to resume)

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

## Status (2026-10-11, end of session; resume in a clean session)

Measured on `claude/laughing-franklin-08z9vo` at `b844b7b9` (plus the docs commit that adds this
text). Exact = equal exit code and normalized ordered stdout. False reject = real compiler
accepts, opencrbasic rejects: **none observed** in any differential, replay or fixture run.

| Gate | Result |
|---|---|
| `uv run --no-sync pytest -q` (strict: checker exceptions fail tests) | 21,701 passed, 311 skipped |
| `tools/difftest.py --group all`, crbasic/ (16) exact | CR6 16, CR1000X 16, CR300 6, CR350 6 (the rest are matching-verdict rejections of CR6 programs) |
| help corpus (507) exact / msg differs / verdict mismatch / oracle err | CR6 502/4/0/1, CR1000X 502/4/0/1, CR300 505/0/0/2, CR350 505/0/0/2 |
| `tools/fuzz_replay.py` (2,251 reproducers) | 1,843 exact, 4 false accepts, 0 false rejects, 376 message diffs, 12 mode diffs, 16 oracle errors (`scratch/replay-r12.json`) |
| `tools/tdftest.py --group all` | repo 16/16; CHM (208 CR6-accepted) TDF 207/208, table list 207/208 (only `goestable_1` field name) |
| `-t` [Times] (`scratch/timing_harness.py`, untracked) | repo 16/16, CHM 506/507 (only `pointervariable_2`, a real-compiler crash) |
| implementation exceptions (difftest/replay class) | 0 |
| `cd ../scripts && uv run --no-sync verify.py` (env `$HOME/.uv-envs/firnmelt-scripts`) | 2026-10-11: `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` |

Reference faults (`pointervariable_2.CR6` on all loggers, `strcomp_3.CR6` on CR300/CR350, 16
replay reproducers) make the gates exit nonzero; they are counted separately, never as verdicts.
Every fixture under `tests/fixtures/` records real-compiler output; recheck one with
`uv run --no-sync tools/fixture_audit.py tests/fixtures/<x>.json` (must report 0 differing).

Remaining help-corpus diffs: CR6/CR1000X `mqttpublishtable_1`, `clockset_1`, `sprintf_1`,
`strcomp_3` (pass-1/pass-2 classification of certain arguments; see docs/checker.md "Round 12
CR6/CR1000X"). Remaining fuzz false accepts: CR300/CR350 storage limit (2, `docs/tdf-rules.md`
"Storage limit"), Scan-interval overflow autoallocate (1), `CDM_Delay` >60 min message count (1).

| Phase | State |
|---|---|
| 0 Oracle + corpus | done: `tools/oracle.py`, `tools/difftest.py`, 507 CHM examples |
| 1 Binary RE | done and ongoing: `docs/binary.md` (functions, message tracer), Ghidra 12 dumps, CLI, type codes, messages, modes, TDF rules, signature, logger differences |
| 2 Front end | implemented; small residual gaps (`docs/frontend.md`, `docs/checker.md`) |
| 3 Checker | near parity on the help corpus; long tail of message ordering in fuzz reproducers |
| 4 Fuzzing | `tools/fuzz.py`, `tools/fuzz_replay.py`; 2,251 saved reproducers; seeds used: 0, 1, 42-54, 100, 201-208, 301-308 (`docs/fuzz-findings.md`) |
| 5 Outputs | TDF/table list exact except one name; `-t` exact except one crash; exact auto-size model; CDM binary image not attempted |
| 6 Complete parity gate | incomplete: 376 fuzz message diffs, 4 false accepts, 4 CR6/CR1000X help diffs, storage limit, CDM image |

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
- **Usage pacing.** Check `mcp__ccd_session_mgmt__get_usage`; tell workers to wrap up at ~85% of
  the 5-hour window (they otherwise get cut off mid-task; a cut-off branch is recovered by saving
  uncommitted work as a WIP commit and verifying the last real commit).
- **CRBasic Field Notes artifact** (https://claude.ai/artifact/FngiuvrYeHto3WivhRouX4; source
  `scratch/crbasic-field-notes.html`, Codex summary `scratch/crbasic-lessons-source.md`): update
  ONLY when the human asks; publish with `url` to keep the link.

## Resume here (2026-10-11)

- No rounds are scheduled; no worktrees exist; no WIP is pending on main. Old worker branches
  (`ocb-*`) are all integrated except: `ocb-ranges` (WIP `3228f99`, unverified CDM range work;
  compare with current CDM rules or discard).
- Not yet reviewed by gpt-6.1-sol: rounds 10-12 after `75b94c1a` (`git log 75b94c1a..HEAD`);
  `scratch/codex/review-round11.md` covers `46f79c5d..75b94c1a` and has NOT been acted on yet.
- Local untracked state worth keeping: `scratch/re/` (Ghidra dumps, patched exes),
  `scratch/tdf-sweep/` (allocator sweep data), `scratch/timing_harness.py` (`-t` comparison),
  `scratch/mkfixture.py`, `scratch/codex/` (reviews, worker brief), `scratch/replay-r12.json`.

## Next steps

1. Act on `scratch/codex/review-round11.md` (one Haiku or Sonnet worker, confirm each finding
   against the oracle first), then start a gpt-6.1-sol review of `75b94c1a..HEAD`.
2. Remaining CR6/CR1000X help diffs (`mqttpublishtable_1`, `clockset_1`, `sprintf_1`,
   `strcomp_3`): which pass reports `MQTTPublishTable(MQTT QoS, 5)`, `&` after a comma, `%`- or
   float-led continuation lines. Use the tracer (which stage emits each message) instead of
   probing blind (Sonnet + RE tools).
3. Fuzz message diffs (376): rank by first differing line (`scratch/replay-r12.json`), prefer
   shared ordering/gating rules; the tracer settles ordering questions.
4. Remaining false accepts: storage limit (CR300/CR350; evidence in `docs/tdf-rules.md`; likely
   readable from the decompiled allocator now), `CDM_Delay` >60 min count, Scan-interval overflow
   autoallocate.
5. Fresh fuzz seeds (next unused: 401+) to find new families; fix false rejects first.
6. Outputs: CDM binary image (`-b`), `goestable_1` field name, timing for variable arguments and
   Reps > 1000.
7. Before publishing anything outside this repository, settle `docs/legal.md`.
