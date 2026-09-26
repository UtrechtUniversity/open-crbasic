# Per-instruction rules: warnings and value-range checks

Owned by the same workstream as `checker/warnings.py`, `checker/instr_rules.py`,
`checker/terminals.py`, `checker/instructions.py` (`PLAN.md`). Records the rule, the oracle
evidence, and per-logger differences for each check added this round. All probes below were run
2026-09-25/26 via `python3 tools/oracle.py <file> [--siblings DIR]` or a direct
`WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine ../compiler/<exe> t.cr6` scratch probe against the
real compilers, per `AGENTS.md`'s oracle priority. CR6 rules stay cross-referenced from
`docs/checker.md`/`docs/fuzz-findings.md`; this file adds the value-range/warning work and the
per-logger differences found while implementing it.

## Two ungated call sites

Two architectural facts, found by direct oracle probing (placing an unrelated pass-1 error both
before and, separately, nowhere near each check below): **all** checks in this document fire
regardless of what else is wrong in the file, unlike `checker/instructions.py`'s pass-2 channel
checks (`docs/checker.md` "Two-pass compilation"). Concretely: a file with an earlier `NOTE`
(undefined bare identifier) or `PanelTemp(UndeclaredVar,...)` error still gets every check below's
own diagnostic, in ascending line order together with the other errors (oracle: `t8.cr6`/`t9.cr6`/
`di9.cr6`/`di10.cr6` scratch probes, this session). `checker/warnings.py`'s `check_early` is the
single call site (`checker/__init__.py`, one additive line, ungated); `instr_rules.py`'s
`check_scan_params`/`check_data_interval` are called from there rather than from
`checker/__init__.py` directly, to keep that file's edit to one line.

A second, unrelated finding needed to make any of this visible in `crbc`'s own output: warnings
with a real line number print `line N: Warning: <message>` (not bare `Warning: <message>`, which
`diagnostics.py` previously assumed for every warning because the only warning that existed before
this round, "table declared but never called", has no line at all). A line-less warning still
prints bare, and sorts *after* every numbered line (oracle: `t10.cr6` -- a `Delay never reached at
run time.` warning at line 12 printed *before* a `Table Foo, is declared but never called.`
warning that has no line). `diagnostics.py`'s `cr6comp()` and `compiler.py`'s `result_lines()` were
adjusted for both (a stable sort by `(line == 0, line)` over errors and warnings together,
replacing the old errors-only sort).

## `%s never reached at run time.` (`checker/warnings.py`, `_check_reachability`)

Fires for the *first* executable top-level statement that textually follows a `Scan(...,Count=0)`
(loop forever) with no `ExitScan` anywhere in its body, up to the next `SlowSequence` or end of
file. All four loggers share this (oracle: identical text/condition on `cr1000xcomp.exe`,
`cr300comp.exe`, `cr350comp.exe` and `CR6Comp.exe` for the same minimal probe).

- `%s` is the statement's canonical instruction name (`Delay`, `Scan`, proper-cased from
  `data/<logger>/instructions.json`) or `Expr`, the compiler's generic bare-expression-statement
  pseudo-instruction, for a plain assignment or anything that failed to parse as a recognised
  instruction (oracle: a mutated `EndProg` that fails to parse gets both `Invalid, or out of place
  expression:` *and* `Expr never reached at run time.` on the same line,
  `tests/corpus/fuzz/cr6/message_diff/caf5c15cd16b.CR6`).
- Only the *first* such statement is ever reported; every oracle/corpus example found shows
  exactly one, matching this project's other "one diagnostic, then silence" findings
  (`docs/checker.md`).
- `SlowSequence` genuinely resets/exempts reachability (oracle: `SlowSequence`/its own
  `Scan`/`NextScan` right after an infinite, no-`ExitScan` main scan is never flagged) -- CRBasic
  semantics, not a compiler blind spot: a slow sequence is a real, separately-scheduled task.
  **Found as a regression during development**: this checker's first version only *reset*
  reachability at `SlowSequence` and kept tracking afterward, which produced 10 corpus false
  warnings on real, oracle-accepted programs whose slow sequence's own inner scan is itself
  infinite-with-no-exit (`ftpclient_1.CR6`, `sdi12recorder_3.CR6`, `udpsocketopen_1.CR6`, and 7
  more, all flagging the `EndSequence` terminator as "never reached"). No oracle evidence exists
  either way for reachability *within* a slow sequence's own (possibly chained) scan structure, so
  the fix is to stop this analysis entirely at the first `SlowSequence`, not to guess at a correct
  rule for it.
- A `Sub`/`Function` body is skipped entirely (its own statements are reachable whenever called,
  independent of where the definition textually sits).
- Non-executable (never flagged even when "dead"): `Public`/`Dim`/`Const`/`ConstTable`/`Alias`/
  `Units`/`DataTable...EndTable`/`StructureType...EndStructureType`/`ObjectType...EndObjectType`/
  `BeginProg`/`EndProg`, and (see above) anything from `SlowSequence` onward.

Boundary probes (`Scan(N,Sec,3,2000)`, a finite count -- no warning, since the loop returns after
2000 iterations) and (`ExitScan` present anywhere in the body, even inside a conditional that may
never fire -- no warning either) both confirm the rule is purely syntactic ("does this Scan block
contain the *text* `ExitScan` anywhere, and is `Count` a resolvable constant `0`"), not a real
control-flow/liveness analysis.

## `When Main Sequence Scan %d is running, background calibration will be disabled.`

CR6/CR1000X only (`profile.background_calibration`, absent for CR300/CR350). Fires once per
*main-sequence* `Scan` (not one declared under `SlowSequence`, whose own minimum interval, 100mSec,
is far above every threshold below) whose folded `Interval`/`Units` is below a threshold, reported
at the file's *last* statement's line (oracle: `t.cr6`/`54f0b2ff38cf.CR6`/`19a402de98a4.CR6` all
report at `EndProg`'s own line, not the `Scan` statement's line -- the same "last statement" spot
`structure.py`'s `No BeginProg in Program.` uses).

Threshold, found by binary search on the `Scan` interval (`mSec`/`uSec` units, boundary confirmed
to within 0.1ms on CR6 and to the minimum achievable step on CR1000X):

| Logger | Threshold | Evidence |
|---|---|---|
| CR6 | < 8ms (8000us) fires; >= 8ms does not | `7.9mSec` fires, `8.0mSec` doesn't; `7999uSec` fires* |
| CR1000X | < 2ms fires (in practice, only exactly `1ms`, since CR1000X's own minimum `Scan` granularity is 1ms) | `1mSec` fires, `2mSec` doesn't |
| CR300/CR350 | never (no such warning at any interval probed) | oracle-probed down to `100uSec` (far below CR6/CR1000X's own minimum), no warning either logger |

\* `7999uSec` on CR6 actually fails a *different*, unrelated check first (`Scans must be a
multiple of 1mSec.` -- not implemented, see "Known gaps" below); the threshold above was
confirmed with the `mSec`-unit probes, which don't hit that other check.

Both thresholds fit one formula: fires when `floor(interval_us / granule_us) < 2`, `granule_us` =
4000 (CR6) / 1000 (CR1000X) -- consistent with the decompiled condition this message's function
tests (`*(uint*)(...) < 2`, `docs/checker.md`-style evidence, not itself re-derived byte-for-byte
this round) and exactly matching both loggers' independently-probed boundaries.

`Scan`'s `Units` argument folds (via `profile.symbols`) to a `usec`=0..`day`=5 index
(`data/symbols.json`); `checker/warnings.py`'s `_MS_PER_UNIT` converts it to whole milliseconds for
this comparison.

## `Invalid fnotch value, must be 50,60 400 or 4000` (CR300/CR350 only, no trailing period)

Every instruction with an `fN1` filter-frequency argument (found exhaustively: every CR300/CR350
instruction whose `data/<logger>/instructions.json` `params` contains type code 20, cross-checked
one-for-one against each instruction's own CHM `Syntax` line to confirm the position, three of
them -- `PanelTemp`, `VoltSe`, `TCDiff` -- also directly oracle-verified) restricts it to exactly
`{50, 60, 400, 4000}` on CR300/CR350; CR6/CR1000X accept any value in a continuous range instead
(oracle: `PanelTemp(...,15000)` compiles clean on both, and `15000` is even the CHM's own example
value for that instruction). `0` is *not* exempt (unlike CR6's own, unrelated fnotch minimum-value
error, see "Known gaps" below) -- oracle-confirmed `PanelTemp(X,0)` on CR300 still warns.

Instruction -> 1-based argument position of the sole `fN1` slot (`data/cr300/fnotch.json` /
`data/cr350/fnotch.json`, identical on both loggers -- their `instructions.json` `params` arrays
for every one of these are byte-identical):

| Instruction | Position | Instruction | Position |
|---|---|---|---|
| `InternalTemp` | 2 | `BrHalf` | 10 |
| `PanelTemp` | 2 | `BrHalf3W` | 10 |
| `Therm107`/`108`/`109` | 6 | `BrFull` | 11 |
| `VoltSe`/`VoltDiff`/`CurrentSe` | 7 | `BrHalf4W`/`BrFull6W` | 12 |
| `TCSe`/`TCDiff` | 9 | `AM25T` | 13 |

Fires even when the file has unrelated errors elsewhere (oracle: `tablefile_1.CR6` under
`cr300comp.exe` shows this warning *alongside* an unrelated `Invalid drive` error). This single
warning, once implemented, is the dominant fix for CR300/CR350's fuzz `mode_diff` class (`docs/
fuzz-findings.md`: 54/300 and 58/300 mutants respectively; both loggers' single saved `mode_diff`
reproducer was exactly this warning) and for a large slice of the `chm` corpus's own `verdict`
count (`PanelTemp`/`TCDiff` alone appear in 188 and 102 corpus files respectively).

## `Scan Interval/BufferOption/Count parameter must be a non-negative integer` (CR6/CR1000X only)

No trailing period (verbatim oracle/`docs/messages.tsv` text, function `FUN_00468900`), for
`Scan`'s 1st/3rd/4th arguments (`Scan(Interval,Units,BufferOption,Count)`). Fires when the argument
is a compile-time constant that is either non-numeric (a string: `Scan("fuzz",Sec,1,0)`) or, once
truncated *toward zero*, negative:

```
Interval=-1   -> fires (int(-1) == -1, negative)
Interval=-0.5 -> does NOT fire (int(-0.5) == 0, not negative -- falls through to a separate,
                 unimplemented "too small" check instead, `Scan 0uSec invalid must be at least
                 1mSec.`)
Count=-0.5, BufferOption=-0.5 -> neither fires (same truncation rule; neither has a further
                 downstream range check the way Interval does, so these two compile clean)
TRUE (-1)     -> fires; FALSE (0) -> does not
```

**CR300/CR350 do not perform this validation at all** -- found as a regression during development:
the corpus's own `hex_2.CR6` (`Scan(1,Sec,3,-1)`, already a documented CR6/CR1000X-rejected
example, `docs/checker.md` "Known remaining gaps") is real CR300/CR350-*accepted* code
(oracle-confirmed: `Scan("fuzz",Sec,3,0)` and `Scan(-1,Sec,3,0)` both compile clean on
`cr300comp.exe`). Implementing this logger-unconditionally was a genuine false reject on CR300 (`0
false rejects` regressed to `1` in this round's own difftest run before the fix); gated to CR6/
CR1000X only (`instr_rules._SCAN_VALUE_CHECK_LOGGERS`) after that was found.

This closes the fuzz-found crash in `tables.py`'s `_scan_weights` (a non-numeric `Scan` interval
unconditionally passed to `float()`, `docs/fuzz-findings.md` "2. Crash") and the fuzz-found hang in
`tables.py`'s `render_table_list`/`_explode_indices` was independently already fixed by the
concurrent `structure.py` workstream's own array-dimension check (`Illegally dimension: ...`) before
this check was written -- both re-verified fixed this round (`tools/fuzz_replay.py`).

## `DataInterval TintoInt and Interval parameters must be non-negative` (CR6/CR1000X only)

No trailing period (function `FUN_004f11d0`), when either of `DataInterval`'s first two arguments
(`TintoInt`, `Interval`) is a negative compile-time-constant number, reported at the `DataInterval`
statement's own line (not the `CallTable` site, unlike the divisibility check below). **Also
CR300/CR350-absent** (oracle: `DataInterval(0,-3,Sec,0)` compiles clean on `cr300comp.exe`), found
and gated the same way as the `Scan` checks above.

## `DataTable interval %d uSec not integrally divisible by scan interval %d uSec.` (all four loggers)

Confirmed project knowledge (`knowledge/crbasic/lessons.md` "`DataInterval` versus `Scan`": "a
table interval must be at least the scan interval and an exact integer multiple of it", human-
verified against `CR6Comp.exe` 2026-08-21), now oracle-re-confirmed with the exact message text
and, unlike the two checks just above, found identical on **all four loggers including CR300**
(oracle: `DataInterval(0,3,Sec,0)` + `Scan(5,Sec,...)` gives the same message, digit-for-digit, on
`cr300comp.exe` as on `CR6Comp.exe`). `Interval = 0` (CHM: "the data storage interval is the scan
interval") trivially always divides evenly and needs no special case. Reported at each
`CallTable <name>` site, not the `DataInterval` statement's own line (oracle: `di10.cr6` scratch
probe -- the divisibility error's line is the `CallTable` line, the non-negative error's line
above is the `DataInterval` line, in the same file).

**Deliberately conservative** (`checker/instr_rules.py`'s `check_data_interval` docstring has the
full reasoning): only tracks the textually innermost `Scan`/`SlowSequence Scan` a `CallTable` sits
inside, with no depth stack beyond "currently open or not" (`Scan` never nests with itself; only
`SubScan` does, using the distinct `SUBSCAN`/`NEXTSUBSCAN` keywords, so it never interferes). Not
attempted: a table called indirectly through a `Sub`, a table called from more than one
differently-interval'd `Scan`, a table called from outside any `Scan` at all (e.g.
`AcceptDataRecords` -- silently skipped, no check), or the CHM's documented `GetDataRecord`-
destination-table exception. None of these gaps risk a false reject (they only mean the check
silently doesn't fire), consistent with AGENTS.md's priority order.

## Investigated, not implemented

- **`%s declared but not used.`** (`FUN_004ba180`, `docs/messages.tsv`) -- probed an unused
  top-level `Const`, an unused top-level `Public`, and an unused `Sub` parameter plus an unused
  `Sub`-local `Dim`; none produced this warning on CR6. Not chased further (no fuzz/corpus
  evidence of it firing anywhere either), so its actual trigger is still unknown; left for a future
  session with a concrete example to start from.
- **CR6's own `Invalid fnotch, minimum allowed is 2 Hz.`** (an *error*, `docs/messages.tsv`
  function `FUN_005166a0`, unrelated to CR300/CR350's warning above) -- confirmed real (binary
  search: `PanelTemp(X,1.4)` fails, `PanelTemp(X,1.44)` compiles, boundary near but not exactly
  the message's own claimed "2 Hz", and `0` is exempt, unlike CR300/CR350's check). Not
  implemented: the true numeric boundary sits at a non-round value (~1.435-1.44) that doesn't match
  the message text's own claimed threshold, and getting it wrong by rounding to a clean "2" would
  falsely reject any real program using a value in `[1.44, 2)` -- not found in the corpus, but not
  worth the false-reject risk for an edge case this narrow (AGENTS.md priority order).
- **`Scan %duSec invalid must be at least %dmSec.` / `Scans must be a multiple of %dmSec.` /
  `Maximum Scan interval is 1 Day.`** -- all three are real (oracle-confirmed present, same
  `FUN_00468900` family as the non-negative check above) but each has a boundary this session
  could not pin down cleanly: the multiple-of-1ms message fires for some sub-millisecond `uSec`-
  unit values but not for numerically equal `mSec`-unit fractional values (`7999uSec` fails,
  `7.9mSec` compiles), and the 1-day maximum accepted `1.0001 Day` while rejecting `25 Hr` (~1.04
  days) -- both smell like float-precision artifacts in the real compiler's own internal
  representation rather than a simple threshold. Implementing either risks a false reject on a
  borderline value; left unimplemented (`checker/instr_rules.py`'s `check_scan_params` docstring
  notes this explicitly).
- **`ExChan must be U1-U12.`** (`BrHalf`/`BrHalf3W`/`BrHalf4W`/`BrFull`/`BrFull6W`'s own
  `ExChan`/`SEChan` positions, `FUN_005147a0`/`FUN_005177a0`) -- already noted as not implemented
  in `docs/checker.md`; not picked up this round either (no corpus mismatch currently needs it).
- **CR6's per-instruction "measurement instruction reps/range/channel/excitation" checks** beyond
  the existing `U1..U12`/CR300-CR350 channel-range check (`checker/terminals.py`) -- out of scope
  for this round's budget; the highest-frequency gaps chased were the `Scan`/`DataInterval` value
  checks and the warnings above, per the task's own prioritisation ("frequency in the fuzz
  false_accept reproducers").
