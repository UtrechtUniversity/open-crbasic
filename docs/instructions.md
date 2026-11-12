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
`checker/__init__.py` directly.

Warnings with a real line number print `line N: Warning: <message>`. A line-less warning (such as
"table declared but never called") prints bare, and sorts *after* every numbered line (oracle: `t10.cr6` -- a `Delay never reached at
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

## `Invalid fnotch, minimum allowed is N Hz.` (CR6 `2 Hz`, CR1000X `.5 Hz`; an error)

Observed 2026-10-02 on `CR6Comp.exe` and `cr1000xcomp.exe` (compiler hashes in
`tests/fixtures/fnotch_min.json`), 134 fixture cases
(`tests/test_fnotch_min.py`), data `data/fnotch.json` (CR6) and `data/cr1000x/fnotch.json`.
Unlike CR300/CR350 above (a warning on a fixed value set), this is a hard **error** and has no
maximum (`1e30` and `15000` compile clean).

Value rule on the folded `fN1` argument, taken as a 32-bit float: `0` (also `1E-50`, `-1E-50`,
which round to zero), `+INF` and `-INF` (also `1E39`, `-1E300`) pass; `NAN`, a string (including
`""` and `"60"`) and `True` (= -1) fail; a positive number fails when it is below the minimum. A
negative number fails only when its integration code `int(N / fN1 + 0.5)` (C truncation; `N`
93750 on CR6, 31250 on CR1000X, `checker/scan_time.py`) is negative, so a large one passes: CR6
`-62501`, `-1E5` and `-1E30` compile, `-62499` and `-50000` fail; CR1000X `-20834` and `-25000`
compile, `-20833` fails (oracle, 2026-10-24, `tests/fixtures/r31_review_fixes.json` `f6-fn*`). The message always says `2 Hz` (CR6) / `.5 Hz` (CR1000X) but the real cut-off
is not that number. Bisected: CR6 fails at `1.43052` and passes at `1.43053`; CR1000X fails at
`0.47684` and passes at `0.476845` (`1e6 / f` is about 699,04x us and 2,097,1xx us, the CR1000X
figure is three times the CR6 one). `min_hz` in the data files is the midpoint of each bracket
(1.430525, 0.4768425), so the unprobed digits beyond it are an assumption.

Instruction and 1-based position (identical positions on both loggers, `params` type code 20 in
`data/instructions.json`): `PanelTemp` 2, `VoltSe`/`VoltDiff`/`CurrentSe` 7, `TCSe`/`TCDiff` 9, `Therm107/108/109` 6,
`BrHalf`/`BrHalf3W` 10, `BrFull` 11, `BrHalf4W`/`BrFull6W` 12, `AM25T` 13. CR6 additionally has
`PanelTemp2` 4, `Thermistor` 8, `Resistance` 11 and `Resistance2W`/`Resistance3W` 8 (each only
probed with `-1`, a single failing value, and with unusual argument lists; the positions come from
the instruction table). The message is printed **twice** for `Therm107/108/109`, `BrHalf`,
`BrHalf3W` and `BrFull` (both loggers, also with `Reps` 2 and with other `RevEx` values), once for
the rest; the reason is unknown, it is modelled as the data key `twice`.

Pass and ordering (all oracle-confirmed on both loggers unless noted):

- It is a pass-1, constant-family value check, like the unresolved-symbol check of
  `expressions.check_const_family_args`. It is reported next to earlier pass-1 errors (`Dim Z
  banana`, `Delay(0,5,Foo)`, a stage-2a `Battery((1+2))`), and it makes pass 2 not run:
  `PanelTemp(PTemp,-1)` followed or preceded by `Foo = Bar` prints only the fnotch line, and an
  undefined destination (`PanelTemp(Nope,60)`) is not reported either.
- Within a statement: an unresolved name or a variable in any constant position (e.g. `U99`,
  `mV7`) abandons the statement before the fN1 check; the `Range` code error (below) comes first
  when both apply; the fnotch line precedes `Invalid or missing parameter.` and `Too many
  parameters:  N.` on the same line (`PanelTemp(PTemp,True,1)`, `VoltSe(...,-1)` with `Mult`
  missing). A bad `Mult`/`Offset` name (general family) is not reported. Two fnotch errors on one
  line (`:` separated) are both printed, and an inline `If ... Then PanelTemp(PTemp,-1)` counts.
- A variable at the fN1 slot (`PanelTemp(PTemp,PTemp)`, `X(1)`, `PTemp+1`) is `Variable illegal in
  Parameter: <text>.` on CR6/CR1000X (CR300/CR350 not covered).
- `Range` code (`Invalid range code, valid options are: ...`) is the same kind of check: it is
  reported after `Dim Z banana` and `Delay(0,5,Foo)` errors on both loggers (CR1000X cases use
  channel `1`). It runs in the same pass-1 function
  (`instr_measure.check_pass1_values`).

CR350/CR300 fuzz reproducers that were listed as fnotch false accepts (`cr350/false_accept/2e32a4ebade7`,
`165c693049da`, `1549108234a4`, `881ad67a2b3f`, `cb998c48d68a` (now closed, `docs/checker.md` "Matrix dimensions"), `cr300/false_accept/6c8fb0434bdb`)
are not fnotch problems: the warning (`Warning:`, which does not fail a compile by itself) is
already emitted exactly. The compile fails because of other errors we do not model (`DataTable
interval ... not integrally divisible by scan interval`, `Illegal Semaphore, 1..3 are legal`,
`Could not find ScanConstTable.ScanConst(1).`, `Illegal option.`, `Illegal Matrix dimensioned`,
`Variable Temp out of bounds.` for `Therm109` with a missing argument).

Not modelled: on `Scan (1,Sec,0,0)` a low `fN1` (CR6 1.43 to 2, CR1000X 0.477 to 2) additionally
gives `Scan interval too short for measurement by N Sec.` on the `NextScan` line; it depends on the
fnotch value and is not implemented.

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

Confirmed project knowledge ([crbasic skill lessons](https://github.com/UtrechtUniversity/open-crbasic-skills/blob/main/plugins/crbasic/skills/crbasic/references/lessons.md#datainterval-versus-scan): "a
table interval must be at least the scan interval and an exact integer multiple of it", human-
verified against `CR6Comp.exe` 2026-08-21), now oracle-re-confirmed with the exact message text
and, unlike the two checks just above, found identical on **all four loggers including CR300**
(oracle: `DataInterval(0,3,Sec,0)` + `Scan(5,Sec,...)` gives the same message, digit-for-digit, on
`cr300comp.exe` as on `CR6Comp.exe`). `Interval = 0` (CHM: "the data storage interval is the scan
interval") trivially always divides evenly and needs no special case. Reported at each
`CallTable <name>` site, not the `DataInterval` statement's own line (oracle: `di10.cr6` scratch
probe -- the divisibility error's line is the `CallTable` line, the non-negative error's line
above is the `DataInterval` line, in the same file).

Round 87 confirmed that quoted names and names supplied by String constants participate in the
same check. A quoted `CallTable` also counts as a table use for the uncalled-table warning.
The four-profile observations and hashes are in `tests/fixtures/r87_table_interval_names.json`.

**Deliberately conservative** (`checker/instr_rules.py`'s `check_data_interval` docstring has the
full reasoning): only tracks the textually innermost `Scan`/`SlowSequence Scan` a `CallTable` sits
inside, with no depth stack beyond "currently open or not" (`Scan` never nests with itself; only
`SubScan` does, using the distinct `SUBSCAN`/`NEXTSUBSCAN` keywords, so it never interferes). Not
attempted: a table called indirectly through a `Sub`, a table called from more than one
differently-interval'd `Scan`, a table called from outside any `Scan` at all (e.g.
`AcceptDataRecords` -- silently skipped, no check), or the CHM's documented `GetDataRecord`-
destination-table exception. None of these gaps risk a false reject (they only mean the check
silently doesn't fire), consistent with AGENTS.md's priority order.

## Scan and table interval limits (2026-09-27, all four loggers)

Decompiled `FUN_004f6540` (the `CallTable` compile helper) keeps each interval as whole seconds
plus microseconds. The divisibility check depends on the table interval:

- Under 3601 s: compared in 100 uSec units and printed as `%d00 uSec`. The scan interval may be
  up to a day (`Scan(1,Day)` against a 1-hour table -> `DataTable interval 3600000000 uSec not
  integrally divisible by scan interval 86400000000 uSec.`).
- 3601 s or more, with a whole-second scan: compared in seconds, `DataTable interval %d Sec not
  integrally divisible by scan interval %d Sec.` (`Scan(2,Hr)` against a 3-hour table -> `10800
  Sec ... 7200 Sec.`).
- 3601 s or more with a fractional-second scan: `DataTable with interval > 1 hour cannot be called
  by scan whose interval is not an integral second is not divisible into a second.` (from the
  decompilation; not probed).
- Monthly table intervals are not checked.

`Maximum Scan interval is 1 Day.` fires on the `Scan` line when the interval truncated in its own
unit is over 86400 s (`Scan(86401,Sec)`, `Scan(1441,Min)`, `Scan(25,Hr)`, `Scan(2,Day)`). It
prints before a divisibility error. `Scan(1,Day)` is accepted.

An interval above `INT32_MAX` gets neither message. CR6/CR1000X report `Scan Interval parameter
must be a non-negative integer`, and CR350 accepts `Scan(999999999999,Sec,...)` (a fuzz-found
false reject before this). Oracle probes and `tests/test_scan_interval_limits.py`.

## `Invalid Variable or Array: %s.` (`AddPrecise`/`MovePrecise`'s `PrecisionVariable`, CR6/CR1000X only)

`AddPrecise`/`MovePrecise`'s own first argument (`PrecisionVariable`) is a `variable_or_array`
(type code 8) position on every logger that has these two instructions at all -- present in
`data/instructions.json`/`data/cr1000x/instructions.json`, **absent entirely from
`data/cr300/instructions.json` and `data/cr350/instructions.json`**, so `checker/instr_rules.py`'s
`check_precision_variable` gates on `profile.instructions` and is a no-op on those two loggers.

Oracle-confirmed (scratch probes, `CR6Comp.exe` and `cr1000xcomp.exe`, byte-identical on both):
this position rejects with `Invalid Variable or Array: <echo>.` whenever the argument has no
genuine variable reference anywhere in it -- a bare literal (`AddPrecise(1, .1)` -> `... 1.`), a
parenthesised or unary-negated constant expression (`(1+2)` -> `... (1+2).`, `-1` -> `... -1.`), a
declared `Const` name (`K` -> `... K.`), and the special float literal `NAN` (`... NAN.`) all
reject this way. A declared `Public`/`Dim` variable compiles clean regardless of how it appears --
bare, negated, parenthesised, indexed, or mixed into a larger expression with literals (`Precise`,
`-Precise`, `(Precise)`, `d(1)`, `Precise+1`, `1+2+Precise`) -- and so does an *undeclared* name
(`AddPrecise(UndeclaredFuzzName123, .1)`, the pre-existing saved fuzz false-reject reproducer,
`tests/corpus/fuzz/cr6/false_reject/fac2798b6778.CR6`; this position also never runs the ordinary
undefined-symbol check, `checker/expressions.py`'s own `_UNDEFINED_CHECK_EXEMPT_POSITIONS`). No
type restriction was found: a `String` variable is accepted here exactly like a numeric one.

The trigger is "does the argument's own name(s), if any, fail to resolve as a fixed compile-time
symbol" -- reusing `checker/expressions.py`'s existing `_undefined_names`/`_variable_refs` walk
(`not (_undefined_names(...) or _variable_refs(...))`) rather than a fresh `consteval.is_constant`
check: `NAN` is *not* `consteval`-constant (`_EXTRA_PREDEFINED_SYMBOLS` is a separate, untraced
resolution path) yet still rejects here, so plain constant-foldability alone is not what the real
compiler is testing. `X` (the second argument, a plain `expr` position) has no such restriction --
`AddPrecise(Precise, (1+2))` compiles clean.

**Pass 1, unconditional** (`checker/__init__.py`, alongside `instr_rules.check`/`check_too_many`):
oracle-confirmed to accumulate with an unrelated, earlier pass-1 declaration error in the same
file, and, run alone with no other error in the file, to itself suppress a later, otherwise-live
pass-2 finding elsewhere in the file -- the same "pass 1 found something anywhere in the file, pass
2 does not run at all" architecture this document's "Pass model" (`docs/checker.md`) already
describes for this module's other unconditional checks.

**Deliberately scoped to just these two instructions' own position 0**, not generalised to every
`variable_or_array` position: `checker/expressions.py`'s `_VARIABLE_REQUIRED_KINDS` docstring
documents a real regression at the same type code (`Move`'s third argument, `ModBusClient`'s last
argument, both oracle-confirmed to accept a literal there on CR1000X) this session has no evidence
either way for, so this stays narrow rather than guessing.

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

### `Table %s declared with no output fields.`

Also `check_table_structure` (same function, same pass-2 gate): at a properly-paired `EndTable`,
if the just-closed table's body contained no instruction from `_FIELD_PRODUCING_INSTRUCTIONS` (a
curated subset of `TABLE_ONLY_INSTRUCTIONS` -- every instruction there that is purely
configuration, `DataInterval`/`CardOut`/`TableFile`/`DataEvent`/`FillStop`/`DataTime`/
`OpenInterval`/`TableHide`, is excluded), the table declaration itself is `Table %s declared with
no output fields.` at the `EndTable` line -- oracle-confirmed with three from-scratch probes (a
body with only `DataInterval`, only `CardOut`, or nothing at all, all three give this exact message
at the `EndTable`'s own line) and a saved fuzz reproducer for the "nothing at all" shape
(`tests/corpus/fuzz/cr6/false_accept/5d5ffec92ac4.json`, a `change_interval` mutant of a real CHM
seed).

Only an `Unparsed` line (which may be the missing field) suppresses the message. Parsed
non-instruction lines inside the table (`X=1`, `Dim`, `Public`, `Const`, `Alias`, `Units`, a bare
unknown name) do not: all four loggers report it (fixture `table_no_fields.json`, 2026-10-08), and
CR6/CR1000X also still report the `Invalid, or out of place expression` for the bare name first.

Deliberately does **not** reuse `result.tables`' already-computed field *count* the way
`check_duplicate_fields` does (a `len(table.fields) == 0` test would have been simpler): `tables.py`
does not yet derive output fields for several instructions this project already knows are real
field producers (`RainFlow`/`LevelCrossing`/`Histogram4D`/`SampleFieldCal`, `docs/tdf-rules.md`'s
open gaps), so that shortcut would misreport a table containing *only* one of those as having no
output fields too. `FFT` is exactly this shape (not handled by `tables.py`'s `_emit_output_fields`
at all): it is in both `TABLE_ONLY_INSTRUCTIONS` and `_FIELD_PRODUCING_INSTRUCTIONS`. CHM corpus
files `fft_1.CR6` and CR1000X's `covspa_1.CR6` have `DataTable` bodies containing only `FFT(...)`
calls and compile, and a bare `FFT(...)` outside any table gives `FFT must be used inside a table
declaration.`. The unattributed string constant `"Et"` stays excluded.

`GoesTable`/`MQTTPublishTable` are included in `_FIELD_PRODUCING_INSTRUCTIONS` leniently, without
oracle confirmation that either alone (with no accompanying `Sample`/`GOESField`, a shape not seen
in this project's corpus) counts as a field producer by itself -- if that lenient guess is ever
wrong, the failure mode is under-reporting a table this rule should flag but doesn't, never a false
reject.

### `Duplicate field name %s in DataTable %s.`

`check_duplicate_fields` deliberately does not re-derive each output instruction's own default
field-naming rule (`_Avg`/`_Min`/`_TMx`/... suffixes, `FieldNames` renaming, reps-array expansion)
a second time; it reuses the names `tables.py` has already computed
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
exercised by the TDF/`-l` output this project has verified byte-identical. Two or more such sources in the same table collide on this one shared
sentinel and were flagged as duplicates of each other, even though the real compiler accepts the
file cleanly: `tests/corpus/chm/goestable_1.CR6` (two `Sample`s of a cross-table `HR_DATA.
battery_voltage_Avg`/`HR_DATA.panel_temperature_Avg`) went from accepted to a false reject on all
four loggers, caught by this round's own full `tools/difftest.py --logger <l>` re-run before
landing (AGENTS.md: verify a generalisation against the whole corpus, not a hand-written probe,
before trusting it -- the same methodological finding `docs/checker.md`'s "Round 3" section
records for a different check). (Since round 21 W2 a non-variable Source is named `Anonymous<n>`, docs/tdf-rules.md §9c; numbered names are distinct, so they cannot collide, and only the unnumbered `Anonymous` of an unresolved `Table.Field` remains excluded.) Fixed by excluding every field named exactly `"Anonymous"` from
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
- **GPS ports**: implemented 2026-10-01; the earlier `PPSMethod` interpretation was incorrect.
  The fourth parameter is `MaxTimeDiff`; synchronization mode is selected by the port sign.
  See [GPS ports](#gps-ports) for the measured rule and boundaries.

### GPS ports

Oracle evidence: 88 fresh CR6/CR1000X runs on 2026-10-01, including named and numeric ports,
both port signs, undefined port constants, and `MaxTimeDiff` values -1, 0, 1, 9, 10, 99, 100,
101, 199, 200, 201, 255, 256, 300, 1000. The local reference
`../../extracted_chm/Instructions/gps.htm` names the third/fourth arguments `TimeOffset` and
`MaxTimeDiff`; `../../extracted_chm/parameters/comport5.htm` explains port negation for NMEA
time synchronization. Its COMC1 PPS description is specific to CR1000X, not CR6.

The shared `instr_measure._check_gps_port` handler runs in the existing instruction-validation
tier, with profile data in `data/{,cr1000x}/measure.json`:

- CR6: legal absolute port codes are 1 (`ComRS232`) and 9..16 (`ComC1`, `ComC3`, `ComU1` through
  `ComU11`); a positive port must be 11 (`ComU1`), otherwise
  `GPS pps synchronization is only available on COMU1.`
- CR1000X: legal absolute codes are 1 and 9..12 (`ComC1`, `ComC3`, `ComC5`, `ComC7`); a positive
  port must be 9 (`ComC1`), otherwise `GPS pps synchronization is only available on COMC1.`
- Legal negative ports compile (NMEA mode), independently of the tested `MaxTimeDiff` values.
  Illegal absolute ports, including zero, emit only the logger's legal-port list; they do not
  also emit the PPS message. Undefined names are already pass-1 errors and suppress this tier.

For `Public G(15)` followed by `GPS(G(),ComC1,0,100)` inside a scan, CR6 emits only the COMU1
message; CR1000X compiles. `GPS(G(),-ComC1,0,100)` compiles on both. Numeric port `0` emits
`Only COMRS232, COMC1, COMC3, or COMU1,COMU3...COMU11 are legal.` on CR6 and
`Only COMRS232, COMC1, COMC3..COMC7 are legal.` on CR1000X.

Representative verbatim expected outputs, inputs, observation date, and compiler hashes:
`../tests/fixtures/gps_ports.json`; regression: `../tests/test_gps_ports.py`. All 88 development
probes matched ordered diagnostic output after the handler was added. CR6 help cases
`gps_2.CR6` and `structuretype_3.CR6` now match exactly. CR300/CR350 have a different GPS
signature and are deliberately not covered by this handler. Optional NMEA string-array
validation is a separate remaining rule.

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
- **`Scan %duSec invalid must be at least %dmSec.` / `Scans must be a multiple of %dmSec.`** --
  **implemented, round 6** (`checker/instr_rules.check_scan_min_interval`, `docs/checker.md` "Round
  6" item 1): the "boundary this session could not pin down cleanly" turned out to have no float-
  precision subtlety at all -- the declared `Interval` truncates toward zero *in its own declared
  unit* before multiplying by that unit's microsecond factor, which reproduces the `7999uSec`-fails/
  `7.9mSec`-compiles asymmetry exactly, with one rule and no per-unit branch.
- **`Maximum Scan interval is 1 Day.`** -- implemented 2026-09-27; see "Scan and table interval
  limits" below.
- **`ExChan must be U1-U12.`** (`BrHalf`/`BrHalf3W`/`BrHalf4W`/`BrFull`/`BrFull6W`'s own
  `ExChan`/`SEChan` positions, `FUN_005147a0`/`FUN_005177a0`) -- already noted as not implemented
  in `docs/checker.md`; not picked up this round either (no corpus mismatch currently needs it).
- **CR6's per-instruction "measurement instruction reps/range/channel/excitation" checks** beyond
  the existing `U1..U12`/CR300-CR350 channel-range check (`checker/terminals.py`) -- picked up in
  the round below ("Measurement/I-O instruction checks").

## Measurement/I-O instruction checks (`checker/instr_measure.py`, `checker/terminals.py`)

Owned by the same workstream as `checker/terminals.py`/`data/<logger>/terminals.json` (the
channel/port *terminal* model) and the new `checker/instr_measure.py`/`data/<logger>/measure.json`
(everything else: range codes, `SW12`, `Delay`, `CS616`, `TimerInput`, `ExciteV`/`ExciteI`).
`instr_measure.check` is pass 2 (`docs/checker.md` "Pass model"), wired into `checker/__init__.py`
alongside `instructions.check`; it emits one diagnostic per statement with that statement's own
line number and relies on no ordering trick of its own -- the record/line-order interleaving with
`expressions.py`'s errors and the placement-error cutoff are both handled centrally in
`checker/__init__.py`. All rules below are oracle-confirmed this round (`python3 tools/oracle.py`
CR6 scratch probes, and direct `wine <logger>comp.exe` scratch probes for CR1000X/CR300/CR350 --
`tools/oracle.py`'s own CLI always runs `CR6Comp.exe` regardless of a `.cr1x`/`.cr300`/`.cr350`
file's extension, so the other three loggers need the direct invocation the task/AGENTS.md
describes).

### Channel/port terminal model fixes and additions (`checker/terminals.py`)

**Real bug fixed, CR6 only (the session's highest-priority finding: an active false reject).**
`VoltDiff`/`VoltSe`/`TCDiff`/`TCSe`'s `DiffChan`/`SEChan` (terminals.py's pre-existing `u_terminal`
kind, unconditionally `transform: "none"`) rejected a burst-mode negative channel value
(`VoltDiff(X,1,mv5000C,-U1,0,0,15000,1,0)`, oracle-accepted) with `Invalid channel -201, must use
U1..U12.` -- confirmed by direct call before any other change this round. Root cause: the CR6
`u_terminal` kind never gained the `transform: "abs"` burst-mode allowance CR1000X's own
`diff_chan`/`se_chan` already had (`docs/loggers.md`). Split into three kinds, all oracle-verified
this round with a `U1`..`U12`/`-U1`..`-U12`/plain-integer-literal boundary sweep on `CR6Comp.exe`:

- `u_terminal_se` (`VoltSe`/`TCSe` position 4): any `U1..U12`, burst negative allowed (`transform:
  "abs"`).
- `u_terminal_diff` (`VoltDiff`/`TCDiff` position 4): burst negative allowed, **plus** a second
  constraint -- the (absolute) value must be an *odd* terminal
  (`U1,U3,U5,...,U11`); an even one in range (`U2`..`U12`) gets a distinct message, `Invalid start
  channel for Differential Measurement. Must be U1, U3, U5 ...` (`terminals.py`'s new `odd_only`/
  `odd_message` fields). `docs/checker.md`'s round-2 entry claiming `VoltDiff`'s `DiffChan` is "a
  plain small integer, not a terminal name" is now known wrong -- that session's own probe used a
  CR1000X CHM example's argument style (`DiffChan=1`) against `CR6Comp.exe` without checking the
  odd/burst rule, and mistook the two-pass gate's suppression of an *unrelated* co-occurring error
  for "no channel check at all"; corrected here with a from-scratch, isolated sweep.
- `u_terminal` (unchanged: `ACPower`, plus newly added `CS616`'s `CS616Chan` and `ExciteV`/
  `ExciteI`'s `ExChan`) keeps the old no-burst, no-oddness shape -- not re-verified for burst/odd
  behaviour on those three, so left conservative rather than guessed.

**New kinds, both oracle-confirmed on CR6 and CR1000X (absent on CR300/CR350, which never validate
either shape -- `PortSet(1,1)`/`PortSet(C1,1)` both compile clean on `cr300comp.exe`/
`cr350comp.exe`):**

- `port_c1c4_u1u12` (CR6: `PortSet` position 1, `PortGet` position 2, `CS616`'s `CS616Port`
  position 4) / `port_c1c8` (same three, CR1000X): a disjoint two-range union, `C1..C4` or
  `U1..U12` on CR6 (`Invalid port {v}, use C1..C4 or U1..U12.`) vs. `C1..C8` only (no `U`-terminal
  at all) on CR1000X (`Invalid port {v}, use C1..C8.`). Closes two long-standing corpus mismatches,
  `examples/SBD_example_hirise.CR6`/`SBD_example_hirise_SBDWB.CR6` (`docs/checker.md`'s own "Known
  remaining gaps": "a check not yet implemented ... likely the same `_check_channel_arg`-shaped
  rule ... but for a 'port' parameter accepting `C1..C4` too").
- `sdi_port` (CR6: `SDI12Recorder` position 2, odd-only `C1,C3` or `U1,U3,U5,U7,U9,U11`, message
  `Invalid SDI-12 port, must be C1,C3 or U1,U3...U11`) / `sdi_port_cr1000x` (same position, odd-only
  `C1,C3,C5,C7` only, no `U`-terminal at all, message `Invalid SDI-12 port, must be C1, C3, C5, or
  C7`) -- CR1000X's 8-control-port hardware exposes twice CR6's own `C1..C4` range for this check
  specifically, oracle-confirmed with a `C1..C8` sweep. CR300/CR350 accept any value here too.
- `excite_channel` (CR6 only: `ExciteV`/`ExciteI` position 1, same `U1..U12` range as `u_terminal`
  but its own message, `Invalid Excitation channel (must be U1-U12).`) -- both this and the shared
  `u_terminal` message fire *together* for the same bad value (oracle: a literal `1` or `C1` gets
  both `Invalid channel 1, must use U1..U12.` and `Invalid Excitation channel...` on the same line,
  in that order), so `terminals.json` lists the position twice, once per kind, rather than
  reworking the model to emit two messages from one entry.

`terminals.py` itself: `_evaluate` now returns `(in_range, odd_ok, shown)` instead of `(is_valid,
shown)`, and a kind's range may be `"ranges": [[min,max], ...]` (checked as a union) in place of a
single `"min"`/`"max"` pair, kept for the pre-existing CR1000X/CR300/CR350 `diff_chan`/`se_chan`
entries. Both changes are additive to the existing per-kind dict shape; no existing kind's
behaviour changed except the two CR6 fixes above.

### `Invalid range code, valid options are: mV5000, mV1000, mV200, or AutoRange.` (CR6/CR1000X only)

`VoltSe`/`VoltDiff`/`TCSe`/`TCDiff`/`CurrentSe`'s own `Range` argument (position 3 on all five,
`FUN_00514690`) is a named constant like any other (type code 1, not `20` -- it folds via the same
fast path as `U1` or `Sec`, so a genuinely undefined name there is `expressions.py`'s concern, not
this one's); this instruction-layered check additionally restricts the *folded value* to
`{mV5000, mV1000, mV200, AutoRange}` and their open-detection `...C` siblings (`data/symbols.json`
values `800,802,804,810,820,822,824,830`) -- every other defined range symbol (`mv2500`, `mv250`,
`mv50`, `mv25`, `mv20`, `mv7_5`, `mv2_5`, `mv10000`, and their own `...c` siblings) is rejected with
this exact message, oracle-confirmed identical on CR6 and CR1000X (`VoltDiff(X,1,mv2500,...)` fails
with this text on both `CR6Comp.exe` and `cr1000xcomp.exe`; `VoltDiff(X,1,mv5000,...)`/`mv1000`/
`mv200`/the `...c` variants all compile clean on both). **CR300/CR350 have no such check at all**:
their own symbol table simply does not define `mv5000`/`mv1000`/`mv200` (`docs/loggers.md`: "CR300/
CR350's *only* two range codes are `mv2500` and `mv34`"), so the same corpus shape surfaces there
as a plain, already-handled `mv5000 is not defined.` (`expressions.py`'s concern) -- oracle-
confirmed this round (`cr300comp.exe`/`cr350comp.exe` both reject `mv5000` as undefined, and both
compile `VoltDiff(X,1,mv2500,...)`/`mv34` clean with no warning).

### `SW12`: `Invalid SW12 port %d.`

An exact, small, per-logger set of valid ports -- both a plain small integer and the logger's own
named `SW12_n`/`P_SW12` constant resolve to *different* raw values but both compile; every other
value in the swept neighbourhood fails, oracle-confirmed by a boundary sweep (`0` through `12`, and
each logger's own named constants, `data/<logger>/symbols.json`) on each of the four compilers:

| Logger | Valid ports | Evidence |
|---|---|---|
| CR6 | `{1, 2, 501, 502}` | `SW12_1`=501/`SW12_2`=502 and their plain-integer siblings `1`/`2` all compile; `0,3..12,501..503` swept and rejected (`SW12_CSIO`=503 also rejected) |
| CR1000X | `{501, 502, 503}` (round 52) | same symbol values as CR6; `SW12_CSIO`=503 and `503.4` compile (SequentialMode; PipelineMode with a third argument), so the old `{501, 502}` set was a false reject. An invalid port goes through a reference formatting bug (Codex research on the exe, round 52: the `%d`/`%s` arguments of `Invalid port %d, must be %s.` are swapped), so `0` (and `0.4`) prints `Invalid port 6642484, must be NULL.` and `-1` printed `Invalid port 6642484, must be .` on 2026-10-27 (an earlier sweep recorded `-1` as a crash); `1`/`2`/`3`/`500`/`504`/`1000` page-fault the reference (no verdict) and keep our `Invalid SW12 port %d.`. Messages are profile data (`sw12_port_messages`); fixture `tests/fixtures/r52_sw12_ports.json` |
| CR300 | none (`SW12(Port)`, type code 25, no compile-time check at all) | `7`/`1`/`2`/`501`/`3`/`0` all compile clean |
| CR350 | `{1, 2, 9, 10}` | own `SW12_1`=9/`SW12_2`=10 plus plain `1`/`2`; `0,3..8,11,12` swept and rejected |

### `Delay`: three checks, split by `Option`

`Delay(Option, Interval, Units)` (`FUN_0045d530`/`FUN_00450e80`/`FUN_004f11d0`-family). Oracle-swept
per logger (boundary values in `min`/`hr`/`sec` units, and a string/variable `Interval`):

- `Illegal option %d` (all four loggers) when `Option` does not fold to `0`, `1` or `2` -- the shown
  value is the folded number truncated to an unsigned byte (oracle: `Option=-1` shows as `255`,
  `Option=3` shows as `3`, matching Python's own `value % 256`).
  The byte is also what is tested: `Delay(256,...)`, `257`, `258`, `-254`, `-256` and `256.5` act as
  options 0/1/2 and compile (R14, `tests/fixtures/r14_delay_option_byte.json`, fuzz reproducer
  `cr6/false_reject/77cb0ca38412`). A constant outside the Long range is a float32 whose low byte is
  0, so `999999999999`, `4294967297`, `-999999999999`, `-2147483649` and `1e38` compile; above the
  float32 maximum (`3.5e38`, `1e39`) the shown value is `255`, below it (`-1e39`) it compiles. Not
  modelled: CR300/CR350 report `255` for exactly `2147483648`, and a huge option with a `Hr`
  interval on CR300/CR350 (`Delay(4294967297,25,Hr)` gives `Max Delay is 24 Hours` there); the
  `Scan interval too short` line that follows `Measurement Delay Exceeds 60 min.` is also missing for
  `Delay(256,61,min)` (and differs by one second for `Delay(0,61,min)`) on CR6/CR1000X.
- `Delay interval cannot be of type String.` (all four loggers) for String intervals with options
  0 and 1. The same diagnostic follows an illegal option number. The rule covers literals,
  variables, aliases, and String-valued expressions such as concatenation and `FormatLong`.
  With option 2, a String interval gives `SDM Delay interval must be constant.`, including a
  String literal or `Const`. A local declared after the `Delay` does not shadow the global at that
  use site. See the dated probes and compiler hashes in `tests/fixtures/r81_delay_interval_type.json`.
  Round 83 covers String fields of `Status` and user structures, and `Public` fields on
  CR300/CR350. CR300 rejects reads of `Status.RecipeStatus`. Direct `Public` field probes on
  CR6/CR1000X faulted in the reference compiler. Usable observations and compiler hashes are in
  `tests/fixtures/r83_delay_member_type.json`.
- Then, by `Option`:
  - `Option=0` (measurement-task-sequence delay) -- **CR6/CR1000X only** (oracle: CR300/CR350 both
    compile `Delay(0,61,min)`/a variable interval clean, no error at all): `Interval` must itself be
    a compile-time constant (`Measurement delay must have constant interval.`) and, once resolved,
    at most 60 minutes inclusive (`60,min` compiles; `61,min` / `3601` seconds worth in any unit
    fails with `Measurement Delay Exceeds 60 min.`).
  - `Option=1` (processing delay) -- **all four loggers**: no constant-interval requirement (a
    variable compiles clean, unlike `Option=0`), but the resolved interval must be strictly less
    than 24 hours (`23.999,hr` compiles; `24,hr`/`1440,min`/`86400,sec` all fail with `Max Delay is
    24 Hours`, oracle-confirmed identical text and boundary on all four compilers).
  - `Option=2` (SDM delay) -- **all four loggers**: `Interval` must be a compile-time constant
    (`SDM Delay interval must be constant.`); no duration limit found (`24hr`/`25hr` both compile).

### `CS616`: `Invalid reps %d, must be > 0` (CR6 only)

`CS616(Dest,Reps,CS616Chan,CS616Port,MeasPerPort,Mult,Offset)`'s `Reps` (position 2) must be a
positive folded constant; `0` and negative values fail with this exact text. Not re-verified on
CR1000X/CR300/CR350 this round (`CS616Chan`/`CS616Port` themselves are covered by the terminal-model
entries above, which *are* per-logger); left CR6-only rather than guessed.

### `TimerInput`: `TimerInput starting channel must be C1 or U1.` (CR6 only)

`TimerInput(Dest,StartChan,Edge,Function,Timeout,Timeout_Units)`'s `StartChan` (position 2) must
fold to exactly `C1` or `U1` -- every other terminal in either family (`C2`, `U2`, ...) fails with
this text, oracle-confirmed with a `C1/C2/U1/U2` sweep. Not re-verified on CR1000X/CR300/CR350.

### `ExciteV`/`ExciteI`: `Excitation exceeds 2500.000000.` (CR6 only)

`ExciteV(ExChan,ExmV,Delay)`/`ExciteI(ExChan,ExmI,Delay)`'s excitation magnitude (position 2) must
have `|value| <= 2500`; `2500` compiles, `2501` (and `-2501`) fail with this exact text (note the
message's own `%f`-style fixed six-decimal formatting of the *limit*, not the input value).
**Deliberately not applied to CR1000X**: oracle-confirmed `ExciteV(Vx1,2501,0)` (CR1000X's own
excitation-channel name) compiles clean at `2501` and even `2500`-plus values were not found to have
any limit in the range swept -- applying the CR6 threshold there would be a false reject, so this
check is CR6-only rather than logger-unconditional. `ExciteI` does not exist as an instruction on
CR1000X/CR300/CR350 at all (`docs/loggers.md`), so it is moot there regardless.

### Investigated, not implemented (this round)

- **`AM25T`'s own channel/excitation checks** -- a boundary sweep of `AM25TChan` (1, 16, 17, 0)
  against `CS1000, AM25T(...)` all reported the *same* `Invalid channel -1, must use U1..U12.`
  regardless of the swept value, meaning some *other* argument (not `AM25TChan`) is what that
  message is actually validating in this probe's shape; not chased further to avoid guessing at an
  argument mapping this round could not isolate cleanly.
- **`PortsConfig`'s `Function` argument** -- swept `0..5` and `-1`, all compiled clean (type code 2,
  no compile-time validation found).
- **`WriteIO`/`ReadIO`'s `Mask` argument** -- swept `0, 255, 256, -1, 1000`, all compiled clean (type
  code 1, no range restriction found).
- **`SDI12Recorder`'s `SDIAddress` argument** -- swept `0, 9, 10, -1, "A", "Z", "z"`, all compiled
  clean (resolved at run time, not compile time).
- **General terminal-allocation-conflict tracking** (`%s: channel %c%d already in use or
  unavailable.`, `FUN_0050cf90`) -- decompiled call-site tracing found this shared validator is
  called from many instructions' own handlers, including ones in this document's scope
  (`TimerInput`, `PulseCount`, the channel-range function `VoltSe`/etc. itself use), but the exact
  conflict semantics (which instruction/position pairs actually collide, and the meaning of each
  call site's own flags word) were not isolated with confidence this round; implementing a plausible
  but wrong version of this risks the worst failure mode (AGENTS.md), so it stays unimplemented.
- **`Resistance`/`PeriodAvg`/`Pulsecount`'s own excitation/gain/reps checks** -- not reached this
  round (time budget); `Pulsecount`'s low-level-AC-pulse "even U channel only" restriction and its
  own reps range are both real (`messages.tsv`, `FUN_00510250`) but not oracle-probed yet.

## SDM addresses (2026-09-28, CR6/CR1000X)

`Invalid SDM Address: %d, must be 0 - 14.` (decompiled `FUN_0043c680`) for a constant address
whose low byte is 15 or more: `SDMX50 (-1,4)` prints `255`, and `SDMX50 (256,4)` is accepted. It
is a pass-2 error that accumulates with others (`instr_rules.check_sdm_address`,
`tests/test_sdm_address.py`). The address position was measured per instruction by setting each
numeric argument of a chm example to 99: 0 for `SDMX50`; 1 for `EC100`, `EC100Configure`,
`SDMGeneric`, `TDR100` and `TDR200`; 2 for `CS7500`, `CSAT3`, `CSAT3B`, `CSAT3BMonitor`, `LI7200`,
`LI7700`, `SDMCD16AC`, `SDMCD16Mask`, `SDMIO16`, `SDMSIO4` and `SDMSW8A`. Not measured: `SDMAO4`,
`SDMAO4A`, `SDMCVO4`, `SDMINT8`, `SDMCAN`. `SerialInRecord`/`SerialOutBlock` reach the same
check only for SDM ports.

**Float typing of the address** (`r7_sdm_float_typing`, CR6/CR1000X, 2026-10-10): the float32 rule
(a float-typed address goes through float32; one outside Long range is accepted unchecked) follows
the *type* of the constant expression: a decimal literal, `Const` of one (also through `Const B=A`),
unary `+`/`-`, and any `+ - * \\ MOD` with a float operand are float; `/`, `^` and `Int()` are always
float; `Abs` keeps its operand's type. `2147483647`, `2147483646+1`, `Abs(2147483647)` and
`2147483647 MOD 3000000000` stay Long (`255`); `+2147483647.0`, `2147483647.0*1`, `Int(2147483647)`,
`2147483647/1` are accepted. `consteval.is_float`; a float-typed `Const` is stored as `CFloat` (an
integer-typed one as `int`) so the typing survives the checker's `dict(program.consts)` copies.
Bitwise and shift operators are not probed and keep the old value-based reading.
## SerialIn destination

The supplied four compiler versions require `SerialIn`'s first argument to be a String
variable or String array element. A Float, Long, Boolean, or UINT1 destination, a numeric or
string literal, a named constant, and an expression such as `Dest+1` produce
`A string variable destination is required` (no final period). String destinations and aliases
of String storage compile; aliases of Long storage fail. Parenthesized String variables compile;
runtime String concatenation and String-producing `FormatFloat`/`CHR` calls fail. This is specific
to `SerialIn`, not a
uniform rule for its parameter type code 33. The local `serialin.htm` reference mentions UINT1
receivers in newer OS behavior, but the supplied executable probes reject them; this documents
the observed versions rather than inferring behavior from that note.

This check belongs to pass 1: it survives an earlier unrelated declaration/shape error and
suppresses CR6/CR1000X pass-2 errors elsewhere in the file. On CR6/CR1000X, missing or excess
arguments still follow the destination error. An undefined destination instead keeps the
ordinary `Invalid symbol in expression: Dest.` result. CR300/CR350 additionally report the
next argument token as `Too many parameters:  ComRS232.`, ending the argument list before any
ordinary count checks. The same destination check still runs on subsequent `SerialIn` statements
after this starts the CR300/CR350 cascade. An undefined bare destination on these profiles
reports the undefined name, then the String-destination error, then the next-token error.

Evidence: 100 fresh observations, recorded with executable SHA256 identities in
`tests/fixtures/serialin_destination.json` and replayed by `tests/test_serialin_destination.py`.
They cover all four profiles, scalar/array types, aliases, constants/literals/expressions,
undefined names, earlier errors, repeated statements, following instructions, and short/excess
argument lists, and String expression boundaries. Structure-member and scoped parameter type
resolution remain separate gaps.
Implementation: `instr_rules.check_serialin_destination` in pass 1, with the existing CR300/CR350
argument-abort and cascade recovery retaining the String-destination diagnostic.

## SetSetting and SetStatus field names

`SetSetting("Name", value)` and `SetStatus("Name", value)` look their constant first argument up
in the logger's field tables (`FUN_004610e0`). A name that is not found gives
`Name is not a Settings Field Name.` (`SetSetting`) or `Name is not a Status or Settings Field
Name.` (`SetStatus`). `Name` is printed exactly as written, including any trailing blanks, and an
empty string prints `NULL` (CR6, CR1000X) or `(null)` (CR300, CR350).

Field tables (`data/**/settings_fields.json`). The Settings table is a pointer array in each exe
(entry stride 0x4c on CR6/CR1000X, 0x48 on CR300/CR350; the entry's first word points at the name).
Every candidate name from the four exes' tables plus a few extras was then compiled through
`SetSetting` and `SetStatus` on each logger, 40 names per program. `SetSetting` accepts exactly
the Settings table (CR6 257 names, CR1000X 259, CR300 209, CR350 232). `SetStatus` accepts the
Settings table plus the Status table (additional names: 55, 52, 40, 50). The lists differ per
logger: `CPUDriveFree` is a Settings name on CR6/CR1000X/CR300 but a Status-only name on CR350,
`USRDriveSize` is a Settings name on CR6/CR1000X and in neither list on CR300/CR350, and
`DisableTcpDelayedAck` is in no list. The walked CR300/CR350 Status tables also contain six
`DataTableName`-style fields that are rejected, so the lists come from the oracle result, not from
the walk. Status-only names such as `Battery` or `OSVersion` give
`is not a Settings Field Name.` under `SetSetting`.

Spelling rules, identical on all four loggers unless noted:

- Matching is case-insensitive; the message echoes the source spelling.
- CR6/CR1000X ignore trailing spaces and tabs when matching (`"PakBusAddress  "` is accepted);
  CR300/CR350 do not (`PakBusAddress   is not a Settings Field Name.`). Leading blanks are never
  ignored. Other trailing characters (`\x0b`, `\x0c`, `\xa0`, `\x01`) are not trimmed.
- The name must be a compile-time constant: a string literal, a String `Const`, or `+` of those
  (`"Pak"+"BusAddress"` is accepted, `"Pak"+"Foo"` reports `PakFoo`). `CHR(9)`, variables and
  non-strings give `Status Field name must be constant.` (not implemented, so such arguments are
  skipped).
- `Name(index)`: only the text before the first `(` is looked up (`Foo(1)` reports `Foo`,
  `DNS(1)`, `Security(2)` pass). The index is then checked (see "Field index dimension" below for
  the two fields modelled). An empty name before `(` is skipped.
- A name containing `.` reports `Cannot find <text before the first dot>` (no final period) for
  every table name tried, including `Status`, `Settings` and a `DataTable`. A declared
  `ConstTable` prefix (`ScanConstTable.ScanConst`, as in the repository's `PowerProfile_tester.CR6`)
  is valid; its field lookup is not modelled (CR300/CR350 print `Cannot find <field>` for an
  unknown field; CR6/CR1000X crashed under Wine on those probes, so there is no CR6 evidence).


### Field index dimension (`settings_fields.json` `index_size`) {#field-index-dimension}

`Illegal dimension N` (no final period) for `Baudrate(index)` and `Security(index)`, constant
index only (fixture `tests/fixtures/settings_index.json`, all four loggers, `SetSetting` and
`SetStatus` alike). The index is a decimal integer, or a bare identifier: a predefined symbol
contributes its value (`ComRS232` is 1 on CR6/CR1000X, 2 on CR300/CR350; `ComC1` is 9 on
CR6/CR1000X and unknown on CR300/CR350), any other identifier (including a Public variable) is 0.
Valid values are `1..size`; otherwise `N` is the value (`Baudrate(COMC1)` on CR300/CR350 gives
`Illegal dimension 0`). Sizes: `Baudrate` CR6 18, CR1000X 14, CR300 8, CR350 6; `Security` 3 on all.
Evidence: every `COM*` symbol of every logger plus numerics probed, and non-COM symbols (`Sec`,
`Min`, `True`, `Pi`, ...) behaved as predicted. It runs in the same phase as the name check
(same ordering, gating, `CallTable` stop line); an unknown field name reports only the name.
Not modelled: other fields' sizes, expression or space-padded indices, a missing `)`, and `CS I`.

Ordering and gating (CR6/CR1000X versus CR300/CR350):

- It is not a pass-1 check: an earlier or later declaration or pass-1 error (`Variable Type
  Bogus not supported`, `Too many parameters`, wrong argument count) suppresses it, anywhere in
  the file.
- CR6/CR1000X: a tier-B finding (`Invalid symbol in expression`) does not suppress it, and it does
  not suppress later tier-C findings (`Invalid SW12 port 9.` appears together with it). On one
  line the argument error comes first (`Invalid symbol in expression: Bar.` then `Foo is not a ...`).
  It is hidden after a placement stop (`Sw12 illegally occurs before BeginProg.` on an earlier
  line; CR6 only, the CR1000X compiler crashed under Wine on that probe) and after `Could not find DataTable X.` on an earlier line; before such a line it is
  reported. The unclosed-block end checks (`IF or SELECT without EndIf or EndSelect.`) do not run
  once it has fired.
- CR300/CR350: an undefined name in the same statement prints only `Bar is not defined.`; any
  earlier or later non-`CallTable` error (undefined name, pass-1 error) hides the field-name
  error, while the `Invalid port specified 103` tier-C error and `Could not find DataTable` do not.
  Once a hiding error has started the `Too many parameters` cascade (`checker/cr3xx.py`) the
  later `SetSetting` line prints `Too many parameters:  5.` instead. A field-name error does not
  itself start the cascade.
- Applies inside `Sub`, `Function`, `SlowSequence`, `Scan`, one-line `If` and before `BeginProg`
  (a constant `SetSetting` before `BeginProg` is accepted when the name is valid). Statements
  after `EndProg` are not checked.

Not implemented (observed, separate rules): `X is read only.` (valid but read-only fields, for
example `SetStatus("Battery", 5)` and `SetSetting("CPUDriveFree", 5)`), `Status Field name must
be constant.`, `Illegal dimension N`, ConstTable field lookup, and the `(1)` name whose message
prints `1)`.

Evidence: 474 compiled programs plus an eight-part name sweep in `tests/fixtures/settings_fields.json` (compiler SHA-256 values and
observation date 2026-10-02 inside), replayed by `tests/test_settings_fields.py`. They cover all
four profiles and the candidate-name acceptance sweep. Probe sources are not kept. The ConstTable
and `Name(index)` rules above are partly outside the fixture (`Illegal dimension`,
ConstTable field lookup) and are described from the same-day probes only.
Implementation: `instr_rules.check_settings_fields`, called from `checker/__init__.py`
(CR6/CR1000X in the tier-B block, CR300/CR350 in the late section), findings collected aside so
tier C is not hidden.

## Variable data types (`checker/var_types.py`)

Per-instruction type requirements on variable arguments, pass 2 tier C (run after
`check_dest_bounds`; findings sort by line, so a bounds error on the same statement stays first).
There is no uniform rule per type code: the "variable-required" codes 27/29 only say a variable is
needed, and each instruction then applies its own type test. Fixture
`tests/fixtures/var_types.json` (real-compiler output, compiler SHA-256 and date 2026-10-02 inside),
replayed by `tests/test_var_types.py`.

- `RoutersNeighbors(Dest)` (all four loggers): the destination must be a `Long` variable or array
  element, else `Destination variable must be type LONG`. A string literal gives the same message;
  a number literal is still `Invalid Variable or Array`. Float, String, Boolean and Double all fail.
- `SortSpaIndexed(Dest,Reps,Source)` (CR6/CR1000X; CR300/CR350 lack the instruction): `Source and
  Dest types must be the same.` unless the types are equal, with one oddity, a String `Dest` with a
  Float `Source` is accepted. `Source` is an expression with an inferred type: an integer or `&H`
  literal, `Const` integer, `TRUE`/`FALSE` and `+ - *` of Longs are Long; a literal with `.` or `E`,
  `NaN`, a `/` and any mix with a Float are Float; a String literal is String; variables (through
  `Alias`) have their declared type. Functions, comparisons and other operators are not typed
  (`Int(3.5)` is Float for the compiler but unknown here, so it stays silent). A scalar `Source`
  also draws an out-of-bounds error (destination-bounds rule, not here).
- Parenthesised dimension (all four loggers): a `(` anywhere in a variable's dimension list,
  `Public A((1+2),5) As Long`, `A(3,2*(1+1))`, `A(Abs(3))`, makes the variable Float whatever its
  `As` type (seen through `RoutersNeighbors` and `SortSpaIndexed`; String and Boolean too). The
  parser sets `VarDecl.paren_dim` and `symbols.declared_variable_types` returns FLOAT for it. The
  dimension following the type (`As Float(5)`) was not probed.
- `SDMGeneric(Dest,...,Source,...,BytesPerValue,...)` (CR6/CR1000X): when both `Dest` and
  `Source` are declared `String` variables, a compile-time `BytesPerValue` other than 1 gives
  `If source and destination are of type STRING, bytes per value must be 1`; 1 is accepted. A
  numeric source with a String destination is not subject to this rule. Observed on both supported
  profiles on 2026-10-03; fixture `tests/fixtures/table_allocation.json`.

Probed, not implemented: `Median` of a String (`Type must be Float, FP2 or Long`),
`Sample(...,Bool8)` with Reps 1.

### `Sample` with data type `Nsec`

Same module, all of CR6/CR1000X/CR300/CR350 (fixture cases `*-sample-nsec-*`). `Sample(Reps,Source,
Nsec)` with a String, Boolean or Double source (variable, element, array, or a String expression
such as `S+"x"`) gives `Source type must be either LONG or FLOAT or NSEC for data type NSEC`. A
Float source (variable, literal or expression; an expression counts as one element) needs the
variable's whole array to hold at least `7*Reps` elements, else `Float to NSEC expects source array
size >= 7` (`FB(7)` Reps 1 passes, also as `FB(2)`; Reps 2 passes for 14 elements, fails for 13).
This one is skipped when the statement already has an error (an out-of-bounds finding replaces it).
Long sources and integer literals pass. Only `Sample` has the rule: `Maximum(1,S,Nsec,...)` compiles.

### SDMCD16Mask/SDMCD16AC, SNMPVariable, MenuRecompile

Same module (fixture cases `*-sdmcd16-*`, `*-snmpvariable-*`, `*-menurecompile-*`).

- `SDMCD16Mask(Source,Mask,Command)` (CR6/CR1000X): a Boolean `Mask` or Boolean `Source` variable gives
  `Source and Mask type must be LONG.`; Float, String, Double, Long, literal masks and expression
  sources all pass. A literal or `Const` `Source` (also `SDMCD16AC`) gives `Invalid Source.`, after the
  type message when both apply. A Boolean `Source` additionally gets `Variable X out of bounds.`, which
  is not modelled (so that shape stays a message difference).
- `SNMPVariable(Name,OID,Type,...)` (a dotted OID is an `Unparsed` statement, so the rule reads its
  text): `Miss match of types` when the declared variable type and the `Type` keyword disagree. Float
  variables accept only `Float`; String variables `String` or `Float`; Long, Boolean and Double
  variables anything but `String`. Omitted type always passes. On CR300/CR350 the message also starts
  the `Too many parameters` cascade (`cr3xx._is_cascade_trigger`); `RoutersNeighbors` does not.
- `MenuRecompile(Text,Var)`: a String variable, element or `Const` string gives `String type variable
  not allowed.`; `MenuItem`/`DisplayValue` accept strings. A numeric `Const` gives `Invalid Variable or
  Array: A.` (not modelled here).

### String operands

Same module (fixture cases `*-string-operand-*`, all four loggers). A String-typed operand (variable,
element, string `Const`/literal, a String-returning built-in such as `Mid`, `FormatFloat`, `Chr`,
`Hex`, `Trim` or `UpperCase` (function `kind` 312, `docs/binary.md`; `CType` only with `String` as its
second argument, round 67), a `Function ... As String`, an
`IIF` with a String second argument, or a `+`/`&` with a String on either side) in
`AND OR XOR IMP EQV << >>` gives `String is illegal here`; in `* / \ MOD` the left String gives
`Illegal String operand`, the right one `Illegal operand of type STRING` (both when both). `+ - & ^`,
comparisons, `NOT` and unary minus accept strings (`S` alone in `If S Then` passes); `-` and `^` give a
number, so `(Mid(S,1,1) - 1) * 2` compiles while `(2000 + Mid(S,5,2)) * 2` does not. Found in
assignments, `If`/`ElseIf` conditions, instruction and call arguments. Every offending operator
reports, in source order with an operator after its left operand
(`(S * 2) + (S AND 1)`, `(S * 2) * S`; round 65, `r65_string_operands.json`). Before round 65 a
String built-in, a mixed `+` and a statement with several offenders were false accepts.

### `CType` argument forms (`var_types.check_ctype`, `expressions._call_arity_problem`)

Pass 1, all four loggers (fixture cases `*-ctype-*`). `CType(Value,Type)` takes a type keyword `Long`,
`Double`, `Float` or `String`. Each call written with no type argument, or with `Boolean`, `UINT2`, a
`Const` name or a constant numeric expression such as `(1+2)` as the type, prints `Illegal type` (once
per call, so two bad calls print two). A one-argument call also prints the ordinary `Expression
expects , : <echo>.` after it, a three-argument call only `No Matching, or out of place )` (no `Illegal
type`). The function table lists `CType` with `nargs` 0, so `_call_arity_problem` special-cases it.
A variable, undefined name, string or any other constant type is `Illegal type` too since round 41
([CType type arguments](checker.md#ctype-type-arguments-round-41)); the further message each prints
after it is not modelled, nor are zero or empty arguments (`Illegal type` plus `Invalid, or out of
place expression`).

Echo detail found on the way, affecting every arity message on CR300/CR350: the closing `.` goes on
its own line only when the echo is a statement that reaches the end of its line; a trailing `'`
comment keeps it on the same line (`Stmt.at_eol`), and an echo of an instruction *argument*
(`Randomize(Round(1.0))`) never has the line break.

## Constant-parameter range checks (2026-10-02, fixture `tests/fixtures/const_ranges.json`)

Observed with the four real compilers (sha256 in the fixture); `tests/test_const_ranges.py` replays
the verbatim output. Common to every check below: a constant parameter converts as C's `(long)`
does -- truncated toward zero (`0.5` -> 0, `-0.5` -> 0), `TRUE` is -1, a string constant or a
value outside Long range becomes `-2147483648`. A variable in the position is the separate
`Variable illegal in Parameter` error.

### `number of averages %d and reps %d in RunningAvg must be > 0.` (all four loggers)

Not only `AvgRun`: `TotalRun`, `MinRun`, `MaxRun` and (CR6/CR1000X) `StdDevRun` share it, for
`Reps` (position 2) or `Number` (position 4) below 1. Both values are printed after the
conversion above (`"fuzz"` -> `-2147483648`, `999999999999` -> `-2147483648`, `0.5` -> `0`).
Implemented in `check_avgrun_options`, so the gating is the one already recorded for `AvgRun`
(reported alongside pass 2 without hiding tier C on CR6/CR1000X; only in a clean file on
CR300/CR350). The conversion is `longconv.long_value` (`docs/checker.md` "Option arguments"), so a literal
`-2147483648` prints `-2147483647` on CR300/CR350 (observed 2026-10-03). Not covered: `Variable %s out of bounds.` that accompanies a negative or overflowing `Reps` is a
separate check.

### Data-driven rules (`measure.json` `const_ranges`)

`instr_measure.check_const_ranges` reads a per-logger list of `{instr, pos, min/max or values,
message}` rules; `{value}` in the message is the converted number (or that of the parameter at
`show`). Placement in `checker/__init__.py`: on CR6/CR1000X the findings are collected aside like
the index bounds (reported with pass 2, cut off at a placement error, neither hiding the tier-C
channel checks nor hidden by tier-B findings -- observed: a bad `SemaphoreGet` beside a bad
`VoltDiff` start channel reports both, and `X = Foo` before or after it does not hide it); on
CR300/CR350 they run with the tier-C checks, so any earlier `... is not defined.` hides them.
Pass-1 errors hide every rule (`Dim Z banana`, a variable in the position, a bad `Range` code).

### CDM / SDM / TDR constant channels (`CR6` / `CR1000X`)

Oracle boundary sweeps on 2026-10-03 (both compilers; CR300/CR350 do not define these
instructions) established the following `const_ranges` entries, covered by
`tests/fixtures/value_ranges.json`:

- `CDM_BrHalf3W` position 6: `1..16`, emitting `Input channel: %d, out of range (1 - 16).`.
  A separate, ordered entry emits `Reps cycle input beyond last channel!` for values `16` and
  above, but not for `0`; channel 16 itself therefore has only that second diagnostic.
- `CDM_TCComp` position 5: `1..20`, with the same `Input channel` spelling and that range.
- `CDM_PanelTemp` position 5: channels 1–2 on CDM-A108 and VOLT108, 1–4 on CDM-A116,
  VOLT116, and the 408-series modules, and 1–30 on TEMP120. TEMP120 reports a separate
  diagnostic for negative channels. The builder substitutes channel 1 when checking repetitions
  after an out-of-range channel. Round 86 observations and hashes are in
  `tests/fixtures/r86_paneltemp_channels.json`.
- `SDMSw8a` position 5: `1..8`, emitting `Invalid Start Channel: %d, must be 1-8.`; a string
  displays as `0` for this instruction.
- `TDR200` position 7: `100..10112`, emitting `Points %d is illegal: range is 100..10112.`.

These share the existing CR6/CR1000X deferred `const_ranges` phase: a pass-1 error hides them,
while their diagnostics remain ordered after tier-C instruction checks. The CDM ranges were
tested at both accepted endpoints and one value outside each side where applicable.

### `FileManage` drive and `DNP` ComPort constants

`FileManage` with a literal string whose first four characters, upper-cased, are not a valid
drive plus colon emits `FileManage invalid drive: <first four characters>.` (probed `"SD:a.crb"` ->
`SD:a`, `"a.crb"` -> `a.cr`, `"CPU"` -> `CPU`, `"CP"`, `""`; case-insensitive: `"cpu:a.crb"`
compiles). Valid drives: CR6/CR1000X `CPU CRD USR USB`; **CR300/CR350 `CPU` only** (`CRD:`, `USR:`,
`USB:` all rejected, fixture `tests/fixtures/filemanage_drives.json`). This deliberately models
the drive check and the extension rule below. It is a tier-C instruction check.

`RunProgram` shares both checks (same messages, `FileManage ...` wording). **Extension** (all four
loggers, fixture `tests/fixtures/r5cr6_filemanage_ext.json`, 2026-10-08): when the constant option
(second argument, C-converted) has bit 1 or 2 set (`option & 6`: 2..7, 10, 100, -1, `True`; not 0, 1,
8, 9, 16, 17, 24, 64, 1000 or -8), the text after the *last* `.` of the constant name, case-insensitive,
must start with `crb` or the logger's own extension (`cr6`, `cr1x`, `cr300`, `cr350`; `cr6x` and
`crbx` pass, `cr1`, `cr`, empty and a name without `.` do not), else `FileManage name <name> invalid
extension.` Only the **first** bad `FileManage`/`RunProgram` of the program is reported, drive or
extension (a later one is silent), and a bad drive hides the extension check of the same name.

### `TableFile` drive and file-type option (`measure.json` `tablefile`)

Constant file name (a `+`/`&` concatenation of literals; `Status.SerialNumber` folds to the empty
string), tier-C check, fixture `tests/fixtures/tablefile_drives.json`:

- First four characters, upper-cased, must be a valid drive plus colon, else
  `Invalid drive or no drive specified in filename <name>.` Valid: CR6/CR1000X `CRD USB USR`;
  CR300 `CPU`; CR350 `CPU USB` (probed `CRD`, `USB`, `USR`, `CPU`, `SD`, no drive on all four).
- CR300/CR350 only: with `r` = C remainder of the option by 100, `Invalid file type option.` when
  `|r|` is 24..31 or >= 40 (swept 0..1099 and a negative sample on CR300 `CPU:`; swept 0..299 on
  CR350; the hundreds digit is ignored, e.g. 512 and 1024 behave as 12 and 24).
- CR350 only: on `USB:`, when the magnitude of the remainder is 64 or above, `TOB3 TableFile
  output only allowed on CRD: drive.` precedes the file-type error.
- CR6/CR1000X (round 97): take the option's magnitude as a signed Long, then subtract 100
  for 100–135 and 356–363. Bit `0x40` selects TOB3 and requires `CRD:`. Otherwise the
  shifted format code (arithmetic right shift by three) must be 0, 1, 2, 4, 8, 32, 64 or 128.
  An invalid drive ends these checks. The CR6 builder is `FUN_004cccb0`.
- All four profiles require the last filename result variable to have String type, including
  through an alias. Literal arguments do not request a result variable.
  [Round 97 probes](../tests/fixtures/r97_tablefile_options.json).
- Open cases include the required result-string capacity, `Invalid filename.` for an empty
  name, and file names that use the current value of a variable.

`DNP` argument 1 emits `ComPort -2147483648 illegal.` for a string or `NAN` on all four loggers.
CR6/CR1000X additionally emit it for a finite number outside Long range and literal
`-2147483648`; all four accept `INF`, `-1`, `1.5`, and a numeric TCP port such as `20000`.
The rule is data-driven through `const_ranges`' `invalid_raw` form so it preserves the observed
CR300/CR350 distinction: their literal `-2147483648` and `2147483648` compile, whereas strings
still fail. These checks share the existing constant-range phase and are hidden by a pass-1 error.

### GPS `NMEASentences` structure member (`CR6` / `CR1000X`)

When `GPS` receives a variable of a `StructureType` containing a `String` member named
`NMEASentences`, its declared string size below 96 emits at the `GPS` line:
`Size of NMEASentences is too small, must be at least 100`. The real compiler's printed minimum
does not equal the source boundary: `String * 95` fails and `String * 96` succeeds on both
loggers (swept from 0 through the boundary). The rule is data-driven under each profile's `gps`
configuration and runs before the existing GPS port diagnostic, matching CR6's ordered combined
output. It is not configured for CR300/CR350, whose GPS signature differs.

### `SemaphoreGet` / `SemaphoreRelease`: `Illegal Semaphore...`

The constant must convert to 1..4 on CR6/CR1000X (`Illegal Semaphore: 1..4 are legal, 1 to 3 are
general purpose, 4 is reserved (see docs)`) and 1..3 on CR300/CR350 (`Illegal Semaphore, 1..3 are
legal`). `1.9` is accepted (truncation), `0.9`, `-1`, `TRUE`, strings, `NAN` and `K*3 = 6` are not.
Not modelled: before `BeginProg` the real compiler reports this value error ahead of the
`illegally occurs before BeginProg.` placement error of the same line (we print the placement
error first, or on CR300/CR350 only that one).

### `ACPower`: `Legal configuration is 1..3.` (CR6/CR1000X)

`ConfigAC` (position 2) must convert to 1..3 (`0`, `4`, `-1`, `0.9`, strings and `TRUE` fail, `3.9`
passes). Printed before any channel errors of the same statement. Known gap: on CR6 the real
compiler also reports its `Invalid channel N, must use U1..U12.` series for `ACPower` channels
(and CR1000X `must use 1..16.`) beside it; those are not implemented, so such a file matches only
when its channels are valid.

### `Broadcast`: `Illegal message type %d` (all four loggers)

The `Message` constant (position 2) must convert to 0, 12, 13 or 14 (0..20 plus -1, 255, 256, `14.9`, `12.5`,
`TRUE`, a string swept on all four loggers, 2026-10-03; accept/reject identical on each). The converted number is printed (`14.9` is accepted,
`TRUE` prints `-1`, a string or `999999999999` prints `-2147483648`; CR300/CR350 saturate a bare
literal, `2147483648` -> `2147483647`, `-2147483648` -> `-2147483647`). The number comes from
`longconv.long_value`, shared with the `const_ranges` and `AvgRun` paths (`65536*65536+12` wraps to 12
and is accepted; `2147483648*1.0` prints `-2147483648` even on CR300). Not modelled: float32
rounding of values within 2^31 of the Long limit (`2^31-100` prints `2147483520`). Same tier as the other
`const_ranges` rules. Not modelled: `Broadcast` before `BeginProg`, where the real compilers print
only this error (no placement error) while we add the placement one.

### `StaticRoute`: `Illegal neighbor address %d, range 1..4095` / `Illegal PakBus address %d, range 1..4095` (all four loggers)

`StaticRoute(ComPort, NeighborAddr, PakBusAddr)`: both addresses must be 1..4094 (the message says
`1..4095`, but 4095 is rejected). Each is reduced to its low 16 bits first (`65537` is accepted as
1, `-1` prints `65535`, `70000` prints `4464`); a string or NaN is 0. The `PakBus address` message
prints the *neighbor* value (`StaticRoute(ComRS232,2,"x")` gives `Illegal PakBus address 2`), and
a statement with both wrong prints both messages, neighbor first. Rule options `wrap16`/`show`/
`overflow` in `measure.json`. A finite number outside Long range is 0 on CR6/CR1000X; on
CR300/CR350 it varies (`2147483648` prints 65535, `4294967296` prints 0, a literal `-2147483648`
is accepted), so those are skipped (`overflow: null`). Infinity (prints 65535) is not modelled.
Declarations run before `BeginProg`, and the same gating applies as for the other rules: a `Dim`
error anywhere hides them. Other `PakBus address` parameters (`Route`, `PakBusClock`, `ClockReport`,
`GetVariables`) have no compile-time range check (0, 4095, -1, 5000 all accepted, CR6 and CR350).

### Analog settling time (CR6/CR1000X, round 24)

Fixture `tests/fixtures/r24_dest_counts.json` (`measure.json` `const_ranges`). CR6Comp validates the
settling time of its analog measurement builders in one helper (`FUN_00516740`, called from the
VoltSe/VoltDiff/bridge helper `FUN_005147a0`, Resistance `FUN_005177a0`, CurrentSe `FUN_00519a90`,
Thermistor, AM25T, Therm107-109). The value converts as C `(long)` (a float past the range or a
string is `-2147483648`); 0 means the default. A nonzero value below 20 is `Settling time %d too
short, min time is 20.`, a value above 6000000 `Settling time %d too long, max time is 6000000.`,
after the line's channel error. Probed on CR6 and CR1000X for `VoltSE`, `VoltDiff` (6th argument),
`TCSE`, `TCDiff` (8th), `BrHalf` (9th), `BrFull` (10th) and `CurrentSE` (6th); CR300/CR350 did not
reach the check with these programs. The other callers are not modelled. The settling time also
counts, unclamped, in the scan budget (round 31: `docs/checker.md`, "Bridge channel spans and
settling-time timing", which also adds BrHalf3W/4W/6W and the CR300/CR350 range 10..50000).

## Instruction-resource and pairing checks (2026-10-03, fixture `tests/fixtures/instruction_resources.json`)

Fixture cases are real-compiler observations (`tests/test_instruction_resources.py`); each family
below lists what was probed and what was left unchecked.

### EmailRecv destination (CR6/CR1000X)
`A string variable destination is required` (no period) is reported for `EmailRecv` when argument 9
is missing or when any argument from 9 on is a Float/Long variable or element, a named constant, or
a literal. It precedes `Expression expects , : <statement>.`, which the compiler adds for 6 to 8
arguments (fewer already got it from the arity check). `Result,Result` (a second String) and a
String array element compile; `Result+"a"` also compiled, so expressions are never flagged, nor is
a parenthesized variable (rejected by the compiler, not modelled). Observed on assignment RHS and
as a bare statement; `If EmailRecv(...)` was rejected the same way but is not implemented.
`instr_rules.check_emailrecv_destination`.

### PulseCount terminal reuse (CR6/CR1000X)
A second `PulseCount` in the same `Scan..NextScan` that names a terminal an earlier one named
gets `<T> already used as PulseCount in this scan.` on its own line (CR6 `C1..C4`/`U1..U12`,
CR1000X `C1..C8`; case-insensitive, parenthesized names too). CR1000X prints `Pulsecount: channel
<T> already in use or unavailable.` first. Every further reuse reports again. The rule is static:
`If`/`Else` branches, inline `If` and `SubScan` bodies count, and the options (`PConfig`) do not
matter. It is an aside like the index bounds: it coexists with a tier-B error elsewhere and is
hidden by a pass-1 error (`Battery()`). Numeric channels, `P1`/`P2`, `Const` channel names,
CR300/CR350 (different messages, and CR350 compiled the reuse), and a reuse across scans were
probed but are not implemented. The clash with `SerialOpen`, `PortSet`, `WriteIO` or
`SDI12Recorder` on the same terminal is [terminal ownership](checker.md#terminal-ownership-round-37). `instr_rules.check_pulsecount_channels`.

### I2C and SPI port conversion and I2C open order (round 99)

On CR6 and CR1000X, I2C and SPI ports convert to a signed Long. Fractions
truncate toward zero, strings and wide values become `INT_MIN`, and `INF`
becomes `INT_MAX`. The existing port ranges and secondary diagnostic messages
apply to the converted value. CR300 and CR350 lack these instructions.

`I2CRead` and `I2CWrite` require an earlier `I2COpen` on the same physical
port. On CR6, `U1..U12` pair with `C5..C16`, and an out-of-range port reports
its error and uses the C1 slot for the order check. An in-range even port skips
the order check. CR1000X skips it for both out-of-range and even ports.
The CR6 builders `FUN_0050e4e0` and `FUN_0050ec50` use the port returned by
`FUN_00515ff0` to index the same open-port table.
[`r99_serial_bus_ports.json`](../tests/fixtures/r99_serial_bus_ports.json).

### SPIRead/SPIWrite before SPIOpen (CR6/CR1000X)
`SPIOpen must precede SPIWrite.` / `...SPIRead.` is reported for an `SPIRead`/`SPIWrite` whose port
no earlier `SPIOpen` (textual order only: `Sub` bodies, branches and an inline `If` count; an
`SPIOpen` after the call does not) opened. Pairing is per terminal, with
`C1`, `101` (and CR6 `U1`, `201`) the same terminal and CR6 `C1`/`U1` different: `Open(C1)` then
`Write(U1)` fails. CR1000X pairs `C1` and `C5` the same way. It is an aside: it coexists with a
tier-B error elsewhere or on the same line, does not hide the tier-C `SPI Port must be ...` of
another line, prints first on its own line (ahead of `SPIWrite illegally occurs before BeginProg.`
and of `Invalid symbol ...`), and a pass-1 error (`Battery()`, a short argument list) hides it.
An `SPIOpen` on an invalid port (`C3`) opens nothing: `SPIOpen(C3,...)` then `SPIWrite(C1,...)` reports
both `SPI Port must be C1, U1, U5, or U9.` and `SPIOpen must precede SPIWrite.` on CR6 and CR1000X, and a
later valid `SPIOpen(C1,...)` after the write does not rescue it; a valid `SPIOpen(C1)` after the bad
one does (oracle, 2026-10-03; `tests/fixtures/spi_open_order.json`). `SPIOpen(7,...)` differs: CR6
reports only `Invalid port 7, use C1..C4 or U1..U12.` (file left unchecked), CR1000X also reports the later
write. Not implemented: a write whose own port is not a valid SPI port (no must-precede was seen for
`C3`), and any file with an `SPIOpen` whose port is not a plain terminal/number, which is left
unchecked. `instr_rules.check_spi_open_order`.

### DNPUpdate without DNPVariable (all four loggers)
`DNP3Statics and/or DNP3Events are not defined` (no period) on a `DNPUpdate` with no `DNPVariable`
textually before it (a `DNP` call alone is not enough; a `Sub` body, an inline `If`, a call in the
scan and a `DNPVariable` inside the scan above it all follow the same text order). CR6/CR1000X
treat it as an aside, printed ahead of a same-line `DNPUpdate illegally occurs before BeginProg.`, that coexists with tier-B and tier-C findings on other lines and is hidden by a
pass-1 error (`Battery()`); CR300/CR350 print it only when no earlier error exists (an undefined
name or a bad `PortSet` port hides or replaces it). A `DNPVariable` of an event group whose
static group is not assigned earlier is covered in "DNPVariable groups" below. Not implemented: the real compiler dropping the `DNPVariable never reached at run
time.` warning when this error fires (a `DNPVariable` after the scan). `instr_rules.check_dnp_update`.

### PortBridge array shape (all four loggers)
`Bridge parameter must be an Array(2,3) of type long.` (the dimensions are literal text, whatever
the variable's own) for `PortBridge`'s second argument when it is a numeric literal or a declared
variable that is not a Long array with first dimensions 2 and 3: Boolean/Float/String, `(3,3)`,
`(2,2)`, `(2,4)`, `(3,2)`, `(6)` and a scalar all fail, while `(2,3,1)`, `B()` and `B(1,1)` compile.
Gating mirrors `DNPUpdate`: CR6/CR1000X aside, CR300/CR350 only without an earlier error. Not
implemented: an undeclared second argument (CR6/CR1000X add this line after `Invalid symbol`) and
the separate `Enable parameter must be of type Boolean.` for the first argument.
`instr_rules.check_portbridge_array`.

### CDM address and module type (CR6/CR1000X)
`Address <n> already used with a different type CDM.` on a CDM instruction whose constant `CPIAddress`
(1..120) was earlier used with a different `CDMType` value; the first type seen for an address is
the reference, so `Open(A), B, A` flags only `B` and `A, B, B` flags both `B`s. Types compare by
value (`VOLT116` and `223` agree; `CDM_A116` and `1` differ from it), and different CDM
instructions share the address table. Registering instructions observed on CR6: `Battery`,
`VoltSe`, `VoltDiff`, `TCDiff`, `TCSE`, `PanelTemp`, `BrHalf`, `ExciteV`, `ExciteI`, `SW12`, `SW5`,
`SWPower`, `Delay`, `PulsePort`, `MuxSelect`, `Therm107`, `Resistance`, `ACPower`, `CurrentDiff`
(others were not confirmed and are not checked). It is an aside like the PulseCount rule. Not
implemented: addresses outside 1..120 (the real compiler adds `CPI Address must be 1..120.` and
folds the address modulo 256) and non-constant arguments. `instr_rules.check_cdm_address_types`.

### CDM instruction on the NULL module (CR6/CR1000X)
A `CDMType` constant that names no module (any integer 0..65536 outside 100-105, 200-202, 220-231,
260 and 261; swept 0..299 on both loggers, 300, 1000 and 65536 spot-checked) is the `NULL` module,
and a CDM instruction using it gets `<name> instruction not supported by module NULL.`, where
`<name>` is `Analog measurement` for `VoltSe`, `VoltDiff`, `TCSE`, `PanelTemp`, `BrFull6W`,
`BrHalf3W`, `Therm107`, `Resistance`; `SW` for `SW12`/`SW5`; `CDM_Excite` for `ExciteV`/`ExciteI`;
`CDM_MuxSel` for `MuxSelect`; `CDM_<Name>` for `Battery`, `Delay` (only `Option` 0; options 1 and 2 print no module line),
`PulsePort`, `TCComp`, `ACPower`; and two lines for `SWPower` (`CDM_SWPower`, `SW`) and `CurrentDiff` (`CDM_CurrentDiff`,
`Analog measurement`). It replaces the instruction's other value errors (a bad channel, range or
`Reps` were not reported), is hidden by a pass-1 error and coexists with tier-B/tier-C errors on
other lines (same aside as the address rule). A statement that clashes on its address gets only the
clash line. Not implemented: named modules that lack an instruction (`EC100`, `CURRENT408`,
`VOLT408V1` and others print the same message with their own name, or a warning), `PeriodAvg`
(not confirmed to register its address), `BrHalf`/`BrFull`/`TCDiff`/`Resistance3W`, and addresses
outside 1..120.

### AVW200 scan time (pipeline mode, CR6/CR1000X)
`Scan interval <I> secs is too small; AVW200 measurement time is <T> secs (2 secs per sensor).`
at each `AVW200` after which `2 * (sum of Reps so far)` exceeds the scan interval `I` in seconds
(equal is accepted: 16 sensors need a 32 s scan, 63 s fails 2x16 and 64 s passes). The sum runs over
all `AVW200` calls in the scan regardless of `ComPort`, channels, frequencies, excitation or
thermistor options; every call past the limit reports its own running time (`64`, then `96`).
`Min` and whole-second `mSec` intervals are shown in seconds. No message in `SlowSequence` or
`SequentialMode`. Aside, like the other asides here: coexists with tier-B and tier-C findings, hidden
by a pass-1 error. Not implemented (the check stays silent): a non-whole-second interval (the real
compiler adds `Scan interval for AVW200 must be integral number of seconds`), `Reps` outside 1..16,
channels outside 1..32 (`Bad parameter(s): ...`), an `AVW200` in an `If`/`For`/`Select`/loop (`AVW200
not allowed inside of a conditional statement in PipeLine Mode.`), `AVW200` in more than one scan or
beside a `SubScan` (`All instances of AVW200 to the same interface must scan at the same interval`).
`instr_rules.check_avw200_scan_time`.

### `BytesPerRecord` is not a DataTable member on CR300/CR350

`Table.BytesPerRecord` compiles on CR6/CR1000X and fails on CR300/CR350 with
`Could not find Table.BytesPerRecord(N).` (member spelling echoed as written; fixture
`tests/fixtures/table_members.json`). `N` is 1 for no index or `()`, the truncated literal for a
non-negative number (`(2.5)` -> 2, `(0)`, `(100)`), and the whole `(N)` is dropped for a variable,
an expression (`1+1`) or a negative literal (`Could not find T.BytesPerRecord.`). Found in
assignments and `If` conditions. A `FieldNames("BytesPerRecord")` entry exactly of that name in the same
table makes the member valid on CR300 (not a substring such as `NotBytesPerRecord`, and not another
table's field: `U.BytesPerRecord` is valid only if `U` renames one; oracle, CR300/CR350,
2026-10-03; `tests/fixtures/table_members_scope.json`). Implemented in `expressions.check_table_members`. Not
modelled: the oracle reports every bad member of a statement (`T.BytesPerRecord + T.Nope` gives two
errors on CR300/CR350; the check stops at the first), `TableBytesX(2)` echoing its index (the
existing rule always prints `(1)`), and CR6's `predefined field name` error for such a FieldNames.

### `SDI12Recorder` with a structure destination on CR300/CR350 {#sdi12recorder-structure-destination}

A variable declared `As <StructureType>` as the first `SDI12Recorder` argument (scalar, array, or
`A(i)` element) compiles on CR6/CR1000X but fails on CR300/CR350 with `Could not find DataTable
<argument text>.`, the text echoed verbatim (`A `, `A(1)`; fixture
`tests/fixtures/sdi12_structure_dest.json`). It is a tier-C check in `instr_measure.check`, hidden
by a pass-1 error, and the earlier `fnotch` warning is kept. A plain Float array compiles.
Not modelled: member destinations (`A(1).Temp` already fails, with another message here), `A()`
(two errors), and the interplay with another `Could not find DataTable` (e.g. `CallTable Nope` or an
unallocated `CallTable` later in the file): the oracle reports both, we report only the later one.
Other instructions with a structure destination (`VoltSe`, `Therm107`, `SerialIn`) give unrelated
messages and were not changed.

## Unclosed StructureType (CR300/CR350, 2026-10-03)

Fixture `tests/fixtures/unclosed_structure.json`, `tests/test_unclosed_structure.py`,
`expressions._unclosed_structure_variables`. Reproducer `cr350/false_accept/f89fbcbeef10`. When the
last `StructureType T` never reaches `EndStructureType`, a `Public`/`Dim` variable declared `As T`
is undefined as an instruction argument: `SplitStr (Sensor1,Measurements,",",3,5)` gives
`Sensor1 is not defined.` plus the usual undefined-name cascade (`Too many parameters:  Measurements.`);
`PanelTemp(Sensor1,15000)`, `Move(X,1,Sensor1,1)` and `Battery(Sensor1)` likewise (probed, not
fixture-pinned beyond `PanelTemp`). Not the cause: the parenthesised type name `StructureType
(Values)` and the line-continuation string both behave like the plain forms. Still accepted by the
real compiler: `Sensor1 = 1` and `Sensor1.val_Float = 1`; the parenthesised-name form with a
following `Sensor1.val_Float = 1` is a false reject here (`Invalid Variable declaration .`), and
`Sensor1.val_Float` as an argument (`Could not find DataTable Sensor1.`) is not modelled. CR6 and
CR1000X crash with a page fault on every unclosed variant, so no rule is available there.

The CR300/CR350 observations are not reproducible. The fixture's 12 unclosed (`open*`) cases were
recorded from real-compiler verdicts on 2026-10-03, and replay had earlier recorded the same
messages for `f89fbcbeef10`. Later the same day, uncached reruns of those sources (LF and CRLF)
and of `f89fbcbeef10` itself page-faulted every time (exit 5, empty message file), while closed
variants still compiled. Treat the rule as compiler undefined behaviour. The check rejects only
programs that the real compiler either rejected or crashed on, so it cannot cause a false reject.
`tools/fixture_audit.py` raises an `OracleError` on these cases rather than reporting a
difference.

Re-probed later on 2026-10-03: the unclosed `Sensor1.val_Float = 1` program (plain and parenthesised
type name) shows a clean accept only from an older cache entry; uncached it page-faults on all four
loggers (3 of 3 runs each on CR300/CR350). The earlier "false reject" therefore has no live
verdict and nothing was changed. The parenthesised `StructureType (Values)` form WITH
`EndStructureType` (member without `Public`) matches the real compiler on all four loggers; with a
`Public` member line CR300/CR350 page-fault (no verdict).

## EPI filter instructions: scan buffers and output rate (CR6/CR1000X, 2026-10-03)

Fixtures `tests/fixtures/epi_scan_buffers.json` (`CDM_BridgeFilt`, 30 cases) and
`epi_scan_buffers_fft.json` (`CDM_FFTFilt`, 8), `tests/test_epi_scan_buffers.py`,
`instr_rules.check_epi_scan_buffers`. Corpus: `chm/cdmbridgefilt_1`. Pipeline mode only
(`SequentialMode` gives `CDM_BridgeFilt not allowed in Sequential Mode.`, not modelled).
Per call, on the instruction line, also inside an `If` and a `SubScan`:

- `Insufficient Scan buffers to read EPI data, minimum is N.` when the constant `Scan` buffer
  argument is below `N = min(100, 2 s / interval)` (integer division: 100 mSec -> 20, 200 -> 10,
  500 -> 4, 50 -> 40, 10 and below -> 100, 300 -> 6). Probed for 1..500 mSec. A 750 mSec scan with
  buffers 0 gives no buffer message, so the rule stops at 500 mSec; 1 Sec and above never gives it.
- `CDM_BridgeFilt` only: `Invalid Output Rate.` unless the interval is one of 1/2/5/10/20/50/100/200/
  500/1000 mSec (the help page's table), then for intervals above 1 Sec `Maximum Scan interval for
  CDM_BridgeFilt is 1 Sec.` `CDM_FFTFilt` accepts 2 Sec.

Not modelled: `CDM_FFTFilt` `Scan interval must be at least N usec for FFT length M.`, the
rate message for a table interval that the scan does not divide (gated behind the table-divisibility
error here), the rate/max messages inside `SubScan` (`Cannot mix output rates in a single Spectrum
Module.`), scan intervals under 1 mSec. `CDM_TCComp` and the other `CDM_*` measurements have no buffer
rule (0 buffers at 100 mSec accepted).

## `Scan interval too short` on a zero-interval Sec/Min scan (CR6/CR1000X, 2026-10-03)

Fixture `tests/fixtures/zero_scan_measurement.json`, `tests/test_zero_scan_measurement.py`,
`instr_measure.check_scan_delay_budget(..., zero=True)`. A main `Scan` whose interval is `0` (also
`0.5`) or whose interval argument exceeds `INT32_MAX` (`Scan interval parameter must be a
non-negative integer`, e.g. `999999999999`, `2147483648`) is treated as interval 0 in the Sec and
Min units, and the compile still reports `Scan interval too short for measurement by N <unit>.` at
`NextScan`, together with the scan's own parameter error:

- `PanelTemp`, `VoltSe`, `VoltDiff` directly in the scan (or in `SubScan(0,0,n)`): `by 1 Sec` /
  `by 1 Min` (`Day` also gives `by 1 Day`, not modelled). In an `If` or `For`: no message.
- `Delay(0,1,Sec)`: `by 2 Sec` (the existing `total // unit + 1`, now also at interval 0); delay plus
  `PanelTemp` the same.
- Not modelled: `mSec` (the number is the per-instruction measurement time, e.g. `PanelTemp(,15000)`
  3 mSec, `(,60)` 36 mSec, `(,250)` 11 mSec, `(,50)` 43 mSec); `Battery` (CR1000X `by 1 Sec`, CR6
  nothing); a negative interval gives no `too short` message. A pass-1 symbol error on CR1000X
  (`Invalid symbol ... U1`) also hides the scan error itself; the CR6 tier-C `Invalid channel` still
  prints beside it. Neither is modelled.

The zero scans run ahead of the error-gated tier C and only when no error preceded the early
checks. Reproducers fixed: cr6 `false_accept/27f8cf697cbc`, `8dd29dba276b`, `e17429a2a028`,
`message_diff/9bee9eafb6c3`, `a715a6fa84f0`; cr1000x `false_accept/8fea44ceb325`, `ea6e49fbf407`.

## Fuzz round 5 (seeds 201-208, 2026-10-08)

Evidence and class counts: `docs/fuzz-findings.md` "Round 5". Every rule below is backed by
`tests/fixtures/r5_*.json` (real-compiler output, `tests/test_fuzz_round5.py`).

### `End Table`

`End Table` (one or more spaces) is `EndTable` on all four loggers; a stray one reports
`EndTable without DataTable.`. The parser maps `End` + `Table` to `ENDTABLE`. `End ConstTable`
is not equivalent (`Illegal END or EXIT ConstTable.`, unchanged). Fixture `r5_end_table`.

### SDM address constants beyond Long, and float constants

`Invalid SDM Address: %d, must be 0 - 14.` (`check_sdm_address`) reads a constant in the
CR6/CR1000X as follows (oracle, 2026-10-08, fixture `r5_sdm_address_float32`): an integer
constant inside Long range is exact (`16777233` -> 17, `-2147483633` -> 15); a constant written
with a decimal point or exponent, or an integer past Long, is rounded to float32 first
(`16777233.0` -> 16, `2147483583.0` -> 128); a float32 outside Long range is accepted unchecked
(`999999999999`, `4294967295`, `-2147483649`, `2147483647.0`, `3e9+15`, `-1e300`), except that a
float32 overflow to +infinity (`1e300`) saturates to Long max and gives 255. `consteval` is
float-valued throughout, so only a literal's own spelling (or a fractional result) marks a
constant as float: an integer-valued expression that contains a decimal literal
(`2147483520.0+15`; the real compiler says 128, we say 143) keeps the exact-integer reading.
Reproducer: `tests/corpus/fuzz/cr6/false_reject/ab0c3e6336e7.CR6`.
`struct.pack("f", 1e300)` raises `OverflowError` on some CPython builds (x86 Linux, the original
machine) and returns `inf` on others (Apple Silicon, CPython 3.13.12, observed 2026-10-12);
`check_sdm_address` handles both, so the `1e300` -> 255 case of `r5_sdm_address_float32` holds on
either (oracle re-probed on the arm64/Wine machine: `1e300`, `1e39`, `3.5e38` give 255; `3.4e38`,
`-1e300`, `-3.5e38`, `1e10` are accepted).

### SequentialMode: no scan-budget error, no busy-scan calibration warning

`Scan interval too short for measurement by %d %s.` (`check_scan_delay_budget`) is reported only
in PipelineMode; with SequentialMode (an explicit `SequentialMode`, or any trigger of
`mode.decide`, such as a task instruction after `NextScan`) the real compiler accepts
`Scan(1,Sec,0,0)` + `Delay(0,1,Sec)` silently. The "measurement delays leave under 3 mSec"
branch of the background-calibration warning is likewise absent in SequentialMode (oracle, CR6
and CR1000X for the budget, CR6 for the warning, 2026-10-08, fixture `r5_seq_scan_budget`). The
interval-threshold branch of that warning is not changed. The mode is decided by
`instr_measure.is_sequential`, which leaves a `mode.decide` failure to the compile's own call.
Reproducer: `tests/corpus/fuzz/cr6/false_reject/2a20e3cd2f86.CR6`.

### `SubScan without Next SubScan.`

A `NextScan` (or `Next Scan`) reached while a `SubScan` is still open reports `SubScan without
Next SubScan.` at the `NextScan` line, all four loggers (oracle, 2026-10-08, fixture
`r5_subscan_unclosed`; `check_block_closers`). On CR6/CR1000X a `SubScan` with no measurement
also reports `No Measurements in SubScan.` and a nested `SubScan` reports `Cannot Nest
SubScans.`; neither is modelled, so only the measurement-bearing, unnested shape is exact there.
Reproducer: `tests/corpus/fuzz/cr6/false_accept/b55ff67f8335.CR6`.

### `Maximum() or Minimum() must precede SampleMaxMin().`

Per `DataTable`, a `SampleMaxMin` with no earlier `Maximum` or `Minimum` in the same table
reports this at its own line, all four loggers (oracle, 2026-10-08, fixture
`r5_samplemaxmin_order`). Any earlier `Maximum`/`Minimum` suffices, whatever its variable; an
`Average` or `WindVector` before it does not, and a `Maximum` after it does not. Pass 2 in
`check_table_and_menu_placement`. Not matched: with an undefined name on the same
`SampleMaxMin` line the real order is this message first (we put the symbol error first on
CR6/CR1000X), and CR300/CR350 also print the undefined-name error that we gate out.
Reproducer: `tests/corpus/fuzz/cr6/false_accept/cb214e9bc111.CR6`.

### `Call never reached at run time.`

An unreachable `Call S(...)` or bare `S(...)` Sub invocation is named `Call` in `%s never reached
at run time.`, not `Expr` (oracle, all four loggers, 2026-10-08, fixture `r5_call_never_reached`);
an inline `If ... Then Call S(1)` still gives no warning. Reproducer:
`tests/corpus/fuzz/cr6/mode_diff/e52f75391e79.CR6`.

### `Optional Count parameter must be type long.` for `TotalRun` and `StdDevRun`

`check_avgrun_options` gave the message only for `AvgRun`. With a `RunReset` argument present,
a Float or String variable as the sixth argument (`Count`) of `TotalRun`, and of `StdDevRun`
(CR6/CR1000X only), gives it too (oracle, 2026-10-08, fixture `r5_running_count_type`). `MinRun`
and `MaxRun` read their sixth argument differently (`Optional number of distinct instances
...`) and are not changed; a decimal literal Count on CR300/CR350 is also rejected by the real
compiler and is not modelled. Reproducer: `tests/corpus/fuzz/cr6/false_accept/a5c5a3813f44.CR6`.

## Fuzz round 5 false accepts, second pass (2026-10-08)

Rules below are backed by `tests/fixtures/<name>.json` (real-compiler output, all four loggers,
`tests/test_fa_fixtures.py`).

### `Duplicate Alias` collides by flat offset (fixture `alias_flat_dup`)

The `Duplicate Alias` key was the written index tuple. The real compiler keys on the row-major
flat offset (the one `Illegally dimensioned aliased name` bounds-checks), a bare name being
element 1: on `Public grid(3,3)`, `grid(1,1)`, `grid(1)` and `grid` collide; so do `grid(1,2)` and
`grid(2,-1)`, `grid(2,2)` and `grid(1,5)`, `grid(2,3)` and `grid(3,0)`, a scalar `a` and `a(1)`,
and two out-of-range spellings of the same flat offset (`grid(1,10)`, `grid(4,1)`). A flat
offset below 1 is not matched (`grid(1,-5)` then `grid(0,1)`: no `Duplicate Alias`), so such
indices keep the written tuple as key. Tuples longer than the declared dimensions keep the written
tuple (not probed). Reproducers: cr6 `false_accept/c1a8075740f0`, `cd96d8abd0cc`, `df0fc0a86bcc`.

### `RoutersNeighbors` destination and `ArgosError` type (fixtures `routers_dest_index`, `argos_error_dest`)

`RoutersNeighbors` takes the whole Long array (all four loggers, 2026-10-08). Once the destination
is a `Long`, a subscript that is a variable (`L(I)`), a binary expression (`L(1+0)`,
`DestArray((1+2))`) or a literal of 2 or more (`L(2)`, `(1,2)`, `(2,1)`, `M(1,1,2)`) gives
`Destination variable cannot be indexed`; `L`, `L()`, `L(1)`, `L(1.5)`, `L(-1)` and `(1,1)` compile.
A non-Long destination still gives only `Destination variable must be type LONG`. Not modelled:
`L(0)` (the real compiler also prints `Variable L out of bounds.` first), `Const` names as
subscripts, parenthesised literals (the parser drops the parentheses). `RealTime(V(3))` and the
other code-29 destinations give an out-of-bounds line instead, so the rule is `RoutersNeighbors`
only (`var_types.py`). Reproducer: cr350 `false_accept/1465963db41a`.

`ArgosError (Dest)` with a numeric variable (`Errors`, `Errors(1)`) gives `A string variable
destination is required` on CR6/CR1000X; a String variable or String array element compiles.
Reproducers: cr6 `false_accept/6aba4c3dfeb4`, `e4ee6f95bb9c`.

### `SampleFieldCal with no FieldCal in program.` (fixtures `samplefieldcal_a`, `samplefieldcal_b`)

A `SampleFieldCal` in a table, when the program holds no `FieldCal` (also inside a one-line `If`)
call, gives this error at the first `SampleFieldCal` only (a second table's `SampleFieldCal` adds
none), on all four loggers (2026-10-08). `LoadFieldCal` does not count. `FieldCalStrain` is treated
as counting like `FieldCal` (its own probe was rejected for another reason, so this is
unverified and can only under-report). When the table has no other output instruction the real
compiler adds `Table <name> declared with no output fields.` at `EndTable`; that line belongs to
the table checks and is not produced here, so fixtures only hold tables with another output.
Reproducer: cr1000x `false_accept/0f0e2e02684b` (still differs by that second line).

### CH400Status interval and FFT source length (fixtures `ch400status`, `fft_source_length`)

`CH400Status (Interval, Units)` (CR6/CR1000X, 2026-10-08): `Interval` converts as C's `(long)` and
is multiplied by the unit in microseconds; a result that is not a whole number of seconds gives
`CH400Status update interval must be an integer multiple of 1 second.` (`1,msec`, `500,msec`,
`&HFF,msec`, `1,usec`, `1500000,usec` fail; `1000,msec`, `10,Sec`, `1,Min`, `0,Sec`, `-1,Sec`,
`2.5,Sec` pass). Reproducer: cr6 `false_accept/b9b9135057c4`.

`FFT (Source, Res, N, ...)` (all four loggers): with `N` a constant power of two, the source from
the written element on must hold `N` values or `<name> must be of length >= N.` (`x(16)` with
`N=32`; `x(2)` of a 16-array with `N=16`; `z(2,1)` of a 4x4 with `N=16`; `z` whole passes, a
smaller `N` passes). `N` that is not a power of two gives `FFT N must be a non-zero power of 2.`
plus the empty-table line in the real compiler and is not modelled. Reproducer: cr6 and cr300
`false_accept/a05ccf2caf18`.

### Therm107/108/109 `SettlingTime` on CR300/CR350 (fixture `therm_settling_time`)

`Therm107`, `Therm108` and `Therm109` (position 5, `SettlingTime`, converted as C's `(long)`):
`0` and any value from `10` up compile; every other value gives `Settling time <n> too short, min
time is 10.` (`1`, `2`, `5`, `9`, `9.9` -> 9, `-1`; `1.5` -> 1). It follows the `Invalid fnotch
value` warning of the same line, and is a `const_ranges` entry (new `allow` list for the lone
accepted value `0`). Not probed on CR6/CR1000X (different parameter lists) or for other CR300
measurement instructions. Reproducer: cr350 `false_accept/fed9e7c1ded9`.

### FieldCalStrain option (round 96)

All four profiles accept constant selectors 10–14, 20–24, 30–34 and 40–44
when the other arguments are valid. The argument converts to a signed Long
before validation. For example, `44.999` converts to 44 and compiles, while
`45.0` produces `Invalid function option.`

The CR6 builder `FUN_0044dff0` splits the selector into quotient and remainder
by 10, normalizes remainder 4 to 0, and rejects quotient 0 for the strain
variant. The probes cover the full range and coercion on all four profiles.
Other `FieldCalStrain` argument diagnostics are separate from this option
check. [Reference observations](../tests/fixtures/r96_fieldcal_strain_options.json).

### CDM bridge excitation and settling time (round 93)

On CR6 and CR1000X, `CDM_BrHalf3W` and `CDM_BrFull6W` on module types 220–223
accept excitation magnitudes from 20 to 5000 and settling times of 0 or 100–100000.
Both values convert to a signed Long before validation. The excitation check takes
the magnitude except for `INT_MIN`, which stays negative. Text and out-of-range
decimal values convert to `INT_MIN`. Bare `INF` converts to `INT_MAX`. CPI address
errors precede settling and excitation errors, in that order. CR300 and CR350 do
not expose these two instructions.

The fixtures record the supported-module probes and the remaining
`CDM_BrFull6W` channel diagnostic gap. Other named CDM module diagnostics remain
outside this check. [Reference observations](../tests/fixtures/r93_cdm_bridge_options.json).

### CDM value ranges (CR6/CR1000X; fixtures `cdm_ranges`, `cdm_reps`, `cdm_port_and_type`)

All observed 2026-10-08 on both compilers; implemented as `measure.json` `const_ranges` entries
(plus `check_cdm_address_types`), values converted as C's `(long)`.

- **`CPIAddress` outside 1..120** (position 2): `CPI Address must be 1..120.` for every instruction
  in the address family (`TCComp`, `PanelTemp`, `Battery`, `SW12`, `Delay`, ... all probed ones),
  printed *before* the channel and unit messages of the same statement and before the module and
  address-clash lines. Tested: `-1`, `0`, `121`, `127`, `128`, `255`, `256`, `257`, `300`, `376`, `1000`,
  `65536`, `-5`, `0.5`, `TRUE`, a string, `99999999999` fail; `1`, `120`, `120.5` (-> 120) pass. The
  statement then registers as **address 1** for the clash check (`TCComp(...,0,...)` then
  `Battery(VOLT116,1,...)` gives `Address 1 already used with a different type CDM.`). A fractional
  in-range address now registers as its truncation (`120.5` clashes with `120`). `CDM_PeriodAvg`
  prints the same message **last** (after `Timeout`), so it is the final `const_ranges` entry.
- **`CDM_TCComp` position 8**: `0`, `1`, `2` pass (`2.5` -> 2, `-0.5` -> 0, `1.9` -> 1); `-1` (also
  `TRUE`, `-1.5`, `-1.9`) gives only `Warning: Open detection disabled.` (new `warn_values`); every
  other value (`3`, `4`, `255`, `256`, `-2`, `-255`, a string, an overflow) gives `Temperature unit
  unsupported. Must be 0 (Celsius), 1 (Fahrenheit), or 2 (Kelvin)`, after the channel message.
- **`CDM_PeriodAvg`**: `Reps` (pos 4) 1..65536 -> `Reps: %d, out of range (1 - 65536).` (with a trailing
  period here, none on the measurement instructions below); gain (pos 5) 0..3 else `Unrecognized
  gain code!` (`3.9` passes, `4` and `-1` fail); channel (pos 6) 1..16 -> `Input channel: %d, out of
  range (1 - 16).`; `Cycles` (pos 9) 1..16777215 -> `Cycles: %d, out of range (1 - 16777215).`;
  `Timeout` (pos 10) 1..1000 -> `Timeout: %d, out of range (1 - 1000).` (`0.5` -> 0, a string and an
  overflow show `-2147483648`). Several failures print in the order channel, Reps, gain, Cycles,
  Timeout, CPI Address. `Timeout 1000` also gives `Insufficient time (1000 ms) ...` and `Reps` above
  the destination size gives `Variable H out of bounds.`: neither is modelled.
- **`Reps` (pos 4) on `CDM_VoltDiff`, `VoltSe`, `TCSE`, `TCDiff`, `BrHalf`, `Resistance`, `Therm107`**:
  1..65536 else `Reps: %d, out of range (1 - 65536)` (no period); all but `BrHalf` and `Resistance` add
  `MeasPEx: %d, must be greater than 0.` for a value below 1. `CDM_VoltDiff` position 6 (channel)
  is 1..16 with the `Input channel` message; its `Reps cycle input beyond last channel!` follow-up
  (seen for 17) is not modelled. Negative `Reps` also prints `Variable V out of bounds.` first, not
  modelled, so fixtures hold only non-negative cases.
- **`CDM_SW5` position 3** (port): 1 and 2 pass (`1.5`), anything else (`0`, `3`..`5`, `-1`, `TRUE`, a
  string) gives `Unrecognized port!`.
- **`CDMType` as a string or fraction** (`"fuzz"`, `1.5`) is the NULL module like `0` (`CDM_PulsePort`,
  `TCComp`, `Battery` print `<name> instruction not supported by module NULL.`). A NULL-module
  statement prints no other `const_ranges` line (`TCComp(0,1,...,25,...)`: only the module line).
  `CDM_PeriodAvg` is a NULL-module and address-clash instruction too. `TRUE` as `CDMType` prints an
  empty module name and is not modelled.

Reproducers: cr6 `false_accept/40c80f5860fa`, `43812a224c7f`, `0196a07a009b`, `eef44a5842df`,
`4cb038a7951b`; cr1000x `05d48966b713`, `7539be70e665`, `731dc3f88411` (the `MeasPEx` line needs
`Reps 0` on `CDM_VoltDiff`).

### `GPS` fifth argument (CR6/CR1000X; fixture `gps_nmea_strings`)

`GPS (Dest, ComPort, Offset, Size, NMEAStrings)`: when the fifth argument is given it must be a
String variable of at least 96 characters (declared `As String * n`, `n` from 96; a bare `As
String` is 24), else `Optional parameter must be an array of type string of length >= 100`
(observed on both compilers 2026-10-08: `* 16`, `* 95`, a bare String, a Float, `F()` and the
literal `"abc"` fail; `* 96`, `* 97`, `* 99`, `* 100`, `* 200` and `N100(2)` pass). It precedes
the `GPS pps synchronization` line. A declaration with an initializer (`As String * 16 = {...}`) counts the same (the parser keeps the
`= {...}` inside `string_size`, which the check strips). A `StructureType` first argument keeps its own
`Size of NMEASentences ...` check. Reproducer: cr1000x `false_accept/cb78fdc14db5`.

### `MQTTPublish` numeric payload expressions (CR300/CR350; fixture `mqtt_payload_expr`)

The existing payload check only looked at a number or a name. Observed 2026-10-08 on CR300 and
CR350: `1+2`, `(1+2)`, `F+1` and `-1` (arithmetic of numbers and non-String variables) give `A string
or string variable is required` too, while `"a"+"b"`, `Payload+"x"` and a String array element
compile. `(Payload)` (parenthesised String variable) and `Left(Payload,2)` also fail in the real
compiler but are not modelled. `_numeric_expression` leaves everything else unchecked.
Reproducer: cr350 `false_accept/439f82e4dea8`.

### `Illegal Matrix` operands (all four loggers; fixture `matrix_illegal`)

`Matrix (Option, A, B, Dest)` (2026-10-08): an operand that is a String literal (`A` or `B`), a
number as `B` (options 1-3) or a declared Float array of three dimensions (`A`, `B` or `Dest`)
prints `Illegal Matrix` first, then the dimension messages that follow when that operand has a shape
unlike every real one: options 1, 2, 4, 5 add `Illegal Matrix dimensioned`; option 3 adds `multiply A
columns and B rows must be the same` when `A` or `B` is the bad one and `multiply result illegal
dimensioned, must be m,q` when `Dest` is (`m`, `q` from the real `A` and `B`). Modelled by giving
such an operand the shape `(-1, -1)` in `_matrix_dimension_errors`. A String *destination* has its
own `Illegal destination variable type, expecting Float.` and a number as `A` its own cascade;
both are left unchecked. Reproducers: cr350 `false_accept/90559e8e4db1`, `c65123ff7474`.

### DNPVariable groups (all four loggers; fixtures `dnp_variable_a`, `dnp_variable_b`)

`DNPVariable (Source, Swath, Group, ...)` with a constant `Group` (2026-10-08, identical on all
four compilers), checked in `check_dnp_update`:

- An event or frozen group needs its static group assigned by an *earlier* `DNPVariable` (any
  source): `2` needs `1`, `22` needs `20`, `31` and `32` need `30`. Otherwise
  `Must assign group <static> before using group <group>.` (each offending statement; the group is
  then not counted as assigned). Every later `DNPUpdate` also gets `DNP3Statics and/or DNP3Events are
  not defined`, unless the missing static group is assigned somewhere later in the program (32 then
  30: only the first line).
- Source type: groups `30` and `32` fail for a String or Boolean variable
  (`For DNP3 group <g> variable must be of type Long.`; Float and Long compile, and a failed `30` still
  counts as assigned, so `32` then fails the same way); groups `1` and `2` fail for a Float, Long
  or String variable (`For DNP3 group <g> variable must be of type Boolean.`). A statement already
  failing the "must assign" rule gets no type line.
- Group and argument checks, including `Illegal DNP3 group N.`, are in "`DNPVariable` argument
  rules" below. Not modelled: the `Scan never reached at run
  time.` warning on CR300/CR350 beside a failing `DNPVariable` (seen once), `DNPVariable` in
  `Sub`s or with a non-constant group. Reproducers: cr6 `false_accept/d94ad3ccba51`, cr300
  `false_accept/48e3374d062a`.

## Slow scan intervals and the 1-day limit (CR6/CR1000X, round 9, 2026-10-10)

Fixtures `slow_scan_intervals.json`, `scan_max_interval_seconds.json`. A `Scan` after `SlowSequence` also
reports, after its BufferOption/Count messages: `Slow Scan interval must be >= 100mSec.` when the raw
uSec value is in (-1000000, 100000), `Slow Scan interval must be a multiple of 100mSec.` when it is
not a multiple of 10000 uSec (50 mSec and 150 mSec compile that check), `Maximum Slow Scan interval is
1 Day.` after a `Maximum Scan interval`. `Maximum Scan interval is 1 Day.` (all scans) is now
`(int32 seconds) > 86400` with Min/Day products wrapping at 32 bits (`Scan(2147483647,Day)` compiles)
and mSec/uSec floored to seconds (`86400999` mSec compiles), and it precedes the BufferOption/Count
messages. A value outside Long counts as -2147483648 for the `multiple of 1mSec` check. Cases the
fixture drops (ours differs): see the fixture-prune count in the commit. Round 89 confirmed
the same slow-scan limits on CR300/CR350 after their ordinary scan diagnostics. See
`tests/fixtures/r89_cr3xx_slow_scan.json` for the dated observations and compiler hashes.

### CPI measurement time and `CPISpeed` (CR6/CR1000X; fixture `cpi_time`)

Found by decompiling the end-of-compile pass of CR6Comp (`FUN_0041ce90`, tracer in `docs/binary.md`)
and then verified on the real compilers 2026-10-10 (98 fixture cases, audited 0 differing; Reps 1..20
and a second-address sweep exact on CR6 and CR1000X).

- **`CPISpeed(Rate)`**: the constant, converted as C's `(long)`, must be one of `50`, `125`, `250`,
  `500`, `1000` (the binary's table of five), else `Invalid CPI bit rate.` on the statement's line
  (`1000.9` passes, `999`, `0`, `-1`, `TRUE`, `256`, `65586`, `2000` fail, each bad statement
  reports). A string or a variable is not checked. Implemented as a `const_ranges` rule
  (`skip_strings`).
- **`Insufficient time (%d ms) for measurements from CPI address %d (needs at least %d ms).`**, at the
  program's last line, only when the compile has no other error. The scan interval in whole
  milliseconds (`value*unit/1000`) must be at least the module time. The time of `CDM_TCComp(TEMP120,
  ...)` is `Reps*430+275` microseconds, rounded up to 100 us per statement, summed per CPI address
  over the scan, then rounded up to whole milliseconds (Reps 2..16: 2,2,2,3,3,4,4,5,5,6,6,6,7,7,8 ms).
  `CPISpeed`, the thermocouple type, the integration argument and the unit do not change it. In the
  binary this value is the return of `FUN_004228a0` (stored in the instruction record, divided by
  100 and rounded up with `FUN_00413750`); the other CDM measurement instructions use
  `FUN_00411750` (not modelled).
- **Every occurrence counts once**, wherever it sits: inside `For`, `While`, `If` (both branches),
  `Select` cases and a one-line `If` (SequentialMode; in PipeLineMode a conditional `CDM_TCComp` is
  an error of its own, unmodelled, and the pass stays silent). Slow-sequence scans behave as main
  scans (100 ms scan with 14 `Reps 16` statements needs 101 ms; 13 fit).
- **`SubScan(Interval,Units,n)`** around the statements: the body counts `n` times; with Interval 0
  back to back (`(n-1)*body` added), otherwise `(n-1)*Interval` is added (Interval in 100 us units,
  truncated). A body longer than Interval gives `SubScan interval too short for measurements from
  CPI address %d by %d uSec.` (body minus interval, in uSec) first. Not modelled, and the pass
  stays silent: `Product of SubScan interval and iterations must be less than Main Scan interval.`,
  `SequentialMode SubScan interval must be > 1 mS.` (<= 1 ms), nested or non-constant SubScans.
- **Several modules** (the binary walks the module list in order of first use): for each module it
  totals that module in every scan holding CDM instructions, keeps per scan the first module with
  the strictly largest total so far, then reports every scan whose interval is below that leader.
  Hence k addresses print up to k lines per scan, each naming the leader so far: addresses 1 (3 ms)
  then 2 (6 ms) print `address 1 needs 3` then `address 2 needs 6`; addresses 2 then 1 with equal
  times print `address 2` twice; two scans with one module each print the first scan's line twice.
  Any other CDM instruction or `CPIAddModule2` in the program makes the pass silent (their times
  and module-list effect are unprobed).
- **`SequentialMode` never prints the `background calibration will be disabled` warning** (also
  for `Scan(1,mSec)` with no CDM instruction; CR6 and CR1000X, 2026-10-10). The earlier model only
  skipped the delay-budget cause of it.
- **`CDM_TCComp(TEMP120, ...)` argument checks found on the way** (all in one statement, in this
  order): `CPI Address must be 1..120.`; `Variable <dest> out of bounds.` when `Reps` exceeds the
  destination from its start (third argument) or is negative (a string or overflow counts as
  negative; `-0.5` is 0 and passes; scalar destination holds 1); `Burst measurements unsupported,
  input channel must be > 0!` for a negative channel; `Input channel: %d, out of range (1 - 20).`;
  `Reps cycle beyond last channel!` when `channel-1+Reps > 20` (an out-of-range channel counts as 1,
  so `Reps 21` fails with channel 21 too); `Thermocouple type unsupported.` outside 0..7; the unit
  message. Known order gap: an out-of-range CPI address together with an out-of-bounds destination
  prints the bounds line first here, last on the real compiler.

### Scan time budget (CR6, CR1000X; fixture `scan_time`)

Found 2026-10-10 with `tools/trace_times.py` (a copy of `CR6Comp.exe` that records every call of
`FUN_00522e50`, the function each code generator calls to add time to the open `Scan`; the main
checker never patches `compiler/`) and the decompiled generators of CR6Comp and cr1000xcomp, then
verified on both real compilers (122 fixture cases, audited 0 differing; about 500 random
scan/instruction programs per logger exact except the listed gaps). Implementation:
`checker/scan_time.py` (model), `instr_measure.check_scan_delay_budget` (errors),
`warnings.check_background_calibration` (warning). Units of 100 us.

- **Error**: `NextScan` compares `interval < total`; message `Scan interval too short for
  measurement by N <unit>.` with N = ceil((total - interval) / units per unit) (mSec 10, Sec
  10000, Min 600000). SequentialMode never reports it. A scan whose interval is 0 (invalid, or
  Sec/Min under one) counts as interval 0. The unit is the scan's, except after a `SubScan` that
  ends on the "interval longer than body by more than one unit" path, which leaves its own Units
  argument in force (`Scan(1,Sec)` + `SubScan(100,mSec,5)` with a 50 ms body + `Delay(0,600,mSec)`
  -> `by 51 mSec`).
- **Per instruction** (statically, once per occurrence wherever it sits; no loop or `If` awareness):
  `Delay(0,d,u)` (d truncated to an integer in its unit) adds `us/100 + 1`; `PortSet` 1, and `SW12`
  and `PulseCount` 1; `PortGet` 0; `CS616` `Reps*5+2`; `Battery` 0 on CR6, one analog call of
  8 on CR1000X. Option 1 and 2 `Delay` forms add nothing here.
- **Analog measurements** (`PanelTemp`, `VoltSe`, `VoltDiff`, `TCSe`, `TCDiff`, `Therm107-109`):
  per call `(Rsum + round_up) // 100 + 1` with `Rsum = (settle + integ) * Reps`; `settle` is the
  settling argument (0 = default) turned into a code and back, `integ` comes from `fN1` rounded:

  | | CR6 | CR1000X |
  |---|---|---|
  | integration code `k` | `int(93750/fN1 + .5)`, 24 for 0 | `int(31250/fN1 + .5)`, 8 for 0 |
  | `integ` | `k*32//3 + 1` | `k*32 + 180` |
  | settling code | `int((s or 500) + 810) * 3/32 + .5)` | `int((s or 450)/32 + .5)` |
  | settle | `int(code*32/3) + 5` | `code*32 + 5` |
  | `round_up` | 40 | 80 |
  | scan overhead / refund | +78 / -77 | +16 / -15 |

  Autorange (code 810 / 830) settles twice (`+ Reps*settle`); the open-detect "C" ranges (820-830)
  add 5 (CR1000X 2) to the settling code and `50*Reps`; `Therm107-109` and bridges double
  (excitation); `MeasOff` (single-ended) adds one more `integ + settle`; `RevDiff` (differential)
  doubles. `PanelTemp(Dest,fN1)` is two calls with a fixed settling argument 900 (code 84 CR6, 28
  CR1000X). Reps above 100000, `Therm` Reps above 255, non-constant arguments and burst (negative)
  channels are not modelled (the scan is then a lower bound). Bridges and `CurrentSe` are modelled
  since round 31 (`docs/checker.md`, "Bridge channel spans and settling-time timing"); any
  settling value counts in 32-bit arithmetic and the `NextScan` comparison is unsigned. Not
  modelled: `Resistance`, `PeriodAvg`, `AM25T`, `Thermistor`, `VibratingWire`, `Calibrate`,
  `ExciteV`, `MuxSelect`, `CDM_*` here.
- **Overhead**: the first analog call of a scan adds the overhead (78 / 16); if then
  `interval - total < 500` the refund (77 / 15) is taken back, so a tight analog scan costs net 1.
  `Scan(1,Sec)` + `PanelTemp` -> `by 1 Sec`; `Scan(0,mSec)` + `PanelTemp(PT,60)` -> `by 36 mSec`.
- **SubScan** (pipeline): body time L = total added inside it, count n, interval I (100 us, 0 for
  `SubScan(0,0,n)`). n < 2 or I = 0 or L = 0: total += (n-1)*L. I < L:
  `SubScan interval too short for measurement by N <unit>.` at `NextSubScan` (N from L - I) and
  total += (n-1)*L. I - L > 1: total += (n-1)*I. Otherwise total += (n-1)*L. Not modelled (the
  budget then skips the scan): non-constant SubScan arguments. `Product of SubScan interval and
  iterations ...` and the SequentialMode SubScan messages are other rules (not implemented).
- **Background calibration warning** (`When Main Sequence Scan %d is running, background
  calibration will be disabled.`, last program line, only without errors, never in SequentialMode):
  a scan with timed instructions warns when its slack `interval - total` is under
  `max(floor, max integ + c)` with floor 23 / 20 and c 13 / 17 (CR6 / CR1000X), `max integ` the
  longest integration time (`integ // 100`) of its analog calls (0 for `Battery`); scans whose only
  timed instruction is `PulseCount` never warn; an empty or untimed scan keeps the interval
  threshold of `background_calibration.json` (8 / 2 ms). Delay-only boundaries: CR6
  `Scan(100,mSec)` + `Delay(0,97600,uSec)` quiet, 97700 warns; CR1000X 97900 quiet, 98000 warns.
  Known gaps: the CR1000X threshold is a fit (it matches 11 `fN1` values for `VoltSe` and
  `AutoRange` at 250 Hz is 2 above the real boundary); the exact mechanism in the binary was not
  found.

### Scan interval arithmetic and `IfTime` (all four loggers; fixture `scan_interval`)

Decompiled `Scan` code (CR6Comp `FUN_00468900`, cr300comp `FUN_0050b470`, both through the shared
unit conversion `FUN_005082c0`) and verified 2026-10-10 on all four compilers (312 cases, audited
0 differing). Closes the round-9 `IfTime`/`TimeIntoInterval` items of `docs/fuzz-findings.md`.

- **Conversion**: the interval is converted as a `long` (a string, NaN or a value past Long range
  is -2^31, a fraction truncates), then in 32-bit C arithmetic: `uSec` `/1000000`, `%1000000`;
  `mSec` `/1000`, `%1000*1000`; `Sec` as is; `Min`, `Hr`, `Day` times 60, 3600, 86400 with
  wrap-around; any other unit 0. Nothing validates this: the seconds and microseconds are used as
  they come out, and stay in force after the interval's own error. `Scan("fuzz",Min,..)`,
  `Scan(99999999999,Hr,..)` and `Scan(3000000000,Min,..)` are 0 seconds; `Scan(2147483647,Min,..)`
  is -60 s, `Scan(71582789,Min,..)` is 44 s.
- **CR6/CR1000X** messages after `Scan Interval parameter must be a non-negative integer`:
  seconds 0 and microseconds under 1000 -> `Scan <us>uSec invalid must be at least 1mSec.` (the
  microsecond part, which is why wrapped Min/Hr/Day intervals say `0uSec`); else microseconds not a
  multiple of 1000 -> `Scans must be a multiple of 1mSec.`; else seconds over 86400 ->
  `Maximum Scan interval is 1 Day.` (a wrapped interval therefore passes, and warns about
  background calibration, since the interval is negative).
- **CR300/CR350** have no such messages. For seconds not 0 or microseconds from 10000: microseconds
  not a multiple of 10000 -> `Scans > 10mSec must be a multiple of 10mSec.` (`Scan(15,mSec)`,
  `Scan(1005,mSec)`, `Scan(86401,mSec)`, `Scan(86400001,mSec)`; `Scan(10,mSec)`, `Scan(9500,uSec)`,
  `Scan(1500,mSec)` pass), else seconds over 86400 -> `Maximum Scan interval is 1 Day.`.
- **`IfTime`/`TimeIntoInterval` inside a `Scan`** (`checker/intervals.py`) use the same seconds
  and microseconds: the scan step is `sec*10000 + us/100` in 32-bit wrap (100 us units), or `sec`
  when the call's interval passes 3600 s; a nonzero step that does not divide both values gives
  `Scan interval must be integrally divisible into interval.`, with C's sign rules for `%`. So
  `Scan("fuzz",Sec,..)` (step 0 in 100 us units, -2^31 s above an hour) fails `IfTime(0,24,Hr)` but
  not `(0,5,Min)`; `Scan(-1,Sec,..)` (step -10000) fails only sub-second intervals; `Scan(-5,Sec,..)`
  also `(0,2,Sec)`; `Scan("fuzz",uSec,..)` (step -21474836) fails every `IfTime`. On CR6/CR1000X
  this pass-1 error hides the scan's own pass-2 messages (`Scan Interval parameter ...`).

- **Zero interval and float32 2^31 (round 15, `tests/fixtures/r15_scan_zero_interval.json`, 88
  cases, all four loggers)**: a `Scan` whose seconds and microseconds both come out 0 is outside any
  Scan for `CallTable` placement exactly like the literal `Scan(0,Sec,..)`: `Cannot autoallocate
  memory for DataTable T called outside of Scan, e.g., called by Sub.` for an auto-sized table (the
  DataInterval-table messages are not printed). That covers `0.5` Sec, `0.9` mSec, `1073741824` Min,
  `268435456` Hr, `33554432` Day (the wrap), `999999999999` Min/Hr/Day (a past-Long value is
  -2^31, times the factor 0), and a string or unknown unit (`Scan(&HFF,"fuzz",3,0)`). A table
  `DataInterval` is not divisibility-checked against such a scan. `Scan(4294967296,Sec,..)` is -2^31
  seconds, not zero, so it stays a Scan. On CR300/CR350 a float32 of exactly +2^31 (`2147483648`,
  `2147483776`) converts to `INT_MAX`, not -2^31: `Scan(2147483648,Sec,..)` is `Maximum Scan interval
  is 1 Day.`, `Scan(2147483648,Min,..)` is -60 s (clean); `2147483904` (float32 2^31+256) is
  -2^31. CR6/CR1000X keep -2^31 (`Scan 0uSec invalid` for the Min case).

## `SerialOpen` TxDelay and buffer arguments (2026-10-11, fixture `tests/fixtures/serialopen_constint.json`)

- The 4th and 5th `SerialOpen` arguments (`checker/expressions.py`, `_CONST_INTEGER_BAD_ARGS`) must
  be integer constants on all four loggers: a float (`1.0`), a string, or an integer beyond 32 bits
  (`999999999999`) gives `Parameter must be a constant integer:` with the statement echo. CR300/CR350
  add the usual `.`-on-its-own-line and `Too many parameters` cascade (reproducer
  `cr300/false_accept/7be9f5d53b3a`).
- Accepted on all four: `Hr`, `1+1`, `-1` as the 5th argument, and integers in 32 bits.
- Not modelled: a 4th argument that is an in-range-of-32-bits but out-of-range integer. The real
  compilers print `<n> usec delay before transmit is illegal, must be 0..14000000` (`-1`, `2147483647`),
  and CR300/CR350 print a wrapped value for `2147483648`/`-2147483648`. Ours accepts these.
- A 6th argument that is no constant integer: CR6/CR1000X since round 57
  ([checker notes](checker.md#serialopen-format-and-commsmode-round-57)).

## `FTPClient` argument count (2026-10-11, fixture `tests/fixtures/ftpclient_arity.json`)

- Oracle, all four loggers, identical: 6, 10 and 11 arguments compile. 1-5 and 7-9 give `Expression
  expects , :` (with the call echoed after the `line N:` prefix); 12 and more give `No Matching, or
  out of place ) in expression:`. `FTPClient` is listed with `nargs` 6 in `functions.json`, so the
  allowed set sits in `checker/expressions.py` `_ARITY_ALLOWED` (the same mechanism as `HTTPPut`).
- Reproducer: `cr350/false_accept/1b3ba52e3818` (a 9-argument `FTPClient` on line 35, the only
  reported line). Argument values were not probed, only counts, so the rule is
  about counts and not about the contents of the argument list.

## Open `If`/`Select` at a Sub end (2026-10-11, fixture `tests/fixtures/sub_end_open_blocks.json`)

Oracle, all four loggers, identical except where noted. The block stack is **not** cleared at
`EndSub`/`EndFunction`.

- Each `EndSub`/`EndFunction` while an `If` or `Select` is open (inline `If ... Then x` does not count)
  gives one `If without EndIf inside of Sub.` on that line, however many blocks are open
  (`checker/structure.py` `stray_controls`). The rest of the file continues.
- The open block stays open. An `EndSelect` before `BeginProg` with a `Select` still open
  (any `If` above it does not matter) gives `EndSelect illegally occurs before BeginProg.` and stops.
  With no `Select` open it stays `EndSelect w/o Select` (the probe with only an `If` open).
- An `EndIf` in the same position is already `EndIf illegally occurs before BeginProg.` (no change).
- With this error present, the end-of-file `IF or SELECT without EndIf or EndSelect.` and
  `No BeginProg in Program.` checks do not print (observed in the probes with an open block at a
  Sub end and no other closing error).
- CR300/CR350: the `IF or SELECT statement illegally has SCAN without NEXT SCAN.` closer error
  survives this message (`checker/cr3xx.py` `_hides_nothing`).
- Reproducer: `cr1000x/false_accept/9ec2e021b721` (a `Select` inside a `Function`, closed by
  `EndSelect` after the `EndFunction`); the same output on all four loggers.
- A `Sub`/`Function` declared while a block is still open from an earlier Sub end prints `Sub declaration
  inside If..EndIf.` alone at its line (no `executing sequence` line), then its own `If without EndIf inside
  of Sub.` at its end; the same for an open `Select`, for `Function`, for each further Sub, and when the
  later Sub closes the block (oracle, all four loggers, 2026-10-12, fixture
  `tests/fixtures/r13_sub_inside_open_block.json`). Not modelled: the same with a duplicate Sub name on
  CR6/CR1000X (the real run goes on to later pass-2 errors; ours stops).

## Whole-array `SplitStr` destination and `Sub` element arguments (2026-10-11, fixture `tests/fixtures/bounds_whole_array_subcall.json`)

Oracle, all four loggers, identical unless noted.

- **`SplitStr(A(),...,Reps,...)`** (no subscript on the destination): `Variable A out of bounds.` when
  `Reps` is greater than the element count. Count is the product of the dimensions, paren dimensions
  included (`Public S((2+3))` takes 5; `Public M(2,3)` takes 6, so Reps 6 compiles and 7 does not).
  Float, Long and String arrays alike. Reps 0, 1 and -1 compile. A subscripted destination
  (`SplitStr(S(1),...,6,7)`) compiles. `checker/expressions.py` `check_splitstr_whole_array`, run after
  `check_option_values` so `Invalid option -1` comes first (CR6/CR1000X). This replaces the older
  "scalar only" note in `docs/checker.md` Destination bounds, which the 2026-10-11 probes contradict.
  Reproducer: `cr350/false_accept/73c3fc153711` (`Public ResultString((1+2))`, Reps 5).
- **`Sub` element arguments** (`check_subcall_element_bounds`): `S1(P(10))` for `Public P(9)` gives
  `Variable P out of bounds.`; `S1(P(0))`, `S1(R(4))` for `Public R(3)`, `S1(S(6))` for a 5-element
  String array, `Call S1(P(10))` likewise. Valid: `1..n` per dimension, and `S1(P(9))` compiles.
  Only number-literal subscripts count: `P(-1)`, `P(10)+1` and `P(I)` compile. Reproducer:
  `cr6/false_accept/2435be9885e1` (`rt2(&HFF)` passed to a `Sub`).
- Not modelled: a single subscript on a multi-dimensional array passed to a `Sub` (`S1(M(6))` on
  `Public M(2,3)` is out of bounds on the real compiler and accepted here); `S1(P(1,1))` on a 1-D array
  (`Dimensions not declared` on CR300/CR350, accepted here); a function call argument (`X = FN(P(10))`).

## `FieldCal` Mult and Offset arguments (2026-10-11, fixture `tests/fixtures/fieldcal_variable_args.json`)

- `FieldCal` argument 4 (`Mult`) and argument 5 (`Offset`) must be variables the compiler can resolve.
  All four loggers: `FieldCal Mult must be a declared variable that can be resolved during
  compilation.` (`Offset` likewise) for a string (`"fuzz"`), a non-negative number (`1`, `1.5`, `0`),
  an operator expression (`2+3`, `CMult(1)+1`), and an array element with a string subscript
  (`CMult("fuzz")`). Accepted: a variable (`Mv`), an array element with a number subscript (`CMult(1)`,
  `COff(1)`), and a negative literal (`CMult(-1)`). Which argument is checked depends on `Option`
  (first argument): `Mult` for 2 and 3 only, `Offset` for 0, 1, 2 and 4 only (`Option` 1 accepts
  `Mult` 0 and 1; `Option` 3 accepts a bad `Offset`). `Option` 5 and above print `Invalid function
  option.` (not modelled). `checker/instr_rules.py`
  `check_fieldcal_variable_args`, at the same collected stage as the index bounds.
- Reproducer `cr1000x/false_accept/11dd65c2d832` is only partly reproduced: the real compiler also
  prints `Table FC_History declared with no output fields.` at the `EndTable` of a table whose only
  field is `SampleFieldCal`, once a `FieldCal` argument is bad (or no `FieldCal` is present,
  `SampleFieldCal with no FieldCal in program.` plus that line). Not modelled: the real output puts the
  `FieldCal` line before the table line, and the table check runs in pass 2, so the order needs its own
  evidence. The reproducer's exit code is now right and only that second line is missing.
- The fixture's table holds no `SampleFieldCal` for this reason; with a `Sample` in the same table the real
  compiler page-faults (exit 5) on several of the bad-argument cases, so those are not in the fixture.

## `Matrix` destination constant (CR300/CR350, 2026-10-11, fixture `tests/fixtures/matrix_destination.json`)

- `Matrix`'s fourth argument on CR300/CR350: a constant (`3`, `-1`, `1.5`, `"fuzz"`, `2+3`) gives
  `Illegal destination variable type, expecting Float.` A variable (`X`, `Subtract`), an array element
  (`FA(1,1)`) and a whole array compile. `checker/instr_rules.py` `check_matrix_destination`, CR300
  late stage only (`single_pass`).
- On CR6/CR1000X the same constants give `Invalid Variable or Array: <arg>.` (not modelled, so the
  fixture holds CR300/CR350 only). A variable holding a value (`Mv`) gives `Illegal Matrix
  dimensioned` on all four loggers (not modelled).
- The reproducer `cr300/false_accept/f4771af98130` is now exact: the following `Matrix` lines still get
  `Too many parameters:` from the existing rule.

## `CDM_Delay` option 0 duration (round 15, `tests/fixtures/r15_cdm_delay.json`, 216 cases)

Reproducer `cr6/false_accept/f2c51b769e04` (`CDM_Delay (VOLT116,1,0,-1,mSec)`). CR6 and CR1000X
print `Measurement Delay Exceeds 60 min.` once or twice on the statement's line; the count is two
different checks (decompiled CR6Comp `FUN_004160c0` and `FUN_0040f1c0` / `FUN_00422820`, the
device's own delay code; tracer frames 0x416307 and 0x40f1ec):

1. The instruction's own check, with the interval converted to seconds and microseconds as in
   `Scan` (`scan_split`: 32-bit C arithmetic, `/` and `%` truncate toward zero, Min/Hr/Day wrap):
   prints when the seconds are negative, the microseconds are negative, the seconds exceed 3600,
   or they are exactly 3600 with any microseconds.
2. Every supported CDM module then converts to the total `us + sec * 1000000` as an unsigned
   32-bit number and prints again when it exceeds 3,600,000,000.

So `61` Min, `3600001` mSec and every small negative interval (`-1` in any unit, `-24` Hr,
`-1000` mSec) print twice, while `-1` Hr, `-100` Hr, `1` Day, `86400000` mSec and `-3600000` mSec
print once (their wrapped total stays under the limit) and `60` Min, `1` Hr, `0` Day, `500` mSec
none. Implemented for integral constants in Long range (`_cdm_delay_overlong` in
`checker/instr_rules.py`, Option 0 only, CR6/CR1000X); a fractional or out-of-Long-range interval
goes through float arithmetic (`FUN_0050a340`) that is not reproduced, so the earlier notes that
every large `uSec` value is accepted stay unexplained. `Delay(0,-1,Hr)` is a different instruction
with a different check (`checker/instr_measure.py`), unchanged. A 1-day scan is used in the
fixture: a short scan adds `Insufficient time ... for measurements from CPI address 1` lines.

## `ETsz` and `ETClearSky` are table-only output instructions (2026-10-12, round 13)

Found while probing the deferred-text `Too many parameters` instructions at their documented maximum
argument count (`scratch/r13`): `ETsz` and `ETClearSky` (11 parameters each) alone in a `DataTable`
were falsely rejected with `Table T declared with no output fields.` Oracle, all four loggers
(`tests/fixtures/r13_et_table_only.json`): the body `ETsz(...)` / `ETClearSky(...)` alone compiles;
outside a table both print `Et must be used inside a table declaration.` (the unlabelled `"Et"` call
site the `TABLE_ONLY_INSTRUCTIONS` comment left unattributed); a twelfth argument gives `Too many
parameters:  7.` Both are now in `TABLE_ONLY_INSTRUCTIONS` (display `Et`) and in the field-producing
set. Not modelled: the output fields (`ETsz` twice in one table is really `Duplicate field name ETrs`
and `Rso`; `tables.py` derives no ETsz fields).

The same probes found no `Too many parameters` false reject for the 14 instructions 5e454911 made
eligible (AcceptDataRecords 3, Covariance 5, ETClearSky 11, ETsz 11, FFT 6, GetDataRecord 10, Histogram 8,
Histogram4D 17, LevelCrossing 9, Moment 5, RainFlow 9, RainFlowSample 2, SampleMaxMin 4, SendData 5):
the documented maximum, the maximum with the optional argument omitted or empty (`SendData(...,T,)`),
and parenthesised or array-element arguments all match on the four loggers, and max+1 gives `Too many
parameters:  <token>.` exactly.

`ETsz` / `ETClearSky` output fields (2026-10-12, fixture `tests/fixtures/r13_et_duplicate_fields.json`): both
instructions produce two fields, `ETrs` and `Rso` (real TDF of a lone `ETsz` / `ETClearSky` table, all
loggers; `FieldNames("A,B")` renames them). `tables.py` records them as name-only fields (like FFT: never
written to the TDF or counted in the record size, still no TDF fields derived), so `Duplicate field name ETrs` /
`Rso in DataTable T.` now appears for two of them in one table (also `ETsz` + `ETClearSky`, a `Sample` of a
variable named `Rso`, three instructions give the usual pairwise list; renaming one pair apart removes it).

### `ModemCallBack`: the first (result) argument is never resolved (CR6, CR1000X)

`ModemCallBack(ResultX, ...)` with an undeclared `ResultX` (also `ResultX(2)`, `ResultX+1`) compiles
clean on CR6 and CR1000X, as `AddPrecise`'s first argument does (`_UNDEFINED_CHECK_EXEMPT_POSITIONS`).
The name is not declared by the call (a later `ResultX = 1` still fails), and the other arguments
are still resolved (`AbortX` -> `Invalid symbol in expression: AbortX.`); a literal `5` is still
`Invalid Variable or Array: 5.`. R14, `tests/fixtures/r14_modemcallback_result_undeclared.json`
(audited 0 differing), fuzz reproducer `cr1000x/false_reject/5fe667412d48`. CR300/CR350 have no
`ModemCallBack`. The other code-29 instructions probed (`RealTime`, `ReadIO`, `PortGet`, `SMSSend`)
do report an undefined first argument.

### `AddPrecise` / `MovePrecise`: the second argument is not resolved either (CR6, CR1000X)

`AddPrecise(a, Nope)`, `AddPrecise(Nope, Nope2)`, `AddPrecise(a, Nope+1)`, `AddPrecise(a, Nope(2))` and
the `MovePrecise` equivalents compile clean (the first argument was already exempt,
`_UNDEFINED_CHECK_EXEMPT_POSITIONS`). Nothing is declared by the call (`AddPrecise(a,Nope):Nope=1`
still gives `Invalid, or out of place expression: Nope=1.`). An extra argument is still `Too many
parameters:  3.`. R14, `tests/fixtures/r14_addprecise_second_arg.json` (audited 0 differing), fuzz
reproducer `cr6/false_reject/9124242872d5`.

### Reachability: bare `End` and `End Prog` are never flagged (all four loggers)

After an endless `Scan`, `End` and `End Prog` (any whitespace between the words, any case) give no
`End never reached at run time.` and do not end the search: `End` then `a = 1` warns only for `a = 1`
(`Expr never reached at run time.`, the following line). `a = 1` then `End` still warns for `a = 1`
only. R14, `tests/fixtures/r14_end_prog_alias.json` (audited 0 differing); fuzz mode_diff reproducers
`cr6/mode_diff/901f027f907a`, `cr1000x/mode_diff/64c82a8749c9`, `cr300/mode_diff/158bf4c5872b`.
Not modelled: `End 1` (`Illegal END or EXIT 1.`) and `End Prog 1` (`Too many parameters:  1.`).

### `NewFile`: the third argument is not resolved (CR6, CR1000X)

`NewFile(fa,"CRD:x.crb",Nope)` (also `Nope(2)`, `Nope+1`) with an undeclared third argument compiles
clean on CR6 and CR1000X; CR300/CR350 still report `Nope is not defined.`. The first and second
arguments are resolved as usual. Not modelled: `NewFile(fa,Nope,nf)` also gets `FileName parameter
must be type String.` on CR6/CR1000X. R14, `tests/fixtures/r14_newfile_third_arg.json` (audited 0
differing), fuzz reproducer `cr1000x/false_reject/802ff201c036`
(`_UNDEFINED_CHECK_EXEMPT_POSITIONS_CR6X`).

### `DataInterval` with a String `TintoInt` or `Interval` (CR300, CR350)

`DataInterval("fuzz",15,Sec,10)` and `DataInterval(0,"fuzz",Sec,10)` compile clean on CR300/CR350
(CR6/CR1000X: `DataInterval TintoInt and Interval parameters must be non-negative`, unchanged). The
table builder raised `ValueError` from `float("fuzz")` (an `IMPLEMENTATION EXCEPTION`, reported as
`Internal error`); the string is now taken as 0. R14, `tests/fixtures/r14_datainterval_string_arg.json`
(audited 0 differing), fuzz reproducer `cr350/implementation_exception/25a45bf41175`.

### Structure-typed output sources on CR300/CR350: one element, no bounds check, `Fieldnames` inert

On CR300/CR350 an output instruction (`Sample`, `Average`, `Minimum`, `Maximum`, ...) whose source is a
`StructureType` variable expands one element's members whatever `Reps` says: `Sample (255,P,VW)`,
`Sample (4,P(2),VW)` and `Average (3,P,VW,False)` for `Public P(3) As VW` compile, with no `Variable P
out of bounds.` and no `Duplicate field name` lines for the repeated members. `Fieldnames` after such
an instruction renames nothing (`Fieldnames("a,a,a")` is clean, two instructions both followed by
`Fieldnames("a,b,c")` do not collide). Duplicates still appear for a genuine second element or
instruction (`Sample (1,P(1),VW) : Sample (1,P(2),VW)` -> `F`, `A`). CR6/CR1000X are unchanged (the
duplicate lists there are long and not re-verified here). R14, `tests/fixtures/r14_cr3xx_struct_source_reps.json`
(CR300/CR350 only, audited 0 differing); fuzz false rejects `structuretype_1.CR6` mutants (seed 403,
`Average (&HFF,Piezometer,...)` and an `EndTable` moved above the last `Fieldnames`).

### `MoveBytes` option: only the low 15 bits are tested (all four loggers)

`MoveBytes(dest,off,src,off,n,Option)` reports `Invalid transfer option N, must be 0..4.` (N is the whole
value) only when `Option & 0x7FFF` is above 4. `65536`, `65540`, `32768`, `32772`, `-65532`, `-65536`,
`-16777216`, `-2^31` and the INT_MIN foldings (a string such as `"fuzz"`, `NAN`, numbers beyond the Long
range) all compile; `5`, `32773`, `65541`, `-65531`, `-1`, `65535` and `INF` (saturates to
2147483647) are rejected. Identical on CR6, CR1000X, CR300, CR350. R15,
`tests/fixtures/r15_movebytes_option.json` (audited 0 differing), fuzz false reject
`cr1000x/false_reject/240340975c77`.

### `AddPrecise`/`MovePrecise` first argument: strings, function calls and `Status.X` are not constants (CR6, CR1000X)

`Invalid Variable or Array: <arg>.` (see `check_precision_variable`) is only for an argument made of
numeric literals and `Const` names. `AddPrecise("fuzz",.1)`, `MovePrecise("",1)`, a string `Const`
(`AddPrecise(KS,1)`), `"a"&"b"`, `"fuzz"+1`, `("fuzz")`, any function call (`Sqr(4)`, `Len("abc")`,
`Left("abc",1)`, `Chr(65)`) and `Status.Battery`/`Public.x` all compile. Still rejected: `-"fuzz"` and `-KS`
(a unary minus makes it numeric), `"a" = "b"`, `5`, `NAN`, `KN+KN`. `"a"*2` gives `Illegal String operand`
(not modelled). R15, `tests/fixtures/r15_addprecise_string_arg.json` (audited 0 differing), fuzz false
reject `cr1000x/false_reject/bea2207a74da`.
### `Matrix` with undefined operand names, CR6/CR1000X (oracle, 2026-10-12; `tests/fixtures/r15_matrix_undefined_operands.json`)

`Matrix (Option, A, B, Dest)` with an operand that is an undefined bare name prints, for each such operand in order,
`Invalid symbol in expression: <name>.` and `Illegal Matrix`, then the dimension messages (a name that resolves to nothing has
the uninitialised shape `-842150451,-842150451`): options 1, 2, 4, 5 `Illegal Matrix dimensioned`; option 3
`multiply A columns and B rows must be the same` when exactly one of `A`/`B` is undefined, and `multiply result illegal
dimensioned, must be m,q` (`m` = rows of A, `q` = columns of B, either may print as `-842150451`) when `Dest` is undefined.
`Matrix(Zork,X,X,Add)` (the option undefined) and an indexed operand are unchanged. CR300/CR350 are unchanged (`Zork is not
defined.` then the cascade). `instr_rules.matrix_undefined_operands`.
### `RoutersNeighbors` destination subscripts: string literals, negated literals, `Const` names

`RoutersNeighbors (DestArray(<index>))` is `Destination variable cannot be indexed` (all four loggers)
for a string literal subscript (`("fuzz")`, `("")`, `("1")`, `(1,"1")`, `("a",1)`) and a `Const` name whose
value is 2 or more (`Const MaxRouters=3`, `Const Two=2`); `Const One=1` and `Const OneF=1.5` compile, as do
`(1)`, `(+1)`, `(1.5,1)`. A `Const` of 0 or -1 and the literal 0, 0.5, `TRUE`, `NAN` also add `Variable
DestArray out of bounds.` before it (not modelled). A negative literal is not modelled either: `Arr(-1)`
and `DestArray(1,-1)` (the last subscript) compile, `DestArray(-1)`, `DestArray(-1,1)` and `Arr(-2)` do not. R15,
`tests/fixtures/r15_routers_neighbors_index.json` (audited 0 differing); fuzz reproducer
`cr350/false_accept/00c15e34f28e`.

### DNPVariable Flag type (round 98)

On all four profiles, the sixth argument accepts Long and Boolean operands,
including aliases, and rejects Float and String operands. The diagnostic is
`Flag paramter must be of type LONG`. A direct `1.0` fails, whereas a named
`Const R=1.0` passes. Signed decimal bounds follow the logger's expression
conversion. CR300/CR350 accept the rounded literal `2147483648`, while
CR6/CR1000X reject it. The flag check follows Class validation and precedes
event-count validation. A flag type error still registers the group.

The CR6 builder `FUN_004316c0` checks the operand's integer type bit returned
by `FUN_00507680`. The fixture records boundary and ordering probes and the
two remaining CR300/CR350 `CType(...,Boolean)` cascade differences.
[`r98_dnp_flag_type.json`](../tests/fixtures/r98_dnp_flag_type.json).

### `DNPVariable` argument rules (all four loggers, round 15)

Fixture `r15_dnp_variable_args.json` (audited against the real compilers). Constant arguments convert like `Long` ones
(`longconv`; `TRUE` is -1; a string or NAN is -2147483648); `Group` and `Variation` keep their low 16 bits as a
signed value (`65537` is 1, `65536` is 0, `32768` is negative). Checks per statement, in this order, with
`instr_rules.check_dnp_update`:

1. `Swath` (truncated) outside `1..N` -> `Swath parameter illegal, must be 1..N`, N = elements left in the
   source (scalar 1, `A()` the whole size, `A(n)` of a 1-D array size-n+1; `S` of group 110 is 1; a String
   source is not measured for other groups). Nothing else is reported for the statement.
2. `Variation` <= 0 -> `Variation must be greater than 0.`; stops there.
3. `Group` not one of 1, 2, 10, 12, 20, 21, 22, 23, 30, 31, 32, 33, 40, 41, 110 (50 and 60 included) ->
   `Illegal DNP3 group N.`; stops there.
4. Static group needed first: 2<-1, 12<-10, 21<-20, 22<-20, 23<-21, 31<-30, 32<-30, 33<-31, 41<-40
   (`Must assign group B before using group G.`; stops there).
5. Source type: groups 1, 2, 10, 12 Boolean; 20-23 Long only; 30-33, 40, 41 Long or Float; 110 String
   (`For DNP3 group G variable must be of type Boolean|Long|String.`).
6. `Variation` above the group maximum (1:2, 2:3, 10:2, 12:1, 20:8, 21:12, 22:8, 23:8, 30:5, 31-33:7, 40:3,
   41:3; none for 110) -> `Illegal Variation V, max for group G is M.` (V is the 16-bit value).
7. `Class` (truncated) 1..3 on a static group (1, 10, 12, 20, 21, 30, 31, 40, 41, 110) -> `Static Data
   Class: must be 0`, and the statement then fails (see below); any `Class` outside 0..3 (a string, `TRUE`, 4,
   -1) -> `Illegal Class: must be 0..3`, which does not fail it. Event groups (2, 22, 23, 32, 33) take 0..3.
8. [Flag type](#dnpvariable-flag-type-round-98).
9. `NumEvents`: nonzero on a static group -> `N Events allocated for a non-Event object` (N is the converted
   value: `"a"` and NAN -2147483648, `2147483648` -2147483648 on CR6/CR1000X and 2147483647 on CR300/CR350);
   zero on an event group -> `Event size must be non-zero to hold events` (negative is accepted).

0. Before all of these, the `Source` subscripts (all four loggers): only a literal, a negated literal or a `Const`
   name is usable; `B(M)`, `B(1+1)`, `B("x")`, `B(F(1))`, `D2(M,1)` and `D2(-1,1)` (a negative non-last
   subscript) give `Cannot use a variable index reference.` alone; a truncated constant outside `1..size`
   (`B(0)`, `B(6)`, `B(0.5)`, `D2(4,2)`) gives `Variable B out of bounds.` first. A negative last subscript counts by
   magnitude (`B(-2)` of `B(5)` leaves 4 for `Swath`); the elements left are counted row-major (`D2(2,1)` of
   `D2(3,2)` leaves 4). Not modelled: `B(TRUE)` (out of bounds plus the line), `B(1,"x")` on a 1-D array,
   the `Flag` argument (`L("x")` is `Variable L out of bounds.`). Fuzz reproducer cr300 `false_accept/2b28ebdf538a`.

A statement stopped by 0-3 or by the static-class rule is not counted as defined, and is not "assigned"
(a later event group gets `Must assign`); group 110 never counts as defined either, so a `DNPUpdate` after
only those gets `DNP3Statics and/or DNP3Events are not defined`. Type, variation-range and
`Illegal Class` failures still count. Not modelled: `L(6)` past the array end (`Variable L out of bounds.`
plus `Cannot use a variable index reference.`), `Flag`. Fuzz reproducers cr6 `false_accept/b1e01ed1dd76`,
cr350 `9ad721676cb3`.

### `SNMPVariable` object identifiers (all four loggers, round 15)

Fixture `r15_snmp_oid.json`. `instr_rules.check_snmp_oid`; the parser (`parser.SNMP_OID_REASON`) leaves
any statement whose OID is a digits-and-dots literal as the known `Unparsed` gap (`1.4.9`, `2.5.7.1`,
`5.5.5` and `10.20.30.40` compile on the real compilers).

- Floor `1.3.6.1.2.1.1.7`: an OID that sorts at or below it (component by component, a shorter prefix is
  lower, an empty component such as a leading dot counts as 0) is `SNMP OID must be greater than
  1.3.6.1.2.1.1.7`; CR6/CR1000X exclude only below (the equal OID is `Illegal OID since already declared.`
  alone); CR300/CR350 include the equal one.
- Already declared: CR6/CR1000X own `1.3.6.1.2.1.1.1` .. `.7` (so `.1.1.6` gets both lines, `.7` only
  the second); CR300/CR350 own `1.3.6.1.2.1.1.7.0` and `1.3.6.1.2.1.1.7.` instead (`1.3.6.1.2.1.1.7.0`
  compiles on CR6/CR1000X). A repeated OID gets one `Illegal OID since already declared.` per earlier
  statement with the same OID, the earlier statement's own errors notwithstanding; `5.` and `5.0` differ
  from `5`. Components past 65535 are not modelled (`...1.65543` counts as 7 on CR6/CR1000X; 4294967303 does
  not) and non-numeric components compile.
- Non-dotted OID argument (the argument after the variable, so a dropped OID shifts `Float` into it): a
  declared or undefined name, `0`, `1`, `-1`, `""`, `"x"` give `Illegal object id.` then the greater line;
  `2` adds `Variable array must be dimensioned at least 2` (not modelled); a decimal literal compares as an
  OID (`1.3` is the greater line, `1.4`, `1.5`, `2.0`, `5.5` compile); a string of digits and dots gives only
  the greater line (`"1.4"` too, whatever its value). An expression (`1+1`) is `Illegal object id.` too, not
  modelled; a leading-dot literal (`.1.3.6.1.4.1.5`) is a parser gap here.
- Messages stay on the statement's line before `Miss match of types` for a dotted OID; they hide neither
  other lines nor `Too many parameters:  Sec.`-style CR300/CR350 cascades, which they start; an
  `SNMPVariable` after a cascade is never rewritten to `Too many parameters:  <arg>.`. Not modelled: the
  `Miss match of types` / `Too many parameters` ordering when both occur. Fuzz reproducers cr1000x
  `false_accept/e3a11f6b9adc`, cr6 `f2f6e5d8b8e6`.

### `FieldCal` argument rules (all four loggers, round 15)

Fixture `r15_fieldcal_args.json` (template: `Public Voltages(2), CMult(2), COff(2), Meter(2), Mv, Mode, Str As
String * 20`). Extends "FieldCal Mult and Offset arguments"; `instr_rules.check_fieldcal_variable_args`.
Statement order of the messages: `MeasVar`, `Illegal Reps`, `Mult`, `Offset`, `Mode`, `KnownVar`, then the size line.

- `Option` (constant, truncated: `2.5` and `1+1` are fine) outside 0..4, or a string or `TRUE`: `Invalid
  function option.` alone.
- `Reps` (arg 3, truncated; `2.5` is 2, `0.5`, `TRUE` and NAN are not): outside 1..2 -> `Illegal Reps: must be
  1 or 2.`, every option; not printed when `MeasVar` is bad.
- `MeasVar` / `Mode` (args 2 and 6) written as an element with a string subscript (`Meter("fuzz")`) ->
  `FieldCal MeasVar|Mode must be a declared public variable that can be resolved during compilation.`, every
  option. (A literal or a string variable there differs per logger, not modelled.)
- `KnownVar` (arg 7), options 1, 2 and 3 only (0 and 4 never resolve it, so `Meter(3)` of `Meter(2)` compiles
  there): a string, any number, `-1`, an expression,
  a String variable, an element with a string subscript, or an out-of-range element (`Meter(3)`, after
  `Variable Meter out of bounds.`) -> `FieldCal KnownVar must be a declared public variable that can be
  resolved during compilation.`
- CR300/CR350 print only the first of `MeasVar`/`Reps`/`Mult`/`Offset` for a statement (`Mode` and `KnownVar`
  still follow).
- Options 1-3: `KnownVar` and `MeasVar` one-dimensional or scalar of different declared sizes (`Mv` against
  `Voltages(2)`; elements count as their array) -> `<KnownVar name> and <MeasVar name> must be declared the
  same size.`, only for a statement with no other message, and once per distinct text in the program.
Reproducer cr1000x `false_accept/a93fa575340a`.

### CDM port sets per device (CR6/CR1000X, round 15)

Fixture `r15_cdm_ports.json` (CR300/CR350 have no CDM instructions). `CDM_SW12`, `CDM_SW5` and
`CDM_PulsePort` carry only the CDM-A108 (220), CDM-A116 (221), VOLT108 (222) and VOLT116 (223); their `Port`
(argument 3, truncated; a string, `TRUE`, 0 and negatives never pass) must be in the device's set else
`Unrecognized port!`:

| Module | `CDM_SW12` | `CDM_SW5`, `CDM_PulsePort` |
|---|---|---|
| CDM_A108, VOLT108 | 1 | 1, 2 |
| CDM_A116, VOLT116 | 1, 2 | 1, 2, 3, 4 |

(`measure.json` `const_ranges` `by_module`; the old rule allowed 1 and 2 for every module, a false reject for
A116/VOLT116 ports 3 and 4.) Any other constant named module prints only `SW instruction not supported by
module <name>.` (`CDM_PulsePort instruction ...` for that one) with no port line: names `EC100`(100), `CSAT3B`,
`TGA300`, `IRGASON2`, `EC155UNH`, `IRGASONHP`(105), `CDM-VW300`(200), `CDM-VW305`, `VWIRE305`, `TEMP120`(224),
`VOLT408V1`, `VOLT408V10`, `VOLT408V60`, `TEMP408J`, `TEMP408K`, `TEMP408T`, `CURRENT408`(231), `SPECTRUM103`
(260), `SPECTRUM109`; unnamed values are `NULL` as before. The same support matrix for the other CDM
instructions is not modelled. Fuzz reproducers cr1000x `false_accept/496cca6dbced`, cr6 `05c461af995f`.

### CDM measurement channels (CR6/CR1000X, round 15)

Fixture `r15_cdm_channels.json`. `CDM_VoltSe`, `CDM_VoltDiff`, `CDM_TCSE` and `CDM_TCDiff` on the four
standard modules (`measure.json` `const_ranges` entries with `channel_by_module`, handled by
`instr_measure._apply_cdm_channel`; other modules keep the general channel rules):

- Last channel: single-ended (`VoltSe`, `TCSE`) 16 for CDM-A108 and VOLT108, 32 for CDM-A116 and VOLT116;
  differential (`VoltDiff`, `TCDiff`) 8 and 16.
- A negative `SEChan` is a two-measurement pair counted by magnitude: `-1`, `-16` (and `-1.5`, `TRUE`) compile when
  the magnitude is in range; a larger magnitude gives `Input channel: <magnitude>, out of range (1 - N).` and
  `Two-measurement instruction starting on last input channel!` after the `Reps` lines.
- Zero (also `0.5`, `-0.5`) gives `Input channel: 0, ...`; a string, NAN, a number past Long (`999999999999`) and
  `-2147483648` give `Input channel: -2147483648, ...` alone (no follow-up line).
- A channel above the last one adds `Reps cycle input beyond last channel!` (after the `Reps: ...` range line);
  a valid channel adds it when `Reps` is 1..65536 and `channel - 1 + Reps` runs past the last one (`Reps 2` at
  channel 16 of 16, `Reps 3` at 15).
- Order on the line: `Settling time`, `Input channel`, `Reps: ... out of range`, `Reps cycle input...` or
  `Two-measurement...`, `MeasPEx`. Not modelled: `Variable TT out of bounds.` for `Reps` above the destination, and
  `When channel < 0, MeasPEx must equal Reps.` for an out-of-range `Reps` with a negative channel.
Fuzz reproducer cr1000x `false_accept/f543da8c46ab`.

### `Debug` argument types and `HistorySize` (CR6, CR1000X, round 16)

Fixture `r16_debug_args.json`; `instr_rules.check_debug_args` and `debug_history_oom`. `Debug` does not exist on
CR300/CR350 (`Debug is not defined.` cascade, unchanged).

- `Control` (third) and `LineBreak` (fourth) must be a `Long` or `Boolean` variable (scalar, array, element or
  parenthesised); `TraceHistory` (fifth) a `String` variable (any `String * N`). Anything else -- a number, string,
  `Const`, `TRUE`/`FALSE`/`NAN`, an expression, `-X`, a function call (`Abs(1)`, `Left(..)`), a `Status` member, a
  `Float`/`Double`/other-typed variable or alias of one, an undeclared name -- adds one `Illegal parameter(s)` to the
  statement, after the `Invalid symbol in expression:` / `Could not find Status.X(1).` lines its arguments draw. Each
  bad `Debug` statement prints it once. Not modelled: `Variable X out of bounds.` before it for a scalar given an
  index (`Debug(-1,20,C,L(2),T)`).
- `HistorySize` (second): the `long` of the argument times 4, as an unsigned 32-bit byte count, above 26,843,544 gives
  `Out of memory.` (6,710,886 passes, 6,710,887 fails; same on CR6 and CR1000X). The `long` follows
  `checker/longconv.py` (NAN, strings and numbers beyond 2^31 are -2^31 and wrap to 0 bytes, so they pass; `-1`,
  `&HFFFFFFFF`, `2147483647` and `INF` wrap to 4294967292 and fail; 1073741824 wraps to 0 and passes). A decimal literal
  is a float32 first (`6710886.6` passes, `6710886.9` fails, `1073741823.6` becomes 2^30 and passes). `Out of memory.`
  ends the compile (no `Invalid symbol`, `Illegal parameter(s)` or later-line errors; a wrong argument count still wins).
Fuzz reproducer cr1000x `false_accept/823848325de3`.

### `GZip` argument count (CR6, CR1000X, round 16)

Fixture `r16_gzip_arity.json` (`expressions._ARITY_RANGE`). The table says one argument; the call takes exactly two
(`Filename, ZippedFilename`): one gives `Expression expects , : <statement>.`, three or more `No Matching, or out of place
) in expression: <statement>.`, in an assignment or as a bare call. Fuzz reproducer cr1000x `false_accept/27dda59dbe0b`.

### `HTTPPut` / `FTPClient` trailing `TimeOut` type and its parser-mode gate (all four loggers, round 17)

Fixtures `r16_http_ftp_timeout.json` (ungated), `r17_timeout_gate.json`, `r17_timeout_mode.json`
(`expressions._CONST_INTEGER_TYPE_ARGS`, `intervals.timeout_gate`/`_check_timeout`). The 9th argument of `HTTPPut` and the 11th
of `FTPClient` is `TimeOut`. What a string, `NAN` or float literal there draws depends on a parser mode that `IfTime` and
`TimeIntoInterval` set and never restore (oracle, all four loggers; hypothesis from the decompilation notes in
`scratch/codex/re-r17.md` section 3 confirmed by probes):

- No earlier `IfTime`/`TimeIntoInterval`: `Parameter must be a constant integer: <statement>.` before the argument-count check
  (the round-16 rule; the first round-16 attempt applied it everywhere and made fuzz reproducers cr350 `mode_diff/85bcd6b49434`,
  `f9b89ecaf1f0` false rejects).
- After one -- in any earlier statement (another `Scan`, a `Sub` before `BeginProg`, the condition of an enclosing or earlier
  `If`, earlier in the same statement), source order, not execution order: a string (also `""`) or a variable/function call is
  `Warning: Parameter will truncate to the nearest integer.` (once, at the call line, before any argument-count error); a
  float-typed constant (`1.5`, `1.0`, `1E3`, a `Const` of 1.5) or `NAN` is `Parameter must be integral.` (before the
  argument-count error). A literal beyond 32 bits is also `integral` on CR6/CR1000X (round 19: and on CR300/CR350 past the
  float32 band, see the next section).
- A later `Timer(` or `SerialOpen(` call resets the mode (CR6, CR350 probed): the constant-integer error returns. `Sqr`,
  `Round`, `Public.Timestamp` do not set the mode. An `IfTime` after the `FTPClient` in the same statement is not modelled.

Round 19 (fixture `r19_inline_if_timeout.json`, 140 cases, all four loggers, `intervals.gate_text`): inside a one-line `If`
the mode also advances in source order: the condition, then the `Then` body, then the `Else` body, then each `:`-separated
statement in turn. `If 1 Then FTPClient(..) Else Timer(..)` after an `IfTime` is a truncate warning only (the `Else` `Timer`
does not reach back); `If 1 Then FTPClient(..) Else IfTime(..)` with none before is the constant-integer error; an `IfTime`/
`Timer`/`SerialOpen` in the condition counts before the bodies. `HTTPPut` and a float `FTPClient` timeout follow the same
rule. One-line `ElseIf` is not valid CRBasic (`Incomplete expression`; also `ElseIf is not defined` on CR300/CR350).

The truncate warning of a clean compile prints before every later-pass warning (reachability, fnotch) whatever its line
(`compiler._sort_key`). Not modelled: variable timeouts outside the gated state (constant-integer, needs the symbol table).

### `HTTPPut` / `FTPClient` constant `TimeOut` value and sign (all four loggers, round 19)

Fixture `r19_timeout_values.json` (244 cases; `intervals.timeout_value`, `intervals._check_timeout_value`, `intervals._decimal`,
`expressions._timeout_wide`). Oracle probes 2026-10-17 (`scratch/r19/W8/`, `scratch/r19/timeout/run.txt`,
`scratch/r18/W3/run_timeout1.txt`). Gated and ungated results agree wherever both are noted below. Rules:

- **Negative product.** A constant integer `TimeOut` with 32-bit value `v` gives `p = signed32(10 * v)`; `p < 0` is
  `line N: Parameter TimeOut cannot be negative` (no statement text), on every logger, gated or not. Observed: `214748364` OK,
  `214748365` NEG, `429496729` NEG, `429496730` OK, `2147483646` NEG, `2147483647` NEG, `-2147483646` OK, `-2147483647` OK,
  `-2147483648` OK, `-1` NEG, `+2147483647` NEG, `02147483647` NEG, `TRUE` NEG, `FALSE` OK, `&H7FFFFFFF` NEG, `&H80000000` OK,
  `&HFFFFFFF6` NEG, `&H00000001` OK. The check does not depend on the mode (`IfTime` earlier or in the same statement).
- **Float classification.** CR6/CR1000X: a decimal literal past `2147483647` is a float (`+2147483648`, `2147483649`,
  `-2147483649`, `-2147483650`, `4294967295`), except the exact `-2147483648` (integer, product 0, OK). Ungated it is `Parameter must
  be a constant integer: <statement>.`, gated `Parameter must be integral.`. A float literal (`-1.5`, `-2147483649.0`) is the same
  constant-integer error ungated. CR300/CR350 (single pass): the float32-rounded magnitude past `2147483648` is a float (`2147483777`,
  `-2147483777`, `4294967295`, `-4294967295`), while `2147483775` and `2147483776` round to `2147483648` and are integers.
- **CR300/CR350 saturation.** Inside the rounding band the digits saturate at `2147483647` before the sign: `2147483648`,
  `+2147483648` and `2147483776` are NEG (`-10`), `-2147483649`, `-2147483650` and `-2147483776` are OK (`-2147483647`).
- **Const holders.** An in-range integer `Const` is its value (`Const KT = 214748365` NEG, `429496730` OK, `-1` NEG, `-2147483648`
  OK, all four loggers). `Const KT = 2147483648` is a float on CR6/CR1000X (constant-integer, ungated) and NEG on CR300/CR350;
  `Const KT = -2147483649` is OK on all four (it is not a float constant; out-of-range negative `Const` values are not modelled).
- **Undefined names and ordering (probes 2026-10-17, fixture rows `a` and `n`).** CR6/CR1000X: a call whose other argument is an
  undefined name prints no NEG line; the statement shows `Invalid symbol in expression:` alone. A NEG line that is printed (pass 1)
  suppresses the pass-2 `Invalid symbol`/`Invalid, or out of place expression` lines of the statements, so with a plain NEG call on
  the same line as an undefined-name call only the NEG line remains (`HTTPPut(...,NoSuchVar,...) : FTPClient(...,429496729)`
  gives one NEG line). After an earlier `Zq = 1` line, a call with an undefined name prints both `Invalid, or out of place
  expression` and its `Invalid symbol` line, and a plain NEG call prints the NEG line only. CR300/CR350: the undefined-name line
  first, then the same-line NEG line(s) (`order_timeout_negative`). The count error on the same call (`214748365,9`: NEG, then
  `No Matching, or out of place )`) keeps NEG first on all four loggers. Not modelled: an undefined name in the TimeOut slot
  itself (CR300/CR350 print an ungated `Parameter must be a constant integer:` line; those rows are excluded from the fixture).
- Integer expressions and signed hex/binary forms are covered by the round-21 and round-23 sections below. Other literal
  spellings (`&O`, leading-zero boundary spellings) and some same-line diagnostic ordering remain open.

### Negative `TimeOut` literals stop folding after a reported error (all four loggers, round 21)

Fixture `r21_negfold.json` (204 cases, 51 sources x 4 loggers, audit clean 2026-10-19; `intervals.fold_timeout_expressions`,
`intervals.order_timeout_negative`, `var_types.check_string_operands`). Mechanism from the read-only RE
(`scratch/codex/re-r20-timeout-cr3xx.md`), every verdict below observed on the real compilers (CR6 and CR300 on all
spellings, CR1000X and CR350 on the 56-case set and the fixture):

- `-N` is `N` plus an integer negation, folded into a constant only while the compiler-wide REPORTED-error count is zero
  (warnings do not count; `:` and newlines do not reset it). Clean: `-1`, `(-1)`, `- 1`, `-&H1`, `-(1)`, `-+1`, `+(-1)` are `Parameter
  TimeOut cannot be negative` (NEG) by value; hex under one minus is `signed32(-signed32(hex))` (`-&H1999999A` NEG,
  `-&H19999999`, `-&H80000000`, `-&H7FFFFFFF`, `-&HFFFFFFF6`, `-&H0` fine); `--1`, `-(-1)` fine.
- After any reported error, ungated: every spelling that needs the fold (`-1`, `-0`, `-5`, `-2147483648`, `-&H1`, `-&H7FFFFFFF`,
  `--1`, `-(-1)`, `+(-1)`) is `Parameter must be a constant integer: <statement>.` (CR300/CR350 add the `| .` echo line), whatever
  the value. Gated (after `IfTime`/`TimeIntoInterval`, `timeout_gate`): silent, no NEG either. Literals that need no fold
  (`214748365`, `+2147483647`, `+&H1`, `&HFFFFFFF6`) keep their clean result. A float literal is the constant-integer error
  clean or not.
- What counts as reported: `Illegal String operand`, `Incomplete expression`, a previous NEG/constant-integer line, any
  error of an earlier line or earlier `:` statement; CR300/CR350 also an undefined name (`Nope + 1 : HTTPPut(..,-1)`, or `Nope` in
  an earlier argument of the call). CR6/CR1000X defer the whole expression holding an unresolved name to pass 2, which runs only
  when pass 1 reported nothing: a deferred name does not count (`Nope + 1 : HTTPPut(..,-1)` is NEG, `-&H1` NEG).
- The String-operand errors (`Illegal String operand`, `Illegal operand of type STRING`, `String is illegal here`) are
  parse-time errors on all four loggers: printed whatever precedes or follows (`Abs(1,2) : X = "a"*2` and the reverse print both;
  `"a"*2 : Nope+1` prints only the String error on CR6/CR1000X). `var_types.check_string_operands` runs them in pass 1; the
  one-error-per-statement limit of `_string_operand_messages` still holds (`"a"*"a"` prints two on the real compilers, not modelled
  there).
- Order: diagnostics in encounter order. CR300/CR350 `HTTPPut(..,-1) : Result(4)=Nope+1` is NEG then `Nope is not defined.`; a
  name in the call or an earlier statement of its line still comes first (`order_timeout_negative`, round 19).
- Not modelled: an earlier error that follows the call in the same line when the call has an earlier segment (counted as earlier),
  the same effect on the other constant-integer parameters (suspected: `TCPOpen`/`SerialOpen` constant arguments, `Timer`'s third
  argument, `Scan`/`DataInterval` literals, `Const` values). Round 23 extends the expression forms below.

### Integer `TimeOut` expressions and named negation (all four loggers, round 23)

Reference observations from 2026-10-19 are preserved in `r23_timeout_expressions.json` (236 cases, 59 sources x four profiles).
The cases cover clean/error/gated combinations, overflow, preceding and following errors, unresolved sibling arguments,
colon and inline-If statements, and both HTTPPut and FTPClient. The checker now classifies integer
`+`, `-`, `*` expressions, unary signs on integer constants and expressions, and signed hex/binary literals. Each arithmetic
result wraps to signed 32 bits before the existing `signed32(10 * TimeOut)` negative check. `+KT` alone needs no fold;
`-KT`, `KT+1`, `1-1` and `-(KT+1)` do. Earlier reported errors disable those folds, yielding the constant-integer diagnostic
ungated and no TimeOut diagnostic after `IfTime`/`TimeIntoInterval`, just as for negative literals in round 21.

The mechanism was traced in a fresh local CR6 decompilation: `FUN_00497d60` evaluates and replaces an expression buffer only
when both its local expression error and `DAT_00667e64` (reported errors) are zero. `FUN_0049edf0` accepts the resulting
integer descriptor (`0x123`); other descriptors either set the constant-integer error or follow the persistent integral-mode
branch. `FUN_0049f6e0` multiplies its result by ten and reports a negative product. These are behavioral observations;
decompiled code is not copied into the implementation. The CR1000X/CR300/CR350 transfer is established by reference probes.

The broader local probe sweep (`scratch/r23_probe.json`, 320 observations) also confirmed these **unimplemented** neighbors
on all four profiles; they are deliberately outside the regression fixture's implemented scope:

- With `Const KT=1`, `KT/2` and `2^0` report the constant-integer error ungated. Gated, they report `Parameter must be integral.`
  while clean, but after `X="a"*2` they instead warn `Parameter will truncate to the nearest integer.`.
- `NOT 0` gives NEG while clean. After a reported error it gives the constant-integer error ungated and no TimeOut diagnostic
  gated. `1<<31` is accepted clean, but needs the constant-integer error after an earlier error when ungated.
- A declared Float variable `X` as TimeOut gives the constant-integer error ungated and the truncate warning gated, with or
  without an earlier error. The current implementation misses both.
- `X=IfTime("a",1,Sec)` before a negative TimeOut preserves its truncate warning ahead of NEG. The warning is currently missing;
  this does not change the observed fact that warnings leave folding enabled.

Unresolved TimeOut operands and the other constant-integer parameter families also remain separate gaps. The existing
same-line attribution limit from round 21 remains; this change does not generalize the expression evaluator for other
instruction parameters.

### Function-as-statement arity, `Timer` arity, `Sub`-call echo, string over-index counts (all four loggers, rounds 16-17)

Fixture `r16_function_statement_arity.json` (172 cases, audits clean against the real compilers, 2026-10-16;
first written as an unverified WIP in round 16 and completed here). Rules, all four loggers:

- `Timer` takes exactly 3 arguments (`expressions._ARITY_RANGE`): fewer `Expression expects , : <statement>.`, more `No
  Matching, or out of place ) in expression: <statement>.`. Its third argument being a string, `NAN` or float literal is
  `Parameter must be a constant integer: <statement>.` whatever came before, also after an `IfTime` (the `TimeOut` gate above
  does not apply; CR6 and CR350 probed).
- A user `Function` called as a statement needs its closing `)` (a `Sub` call does not); a wrong built-in call nested in a
  `Function` statement's arguments echoes the whole statement; inside a `Sub` call only the offending argument is echoed
  (`check_function_arity`, `check_function_call_counts`).
- Declared string variables count their dimensions for over-indexing like numeric ones on every logger
  (`symbols.declared_dim_counts(strings=True)`).

### Table-only instruction with a malformed source argument outside a table (all four loggers, round 17)

Fixture `r17_table_only_source.json` (parser `_table_only_outside_table`, completing the round-16 WIP 4ab1b610).
`Sample (1,x y,FP2)`, `Average`, `Maximum`, `Totalize` (and `Median`, `StdDev`) outside a `DataTable` report `<Name> must be used
inside a table declaration.` (`Max` for `Maximum`) even when the second (source) argument does not parse, because that
argument is not read before the placement error. Other malformed shapes (`Sample (1,x y,FP2) z`, an unclosed parenthesis,
a missing argument) keep their own parse messages, which are still not matched on every logger and are not covered.

### FTPClient PutGetOption

CR6/CR1000X: a constant `PutGetOption` (sixth argument) is truncated to a Long and must lie in
-29..29; otherwise `Invalid PutGetOption: <value>` (`30`, `-30`, `&HFF` as 255, `2147483648` and
`999999999999` as -2147483648). Above the float32 range (`1e39`) the value is 2147483647; below the
Long range (`-2147483649`, `-1e39`) it compiles (round 67). `29.9` and `-29.9` compile; a variable or
a String is not checked. A literal of hundreds of digits gives no usable reference verdict.
CR300/CR350 accept every value. Round 65, `r65_ftpclient_option.json` (316 observations); found by
fuzz `3b789efdd145` (seed 467). The CHM lists fewer options than the compiler accepts.

### GetRecord destination size

`GetRecord(Dest, Table, N)` with a numeric `Dest` whose whole declared size (whatever its subscript:
`E(2)` of `E(3)` holds 3) is smaller than the record's value count is `Destination array not large
enough for the <n> fields in a record`, then `Could not find DataTable <Table>.`, and ends the walk
like a `GetRecord` of an unknown table (all four loggers). The count is every element of every field
(`Sample(6,F2,FP2)` counts 6) with each String element once; a String destination is never checked.
A scalar, a Boolean, a Long and an `Alias` of a scalar are 1. CR300/CR350 drop it when a later line
holds an undefined name (not modelled). Round 65, `r65_getrecord_size.json` (100 observations);
found by fuzz `ca5b1677129c` (seed 468, a GEUS SnowFox program with its String destination changed).

## TriggerSequence event and CDM_PulsePort delay (round 88)

All four compilers truncate the `TriggerSequence` event argument to a Long and test its low
byte. Values 1–4 pass, including 257 and −255 after conversion. Other constant values report
`Illegal EVT, 1..4 are legal`. On CR6/CR1000X, the fourth `CDM_PulsePort` argument accepts
Long-converted delays 0–100000 inclusive. A String or overflow converts to −2147483648 and
produces the same range diagnostic as a negative delay. See the dated results and compiler
hashes in `tests/fixtures/r88_event_pulse_ranges.json`.

## TimedControl and SDMSW8A options (round 90)

On CR6 and CR1000X, `TimedControl` checks its seventh argument after conversion to Long.
A declaration before `BeginProg` requires clock set mode 1–3. A reset inside the program
requires mode 1–2. `SDMSW8A` checks the low byte of its fourth argument and accepts 0–3.
The dated reference results and compiler hashes are in `tests/fixtures/r90_timed_switch_options.json`.
