# Semantic checker: rules and oracle evidence

`checker/` (`__init__.py` + `structure.py`, `expressions.py`, `instr_rules.py`, `instructions.py`,
`warnings.py`, `symbols.py`) implements phase 3 (`PLAN.md`). This document records what each rule
checks, the oracle evidence behind it, and open questions, in the checker's own words (never
pasted decompiled code, `docs/legal.md`). All experiments below were run 2026-09-25 via
`python3 tools/oracle.py <file> --siblings <dir>` (or `--no-cache` scratch probes) against the
real `CR6Comp.exe` under Wine, and cross-checked against `tests/corpus/chm/`.

## Coverage and priorities

Metric (`tools/difftest.py --logger cr6`), round 1 (previous session) / round 2 (this session,
2026-09-25 continued):

| Group | Round 1 start | Round 1 end | Round 2 end |
|---|---|---|---|
| `crbasic/` (16 programs) | 14 exact, 2 verdict, 0 mismatch | **16 exact**, 0 verdict, 0 mismatch | 16 exact, 0 verdict, 0 mismatch |
| `examples/` (4 programs) | 0 exact, 0 verdict, 4 mismatch | 0 exact, 2 verdict, 2 mismatch | 0 exact, 2 verdict, 2 mismatch |
| `tests/corpus/chm/` (507 programs) | 198 exact, 0 verdict, 309 mismatch | 268 exact, 229 verdict, 10 mismatch | **310 exact**, 188 verdict, 9 mismatch |

Round 2's own headline: **268 -> 310 exact** (+42) on `chm`, verdict 229 -> 188, mismatch 10 -> 9
(one instance, `mqttpublishmeta_1.CR6`, moved from false-accept to exact as a side effect of the
new required-parameter check below -- not chased on purpose, just a consequence of it turning out
to be shaped that way). Re-verified after every change this round: every `their_exit == 0` program
in the corpus (accepted by the real compiler) is `exact` for us, zero exceptions -- the working
invariant this round optimised against ("never reject an accepted program" first, "exact" second).

"mismatch" (one side accepts, the other rejects) is the priority-1 metric: a placeholder checker
that accepted everything meant most of `chm`'s (deliberately, doc-derived) invalid programs were
false accepts (309 of them). That count is now 11 across all three groups (0 + 2 + 9) -- "Known
remaining gaps" below lists each one; none is a program this checker *rejects* that the real
compiler *accepts* (this session's actual priority-1 concern), only the reverse (still-accepted
programs the real compiler rejects for a reason not yet implemented). None of the 16 `crbasic/`
programs or the CHM corpus's previously-198-exact set regressed.

## Two-pass compilation (round 2 finding)

`CR6Comp.exe` compiles in two passes, and which pass a check belongs to determines whether it
fires at all once the file has any other problem:

- **Pass 1** parses and resolves every statement in the file (`structure.py`, `expressions.py`,
  `instr_rules.py` below): every such error is reported regardless of what already went wrong
  earlier *or later* in the file -- confirmed with the oracle (`tools/oracle.py`/a scratch probe
  under Wine) by putting an unrelated pass-1 error (`ExciteV(Vx1,2500,0)`, an undefined symbol)
  both *before* and, separately, *after* an otherwise-invalid `VoltDiff` channel argument in the
  same minimal program: the channel check fired in neither placement, only when the file had no
  other error anywhere.
- **Pass 2** is deeper semantic/value validation -- `instructions.py`'s channel-range check is one
  example, `tables_hook`/`warnings.check`'s table-linking is another (already gated this way before
  this session, evidently by someone who had independently run into the same thing) -- and runs
  only when pass 1 found nothing wrong *anywhere in the file*, not just earlier than the statement
  in question.

`checker/__init__.py` now gates `instructions.check` behind `if not diags.errors:` after running
all of pass 1, mirroring the pre-existing `tables_hook`/`warnings.check` gate. This is a
**wiring-only change**: `checker/instructions.py`'s own channel-check logic (owned separately,
`PLAN.md`) is untouched. It also quietly fixed a latent bug in that file worth flagging for whoever
next edits it: `VoltDiff`/`VoltSe`'s `DiffChan`/`SEChan` argument (`CHANNEL_ARGS` position 4) is
**not actually a `U1..U12` terminal name** -- every corpus usage (`avgspa_1.CR6`, `fieldcal_2.CR6`,
`voltdiff_1.CR6`, the CHM reference example itself, ...) and the CHM's own parameter description
give it as a small plain integer (a differential-channel *index*, 1..6 on the CR6, not a terminal
name), oracle-confirmed accepted with a literal `1` once no other pass-1 error exists in the file
(`VoltDiff(X,1,mv5000C,1,0,0,15000,1,0)` alone compiles clean). The two-pass gate happens to mask
this everywhere it currently matters in the corpus (a file with a real `VoltDiff`/`VoltSe` channel
"mismatch" always also had a pass-1 error, so the check never actually ran against the real
compiler's own pass 2 either), so nothing regressed, but the entry is wrong and should eventually
be corrected or removed from `CHANNEL_ARGS` with a fresh oracle probe (`TCDiff`/`TCSe` *do* appear
to genuinely require `U1..U12` there -- a from-scratch probe this session, `TCDiff(X,1,mv200C,1,
TypeT,Y,True,0,15000,1.0,0)` alone, rejects a literal `1` -- so this is specific to `VoltDiff`/
`VoltSe`, not the whole table).

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
  function): `Invalid, or out of place expression: %s.`, `%s` = the statement's own source text
  (`syntax.Stmt.text`/`Unparsed.text`), comment-stripped but otherwise **verbatim, including
  leading indentation and any trailing whitespace before the stripped comment** (round 2 finding,
  corrected from this session's earlier belief that it was leading/trailing-whitespace-trimmed;
  see "Verbatim echo text" below). Oracle: `NOTE` alone -> `line 1: Invalid, or out of place
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

  **Round 2 update: the mystery is broader than "before `BeginProg`".** This session found the
  same "only the first diagnostic, then silence for the rest of the file" shape on files where the
  *un*-diagnosed content is well *after* the point of first failure, and not text-before-
  `BeginProg` at all: `cdmresistance3w_1.CR6`'s `Dim` (no name, see "`Variable not named.`" below)
  fails at line 2, and the real compiler then says *nothing else at all* for the rest of the file
  -- not the later, clearly-undefined `Tref` used three times in `CDM_PanelTemp`/`CDM_TCDiff`/
  `CDM_Resistance3W` calls well past `BeginProg` (lines 15, 16, 19), and not a stray prose line
  (18) that would otherwise fail to parse. Yet `getdatarecord_3.CR6`, shaped almost identically
  (four separate nameless `Public`s), does *not* stop: it reports all four, plus a later, unrelated
  `Invalid or missing parameter.` line. Both files' failures are before `BeginProg`, both are the
  same statement shape, so "before `BeginProg`" was never the actual discriminator -- something
  about which specific pass-1 checks already ran, or something about the compiler's own recovery
  from a `Dim`/`Public` name-list failure that differs based on content this session did not
  isolate. Given this and the original ~45/88 split, this checker still deliberately does not
  attempt an "and then stop" behaviour anywhere -- the risk of suppressing a real diagnostic
  outweighs the "verdict" vs "exact" difference it would fix (AGENTS.md's priority order).

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

### Verbatim echo text (round 2 finding, `parser.py`)

Every `%s` this checker echoes back from a statement's own source (`Invalid, or out of place
expression: %s.` above; the `Units`/`Const`/`Alias` messages below) is the **raw physical-line
text, comment-stripped only** -- leading indentation and any trailing whitespace before the
stripped `'` comment both preserved verbatim, never trimmed. Found by a large block of CHM corpus
cases (`udpsocketopen_1.CR6` etc.) whose statements are indented inside `Scan`/`If`: a 4-space
`    UDPSocketOpen(SockID, 0, 3) 'comment` line is echoed as `    UDPSocketOpen(SockID, 0, 3) .`
(4 leading spaces, one trailing space before the final period, both exactly matching the source),
not the token-trimmed `UDPSocketOpen(SockID, 0, 3).` this checker produced before this session
(never wrong for a *column-1* statement, which is why 268 corpus cases already matched despite the
bug). `parser.py`'s `_segment_text` now returns the segment's raw text unchanged instead of
slicing between the first and last real token's positions; `checker/structure.py`'s two use sites
were not changed, since `stmt.text`/`Unparsed.text` already carry this text through. This alone
moved 268 -> 282 exact on `chm` (14 more corpus files became fully exact, several dozen more moved
from "wrong text" to "right text" on individual lines within files that also had other issues).

### Diagnostic ordering (round 2 finding, `compiler.py`)

`CR6Comp.exe` is a single top-to-bottom pass, so its own diagnostics are always in ascending line
order. This checker's diagnostics were previously only *incidentally* in order, because every rule
that existed before this session happened to walk `program.statements` in one single pass each; the
new required-parameter check (`instr_rules.py` below) breaks that incidental property (it can add
an earlier-line diagnostic after `structure.py` already added a later-line one for the same file).
`CompileResult.result_lines` (`compiler.py`) now stable-sorts `diagnostics.errors` by line before
rendering the failure output, reproducing the single-pass compiler's order regardless of which
checker pass contributed which line, without any checker module needing to coordinate order itself.
This file isn't listed as owned by either this session or the concurrent multi-logger workstream;
it's small, generic glue, and the fix is a correctness issue independent of logger or instruction
set, so it was made here rather than left broken.

### `Public`/`Dim` with no name at all: `Variable not named.`

A `Public`/`Dim` with nothing following it at all on its own physical line -- almost always a CHM
corpus example that wrapped the name list onto the next physical line with no `_` continuation
(`log10_1.CR6`: `Public` / `Number, B, Log10ofNumber, LogBofNumber`) -- reports `Variable not
named.` right at the keyword's own line (oracle-confirmed, round 2), not the generic "not a valid
statement" fallback. `parser.py`'s `_parse_declaration` models this as `Declaration(vars=[])`
instead of raising a parse error, so `structure.py` can special-case it.

The real compiler also says nothing at all about the very next physical line (the stranded name
list) even though it independently fails to parse as a valid statement on its own -- consistent
across every sample this session checked, including several *consecutive* nameless `Public`s
(`getdatarecord_3.CR6`, four in a row, each silences only its own very next line) and files that
otherwise continue reporting further, unrelated errors afterward (`cdmresistance_1.CR6`), so
`structure.py` suppresses exactly the one statement immediately following a nameless `Public`/`Dim`
(by line number), not a broader "give up" behaviour (see the "before `BeginProg`" update above for
why a broader stop was deliberately *not* attempted here either).

### `Units`/`Const`/`Alias` truncated mid-declaration: their own "ran off the end of the line" messages

The same CHM line-wrap shape (a keyword whose own required content is entirely on the *next*
physical line) applies to three more declaration-like keywords, each with its own real-compiler
message, oracle-confirmed round 2:

| Keyword | Parser reason (`parser.py`) | Message | Echo (`%s`) |
|---|---|---|---|
| `Units` | `"Units without '='"` (pre-existing, `_parse_units_segment`) | `Need '=' in units definition: %s.` | text after `Units`, empty in every corpus case found |
| `Const` | `"Const without '='"` (new, `_parse_const`) | `Const with no assignment.` | none -- fixed string, no `%s` at all |
| `Alias` | `"Alias without '='"` (new, `_parse_alias`) | `Need '=' in alias definition: %s.` | text after `Alias`, empty in every corpus case found |

`Const`'s message is uniform regardless of exactly how far its own grammar got before running out
of line (`rectpolar_1.CR6`: `Const` entirely alone; `sprintf_4.CR6`: `Const ToAddr As` with the
type/value on the next line -- both get the same fixed message). `_parse_const`/`_parse_alias` now
wrap their body in a `try`/`except _ParseError` and return an `Unparsed` with the specific reason
instead of letting the exception propagate to the generic catch-all in `_parse_segment` -- a pure
message-text refinement with no acceptance-behaviour change, since every path that used to raise
still produces a rejection, just with more specific text.

**Known imprecise sub-case, not claimed exact**: when `Alias`'s target *is* present but the `=`
that should follow it is what's missing (`realtime_1.CR6`: `Alias rTime(1)` / `= Year`), the real
compiler's echo is `rTime(1)` followed by a literal embedded newline character before the closing
`.` (`Need '=' in alias definition: rTime(1)\n.`) -- the same odd embedded-`\n` shape this session
also saw on an unrelated message from a `Data`-keyword/prose collision (`totalize_1.CR6` etc., see
"Open questions"). No mechanism for it was found, so `_parse_alias` only reproduces the simpler,
fully-empty-target case exactly; the partially-parsed-target case still gets the right message but
an empty (rather than the real, newline-suffixed) echo.

### Required parameter missing: `Invalid or missing parameter.` (`instr_rules.py`, new this session)

A new module, `checker/instr_rules.py`, alongside this package (not `checker/instructions.py`,
which is the concurrently-owned channel/port-check module, `PLAN.md`). Flags an `Instruction`
statement whose declared required-parameter prefix runs past the end of the parsed argument list --
overwhelmingly a CHM corpus example whose instruction call was wrapped across two physical lines
with no `_` continuation (`cosh_1.CR6`: `VoltDiff (Volt1,1,mv5000C,1,True` / `,200,15000,1.0,0)`;
`average_1.CR6`: `Average` alone on one line, `(1,TCTemp,FP2,False)` on the next). This checker's
own tolerant "auto-close an unclosed `(` at end of line" parsing (`docs/frontend.md`) silently
produces a short argument list rather than a syntax error; the real compiler instead reports the
gap right there and abandons the rest of that statement's own parsing (oracle-confirmed: the
generic per-instruction argument tokenizer only ever reads within the statement's own physical
line, `docs/type-codes.md`'s `FUN_004ba5f0`/`FUN_004b9700` description).

`_required_count` finds the required prefix the same way `docs/type-codes.md` documents
(`FUN_004baf00`'s own rule, "all arguments after the first optional parameter must be optional" --
first `code >= 100` marks the boundary), but two refinements were needed before it matched:

- **`_real_params` strips every `none`-kind code** (14 `EndSub`/`EndFunction`, 32
  `RainFlowSample`'s own first slot, 36 `EndSelect`, 46 `EndStructureType`/`EndObjectType`) before
  counting -- these are structural markers in the raw table that consume no user-written token at
  all. Found as a **regression** during development: bare `EndSub` (`params=[14]`) was flagged as
  one required argument short before this fix, and leaving a `none`-kind code in the list at all
  shifts every later position out of alignment with the parsed `stmt.args` (`RainFlowSample`'s real
  first user argument is table position 1, code 11, after the position-0 no-op marker, code 32).
- **`_TOLERANT_CODES` (4, 16, 17, 36, 37, 45, 46)** are exempt even when a real, counted required
  position: `docs/type-codes.md`'s own documented behaviour for code 4 ("if the line has already
  ended when this code is reached, all remaining declared codes are silently filled with a
  sentinel and parsing ends, no diagnostic") is what lets bare `Do`/`Loop`/`Else` (code 4, no
  trailing `While`/`Until`), bare `End`/`Exit` (16), and bare `Next` (17) compile with zero
  arguments even though their own table row marks the code non-optional. (36/45/46 are listed for
  fidelity to that passage but never actually reach this check -- `_real_params` already removes
  them as `none`-kind.)

This is pass-1 work (see "Two-pass compilation" above): it fires regardless of what else the file
already got wrong, and does not need to separately suppress `checker/instructions.py`'s own
per-instruction checks for the statement it flags -- the file-wide pass-2 gate in
`checker/__init__.py` already covers that for free (an earlier version of this check filtered the
specific flagged statements out of what got passed to `instructions.check`; that was replaced by
the simpler, more general file-wide gate once the two-pass finding above was made, since it
subsumes the narrower fix).

Deliberately checks only a *shortfall in count* (`len(stmt.args) < required`), never a `None` gap
from a skipped comma in the middle of an otherwise-complete list -- no oracle evidence this session
found supports flagging that shape, and AGENTS.md's priority (never reject an accepted program)
rules out guessing at it.

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
| `TCDiff`, `TCSe` | 4 (`DiffChan`/`SEChan`) | 44 corpus mismatches, all this shape, all fixed; a from-scratch round 2 probe re-confirms `TCDiff`/`TCSe` genuinely require `U1..U12` here |
| `VoltSe`, `VoltDiff` | 4 (`SEChan`/`DiffChan`) | 7 + 2 corpus mismatches, all fixed at the time -- **round 2 correction: this entry is wrong**, see "Two-pass compilation" above. `VoltDiff`/`VoltSe`'s own `DiffChan`/`SEChan` is a plain small integer (1..6ish), not a `U1..U12` name; the original 9 "fixes" all happened to be files with an unrelated, earlier pass-1 error, so the real compiler's own pass 2 (which would have caught the *true* shape of this bug) never ran against them either. Not corrected this round (owned by the concurrent workstream, `PLAN.md`) |
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
`tcpopen_5.CR6` (`DisableTcpDelayedAck is not a Settings Field Name.` -- a `Settings`-style
keyword-list check), `portbridge_1.CR6` (a name inside a `Public`/`Dim` array initialiser,
`expressions.py`'s "Not implemented" list above). `pointervariable_2.CR6` is not a checker gap at
all: the real `CR6Comp.exe` itself crashes (a wow64 page fault) under Wine on this file, so no
oracle text exists to match. `mqttpublishmeta_1.CR6` (round 2: was in this list, now fixed as a
side effect of the new `instr_rules.py` required-parameter check -- its own three missing
arguments are exactly that shape, not a bespoke `MQttPublishMeta` check after all).

## Open questions

- The "before BeginProg" follow-up line's exact trigger (see `structure.py` section above) --
  round 2 found this is broader than "before BeginProg" (see that section's update): the same
  "one diagnostic, then silence for the rest of the file" shape also happens well after
  `BeginProg`, and an outwardly identical failure shape (a nameless `Public`/`Dim`) sometimes stops
  the file and sometimes doesn't. No discriminator found in either round.
- Whether any predefined symbol present in `data/cr6/symbols.json` is nonetheless rejected on CR6
  specifically (the task notes floated `mV200C is not defined.` as an example); no live corpus or
  oracle case reproducing this was found this session, so `data/symbols.json`'s `extra` field's
  role, if any, is still undocumented.
- `ACPower`'s full channel-argument shape (see `instructions.py` section above).
- General expression-tree name resolution (inside `Public`/`Dim` initialisers, nested expressions,
  scoped rather than whole-program lookup) -- deliberately out of scope this session; see
  `expressions.py`'s "Not implemented" list.
- **Round 2, not chased**: a bare reference to a genuine built-in *function* name (as opposed to an
  instruction) followed by non-paren trailing text reports `Expression expects ( : %s.` instead of
  the generic "Invalid, or out of place expression" fallback (`rectpolar_1.CR6`: `COS of angle in
  radians` / `Sin of angle in radians`, both prose lines that happen to start with a real function
  name). Not implemented: a first attempt at generalising this from `data/functions.json` found
  that table also contains at least three entries that are ordinary English words and CRBasic
  operator keywords rather than genuinely callable functions (`AND`, `IS`, `TO` are all present) --
  applying the rule against the whole table would misfire on ordinary leading-prose lines like "is
  90 degrees..." or "and initialize to False..." (both real corpus lines, both currently correctly
  handled by the generic fallback). A correct fix needs a way to tell a real callable function from
  these table entries (not investigated -- `extra`/some other table field, or a curated list) before
  this can be implemented without risking a regression.
- The embedded-literal-`\n`-before-the-closing-period echo shape seen on two unrelated messages
  this session (`Need '=' in alias definition: rTime(1)\n.`, `realtime_1.CR6`; `Invalid symbol in
  expression: Tables and Store Data\n.` / `Illegal Data parameter Tables and Store Data\n.`, a
  `Data`-keyword/prose collision seen in `brfull_1.CR6`/`brhalf_1.CR6`/`totalize_1.CR6` and others)
  -- not reproduced, no mechanism found; low value; see the `Alias` subsection above.
