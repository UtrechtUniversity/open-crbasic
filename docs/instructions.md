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

## Data tables and output processing (`checker/instr_data.py`)

New module, `checker/instr_data.py`, alongside this workstream's other files (`PLAN.md`: "data
tables and output processing"). Wired into `checker/__init__.py` with two additive lines, both
inside the pre-existing "no pass-1 error anywhere in the file" gate (`docs/checker.md`'s
"Two-pass compilation"; both rules below are oracle-confirmed to belong there, same method as that
section -- an unrelated pass-1 error placed both before and after the statement in question
suppresses the message either way, only firing when the file has no other error at all).

### `%s must be used inside a table declaration.` / `EndTable without DataTable.`

`check_table_structure` tracks one "currently inside a `DataTable`" flag (CRBasic `DataTable`
blocks do not nest, `tables.py`'s own `_scan_program` docstring) over the flat statement list:

- An orphan or extra `EndTable` (no `DataTable` currently open) is `EndTable without DataTable.`
  at its own line -- oracle-confirmed (scratch probes `te1.cr6`/`te3.cr6`/`te5.cr6`): a bare
  `EndTable` alone, and a *second* `EndTable` right after a properly-closed table, both give this
  exact message at the extra statement's own line. A `DataTable` that is *never* closed does
  **not** get a complementary "not matched" message of any kind (despite one existing in
  `docs/messages.tsv`, `'DataTable ... EndTable not matched.'`) -- oracle-confirmed (`te2.cr6`): it
  instead surfaces later, indirectly, as `Could not find DataTable %s.` at the table's first
  `CallTable` site, because everything after the unclosed `DataTable` (including `BeginProg` and
  the whole rest of the program) is apparently still absorbed as part of the table's own body.
  Not implemented (a different, unimplemented message; low corpus value; this checker's own
  simple boolean-flag tracking degrades safely there -- see below).
- Every instruction in `TABLE_ONLY_INSTRUCTIONS` found *outside* an open `DataTable` is `%s must
  be used inside a table declaration.`, `%s` = that dict's own display spelling. The set and
  spelling were found from the decompiled call sites of the shared validator function this
  message comes from -- `thunk_FUN_004ea5d0` on CR6, `thunk_FUN_004ea5f0` on CR1000X,
  `/opt/re/out/decomp.c`/`decomp_cr1000x.c`, grepped narrowly (never the whole file) for each
  function's own call sites, which pass each instruction's own name (or, for `Maximum`/`Minimum`,
  the unlabelled short strings `"Max"`/`"Min"`, read directly out of the compiled string table at
  their call sites with `pefile.get_string_at_rva`, not guessed) as the validator's argument. Both
  loggers' call-site lists are identical: `CardOut`, `MQTTPublishTable`, `GoesTable`, `TableFile`,
  `Totalize`, `Average`, `Max`(imum), `Min`(imum), `SampleMaxMin`, `StdDev`, `Moment`, `Sample`,
  `Histogram`, `Histogram4D`, `Covariance`, `RainFlow`, `LevelCrossing`, `WindVector`, `Median`,
  `SampleFieldCal`, `FillStop`, `DataTime`, `OpenInterval`, `DataInterval`, `DataEvent`,
  `TableHide`. Two more call sites (unlabelled strings `"FFT"`/`"Et"`, near the
  `Histogram4D`/`SampleFieldCal` call sites) could not be attributed to a specific instruction
  with confidence and are deliberately left out of the checked set (AGENTS.md: never guess into a
  false reject).

  Independently oracle-confirmed this round (fresh scratch probes against `CR6Comp.exe`, each
  instruction called correctly-shaped but outside any table): `CardOut`, `TableFile`, `Totalize`,
  `Average`, `Maximum` (as `Max`), `SampleMaxMin`, `StdDev`, `Median`, `WindVector`, `Histogram`,
  `RainFlow`, `LevelCrossing`, `FillStop`, `DataTime`, `OpenInterval`, `TableHide` all reproduce
  exactly, digit for digit. `Sample`, `Average`, `Minimum` (as `Min`) and `DataInterval` are also
  each backed by a saved fuzz reproducer (`tests/corpus/fuzz/cr6/false_accept/
  {470441546122,5b5ae86fd087,1ded68266aec}.json`, all `duplicate_line`/`delete_line` mutants of
  real CHM corpus programs -- not hand-written). `DataEvent` alone is decompiled-call-site
  evidence only: a from-scratch probe placing a bare, correctly-shaped `DataEvent(...)` outside
  any table **crashes the real compiler under Wine** rather than printing this text -- the same
  class of real-compiler-side crash `docs/checker.md`'s `pointervariable_2.CR6` entry already
  documents as unmatchable, so this checker's own clean rejection there is a deliberate
  improvement on the real compiler's own behaviour, not a claim to reproduce it exactly.
  `Moment`, `Covariance`, `Histogram4D`, `GoesTable`, `MQTTPublishTable` and `SampleFieldCal` are
  decompiled-call-site evidence only (each individually blocked from a clean from-scratch probe
  this round by an unrelated pass-1 argument-shape error in the minimal test case -- e.g.
  `MQTTPublishTable`'s first argument must itself be a constant -- not chased further, given the
  time already spent and the strength of the call-site evidence, which is byte-identical in
  structure to every instruction already confirmed).

  Verified this round that `FieldNames` -- named in the task's own illustrative instruction list
  alongside `Sample`/`Average`/etc. -- is **not** in this set despite the obvious analogy: a bare
  `FieldNames("X")` outside any table compiles clean (oracle-confirmed, scratch probe `tfn.cr6`).
  It plainly has its own validation (`FUN_004ec300`'s `'FieldNames cannot be a variable.'`/`'Must
  use FieldNames with reserved field .%s.'`/... in `docs/messages.tsv`) but not this placement
  rule; not implemented (out of this round's time budget).

Per-logger: a from-scratch probe of `Sample` alone outside a table gives the identical message,
digit for digit, on all four loggers' own compilers (`CR6Comp.exe`/`cr1000xcomp.exe`/
`cr300comp.exe`/`cr350comp.exe`); CR1000X's own decompiled call-site list
(`thunk_FUN_004ea5f0`) is identical in membership to CR6's. CR300/CR350 have no separate
decompilation available (`/opt/re/out/` only carries CR6 and CR1000X); the one live probe is
this round's only direct CR300/CR350 evidence, so the checked set is applied uniformly to all four
loggers on the strength of that probe plus the CR6/CR1000X agreement, filtered per logger by
`kw in profile.instructions` (an instruction a given logger's own table does not have, e.g. a
CR6-only CDM/GOES instruction absent from CR300/CR350, simply never matches -- safe by
construction, never a false reject from a logger difference this round did not individually probe).

### `Duplicate field name %s in DataTable %s.`

`check_duplicate_fields` deliberately does not re-derive each output instruction's own default
field-naming rule (`_Avg`/`_Min`/`_TMx`/... suffixes, `FieldNames` renaming, reps-array expansion)
a second time; it reuses the names `tables.py` (a concurrently-owned module) has already computed
in `result.tables`, once per table, flagging every field beyond the first that shares an
already-seen name (oracle-confirmed shape: exactly one diagnostic for a name that occurs twice, not
one per pair -- `tests/corpus/fuzz/cr6/false_accept/{0004dc1d7a39,7076907a77b3,
d4b49fb47507}.json`, three independent `duplicate_line` fuzz mutants of real CHM seeds, plus the
CHM corpus's own `ch400status_2.CR6`). No line number (oracle-confirmed, matching the pre-existing
"table declared but never called" warning, which shares this message's emitting function,
`FUN_004eab30`, per `docs/messages.tsv`).

**Found as a regression during this round's own development, fixed before landing**: `tables.py`'s
`_source_name` returns the fixed sentinel `"Anonymous"` for any output-instruction source that is
not a plain `Name`/`Index` expression (a `Table.Field` cross-table reference, or a string literal
source) -- not a real field name, just a "cannot derive one" placeholder that is never itself
exercised by the TDF/`-l` output this project has verified byte-identical, so it was never
previously visible as a bug. Two or more such sources in the same table collide on this one shared
sentinel and were flagged as duplicates of each other, even though the real compiler accepts the
file cleanly: `tests/corpus/chm/goestable_1.CR6` (two `Sample`s of a cross-table `HR_DATA.
battery_voltage_Avg`/`HR_DATA.panel_temperature_Avg`) went from accepted to a false reject on all
four loggers, caught by this round's own full `tools/difftest.py --logger <l>` re-run before
landing (AGENTS.md: verify a generalisation against the whole corpus, not a hand-written probe,
before trusting it -- the same methodological finding `docs/checker.md`'s "Round 3" section
records for a different check). Fixed by excluding every field named exactly `"Anonymous"` from
this check on both sides of the comparison (never recorded as seen, never itself flagged) --
conservative in the correct direction, since a name already known not to be a genuine derived name
is not evidence of a genuine collision either way. `ch400status_2.CR6`'s own `CH400Status`
reps-cycling shape (`docs/checker.md`'s "Known remaining gaps") is a *different*, already-documented
gap in `tables.py`'s field derivation (it collapses a 30-rep structured measurement into a single
field, never per-element aliased names) that this exclusion does not touch and does not need to:
that gap only ever *under*-reports here (a single field can never collide with itself), never
over-reports.

Difftest impact of this section (`tools/difftest.py --logger <l> --group all`, before this round's
end-of-session measurement / after, all four loggers, zero false rejects throughout both
measurements): cr6 chm 311/187/9 -> 312/186/9; cr1000x chm 327/172/8 -> 329/172/6; cr300 chm
119/369/19 -> 120/369/18; cr350 chm 123/366/18 -> 124/366/17. `tools/fuzz_replay.py --workers 2`:
the seven false-accept reproducers this section targets by name above all now report "FIXED (now
exact)"; no reproducer of any class newly reports `false_reject`.

### Investigated, not implemented (this section's own scope)

- **`SerialClose`'s own `ComPort` argument**: `examples/SBD_example_hirise.CR6`/
  `SBD_example_hirise_SBDWB.CR6` (the top-level repo's historical `examples/`, read-only, used here
  only as `tools/difftest.py`'s pre-existing third oracle-comparison group, never compiled or
  modernised as production CRBasic per `AGENTS.md`) each real-reject twice with `Invalid port %d,
  use C1..C4 or U1..U12.` (CR6) / `...C1..C8.` (CR1000X) at their own two `SerialClose
  (IRIDIUM_COM_PORT)` call sites, where `IRIDIUM_COM_PORT` folds to `ComRS232`. The same constant
  is used freely at `SerialOpen`/`SerialFlush`/`SerialOut`/`SerialIn` call sites earlier in the same
  file with no error at all -- so this is specifically `SerialClose`'s own argument validation
  being stricter than every other `Serial*` instruction's, not a general "COMRS232 is never a valid
  port" rule. Not implemented: confirming the exact boundary (is it `ComRS232` specifically, or
  every non-hardware/virtual port name?) needs more oracle probing than this round's remaining time
  allowed; left for a future session with this entry as its starting point.
- **`GPS pps synchronization is only available on COMU1.`**: two CHM corpus mismatches
  (`gps_2.CR6`, `structuretype_3.CR6`), both a `GPS(Dest,ComC1,...,100,...)` call -- `ComC1` for the
  `ComPort` argument together with `100` for the `PPSMethod` argument. Plausibly a two-argument
  interaction (a specific `PPSMethod` value requiring `ComPort = COMU1`), but only two corpus
  instances exist and both share the same `PPSMethod` value, so the actual triggering set of
  `PPSMethod` values (is it only `100`, or a range?) is not pinned down; not implemented this round
  to avoid guessing at a boundary with no evidence either way (AGENTS.md priority order).

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
