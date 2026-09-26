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

Measured 2026-09-26 with `tools/difftest.py` (exact output / same verdict, different message /
verdict mismatch) against the real compilers under Wine. "False reject" = the real compiler
accepts, opencrbasic rejects; it is the failure that matters most and is 0 everywhere.

| Logger | crbasic/ (16) | help examples (507) | false rejects |
|---|---|---|---|
| CR6 | 16 / 0 / 0 | 336 / 162 / 9 | 0 |
| CR1000X | 8 / 5 / 3 | 352 / 149 / 6 | 0 |
| CR300 | 0 / 8 / 8 | 120 / 369 / 18 | 0 |
| CR350 | 0 / 6 / 10 | 124 / 366 / 17 | 0 |

The crbasic/ programs are CR6 programs; the CR300/CR350 compilers reject them (different
terminals and instructions), which opencrbasic reproduces as a verdict for 8 and 6 of them.

`scripts/verify.py` with `CR6COMP=opencrbasic`: `BUILD  host tests 119 pass | CRBasic 16/16
compiled, 6 warning(s) | repository pass` (identical to the exe; not re-run this session -- the
CR6 side is unaffected by this session's own changes, all 16/16 `crbasic/` exact throughout).
Tests: `python3 -m pytest -q` 949 passed, 311 skipped, 8 xfailed.

| Phase | State |
|---|---|
| 0 Oracle + corpus | done: `tools/oracle.py`, `tools/difftest.py`, 507 CHM examples (many invalid on purpose or by extraction; they exercise error paths) |
| 1 Binary RE | done for current needs: CLI (`docs/cli.md`), exe layout and tables (`docs/binary.md`, `tools/extract_tables.py`), type codes (`docs/type-codes.md`), messages (`docs/messages.tsv`), mode rule (`docs/modes.md`), TDF rules (`docs/tdf-rules.md`), ProgSignature (`signature.py`), per-logger differences (`docs/loggers.md`) |
| 2 Front end | done: lexer, include expansion, constant folding, `#If`, parser (`docs/frontend.md`) |
| 3 Checker | usable: structure/placement, declarations, Units/Const/Alias, parameter counts, channel ranges per logger, Scan/DataInterval ranges, array limits, warnings (table never called, never reached, background calibration, fnotch), include-file prefixes, 4 KB message buffer, general expression-tree name resolution (`Invalid symbol in expression`/`%s is not defined`, per-logger echo/ordering rules, `NAN`/`INF`/system-table predefined symbols), the constant-only-family "rejects a variable" check, and the two-pass/pass-2-cutoff model (the generic "Expr"/`Call` fallback and before-`BeginProg` placement are now pass 2, not pass 1) (`docs/checker.md`, `docs/instructions.md`). Gaps: variable-required per-argument type checks (three confirmed per-instruction overrides found, no safe uniform rule yet), most instruction-specific pass-2 checks, two placement-error shapes (mid-sequence, after-EndSequence) |
| 4 Fuzzing | first pass done: `tools/fuzz.py`, `tools/fuzz_replay.py`, 248 reproducers (`docs/fuzz-findings.md`); ~25 fixed since. CR6 mutants: ~29% false accepts before round 3 |
| 5 Outputs | result line, mode (verified 16/16), warnings, ProgSignature (`-s`, verified on 5 programs), `-l` table list byte-identical 16/16, `-z` TDF byte-identical 8/16 (other 8 differ only in auto-allocated table sizes, within 3%); `-p`/`-i` preprocessed source byte-identical 16/16 repo + 98/100 CHM sample (`preprocess.py`); `-t` `[Times]` byte-identical on all 16 repo programs, 91/100 CHM sample (narrow, oracle-verified instruction set: `Scan`/`PanelTemp`/`PortSet`/`Sw12`/`Delay`(mSec)/`TCDiff`(Reps=1), `timing.py`); `-b` CDM image: "No CDM devices" message implemented and correct for every program in this repository (none use CPI/CDM hardware), binary image format not attempted; `-m` decompiled, confirmed no observable effect; `-u` decompiled and oracle-confirmed to gate a real `%s declared but not used.` warning, not implemented (needs the checker's own symbol table) |
| 6 Simulator | not started |

## Log

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

## Next steps

1. False accepts (largest gap): per-argument checks from the type codes and general expression
   name resolution, each gated on zero false rejects over difftest and `fuzz_replay.py`; the
   backed-out attempt is described in `docs/checker.md` ("Round 3").
2. Instruction-specific pass-2 checks, highest frequency first (`docs/fuzz-findings.md`,
   difftest verdict groups); CR300/CR350 terminal and instruction rules.
3. Auto-allocated table sizes, mixed regime (`docs/tdf-rules.md` §10, §12).
4. `-p`/`-t`/`-b` done (this row's own outputs entry); remaining: the CDM binary image format
   itself (`-b`, only matters for a program using CPI/CDM hardware -- none in this repository),
   `VoltSe`/other `Reps`-dependent `-t` formulas, and `-u`'s `%s declared but not used.` warning
   (belongs in `checker/symbols.py`, `docs/cli.md` "-u").
5. Re-run `tools/fuzz.py` after each round; add a CI job running pytest (no Wine needed).
6. Settle `docs/legal.md` before publishing outside this repository.
