# opencrbasic progress

Living state. Update after every milestone. Plan: `PLAN.md`.

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

## Status (2026-10-11, after round 11)

Measured on `claude/laughing-franklin-08z9vo` at the commit that adds this text. Exact = equal
exit code and normalized ordered stdout (not byte parity). False reject = real compiler accepts,
opencrbasic rejects: **none observed** in any differential, replay or fixture run.

| Gate | Result |
|---|---|
| `uv run --no-sync pytest -q` (strict: checker exceptions fail tests) | 20,439 passed, 311 skipped |
| `tools/difftest.py --group all`, crbasic/ (16) exact | CR6 16, CR1000X 16, CR300 6, CR350 6 (the rest are matching-verdict rejections of CR6 programs) |
| help corpus (507) exact / msg differs / verdict mismatch / oracle err | CR6 500/6/0/1, CR1000X 498/8/0/1, CR300 485/20/0/2, CR350 485/20/0/2 |
| `tools/fuzz_replay.py` (2,251 reproducers) | 1,819 exact, 11 false accepts, 0 false rejects, 389 message diffs, 16 mode diffs, 16 oracle errors (`scratch/replay-r11.json`) |
| `tools/tdftest.py --group all` | repo 16/16; CHM (208 CR6-accepted) TDF 207/208, table list 207/208 (exact auto-size model from the decompiled allocator, `docs/tdf-rules.md` §10; remaining `goestable_1` field name); `-t` [Times] (harness `/private/tmp/claude-501/tw/h7.py`, not in repo) repo 16/16, CHM 506/507 |
| implementation exceptions (difftest/replay class) | 0 |
| `cd ../scripts && uv run --no-sync verify.py` (needs `UV_PROJECT_ENVIRONMENT=$HOME/.uv-envs/firnmelt-scripts`, not the opencrbasic env) | 2026-10-03: `BUILD  host tests 119 pass \| CRBasic 16/16 compiled, 6 warning(s) \| repository pass` |

Reference faults (`pointervariable_2.CR6` on all loggers, `strcomp_3.CR6` on CR300/CR350, 18
replay reproducers) make the gates exit nonzero; they are counted separately, never as verdicts.
Every fixture under `tests/fixtures/` records real-compiler output; recheck one with
`uv run --no-sync tools/fixture_audit.py tests/fixtures/<x>.json` (must report 0 differing).

| Phase | State |
|---|---|
| 0 Oracle + corpus | done: `tools/oracle.py`, `tools/difftest.py`, 507 CHM examples (many invalid on purpose or by extraction; they exercise error paths) |
| 1 Binary RE | done for current needs: CLI (`docs/cli.md`), exe layout and tables (`docs/binary.md`, `tools/extract_tables.py`), type codes (`docs/type-codes.md`), messages (`docs/messages.tsv`), mode rule (`docs/modes.md`), TDF rules (`docs/tdf-rules.md`), ProgSignature (`signature.py`), per-logger differences (`docs/loggers.md`) |
| 2 Front end | implemented; residual parsing/diagnostic gaps remain: lexer, include expansion, constant folding, `#If`, parser (`docs/frontend.md`) |
| 3 Checker | usable: structure/placement, declarations, Units/Const/Alias, parameter counts, channel ranges per logger, Scan/DataInterval ranges, array limits, warnings (table never called, never reached, background calibration, fnotch), include-file prefixes, 4 KB message buffer, general expression-tree name resolution (`Invalid symbol in expression`/`%s is not defined`, per-logger echo/ordering rules, `NAN`/`INF`/system-table predefined symbols), the constant-only-family "rejects a variable" check, and the two-pass/pass-2-cutoff model (the generic "Expr"/`Call` fallback and before-`BeginProg` placement are now pass 2, not pass 1) (`docs/checker.md`, `docs/instructions.md`). Gaps: most instruction-specific value/type checks (per-instruction tables in `var_types.py`, `measure.json` `const_ranges`, `instr_measure._DEST_*`; no uniform per-type-code rule holds), remaining placement shapes |
| 4 Fuzzing | `tools/fuzz.py`, `tools/fuzz_replay.py`; 1,949 saved reproducers (`tests/corpus/fuzz/`), 1,200 exact (`docs/fuzz-findings.md`) |
| 5 Outputs | result line, mode (verified 16/16), warnings, ProgSignature (`-s`, verified on 5 programs), `-l` table list byte-identical 16/16, `-z` TDF byte-identical **16/16** (`tables.py`'s auto-size formula, §10, fully decompiled and oracle-exact as of 2026-09-26 -- was 8/16, the other 8 within ~3%); `-p`/`-i` preprocessed source byte-identical 16/16 repo + 98/100 CHM sample (`preprocess.py`); `-t` `[Times]` byte-identical on all 16 repo programs, 91/100 CHM sample (narrow, oracle-verified instruction set: `Scan`/`PanelTemp`/`PortSet`/`Sw12`/`Delay`(mSec)/`TCDiff`(Reps=1), `timing.py`); `-b` CDM image: "No CDM devices" message implemented and correct for every program in this repository (none use CPI/CDM hardware), binary image format not attempted; `-m` decompiled, confirmed no observable effect; `-u` **implemented** (`checker/unused.py`, wired through `compiler.compile_file`/`cli.py`, not through `checker.check`): `%s declared but not used.` for an unreferenced `Public`/`Dim`/`Sub`-or-`Function`-parameter, and for an unreferenced `Sub` or `Function` itself; oracle-confirmed identical across all four loggers; byte-identical on all 16 `crbasic/` programs with `-u` (`tests/test_unused.py`) |
| 6 Complete parity gate | incomplete: known diagnostics, acceptance, option/artifact gaps remain on all four profiles |

History, older measurements and the per-chunk log: `docs/progress-history.md`.

## Work method (2026-10-02/03)

- One chunk = one observed rule family: probe all four loggers (incl. accepted boundaries) with
  `tools/probe.py` (template with `$BODY$` + cases file; prints `==`/`!=` real vs ours, cached
  oracle), implement data-driven in the existing passes, fixture-backed test, docs section, then
  full pytest + four-logger difftest + replay + fixture audit. Commit each chunk, single-line
  message.
- **Human instruction (2026-10-03): execute chunks of work with Sonnet-class Claude subagents**
  (Agent tool, `model: "sonnet"`, one family per agent, in its own worktree as below). The
  coordinating session plans, reviews, integrates and verifies; it does not implement chunks
  itself.
- **Human decision (2026-10-10): decompilation and debugger tracing of the real compilers are
  approved** (internal project, not published; licence not a concern). Ghidra + Temurin JDK 21
  installed via Homebrew by the human (`brew install ghidra`, a formula: headless analyzer
  `/opt/homebrew/Cellar/ghidra/<ver>/libexec/support/analyzeHeadless`, GUI `ghidraRun`; JDK
  `/Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home`). Use them to find rules quickly (static decompile of
  CR6Comp/cr300comp outside git; error-origin tracing via `winedbg` breakpoints on the error
  reporter `0x5089c0` in CR6Comp, see `docs/binary.md`). Every rule is still verified by oracle
  probes and fixtures; decompiled code is never copied into `src/` (`docs/legal.md`).
- **Human instruction (2026-10-11): trial Haiku 5.5 (Agent tool `model: "haiku"`) for easy and
  medium sub-tasks** (rebases/integration of clean branches, small review findings with a given
  reproducer, fixture/docs chores, single well-scoped families). Keep Sonnet for hard families,
  reverse engineering and anything needing rule discovery. Review Haiku output for quality (all
  gates plus a spot-check of rules and fixtures against the oracle) and record the verdict here.
  **Verdict after round 11 (3 Haiku workers): good enough for easy/medium work; keep using it.**
  Integration of 7 verified commits: correct picks, green after each, stopped cleanly on a real
  design conflict instead of guessing (handed to Sonnet). Round-9 review: 6/7 findings confirmed
  against the oracle and fixed (incl. an internal crash), 1 correctly reported "not confirmed".
  Small items: fixed a false reject, corrected a mis-stated rule from probes, deliberately
  under-modelled a mixed case. All fixtures re-audited by the coordinator: 0 differing; no
  regressions in difftest. One defect: it amended cherry-picked commit messages, replacing the
  original authors' trailers (now forbidden in the brief). Keep Sonnet for rule discovery,
  reverse engineering and design conflicts.
- Orchestration: Opus coordinates; workers get disjoint families in their own git worktree
  (`git worktree add -b <br> .claude/worktrees/<br> claude/laughing-franklin-08z9vo`, then
  `ln -s <main>/opencrbasic/.oracle-cache` into it), and are integrated with `git cherry-pick`
  (never merge commits). Doc conflicts from parallel appends: keep both sides. Code conflicts:
  resolve by hand (a keep-both resolver once produced a duplicated `_arity_message`).
- Worktree gotcha: the shared uv env's editable install imports the MAIN checkout. Inside a
  worktree always `export PYTHONPATH=$PWD/src` (from `opencrbasic/`); difftest/tdftest insert
  their own path.
- Codex CLI: use ONLY `gpt-6.1-sol` (human instruction 2026-10-04; API slug from
  `~/.codex/models_cache.json`) for read-only reviews of each merged batch (human
  instruction 2026-10-03: the Codex 5-hour window exhausts quickly; implementation workers were
  cut off twice). `codex exec -m gpt-6.1-sol -s read-only -C <repo> -o <out.md> "<prompt>"`.
  Use the ChatGPT app's bundled CLI (0.159.2+):
  `/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex`;
  `~/.local/bin/codex` (0.158.0) rejects `gpt-6.1-sol` with a ChatGPT account. Pass `</dev/null`.
  Act on findings with Claude subagents (Sonnet for rule families, Haiku for small precise
  changes). Every review so far found real defects.
- Agent brief for Claude workers: `scratch/codex/brief-claude.md` (untracked; it restates this
  section: worktree-only, PYTHONPATH, probe/difftest/replay/fixture_audit, own baseline before
  editing, one commit per family). Recreate from this section if missing.
- Cherry-pick conflicts in `checker/__init__.py` are usually parallel workers adding
  `_run_pass(...)` lines at the same spot: keep HEAD, add the new pass lines, and confirm with a
  sorted diff of `_run_pass(` names against the worker branch.

## Resume here (open work at 2026-10-04)

- Branch `ocb-ranges` (no worktree): WIP commit `3228f99` holds unverified CDM range work
  (`checker/cdm.py`, `data/cdm.json`). CDM address/NULL-module rules have since landed on the main
  branch, so compare before finishing, or discard.
- Reverse-engineering tooling (round 9): `tools/trace_errors.py` (patched-exe tracer: caller
  addresses per emitted message, all four compilers; `docs/binary.md` "Message tracer"); Ghidra
  12.1.4 headless dumps `scratch/re/CR6Comp.decomp.c`, `scratch/re/cr300comp.decomp.c`
  (untracked; regenerate with `tools/ghidra/DumpAll.java`, project dir outside dot-directories).
  First results: exact TDF auto-size model; one CR300/CR350 compile-stage ordering rule.
- Rounds 10-11 done: CPI measurement time (cpispeed2_1 closed: no verdict mismatch left on any
  logger), scan-time budget, scan-interval arithmetic (scan_split model reconciled with the
  slow-scan contract), deferred-text excess arguments, CR300/CR350 corpus rules (+8 each), round-8
  and round-9 review findings. Not reviewed yet: `46f79c5d`..HEAD.
- Worker worktrees removed and the stash dropped (2026-10-04). Their branches remain; all except
  `ocb-ranges` are already integrated by cherry-pick.
- Unclosed `StructureType` is compiler undefined behaviour on all four loggers: verdicts observed
  once (old cache entries), uncached reruns page-fault (`docs/instructions.md`).

## Next steps

1. Known false rejects: none open. Remaining verdict items: `cpispeed2_1` CPI time (non-linear in
   Reps: at a 1 ms scan Reps 2..16 need 2,2,2,3,3,4,4,5,5,6,6,6,7,7,8 ms); CR300 storage limit
   (`docs/tdf-rules.md`). Rejected-with-wrong-message shapes after Sub/instruction calls are
   listed in `docs/checker.md` "Trailing tokens, round 4".
2. Message differences (262 replay; 49-77 per logger in the help corpus): rank by first
   differing diagnostic; prefer shared gating/ordering fixes (CR300/CR350 cascade, tier gating,
   same-line ordering by argument) over per-instruction edits. Open from round 2: several
   unresolved instruction arguments are each reported in a non-textual order (MoveBytes 1,4,3;
   SendVariables 1,9); `If` with a malformed `Then`; CR6 `Invalid channel` beside a Scan parameter
   error; CR1000X pass-1 symbol error hiding `Scan 0uSec invalid`; `CallTable` inside its own open
   `DataTable`; CR300/CR350 `Return`/nested-Scan cascades. Known: `PortGet(A(9),1)` order on
   CR6/CR1000X, interleaved messages for two port statements on one `:` line, float32 rounding
   of values near 2^31 on CR300/CR350.
3. Local-function resolution gap: CR300/CR350 accept a local `Dim exp` but resolve bare uses
   as the internal function (pending observations in `tests/fixtures/function_name_collision.json`).
4. Outputs: auto-allocated table sizes and mixed regimes (`docs/tdf-rules.md` §10, §12; CHM
   TDF 139/192), CDM binary image, `Reps`-dependent timing, non-CR6 artifact byte parity.
5. Fresh fuzz seeds (`tools/fuzz.py`) to find new families now that the saved set is nearly
   closed; then `cd ../scripts && uv run --no-sync verify.py`.
6. Settle `docs/legal.md` before publishing outside this repository.
