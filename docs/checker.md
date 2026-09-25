# Semantic checker: rules and oracle evidence

`checker/` (`__init__.py` + `structure.py`, `expressions.py`, `instructions.py`, `warnings.py`,
`symbols.py`) implements phase 3 (`PLAN.md`). This document records what each rule checks, the
oracle evidence behind it, and open questions, in the checker's own words (never pasted
decompiled code, `docs/legal.md`). All experiments below were run 2026-09-25 via
`python3 tools/oracle.py <file> --siblings <dir>` (or `--no-cache` scratch probes) against the
real `CR6Comp.exe` under Wine, and cross-checked against `tests/corpus/chm/`.

## Coverage and priorities

Metric (`tools/difftest.py --logger cr6`), before this work / after:

| Group | Before | After |
|---|---|---|
| `crbasic/` (16 programs) | 14 exact, 2 verdict, 0 mismatch | **16 exact**, 0 verdict, 0 mismatch |
| `examples/` (4 programs) | 0 exact, 0 verdict, 4 mismatch | 0 exact, 2 verdict, 2 mismatch |
| `tests/corpus/chm/` (507 programs) | 198 exact, 0 verdict, 309 mismatch | 268 exact, 229 verdict, 10 mismatch |

"mismatch" (one side accepts, the other rejects) is the priority-1 metric: a placeholder checker
that accepted everything meant most of `chm`'s (deliberately, doc-derived) invalid programs were
false accepts (309 of them). That count is now 12 across all three groups (0 + 2 + 10) -- "Known
remaining gaps" below lists each one; none is a program this checker *rejects* that the real
compiler *accepts* (this session's actual priority-1 concern), only the reverse (still-accepted
programs the real compiler rejects for a reason not yet implemented). None of the 16 `crbasic/`
programs or the CHM corpus's previously-198-exact set regressed.

## `structure.py`

### `No BeginProg in Program.`

Fires once per program when no `BeginProg` statement exists anywhere, reported at the *last*
statement's line (or line 1 for an empty program) -- **not** line 0/unprefixed as
`diagnostics.py`'s docstring speculated before this session. Oracle:

```
Public X As Float                       (1 line, no BeginProg)  -> line 1: No BeginProg in Program.
Public X/Y/Z As Float                   (3 lines, no BeginProg) -> line 3: No BeginProg in Program.
```

### `Scan without NextScan.` / `NextScan with no Scan.`

An unclosed `Scan` at end of program (`EndProg` or physical EOF, whichever comes first -- `EndProg`
itself is *not* required: a file ending right after a matched `NextScan` compiles) is reported at
the last statement's line:

```
BeginProg / Scan(...) / X=1 / EndProg          (no NextScan) -> line 5 (EndProg's line): Scan without NextScan.
BeginProg / Scan / X=1 / NextScan               (no EndProg) -> Compiled OK (EndProg not required)
BeginProg / NextScan / Scan(...) / NextScan / EndProg        -> line 3 (the stray NextScan's own line): NextScan with no Scan.
```

Both one-word (`NextScan`) and two-word (`Next Scan`, parsed generically as
`Instruction(keyword="NEXT", args=[Name("SCAN")])` per `docs/frontend.md`) spellings are
recognised.

### `IF or SELECT without EndIf or EndSelect.`

One generic message (doesn't distinguish `If` from `Select`, or say how many are open), reported
at the last statement's line, for an unclosed block-form `If` or `Select...Case` at end of
program:

```
Scan/If X>1 Then/X=2/NextScan/EndProg                     -> line 7 (EndProg's line): IF or SELECT without EndIf or EndSelect.
Scan/Select Case X/Case 1/X=2/NextScan/EndProg            -> line 8 (EndProg's line): same message
```

Inline-form `If ... Then stmt` (no block body) does not open a block. Both one-word (`EndSelect`)
and two-word (`End Select`/`End If`) terminator spellings are recognised.

### Placement before `BeginProg`: `Invalid, or out of place expression: %s.` / `Sub %s not defined.` / `%s illegally occurs before BeginProg.`

A bare statement our parser turns into `Call` (a `Name`/`Name(args)` with no `Call` keyword) or
`Unparsed` (it could not parse at all) is checked against the program's declared `Sub`/`Function`
names and against `data/functions.json` (built-in *functions*, not the 410-entry instruction
table):

- **Resolves to a declared `Sub`/`Function`, called without the `Call` keyword** (`MySub(5)`,
  parens or not): valid *inside* the program body --
  `MySub(5)` inside `Scan..NextScan` -> `Compiled in PipelineMode.` -- but `%s illegally occurs
  before BeginProg.` (`%s` = literally `Call`) if it precedes `BeginProg`:
  `MySub(5)` before `BeginProg` -> `line 7: Call illegally occurs before BeginProg.`
- **Resolves to a built-in function** (`Timer`, `SerialOpen`, `SerialOut`, `SerialFlush`,
  `SerialClose`, ...): always valid, called for its side effect with the return value discarded.
  Found as a **regression** during this session's development: these are real, working
  instructions throughout this repository's own `crbasic/Main.CR6` (`SerialOpen(ComUSB,
  115200,0,0,1000)` etc.), not in `data/instructions.json` at all -- they are
  `data/functions.json` entries. An earlier version of this rule checked declared `Sub`s only and
  rejected all of `crbasic/`.
- **Resolves to nothing** (`NOTE`, `SWVX(Vx1,1,1)` -- `SWVX` is a CR1000X-only instruction absent
  from CR6's `data/instructions.json`, `TableFieldNames(...)` -- neither an instruction nor a
  function): `Invalid, or out of place expression: %s.`, `%s` = the statement's own (already
  comment-stripped, but leading/trailing-whitespace-*trimmed*) source text
  (`syntax.Stmt.text`/`Unparsed.text`). Oracle: `NOTE` alone -> `line 1: Invalid, or out of place
  expression: NOTE.`; matches `docs/frontend.md`'s existing finding for a bare undeclared
  identifier. If this also precedes `BeginProg`, oracle shows an *additional* line, `%s illegally
  occurs before BeginProg.` with `%s` = literally `Expr` (the compiler's own generic
  bare-expression-statement instruction, confirmed present in `data/instructions.json` at index
  5): `NOTE` as the file's first line -> `line 1: Invalid, or out of place expression: NOTE.` /
  `line 1: Expr illegally occurs before BeginProg.`; and real compilation **appears to stop
  entirely** right there (no further diagnostics for the rest of the file, confirmed on
  `alias_1.CR6`/`Tablefieldnames_2.CR6`/`const1_1.CR6` -- 44 of 45 corpus cases showing this
  follow-up line show *no other* diagnostic in the whole file).

  **This checker deliberately does not reproduce the "before BeginProg" follow-up line or the
  stop-entirely behaviour.** A synthetic probe found the follow-up does *not* always fire for a
  statement our parser also classifies as `Unparsed`/unresolved before `BeginProg`: `DoublePrecArith_1.CR6`
  line 2 ("`To force double-precision arithmetic, declare all involved...`", starting with the
  word-operator `To`) gets only the plain "Invalid, or out of place expression" line and
  compilation *continues* to a second, unrelated error on line 4 -- yet a synthetic single-line
  file containing only `This is prose before BeginProg` (no leading keyword at all) *does* get
  the follow-up + stop. The two corpus cases split roughly 45/43 in this session's sample (see
  "Known remaining gaps"), and no discriminator was found cheaply (word-operator-led sentences
  seem to behave differently from a generic first-token failure, plausibly because a failed
  keyword-parameter-parse recovery path doesn't commit an "Expr" pass-1 record the way an
  unmatched-keyword statement does, but this wasn't traced in the decompilation). Given AGENTS.md's
  priority order (never falsely reject > exact match), adding this line and the stop-entirely
  behaviour risks *suppressing* real further diagnostics on the ~43/88 cases where it's wrong,
  which only costs "exact" vs "verdict" (both already reject) -- so the safer, simpler rule (no
  follow-up, no abort, just keep checking every statement) was kept instead. Not chased further
  this session; a real fix needs more oracle probes isolating what specifically differs.

- **Explicit `Call name` (with the `Call` keyword), name resolves to nothing declared**:
  `Sub %s not defined.` (`%s` = the name as written), independent of position. Oracle
  (`docs/type-codes.md` section 3, re-confirmed): `Call NoSuchSub` -> `line N: Sub NoSuchSub not
  defined.`

### Known parser gaps stay silent

Two `Unparsed` reasons are oracle-confirmed accepted by the real compiler despite our parser
failing on them (`docs/frontend.md` "Known residual gaps": `samplemaxmin_1.CR6`'s
`WS_ms=WS_ms+=1`, `snmpvariable_1.CR6`'s dotted-OID literal argument). `structure.py` matches these
two `reason` strings by prefix and stays silent, rather than emitting a false "Invalid, or out of
place expression" for programs the real compiler accepts.

## `expressions.py`: undefined-symbol checking (deliberately narrow)

Only a *direct* `Instruction` argument that parsed as a bare `Name` (never one nested inside a
larger expression, an array index, `Table.Field`, or a unary/binary operator) is checked, and only
at argument *positions* whose declared type code (`data/instructions.json` `params`, decoded
against `data/type_codes.json`'s `kind`) actually routes through the real expression/named-constant
resolver (`_RESOLVED_KINDS` in `expressions.py`: `const_expr`, `expr`, `const_expr_only`,
`bool_expr`, `variable_or_array`, `expr_or_name`, `expr_typed`, `expr_special`, `expr_pair`,
`expr_list`, `datatype_kw`). An unresolved name there is `Invalid symbol in expression: %s.`, `%s`
= the name as written, at the statement's line.

**Why type-code-driven rather than per-instruction:** `docs/type-codes.md` documents that codes
like `6` (identifier) and `45` (type_name) capture their argument as raw text, never resolved
against any symbol table at this layer -- a `DataTable`'s or `StructureType`'s own name, or (found
as a **regression** during development) a `ConstTable`'s own name (`ConstTable(GlobalConfigTable)`
in every `crbasic/*.CR6` entry program, code `137` = optional code `37`, `kind` `expr_marker`, not
checked). Restricting to an explicit include-list of *known-resolved* kinds (rather than excluding
the handful of kinds known *not* to resolve) means a `kind` this session didn't reason about
carefully enough defaults to "don't check" -- under-reporting, never a false reject.

**Known set** a name must be in to pass: every `Public`/`Dim`/`Const`/`ConstTable` variable name
declared anywhere in the program (`symbols.declared_names`, deliberately whole-program and
unscoped, not order-checked -- see that function's docstring), `Sub`/`Function` names and their
parameters, `DataTable` and `StructureType`/`ObjectType` names, `Alias` targets, `For`-loop
variables, `Label`s, `data/<logger>/symbols.json` (predefined constants: terminals, ranges, data
types, time units, `True`/`False`, ...), `data/<logger>/functions.json`, and
`data/<logger>/instructions.json` (defensive: an instruction keyword should never itself appear as
a checked argument, but this costs nothing to also allow).

**Evidence for the rule's shape:** every "Invalid symbol in expression" corpus mismatch this
session found is a CR1000X-only terminal name (`Vx1`, `P1`, `C5`/`C7`, `SWVX`'s own channel
argument) used in a CR6 program, absent from `data/cr6/symbols.json` entirely (not filtered by
some "valid on this logger" flag in the symbol record -- `docs/binary.md`'s `extra` field is `0`
for both `U1`/`C1` (valid) and `mv200c` (a name the task's own notes flagged as CR6-rejected but
which this session did not find a live rejection case for in the corpus to confirm against; left
open below), so no logger-filtering rule was found or implemented this session):

```
TCDiff(TCTemp(),6,mv200C,1,TypeT,PTemp,True,0,15000,1.0,0)   -- literal "1" instead of "U1" for DiffChan
  oracle: line 13: Invalid channel 1, must use U1..U12.      (instructions.py's separate range check, below)
TCDiff(TCTemp(),6,mv200C,Vx1,TypeT,PTemp,True,0,15000,1.0,0) -- CR1000X-only terminal name
  oracle: line 13: Invalid symbol in expression: Vx1.
```

**Not implemented** (left for future, more thoroughly oracle-tested work): a name inside a
`Public`/`Dim`'s array initialiser (`Public Bridge3(2,3) As Long = {ComC5,0,0,0,0,0}`, corpus:
`portbridge_1.CR6`); a name nested inside a larger expression, index, or `Table.Field`; scoped
(rather than whole-program) resolution.

## `instructions.py`: the analog-channel `U1..U12` range check

`Invalid channel %d, must use U1..U12.` -- decoded from `FUN_005160a0`'s call sites in the
decompilation (`docs/messages.tsv`, function `FUN_005160a0`): a shared validator called from
several instructions' own compile-time checks, given the argument's already-parsed numeric value
and (from context) which `param_1[K]` (1-based argument index) to check. Static address-matching
`data/instructions.json`'s `compile_fn` field against the decompiled call sites' enclosing function
addresses did not resolve cleanly (the `compile_fn` values are a thunk table whose addresses do not
correspond 1:1 to Ghidra's labelled functions at that address range -- not chased further), so the
argument positions below were derived from the CHM parameter list plus corpus cross-checking rather
than a verified binary address mapping:

| Instruction | 1-based position(s) | Corpus evidence |
|---|---|---|
| `TCDiff`, `TCSe` | 4 (`DiffChan`/`SEChan`) | 44 corpus mismatches, all this shape, all fixed |
| `VoltSe`, `VoltDiff` | 4 (`SEChan`/`DiffChan`) | 7 + 2 corpus mismatches, all fixed |
| `ACPower` | 4, 7 (`ChanV`, `ChanI`) | 1 corpus case, only partially matches (see below) |

Value range is `[201, 212]` (`U1`..`U12`, `data/symbols.json`), unlike a "port" parameter
elsewhere which also accepts `C1`..`C4` (a *different* message, `Invalid port, must be C1..C4 or
U1..U12.`, not implemented). The argument must already be a resolved constant (an unresolved
non-constant is left to the generic type-code-1 "Variable illegal in Parameter" check, not
reimplemented here) for this check to run at all; a genuinely non-constant argument at this
position is not this rule's concern.

**`ACPower` is known incomplete**: the corpus's one example (`acpower_1.CR6`) gets *five* separate
"Invalid channel" lines from the real compiler for one `ACPower` call, only two of which this
rule's `(4, 7)` predicts; the other three (`1`, `-1`, `-2`) were not traced to specific argument
positions or a value-transform this session could explain, so `ACPower` stays a "verdict" (both
reject), not "exact", match. Not chased further (a single corpus instance, low value relative to
the investigation cost).

Not implemented: `BrHalf`/`BrHalf3W`/`BrHalf4W`/`BrFull`/`BrFull6W`'s own `SEChan`/`ExChan`
positions (found in the decompilation to share a *different* message, `ExChan must be U1-U12.`,
`FUN_005147a0`/`FUN_005177a0`) -- no corpus mismatch currently needs it, so it was left
unimplemented rather than guessed.

## `warnings.py`: `Table %s, is declared but never called.`

Oracle-confirmed rule already recorded independently in `docs/tdf-rules.md` section 13
(`fixeduncalled` experiment, found while implementing `tables.py`): fires for every user
`DataTable` with no `CallTable`/`CallTable(...)` referencing it anywhere in the *post-`#If`-
resolution* statement list, regardless of `DataInterval`/fixed size. `warnings.py` reuses that
finding rather than re-deriving it, and is deliberately independent of `tables.py`/`result.tables`
(which also carries the logger's own system tables -- `Status`, `DataTableInfo`,
`GlobalConfigTable`, the `Public` table -- none of which are ever `CallTable`d and must never get
this warning; an earlier version of this rule iterated `result.tables` directly and was a
**regression**, flagging all four on every `crbasic/` program).

"Called" is deliberately broader than literal `CallTable`: `AcceptDataRecords`' own `TableName`
argument also counts (oracle-confirmed regression, `acceptdatarecords_1.CR6`:
`AcceptDataRecords(3000,32769,CR1000XTable)` with no `CallTable` anywhere still compiles with no
warning). Rather than enumerate every instruction with a table-name argument, `warnings.py` treats
*any* bare-`Name` instruction argument matching a declared table (other than the `DataTable`
statement's own declaration) as a use.

`crbasic/Main.CR6`'s own `Shunt_Data` table is the repository's real example: its only `CallTable
Shunt_Data` is inside `#If ShuntSequenceEnabled Then`, false by default, so the parser's own
`#If` resolution (which only keeps the active branch's statements, `docs/frontend.md`) already
removes it from `program.statements` before this check runs.

## Known remaining gaps (`mismatch`, not "false accept" vs. "exact wording")

All of these are real, oracle-confirmed compile failures this checker currently still *accepts*
(the metric's true false-accept count) or -- for the two `examples/SBD_example_hirise*.CR6`
files -- a check not yet implemented (`Invalid port 6, use C1..C4 or U1..U12.`, likely the same
`_check_channel_arg`-shaped rule as `instructions.py`'s U1..U12 check but for a "port" parameter
accepting `C1..C4` too, not investigated this session). The `chm` ones are single- or
few-instance, instruction-specific checks not yet implemented: `avgrun_4.CR6` (`Optional number of
distinct instances must be a constant of type long.`), `cdmbridgefilt_1.CR6` (`Insufficient Scan
buffers to read EPI data...`), `ch400status_2.CR6` (duplicate `DataTable` field names),
`cpispeed2_1.CR6` (CPI timing budget check), `gps_2.CR6` (`GPS pps synchronization is only
available on COMU1.`), `hex_2.CR6` (`Scan Count parameter must be a non-negative integer`),
`mqttpublishmeta_1.CR6` (a `MQttPublishMeta`-specific required-argument check), `tcpopen_5.CR6`
(`DisableTcpDelayedAck is not a Settings Field Name.` -- a `Settings`-style keyword-list check).
`pointervariable_2.CR6` is not a checker gap at all: the real `CR6Comp.exe` itself crashes (a wow64
page fault) under Wine on this file, so no oracle text exists to match.

## Open questions

- The "before BeginProg" follow-up line's exact trigger (see `structure.py` section above) --
  roughly 45/88 corpus cases in this session's sample get it, 43 don't, and the discriminator was
  not found.
- Whether any predefined symbol present in `data/cr6/symbols.json` is nonetheless rejected on CR6
  specifically (the task notes floated `mV200C is not defined.` as an example); no live corpus or
  oracle case reproducing this was found this session, so `data/symbols.json`'s `extra` field's
  role, if any, is still undocumented.
- `ACPower`'s full channel-argument shape (see `instructions.py` section above).
- General expression-tree name resolution (inside `Public`/`Dim` initialisers, nested expressions,
  scoped rather than whole-program lookup) -- deliberately out of scope this session; see
  `expressions.py`'s "Not implemented" list.
