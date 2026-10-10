# Fuzzing findings (phase 4)

`tools/fuzz.py` mutates programs the real compiler accepts (`crbasic/*.CR6` with their `.crb`
siblings, and the oracle-accepted subset of `tests/corpus/chm/`) and compiles each mutant with
both opencrbasic and the real compiler (`tools/oracle.py`, cached). `tools/fuzz_replay.py`
re-checks a saved reproducer or the whole saved corpus without re-fuzzing; its `--prune-fixed`
deletes each reproducer's `.CR6`/`.json` pair once opencrbasic now matches the *recorded*
real-compiler output exactly, without re-invoking Wine. Both are re-runnable:

```
python3 tools/fuzz.py --logger cr6 --count 1500 --seed 1
python3 tools/fuzz.py --logger all          # cr6 1500, cr1000x/cr300/cr350 300 each, seed 1
python3 tools/fuzz_replay.py                # re-check every saved reproducer
python3 tools/fuzz_replay.py --logger cr350 tests/corpus/fuzz/cr350/hang/*.CR6
```

The 248-reproducer result and first-pass counts below are historical observations from the run that
produced this document. They describe that corpus at that revision and must not be read as current
parity totals. Use a fresh `tools/fuzz_replay.py` report for current counts and classes.

## Worker isolation

`run_ours` executes each opencrbasic compile in a spawned child process on every platform. Spawn
starts a fresh interpreter rather than inheriting the parent process's threaded state and locks.
Python documents the process start methods in the
[multiprocessing contexts documentation](https://docs.python.org/3/library/multiprocessing.html#contexts-and-start-methods).
The original fork measurements below motivated that choice, but they do not establish inherited
locks as the cause of every false hang.

An earlier implementation used `fork`; a 1,949-case replay then reported nine hangs even though
direct compilation of all nine completed in 0.000–0.024s. Replaying those nine with spawn produced
five exact results, one `false_accept`, three `message_diff`, and zero hangs. The completed full
spawn replay reported 1,101 exact, 117 `false_accept`, 697 `message_diff`, 14 `mode_diff`, 19
`oracle_error`, and one `hang`: `tests/corpus/fuzz/cr350/false_accept/165684c6e47d.CR6`. That case
completed in under 0.6s when rerun alone and changed to `message_diff`. These measurements show
that the timeout was not a deterministic compiler hang.

A separate parent-side lifecycle race was reproduced with spawn and the old Queue protocol. The
focused eight-call parallel test failed on its tenth repeated pytest run, yet the failing pytest
process finished in 0.30s. Instrumentation showed `Process.join(8)` return after 0.064s while
`is_alive()` was still true and the Queue already held the complete correct compiler result. The
parent therefore labelled a completed compile as an 8s hang without waiting 8s. `run_ours` now
uses a one-way Pipe, waits for either a result or the actual `time.monotonic()` deadline, and has no
Queue feeder thread. Its focused validation ran 40 parallel spawns, a worker exiting without a
result, and a 128 KiB pipe result; all four tests passed in three consecutive focused runs.

A subsequent full replay with `--workers 2` completed all 1,949 cases with 1,101 exact, 117
`false_accept`, 698 `message_diff`, 14 `mode_diff`, 19 `oracle_error`, and zero `hang`, `crash`, or
`false_reject` results. A fresh full four-worker replay remains pending. See `PROGRESS.md` for the
current implementation and verification status; this document retains the evidence and
interpretation from the fuzzing work.

## Method notes (read before the counts)

- **Classify counts are exact; saved-reproducer counts are a deduplicated sample.** Each run
  classifies every mutant it generates (the `classified N mutants -> ...` counts below are exact,
  complete tallies). Saving a minimal reproducer for *every* non-exact mutant, as literally
  instructed, does not scale once one systematic gap produces hundreds of near-identical mutants
  (this run's dominant case: `false_accept` from the checker's known-incomplete instruction-specific
  value checks, PROGRESS.md's phase-3 state). `tools/fuzz.py` instead deduplicates by a coarse
  signature (class + the real compiler's own detail text, digits and quoted text generalised) and
  keeps up to `--max-per-signature` exemplars (default 3, generous 10-20 for `false_reject`/`crash`/
  `hang`). `tests/corpus/fuzz/summary-<logger>.json` records the true classify-phase counts
  alongside the saved count, and this document's counts table is copied from those files.
- **The `cr6` run's save phase was cut short deliberately.** Deduplication still left several
  hundred distinct `(class, signature)` groups for `cr6`'s 1500-mutant run (`false_accept` alone
  spans most of the corpus's seed files); shrinking and saving all of them was not worth the real-
  compiler time it would cost. The scripted run was stopped once diminishing returns were clear;
  its exact classify-phase counts (all 1500 mutants, none skipped) are preserved, and the one
  `crash` and one `false_reject` it found were specifically recovered from the same deterministic
  mutant set so neither was lost. `summary-cr6.json`'s `note` has the detail.
- **`tools/fuzz.py`'s own `replace_argument`/`drop_argument`/`add_argument` operators split a
  call's arguments on every comma, not honouring commas inside a string literal.** This is a real,
  narrow gap in the fuzzer (not opencrbasic) worth fixing before the next run of these operators;
  it produced the `cr350` `false_reject` pair below, which is still a genuine, reproducible
  divergence but arose from an unintended argument split (`"private,public"` -> two "arguments").

## Historical first-pass counts per logger and class

These are preserved as evidence from the original fuzz pass, not as a current status report.

| Logger | mutants | exact | false_reject | false_accept | message_diff | mode_diff | crash | hang | saved |
|---|---|---|---|---|---|---|---|---|---|
| cr6 | 1500 | 925 | 1 | 433 | 135 | 5 | 1 | 1\* | 162 |
| cr1000x | 300 | 192 | 0 | 83 | 24 | 1 | 0 | 0 | 62 |
| cr300 | 300 | 127 | 0 | 85 | 32 | 54 | 2 | 0\*\* | 12 |
| cr350 | 300 | 134 | 2 | 70 | 35 | 58 | 0 | 1 | 12 |

\* cr6's own 1500-mutant RNG stream did not happen to draw the array-dimension mutation that
triggers the hang; the `cr6/hang/` reproducer is a hand-added copy of the case actually found while
fuzzing `cr350`, confirmed to reproduce for `cr6` too (shared `tables.py` code, `summary-cr6.json`).
\*\* Confirmed logger-independent by direct test (below); `cr300`'s own 300-mutant stream did not
draw it either.

`false_reject` (real compiler accepts, we reject) and `crash`/`hang` (opencrbasic never produces a
usable diagnostic) are the severe classes; they total 7 across all four loggers (3 false_reject, 3
crash, 1 hang -- cr6's own 1500-mutant stream did not separately draw the hang case, see the table
note), against 671 `false_accept` and 344 `message_diff`/`mode_diff` instances that mostly
reconfirm already-documented incompleteness (`docs/checker.md`). Findings below are grouped by root
cause, most severe first.

## 1. Historical hang: an oversized `Public`/`Dim` array dimension made opencrbasic never return

This was an observed compiler bug in the original run: the compiler did not fail or return, and
the case remained running past 60s in an isolated, unloaded single-file test. It has since been
fixed; the saved oversized-array cases now complete without hanging. The worker timeout remains to
contain unknown future non-termination rather than because this known case is still open.

Reproducer: `tests/corpus/fuzz/cr350/hang/dd9cc650482d.CR6` (identical copy at
`tests/corpus/fuzz/cr6/hang/dd9cc650482d.CR6`), minimized by hand to:

```crbasic
Public X(999999999999) As Long

BeginProg
  Scan (1,Sec,3,0)
  NextScan
EndProg
```

Traced with a `SIGALRM`-interrupted stack dump: `compiler.py:59 checker.check` ->
`checker/__init__.py:74 tables_hook.attach` -> `tables.py:940 build_tables` ->
`tables.py:1018 render_table_list` -> `tables.py:969 _explode_indices`, stuck inside
`for d, stride in zip(dims, strides)` (or the index-enumeration loop right after it): building the
`-l` table-list text enumerates every element of every declared `Public`/`Dim` array, with **no
upper bound checked on the declared dimension before that enumeration starts**. A
`999999999999`-element array is an attempt to iterate ~10^12 times; it never finishes in practice.

The real compiler rejects the same input immediately and cheaply:

```
line 1: Illegally dimension: Public X(999999999999) As Long
```

(verified for both `CR6Comp.exe` and `cr350comp.exe`, oracle output above). The historical analysis
identified two necessary protections: a value-range check on a `Public`/`Dim` array dimension
before downstream use, and a bound preventing `_explode_indices`/`render_table_list` from blindly
enumerating an impractically large declaration. The current implementation now completes the saved
cases; this paragraph records the original failure analysis rather than an open fix request.

## 2. Crash: a non-numeric `Scan` interval crashes `float()` in `tables.py`

Reproducers: `tests/corpus/fuzz/cr6/crash/ce0f811dd89e.CR6`,
`tests/corpus/fuzz/cr300/crash/237270eb6999.CR6` and `.../f3505c8f84ae.CR6` (three independent
mutants, two different seed files, same root cause). Minimal shape:

```crbasic
BeginProg
Scan("fuzz", sec, 1, 0 )
NextScan
```

`opencrbasic` raises `ValueError: could not convert string to float: 'fuzz'`. Traced to
`tables.py`'s `_scan_weights` (`docs/fuzz-findings.md` note: same file as the hang above, a
different function):

```python
if kw == "SCAN":
    secs = None
    if s.args:
        val = _fold(s.args[0], env, None)
        if val is not None:
            unit_name = ...
            secs = float(val) * _UNIT_SECONDS.get(unit_name, 1)
```

`_fold` constant-folds `Scan`'s first argument; when it is a string literal, `_fold` returns the
Python string itself (a legitimate fold, just not a number), and `float(val)` is called
unconditionally. This function runs for *every* `Scan` block regardless of whether any `DataTable`
is ever `CallTable`d inside it (it unconditionally pushes a weight onto its scan-nesting stack), so
the crash is reachable from the smallest possible program, not just ones with auto-sized tables.

The real compiler rejects this cleanly and specifically:

```
line 1: Scan Interval parameter must be a non-negative integer
```

(`cr6`'s oracle output for the 4-line seed shown in the reproducer's `.json`; `cr300`'s and
`cr1000x`'s equivalent message differs slightly, see the `false_accept` entries in section 6 that
hit the same missing check without also crashing, e.g. `Scan 0uSec invalid must be at least
1mSec.`). The general fix -- a `Scan`/`DataInterval` interval-value check before any downstream
code assumes a numeric, non-negative value -- is the single highest-leverage item in this report:
it would close the hang above, this crash, and a meaningful slice of the `false_accept` volume in
section 6, all at once.

## 3. False reject: `IncludeSection` with an extra argument is silently dropped

**The most important class per the task brief (real compiler accepts, we reject), and the more
clear-cut of the two found.** Reproducer:
`tests/corpus/fuzz/cr6/false_reject/28b79b4c2e87.CR6` (derived from `crbasic/Preflight_tester.CR6`;
needs its `crbasic/` siblings, marked with the `' fuzz-needs-siblings: crbasic` first line).

The `add_argument` mutation turned one of the file's real `IncludeSection` calls into:

```crbasic
IncludeSection("Rail", "CPU:BenchLib.crb",(1+2))
```

Real compiler: `28b79b4c2e87.CR6 -- Compiled in SequentialMode.` (accepts, extra argument
tolerated). opencrbasic: five cascading `Sub SetRail not defined.` errors, because `SetRail` is
defined in `BenchLib.crb`'s `"Rail"` section, which was never included. Root cause:
`source.py`'s `_INCLUDE_SECTION_RE`,

```python
_INCLUDE_SECTION_RE = re.compile(
    r'^\s*IncludeSection\s*\(\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)\s*$', re.IGNORECASE
)
```

requires the statement to end (`\)\s*$`) right after the second quoted string, so a call with a
third argument does not match at all and is not expanded as an include -- it falls through to being
parsed as an ordinary (and here, meaningless) statement instead, silently. The regex needs to
tolerate (and ignore, matching the real compiler's own apparent behaviour) trailing arguments after
the two it uses.

## 4. False reject: `ESSInitialize`'s argument is parsed as a full expression, not raw text

Reproducers: `tests/corpus/fuzz/cr350/false_reject/71f884967047.CR6` (from
`chm/essinitialize_1.CR6`) and `tests/corpus/fuzz/cr350/false_reject/5e3a202b1dad.CR6` (from
`chm/snmpvariable_1.CR6`) -- two instances, same shape. **Caveat first**: both were produced by
`drop_argument` naively splitting `ESSInitialize("private,public")` on its embedded comma (see
"Method notes" above), leaving `ESSInitialize(public")` -- a string missing its opening quote. That
said, the divergence itself is real and reproducible:

```
ours:   line 7: Invalid, or out of place expression: ESSInitialize(public").
theirs: essinitialize_1.CR6 -- Compiled OK.        (no warning at all)
```

`ESSInitialize`'s one parameter is type code 127 (`data/type_codes.json`: `"kind":
"variable_or_array"`, `"desc": "Optional expression/reference (GPS, ModBus family,
GetDataRecord)"`). The real compiler evidently does not lex/parse this position as a normal
quoted-string expression at all -- a stray `"` inside it is harmless -- while opencrbasic's parser
runs its general expression grammar there and rejects the malformed quote. This matches
`docs/type-codes.md`'s documented pattern of parameter kinds that capture raw source text rather
than a resolved expression (`checker/expressions.py`'s docstring already excludes several such
kinds from its undefined-symbol check for the same reason); `variable_or_array` for this specific
instruction looks like another one, worth confirming with a clean (non-fuzzer-mangled) oracle probe
before changing anything.

## 5. Message diff: missing `Invalid Variable declaration %s.` (very well evidenced, easy fix)

Eight independent mutants (different seeds, different mutation operators) hit the same shape:
a `Public`/`Dim` declaration's `As <Type>` clause has its `As` keyword corrupted (mutated case,
a stray inserted character, or a `rename_identifier` hit on the bare word `As`) --
`ours="asX"`, `"as_Fz"`, `"As_Mut"`, or similar. Examples (all in
`tests/corpus/fuzz/cr6/message_diff/`):

```
e40ebc09f2d0.CR6: ours   line 2: Invalid, or out of place expression: Public StringRight as String * 50, StringLeft asX String * 50.
                  theirs line 2: Invalid Variable declaration asX.
f500a368ae08.CR6: ours   line 18: Invalid, or out of place expression: Public PTemp, batt_volt, counte  r As Long.
                  theirs line 18: Invalid Variable declaration r.
ff612d56267a.CR6: ours   line 23: Invalid, or out of place expression: Public Count as_Fz Long.
                  theirs line 23: Invalid Variable declaration as_Fz.
05a9dd650a73.CR6: ours   line 2: Invalid, or out of place expression: Public pppSocket As Long,pppIP As_Mut String, ...
                  theirs line 2: Invalid Variable declaration As_Mut.
```

The real compiler always reports `Invalid Variable declaration <the bad token>.` and stops (one
diagnostic; see section 6). opencrbasic falls back to the generic "not a valid statement" message
(`structure.py`) with the whole echoed statement text, because it has no specific rule for "a
`Public`/`Dim` declaration's type-clause keyword doesn't match a known type name" -- a direct
sibling of the `Variable not named.`/`Units`/`Const`/`Alias` truncation rules `structure.py`
already implements for other malformed-declaration shapes (`docs/checker.md`). This is the single
highest-confidence, lowest-risk fix in this report: four corpus-distinct mutants, one exact real
message format, a natural place to add it (`structure.py`, next to the existing declaration
checks).

## 6. Message diff: "one diagnostic, then silence" (extends a known open question)

`docs/checker.md`'s "Open questions" already documents that the real compiler sometimes reports
*one* error and stops for the rest of the file, in a shape not fully characterised ("no
discriminator found in either round"). Fuzzing turned up several more concrete instances, all
sharing the pattern "our checker keeps going and reports N errors past the real compiler's one":

```
tests/corpus/fuzz/cr6/message_diff/71830a056193.CR6 (chm/instr_1.CR6, add_argument+rename_identifier)
  ours:   line 2: NextScan with no Scan.
          line 2: No BeginProg in Program.
  theirs: line 2: NextScan with no Scan.

tests/corpus/fuzz/cr6/message_diff/01c9fc1f33a8.CR6 (chm/filereadline_1.CR6)
  ours:   line 2: Invalid, or out of place expression: ...NextSca  n.
          line 2: No BeginProg in Program.
  theirs: line 2: Invalid, or out of place expression: ...NextSca  n.
          line 2: Expr illegally occurs before BeginProg.        <- even the SECOND message differs

tests/corpus/fuzz/cr6/message_diff/05741609c1fd.CR6 (chm/spiread_1.CR6, drop_argument+replace_argument)
  ours:   line 2: Invalid symbol in expression: UndeclaredFuzzName123.
          line 2: Invalid symbol in expression: Dest.
          line 3: Invalid symbol in expression: Z.  (+ more)
  theirs: line 2: Invalid symbol in expression: UndeclaredFuzzName123.
          line 2: Invalid symbol in expression: Dest.
          line 2: MoveBytes illegally occurs before BeginProg.
```

The `01c9fc1f33a8` case is a useful new data point for the open question: it shows the real
compiler's *second* message is context-dependent (`No BeginProg in Program.` in one shape,
`Expr illegally occurs before BeginProg.` in another) in a way that isn't just "stop after the
first error" -- something about *which* statement triggered the first error changes which follow-up
message it emits. Left for the checker workstream owning `docs/checker.md`'s open question, with
these three as fresh, minimized inputs.

## 7. Mode/warning diff: `warnings.py` implements one warning kind of at least five

Every `mode_diff` reproducer (both compilers accept, output text differs) found in this run is a
warning opencrbasic simply never emits -- `warnings.py` currently implements only
`Table %s, is declared but never called.` (`docs/checker.md`). Four more distinct, real warning
texts turned up:

```
tests/corpus/fuzz/cr300/mode_diff/3a84dfa4d9ab.CR6, tests/corpus/fuzz/cr300/crash/237270eb6999.CR6 (theirs)
  Warning: Invalid fnotch value, must be 50,60 400 or 4000

tests/corpus/fuzz/cr6/mode_diff/19a402de98a4.CR6 (repo/PowerProfile_tester.CR6)
  Warning: Delay never reached at run time.

tests/corpus/fuzz/cr6/mode_diff/54f0b2ff38cf.CR6 (chm/sdmspeed_1.CR6)
  Warning: When Main Sequence Scan 1 is running, background calibration will be disabled.

tests/corpus/fuzz/cr6/message_diff/caf5c15cd16b.CR6 (chm/angledegrees_1.CR6)
  Warning: Expr never reached at run time.   (appears even alongside a real error, on a Compile Failed run)
```

`mode_diff` accounts for the largest single class on `cr300` (54 of 300 mutants) and `cr350` (58 of
300) specifically because those two loggers' seeds lean on `fnotch`-checked instructions more; the
same missing-warnings gap is logger-independent (shared `warnings.py`). No single fix closes all
four -- each is a distinct rule (a value-range check, a static reachability analysis, a sequencing
note, a second reachability check) -- but they are a natural, bounded next-checklist item for
whoever owns `warnings.py`.

## 8. False accept: mostly already-tracked incompleteness, volume-confirmed

671 `false_accept` instances (`433 + 83 + 85 + 70`) are, on inspection, not new discoveries so
much as this run volume-confirming `docs/checker.md`'s own stated scope ("instruction-specific
argument checks" and "value never range-checked" are explicitly future work there). Grouped by
theme, most actionable first:

- **`Scan`/`DataInterval` interval value never checked at all** -- the same root cause as sections 1
  and 2, just without also crashing: `Scan Interval parameter must be a non-negative integer`
  (`cr6`), `Scan 0uSec invalid must be at least 1mSec.` (`cr1000x`), `Scan Count parameter must be a
  non-negative integer` (`cr6`, a `doloop_1.CR6`-derived mutant). Fixing this closes the hang, the
  crash, and this whole family in one pass.
- **Argument-count upper bound not checked** -- `instr_rules.py` checks a required parameter is
  *present* but nothing checks there aren't *too many*: `Too many parameters:  <name>.` appears
  repeatedly after `add_argument` mutants (`cr6/false_accept/5fc149f5044d.CR6`,
  `cr1000x/false_accept/16418626ba39.CR6`).
- **No duplicate-declaration / duplicate-structural-block check** -- `duplicate_line` mutants that
  double a `Public`/`Dim` or a `BeginProg` are silently accepted; real messages:
  `<name> already declared as a variable.`, `Multiple BeginProgs.`
  (`cr350/false_accept/53a64b574360.CR6`, `cr1000x/false_accept/02ec158b4a77.CR6`).
- **Assorted single-instruction value checks**, each low-value alone (matches
  `docs/checker.md`'s "Known remaining gaps" framing): `FileManage invalid drive: %s.`,
  `No Matching, or out of place ) in expression: %s` after a mismatched paren from `add_argument`,
  a handful more visible in `tests/corpus/fuzz/*/false_accept/*.json`.
- The remainder (a large fraction of the total) is `expressions.py`'s documented narrow scope: a
  name used inside a nested expression, an array index, or as a `SplitStr`/`FileReadLine`-style
  argument that the mutator swapped for an undeclared name, none of which the current
  "direct-instruction-argument-only" undefined-symbol check reaches (`checker/expressions.py`'s own
  docstring already lists this as not implemented).

None of these are false rejects, so per this project's own stated priority order
(`docs/checker.md`: never reject an accepted program, first; exact match, second) they are lower
urgency than sections 1-6, but the `Scan`/`DataInterval` value check is worth pulling forward since
it is also the hang and crash fix.

## Round 5: fresh seeds 201-208 (2026-10-08)

New seeds, not used before (earlier rounds used 0, 1, 42-54, 100). Run with `--workers 2` because
Wine was shared. Counts are the tool's classify-phase tallies for each run; `oracle_error` mutants
and seeds are real-compiler page faults (exit 5) or 60 s timeouts, which are no verdict and are
excluded from every other class (`tests/corpus/fuzz/summary-<logger>.json` lists them; the tool
overwrites that file per logger, so only the second run of each logger survives there).

First pass, run before any fix of this round (reproducers saved: 92):

| Seed | Logger | Mutants | exact | false_reject | false_accept | message_diff | mode_diff |
|---|---|---|---|---|---|---|---|
| 201 | cr6 | 500 | 474 | 2 | 8 | 13 | 1 |
| 202 | cr1000x | 400 | 384 | 0 | 2 | 13 | 0 |
| 203 | cr300 | 400 | 374 | 1 | 4 | 21 | 0 |
| 204 | cr350 | 400 | 372 | 0 | 8 | 19 | 0 |

Second pass, run while the fixes listed below were being added to the working tree (so the three
false-reject shapes could not recur; reproducers saved: 125; 216 new reproducers in total, one hash recurring):

| Seed | Logger | Mutants | exact | false_reject | false_accept | message_diff | mode_diff |
|---|---|---|---|---|---|---|---|
| 205 | cr6 | 1000 | 953 | 0 | 12 | 33 | 0 |
| 206 | cr1000x | 600 | 571 | 0 | 6 | 19 | 3 |
| 207 | cr300 | 600 | 562 | 0 | 3 | 35 | 0 |
| 208 | cr350 | 600 | 576 | 0 | 2 | 21 | 0 |

### False rejects (3, all minimised, all closed in the fix commits of this round)

- `cr300/false_reject/08ef74be9238`: `End  Table` (also `End Table`) closes a table on all four
  loggers; opencrbasic rejected `DataTable ... EndTable not matched.`.
- `cr6/false_reject/ab0c3e6336e7`: `CS7500(...,999999999999,6)`: opencrbasic reported
  `Invalid SDM Address: 255`, the real compiler accepts (a float32 / Long-range rule).
- `cr6/false_reject/2a20e3cd2f86`: `Scan interval too short for measurement by 1 Sec.` reported
  although the program is in SequentialMode, where the real compiler never reports it.

### Other families seen (not all closed)

False accepts with a clear single rule: `SubScan without Next SubScan.`, `Maximum() or Minimum()
must precede SampleMaxMin().`, `Optional Count parameter must be type long.` for `TotalRun` and
`StdDevRun`. A mode_diff: `Call never reached at run time.` (we said `Expr`). Not attempted this
round (each needs a per-instruction model): `Temperature unit unsupported` and `Timeout: -1, out of
range` / `Input channel ... out of range` on `CDM_*` instructions, `SampleFieldCal with no FieldCal in
program.`, `Duplicate Alias` with a negative index, `Destination variable cannot be indexed`
(CR350), `Illegal Matrix` (CR350), storage-space errors (CR350), plus the many help-corpus
message-order families owned by other workers.

## Round 9: fresh seeds 301-308 (2026-10-10)

New seeds (previous: 0, 1, 42-54, 100, 201-208), 250 mutants each, `--workers 2`. Counts are the
tool's classify-phase tallies; `oracle_error` mutants (real-compiler page fault or timeout, no
verdict) are excluded from every class. 125 reproducers saved (hash-named, not edited afterwards).

| Seed | Logger | exact | false_reject | false_accept | message_diff | mode_diff | oracle_error |
|---|---|---|---|---|---|---|---|
| 301 | cr6 | 234 | 1 | 4 | 11 | 0 | 2 |
| 302 | cr6 | 240 | 0 | 0 | 10 | 0 | 1 |
| 303 | cr1000x | 231 | 0 | 6 | 12 | 0 | 3 |
| 304 | cr1000x | 246 | 0 | 0 | 3 | 0 | 2 |
| 305 | cr300 | 238 | 0 | 3 | 8 | 1 | 3 |
| 306 | cr300 | 237 | 0 | 1 | 12 | 0 | 2 |
| 307 | cr350 | 240 | 0 | 0 | 10 | 0 | 2 |
| 308 | cr350 | 239 | 0 | 2 | 9 | 0 | 4 |

No crash, hang, or implementation_exception in any run. Fix status is recorded in the sections
that follow as each family is closed.

### R9 false reject closed: `CHR()` of an out-of-byte-range constant

`cr6/false_reject/b48d6843b286` (`Const LF = CHR(-1)`): all four real compilers accept `CHR()` in a
`Const` expression for negative, `>255`, `5000000000`, `1e300` and `2147483648` arguments
(`tests/fixtures/r9_chr_arg.json`, audited 0 differing). `consteval._fn_chr` raised on a negative
or non-Unicode argument, which surfaced as `Invalid, or out of place expression`. It now takes the
low byte (the value of the resulting string is not otherwise observable). Not covered: `CHR(-1/0)`
reports `Divide by zero in expression: CHR(-1/0).` on the real compiler, which we do not emit.

### R9 family closed: `MenuPick` value type per item (CR6, CR1000X)

Reproducers `cr6/false_accept/5cbda83b79a2`, `cr6/false_accept/801129666c06`,
`cr1000x/false_accept/6fc36667c86c`. 183 probed `MenuItem`/`MenuPick` pairs per logger, all exact
now (`tests/fixtures/r9_menupick_values.json`, audited 0 differing). CR300/CR350 have no menus.

- Item kind: Long variable or Long-array element, or an integer ConstTable constant -> `int`;
  Float variable (untyped included) or non-integral Float constant -> `float`; String variable or
  String constant -> `string`; other types and integral float constants not probed, unchanged.
- Each `MenuPick` value after a `MenuItem` is checked (all values for a variable item, values after
  the leading constant for a constant item; also on repeated picks of a variable item):
  `int` -> `<text> must be declared as a integer Const.` (sic) for a float literal (`5.5`, `5.0`,
  `1e2`), string literal, a `Const` that is a string or non-integral, `PI`, `NAN`, an undefined or
  variable name, an operator expression (`1+2`, `(1+2)`, `-A`, `-&H10`) or a trailing-blank token.
  `float` -> the same with `a Const.` for string literal, undefined or variable name, `PI`-free
  operator expressions and trailing blanks (numeric literals and any declared `Const`, even a
  string, pass). `string` -> nothing is reported, including variable names (we used to reject
  `MenuPick(lv)` on a String item: a latent false reject, now gone).
- A ConstTable item whose first value is not a constant checks every value as `float`.
- Parenthesised single tokens `(5)`/`(A)` pass; echo strips one `(`..`)` pair. Odd shapes
  (`a b`, `5x`, `&HZ`, nested parens) are left silent; the real compiler reports them with
  the same message but we give a different parser message.

### R9 family closed: `Const x As <type>` with an unsupported type (all four loggers)

Reproducer `cr1000x/false_accept/03ecff3743a0` (`Const myString As String2 = "123"`);
`tests/fixtures/r9_const_type.json` (204 cases, audited 0 differing).

- Accepted `As` types (case-insensitive): Long, Float, Double, String, Boolean. Everything else,
  including `Integer`, `Short`, `Single`, `Byte`, `Text`, `ULONG`, `IEEE4`, `String2`, and keywords
  (`If`, `Const`, `Public`), gives `<type as written> unknown Const type` at the Const's line.
- Reported after the duplicate-declaration messages of the same line (`a is already declared as a
  CONST.` then `Foo unknown Const type`), and as the only message for that Const: an undefined
  name or other problem in the value is not reported. Only the first item of a `Const a As Foo = 1,
  b As Bar = 2` line is looked at. In a `ConstTable` the line is the Const's own.
- CR300/CR350: the message is a declaration trigger of the existing cascade (later `Public z` is
  not defined where used; a later `Public a` gets no duplicate message).
- Not done: a bad type together with a malformed value (`Const a As Foo = `, `= 1 +`) still gives
  the value message; `Const y As Foo = 1` for an existing variable `y` misses the later `Invalid
  assignment in expression` (same pre-existing gap as plain `Const y = 1`).

### R9 not closed (evidence in the saved reproducers)

- Excess arguments (`Too many parameters:  <arg>.`) on instructions outside `check_too_many`'s
  eligible parameter kinds: `cr1000x/false_accept/d84de83e43ed` (`FFT`), `cr300/false_accept/
  dc989f733972` (`SampleMaxMin`), `f4771af98130`. Needs per-instruction evidence.
- A String or negative `Scan` interval together with `IfTime`/`TimeIntoInterval`: the real
  compilers report `Scan interval must be integrally divisible into interval.` (e.g.
  `Scan("fuzz",Sec,3,0)` with `IfTime(0,24,Hr)`, but not with `IfTime(0,5,Min)`; `Scan(-1,Sec)`
  fails only for a sub-second `IfTime`), CR6/CR1000X replacing the interval message. The effective
  scan step is not derivable from these probes (`cr300/false_accept/1376c224f200`).
- `Measurement Delay Exceeds 60 min.` (`CDM_Delay`, `cr6/false_accept/f2c51b769e04`), `Variable
  rt2 out of bounds.` / paren-dimension arrays (`cr6/false_accept/2435be9885e1`,
  `cr350/false_accept/73c3fc153711`), `FieldCal Mult` (`cr1000x/false_accept/11dd65c2d832`),
  `Parameter must be a constant integer` for `SerialOpen` (`cr300/false_accept/7be9f5d53b3a`),
  `Expression expects ,` for an `FTPClient` call (`cr350/false_accept/1b3ba52e3818`), `If without
  EndIf inside of Sub` (`cr1000x/false_accept/9ec2e021b721`).
- The 52 message_diff reproducers are mostly extra or missing follow-on lines in undefined-name
  cascades and structure messages (`No BeginProg` vs a specific first error), not triaged.
