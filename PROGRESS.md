# opencrbasic progress

Living state. Update after every milestone. Plan: `PLAN.md`.

## Environment setup (needed to resume)

- Oracle: `apt-get install wine32:i386 wine64 wine` (on Ubuntu 24.04 with the ondrej PPA,
  first `apt-get install --allow-downgrades libgd3=2.3.3-9ubuntu5 libgd3:i386=2.3.3-9ubuntu5`),
  `dpkg --add-architecture i386` first. Run with `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all`.
  First run takes ~15 s (prefix creation), later runs ~2 s.
- Ghidra 11.4.2 headless (optional, RE only): `/opt/re/ghidra_11.4.2_PUBLIC`, script
  `tools/ghidra/DumpAll.java` writes all decompiled functions to one C file.

## Status by phase

| Phase | State |
|---|---|
| 0 Oracle + corpus | done: `tools/oracle.py` (cached Wine runs, any compiler exe), `tools/difftest.py` (ours vs real, per logger), 507 CHM examples in `tests/corpus/chm/` (many deliberately or accidentally invalid: they exercise error paths); cache warmed for CR6, CR1000X, CR300, CR350 |
| 1 Binary RE | in progress: CLI decoded (`docs/cli.md`); instruction/function/symbol tables extracted (`tools/extract_tables.py`, `docs/binary.md`); message catalogue (`docs/messages.tsv`); TDF format decoded and round-tripped (`src/opencrbasic/tdf.py`); parameter type codes (`docs/type-codes.md`, `data/type_codes.json`); mode rule (`docs/modes.md`); table-definition rules (`docs/tdf-rules.md`). Pass-1 doc agent was cancelled; its findings are covered by `docs/frontend.md` and `docs/type-codes.md` |
| 2 Front end | done: `lexer.py`, `source.py`, `consteval.py`, `parser.py` written; minimal `checker.py` placeholder (mode.py only) so `python3 -m opencrbasic` runs end to end; `docs/frontend.md` records verified quirks. 825 unit/corpus tests pass (`cd opencrbasic && python3 -m pytest -q`); all 15 `crbasic/*.CR6` entry programs and 196/198 oracle-accepted CHM examples parse with zero `Unparsed` (2 known, documented gaps: a doc typo, an SNMP OID literal). |
| 3 Checker | in progress: `checker/` package (`structure.py` block matching/placement + placement-before-BeginProg + declaration/Units/Const/Alias line-wrap messages, `expressions.py` narrow undefined-symbol check, `instr_rules.py` required-parameter-count check, `instructions.py` `U1..U12` channel range (now gated to pass 2 only, see below), `warnings.py` table-never-called, `symbols.py`), rules and oracle evidence in `docs/checker.md`. Round 2 found the real compiler is two-pass (pass 2 semantic checks only run when pass 1 found nothing wrong anywhere in the file) and that echoed source text is verbatim (leading indentation/trailing whitespace preserved, not trimmed) -- both documented in `docs/checker.md`. `tools/difftest.py --logger cr6`: `crbasic/` 16/16 exact; `examples/` 0 exact, 2 verdict, 2 mismatch; `chm` 310 exact, 188 verdict, 9 mismatch (round 1: 268/229/10; round 1 start: 198/0/309) |
| 4 Fuzzing | first pass done: `tools/fuzz.py` (mutation-based differential fuzzer, 16 operators, seeded/reproducible, re-runnable) and `tools/fuzz_replay.py` (re-checks saved reproducers) against all four loggers; 248 reproducers under `tests/corpus/fuzz/<logger>/<class>/`, findings in `docs/fuzz-findings.md`. Headline: one genuine hang (`tables.py` `render_table_list`/`_explode_indices` enumerates every index of an unbounded-size `Public`/`Dim` array with no size check first) and one crash (`tables.py` `_scan_weights` calls `float()` on a non-numeric `Scan` interval unconditionally), both now also caught as false accepts in the corpus's ordinary `Scan`-interval-value cases (the real compiler always rejects these with a specific message; opencrbasic checks neither the type nor the range at all) -- a single value-range check on `Scan`/`DataInterval`/array-dimension arguments would close all three at once; 3 false rejects (an `IncludeSection` call with a harmless extra argument is silently not expanded, `source.py`'s regex; `ESSInitialize`'s type-code-127 argument parsed as a full expression instead of raw text, 2 instances found via a fuzzer artifact -- caveated in the doc); a well-evidenced missing `Invalid Variable declaration %s.` message (4 corpus-distinct mutants); new concrete instances of `docs/checker.md`'s open "one diagnostic, then silence" question; confirmation that `warnings.py` implements only 1 of at least 5 warning kinds the real compiler has. `false_accept` volume (671 mutants) mostly reconfirms already-tracked phase-3 incompleteness rather than new gaps. |
| 5 Outputs | in progress: mode decision matches the reference on all 16 crbasic programs; `-l` table list byte-identical 16/16; `-z` TDF byte-identical 8/16 (rest differ only in auto-allocated sizes, mixed regime — agent working); CHM sample TDF 72%, table list 77% (`tools/tdftest.py`) |
| 6 Simulator | not started |

## Log

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

## Next steps

1. Phase 3 checker (agent running): exact diagnostics, no false errors on accepted programs.
2. Auto-allocated table sizes, mixed regime (agent running).
3. Multi-logger (in progress, `docs/loggers.md`): terminal/channel model, mode lists, and
   unresolved-symbol message text are now per-logger data; `Compiled OK.` output already was.
   Remaining: CR300/CR350's broader `"X is not defined."` message likely also replaces
   `structure.py`'s `Invalid, or out of place expression:` fallback and others (not chased, needs
   the message-text owner); the negative/burst channel value shape on CR300/CR350
   (`abs(v) - 1`, distinct from CR1000X's `abs(v)`) is identified but deliberately left
   unimplemented; CR1000X's one false reject (`structuretype_3.CR6`, a general `StructureType`
   bare-member parsing gap) is unfixed.
4. Optional fallback in `scripts/cr6comp.py` to use opencrbasic when neither Wine nor PowerShell
   exists, clearly labelled as unverified against hardware.
5. ProgSignature (`-s`) algorithm; `-p` preprocessed output; `[Times]` (`-t`) are low priority.
6. From fuzzing (`docs/fuzz-findings.md`), highest leverage first: a `Scan`/`DataInterval`/array-
   dimension value-range check (closes a hang, a crash, and a chunk of `false_accept` volume at
   once); the `IncludeSection` extra-argument false reject (`source.py` regex too strict); a
   missing `Invalid Variable declaration %s.` message (`structure.py`, 4 corpus-distinct mutants);
   more `warnings.py` warning kinds (currently only table-never-called is implemented; at least 4
   more real warning texts found). Re-run `tools/fuzz.py --logger cr6 --count 1500 --seed 1` (and
   the other three loggers) after any of these land, and `tools/fuzz_replay.py` to confirm which
   saved reproducers are now fixed.
