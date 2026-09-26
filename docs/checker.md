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

| Group | Round 1 start | Round 1 end | Round 2 end | Round 3 end |
|---|---|---|---|---|
| `crbasic/` (16 programs) | 14 exact, 2 verdict, 0 mismatch | **16 exact**, 0 verdict, 0 mismatch | 16 exact, 0 verdict, 0 mismatch | 16 exact, 0 verdict, 0 mismatch |
| `examples/` (4 programs) | 0 exact, 0 verdict, 4 mismatch | 0 exact, 2 verdict, 2 mismatch | 0 exact, 2 verdict, 2 mismatch | 0 exact, 2 verdict, 2 mismatch |
| `tests/corpus/chm/` (507 programs) | 198 exact, 0 verdict, 309 mismatch | 268 exact, 229 verdict, 10 mismatch | **310 exact**, 188 verdict, 9 mismatch | **311 exact**, 188 verdict, 8 mismatch |

Round 2's own headline: **268 -> 310 exact** (+42) on `chm`, verdict 229 -> 188, mismatch 10 -> 9
(one instance, `mqttpublishmeta_1.CR6`, moved from false-accept to exact as a side effect of the
new required-parameter check below -- not chased on purpose, just a consequence of it turning out
to be shaped that way). Re-verified after every change this round: every `their_exit == 0` program
in the corpus (accepted by the real compiler) is `exact` for us, zero exceptions -- the working
invariant this round optimised against ("never reject an accepted program" first, "exact" second).

**Round 3 (this session)**: `chm` 310 -> 311 exact, mismatch 9 -> 8 (`hex_2.CR6`,
`Scan Count parameter must be a non-negative integer`, moved to exact as a side effect of a
concurrent workstream's `Scan`-value check, not chased on purpose). The session's own targeted
fixes (array dimension limits, malformed `As` clause, the two fuzz-found robustness bugs, error-
reporting internals) are mostly exercised by `tests/corpus/fuzz/` and targeted oracle scratch
probes rather than `tests/corpus/chm/`'s existing 507 files, which mostly don't happen to hit
these shapes -- `tools/fuzz_replay.py --workers 2` (248 saved reproducers) is the more informative
number: 23 fixed outright, 9 changed class (all toward a less-severe class; none newly false-reject
or crash/hang). **Re-verified with `tools/difftest.py --logger <l> --group all` on all four
loggers after every change this round, not just `cr6`**: false-reject count is 0 on `cr6`/`cr300`/
`cr350` and the 1 pre-existing `cr1000x` false reject (`structuretype_3.CR6`, a `StructureType`
bare-member parsing gap, unrelated to this round's work and not fixed) is unchanged. Two extensions
this round tried and reverted after the full-corpus run (not just hand-written probes) surfaced
real false rejects on `crbasic/Main.CR6` and others -- see `expressions.py`'s "Round 3" section
below for the full account; this is the session's own most important methodological finding:
**a hand-written oracle probe is not a substitute for the full corpus + all four loggers before
trusting a generalisation.** Full detail: `docs/fuzz-findings.md`, "Robustness" and "Array
dimension limits"/"Malformed `As` clause" above, `expressions.py`'s own section below.

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

### Errors from an included file: the `<file>` prefix (round 3 finding, `diagnostics.py`)

An *error* whose statement came from a file other than the main program (pulled in by `Include`/
`IncludeSection`, `source.py`) is printed as `<file> line N: <message>` -- the included file's own
name in angle brackets before the usual `line N:` -- for most message classes, oracle-confirmed
this session with `IncludeSection`d libraries carrying a deliberate error (`python3 tools/oracle.py`
scratch probes, not saved to the corpus): `Variable not named.`, `Illegally dimension: %s.`,
`Variable %s dimension exceeds max of %d.`, `Const with no assignment.`,
`Invalid symbol in expression: %s.` (`expressions.py`) and `Invalid or missing parameter.`
(`instr_rules.py`) all get it when the flagged statement is inside the included file, and never get
it when the statement is in the main program itself (even in the *same* compile, comparing a
library error against a main-file error of the same message class side by side).

**Two oracle-confirmed exceptions, both `structure.py`'s own generic "this statement could not be
resolved as anything valid" fallback**: `Sub %s not defined.` (an explicit `Call` to an undeclared
name) and `Invalid, or out of place expression: %s.` (a bare unresolved name, or anything our
parser marks `Unparsed`) never carry the prefix, regardless of which file the statement came from
-- confirmed with the same side-by-side setup (a library `Call NoSuchLibSub` and a main-file `Call
NoSuchMainSub` in the same compile: the library one prints as plain `line N: Sub NoSuchLibSub not
defined.`, no `<file>`). No other exception is known; every other message class this session tested
carries it. Modelled as `Diagnostic.show_file` (`diagnostics.py`, default `True`; `Diagnostics.
error(..., show_file=False)` is how the two exception call sites opt out) rather than the reverse,
since opting *in* would need every other checker module (including ones this session does not own)
to be updated, while the two exceptions are both in `structure.py`. Warnings are untested against
this rule and are left unprefixed (`cr6comp` only applies it to errors) rather than guessed at.

An early false start, worth recording so it is not retried: a bare "Invalid, or out of place
expression" case looked prefix-less at first only because it happened to be the *sole* diagnostic
in a program that also independently stops compiling after that one statement (see "Placement
before `BeginProg`" above) -- with only one diagnostic to look at, "no prefix" and "this file's
diagnostics never got a chance to need one" were indistinguishable. The side-by-side setup (forcing
a *second*, main-file diagnostic of a different, prefix-confirmed class in the same compile) is
what separated "this message class never gets it" from "no other diagnostic happened to exist yet".

### Error message buffer: a 4096-byte cap, then pass 1 stops (round 3 finding, `compiler.py`)

Error messages accumulate in a 4096-byte buffer; once a message no longer fits, it is dropped with
no truncation marker of any kind, and no further diagnostic is reported for the rest of the file
(oracle-confirmed: a 150-line program with a distinct, guaranteed-non-stopping undefined-symbol
error on every line -- `docs/checker.md`'s "before `BeginProg`" stop-after-first behaviour is a
different, unrelated mechanism, deliberately avoided here by keeping every offending statement
inside a `Scan` block -- prints only 69 of the 150 possible errors and then falls silent). The
budget is spent on the rendered `line N: <message>` text alone (the `<file> ` prefix included, when
present) plus a 2-byte CRLF terminator per line; the `"<program> -- Compile Failed!"` header and
the blank line after it are not part of it. Reproduced byte-for-byte against the real `CR6cmple.xxx`
file (not just stdout, which Wine's console layer doubles a stray `\r` into -- `tools/oracle.py`'s
own docstring): the 69th message's own text brings the running total to 4065 bytes, and the 70th
(`+59` bytes with its own longer line-number prefix) would push it to 4124, over the limit, and is
the first one missing. `CompileResult.result_lines` (`compiler.py`, `_bounded_by_message_buffer`)
applies this to the sorted, rendered error-line list; this only changes *rendering*, not how many
diagnostics this checker itself finds or knows about (see "Robustness" below for why that
architectural shortcut is fine here: nothing past a real truncation point would ever be visible to
a user of either compiler anyway). Not implemented: a warning-side equivalent, if one exists (the
task's own working note floated "warnings capped at 1000"; this session could not construct a
scalable oracle probe for it -- `warnings.py`'s only implemented warning kind, "table declared but
never called," is capped far earlier by a *different*, unrelated, oracle-confirmed real limit,
`Maximum number of tables of 128 exceeded.`, itself worth a future `structure.py`/`tables.py`
declaration-count check but not implemented this session, out of this task's explicit scope).

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

### Malformed `As` clause: `Invalid Variable declaration %s.` (round 3, `parser.py`/`structure.py`)

Well evidenced by 6 independent fuzz mutants (`docs/fuzz-findings.md` "5. Message diff"), all a
corrupted spelling of the `As` keyword (`asX`, `As_Mut`, a stray space splitting one identifier
into two, ...). Oracle-confirmed rules (`parser.py`'s `_parse_vardecl`, a `_ParseError` with reason
`"Invalid As clause: <token>"`, mapped by `structure.py` to the message below):

- A token right after a `Public`/`Dim` variable's own name/dims -- where `As`, `=`, a comma or end
  of line was expected -- that is none of those reports `Invalid Variable declaration %s.`, `%s` =
  that token's own text, and **abandons the whole declaration statement**, not just the one
  variable: a valid first variable followed by a malformed second one (`Public pppSocket As
  Long,pppIP As_Mut String`) reports only the second's own message, parsing no further variable in
  the same comma list (oracle-confirmed).
- **Oracle-confirmed quirk, easy to get wrong**: once a variable's own `As Type` clause has been
  *fully* parsed, anything before the next comma (or end of line) is silently **discarded, not
  rejected** -- `Public X As Long asX String, Y As Float` compiles, `Y` declared normally, `asX
  String` simply vanishes (confirmed not resolvable afterwards, not a second variable). This is
  what distinguishes the message from a plain "unexpected trailing input" rejection: the malformed-
  clause check only fires *before* a valid `As`/`=`/comma/EOL is found for that variable, never
  after. Two pre-existing false rejects (this exact "trailing garbage after a complete `As`
  clause" shape) were fixed as a side effect of getting this ordering right.

### Array dimension limits: `Illegally dimension: %s` / `Variable %s dimension exceeds max of %d.` (round 3, `structure.py`)

Fixes the fuzz-found hang (`docs/fuzz-findings.md` "1. Hang"): `tables.py`'s `-l` renderer used to
enumerate every element of a `Public X(999999999999) As Long`-shaped declaration with no upper
bound checked first. The real compiler rejects the declaration itself, immediately and cheaply,
before any table-building code would ever see it -- so the fix belongs at declaration time, not in
`tables.py` (which still got a defensive bound too, "Robustness" below, for whatever a future gap
in this check lets through).

Oracle-confirmed rules (`Public`/`Dim`/`Static`, `checker/structure.py`'s `_check_array_dims`):

- **More than 3 dimensions** on one variable is checked *before* any dimension's own value:
  `Variable %s dimension exceeds max of %d.`, `%s` = the variable's name immediately followed by
  (no closing paren) its **first** dimension's own raw source text, verbatim -- not evaluated, not
  the dimension that is actually out of range (`Public X(1,1,1,2147483648)`, only the 4th
  dimension being illegal, still reports `Variable X(1 dimension exceeds max of 3.`, naming the
  first). `%d` is always 3 on the CR6 (not independently confirmed on the other three loggers this
  session; `parser.py`'s `VarDecl.dim0_span` -- a source column range captured while parsing the
  first dimension, alongside the existing token stream -- is what lets `structure.py` echo this
  verbatim without the parser needing to reconstruct an expression's spelling from its AST).
- **Otherwise, each dimension that folds to a constant outside `[1, 2147483647]`** (`INT32_MAX`):
  zero, negative, or too large to represent as a signed 32-bit int (the fuzz-found
  `999999999999`, and every oracle-tried value at or above `2147483648` -- `2147483648`,
  `4294967295`, `10000000000`) reports `Illegally dimension: %s`, `%s` = the **whole declaration
  statement's own verbatim source text** (not just the offending variable: `Public A As Long,
  X(0) As Long, B As Float` echoes all three), no trailing period, unlike most other messages this
  document catalogues. One diagnostic per illegal dimension value found, not deduplicated:
  `Public X(0,-1) As Long` (two illegal dimensions on one variable) and `Public X(1,1,1,1) As
  Long, Y(2,2,2,2) As Long` (two over-dimensioned variables) both print the relevant message
  *twice*, oracle-confirmed. A dimension that does not fold to a constant at all is left alone
  (no oracle evidence supports guessing at that shape).
- **`2147483647` (`INT32_MAX`) itself is not this checker's concern**: the real compiler *crashes*
  on it (a wow64 page fault under Wine, confirmed reproducible, not a Wine artifact this session
  could rule out either way) rather than accepting or rejecting it cleanly, so no oracle text
  exists to match. Values just below it (oracle-tried up to `2100000000`) instead get a real,
  different, **not implemented** message, `Out of memory.`, reported at the program's last
  statement's line rather than the declaration's own -- a genuine, memory-size-dependent real
  limit this session did not attempt to model (it would need the same auto-allocation-pool
  reverse-engineering `docs/tdf-rules.md` §10 already flags as low-confidence for a much smaller,
  better-understood table-sizing problem); every value in this session's own accepted-corpus and
  fuzz-seed evidence stays well under it, so this remains a `mismatch`, not a false accept this
  session introduced.
- The check is **per-dimension**, not per total element count: `Public X(100000,100000) As Long`
  (10^10 elements) is *not* `Illegally dimension` (each individual dimension, 100000, is legal) --
  it is the same `Out of memory.` gap above.

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

### Round 3: two extensions tried and reverted (real false rejects on the full corpus)

The task brief for this round asked for exactly the two extensions above, plus a constant-only/
variable-required type-code check. Both were implemented, both initially looked correct against
this session's own hand-written oracle probes, and both were then reverted once a full run of
`tools/difftest.py --logger <l> --group all` (all four loggers, the whole `chm`/`repo`/`examples`
corpus, not just targeted probes) turned up real false rejects -- exactly the failure mode
AGENTS.md ranks worst. Recorded here so a future session does not repeat the same probing without
also checking the full corpus before trusting a hand-written probe:

- **Full expression-tree name resolution** (`expressions.py`'s `_undefined_names`, written but not
  wired into `check`): extending the undefined-symbol check to walk into `Unary`/`Binary`/`Index`/
  `ArrayLiteral` immediately rejected this repository's own `crbasic/Main.CR6` and five of its
  siblings, plus `tests/corpus/chm/tablefile_1.CR6`. Two distinct, previously-invisible gaps in
  `known`: `NAN` (a predefined float constant used throughout this repo's own sensor code, e.g.
  `PT1000_Temp_C <> NAN`) is not present in `data/symbols.json` at all, and the system `Status`
  table (`Status.SerialNumber`) is not in `known` either -- both invisible to the old, direct-
  bare-`Name`-only check because neither previously-exact program happened to reference either name
  as a *direct* instruction argument, only nested inside a larger expression. Given no time left
  this round to audit `known` against every name a nested expression anywhere in the corpus might
  reference, this stays unimplemented; `_undefined_names` is written and ready, but `check` does
  not call it.
- **Constant-only (codes 1/39, 5/38, 20) and variable-required (codes 8, 27, 29, 33, 41, 47, 48)
  type-code checks**: both are real rules for *specific* instructions (oracle-confirmed:
  `WriteIO(Mode,X)` -> `Variable illegal in Parameter: Mode.`; `Scan(X+1,Sec,0,0)` -> `Variable
  illegal in Parameter: X+1.`; `Battery(5)`/`Battery(1+2)`/`Battery(SomeConst)` -> `Invalid
  Variable or Array: <text>.`, a `Const SomeConst = 5` rejected exactly like a bare literal, only a
  *string* constant such as `Battery("x")` accepted) -- but **not uniform across every instruction
  that shares a type code**, which only the full-corpus run exposed: `Sample`'s own `DataType`
  argument is coded `1` (`const_expr`) like `Scan`'s interval, yet legitimately accepts a
  `StructureType` name (`structuretype_1.CR6`, not a foldable constant); `Move`'s third argument
  and `ModBusClient`'s last, optional argument are both coded `27`/`41` (`variable_or_array`) like
  `Erase`'s argument, yet both legitimately accept a bare literal (`embracer.cr6`,
  `modbusclient_2.CR6`, both CR1000X). The most likely mechanism is each such instruction's own
  `compile_fn` recognising a keyword- or type-name-shaped argument before generic constant-folding
  would run, which the type-code table alone does not record. Not implemented; would need each
  const-only/variable-required-coded parameter checked individually (or the compiler's own
  per-instruction override flag identified) before it could be enabled without more false rejects
  like these.

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

## Robustness (round 3): `compile_file` never raises, `tables.py` never hangs

Two fuzz-found bugs, both traced and fixed this session; full reproduction detail and root-cause
analysis stays in `docs/fuzz-findings.md` ("1. Hang", "2. Crash") rather than being duplicated
here.

- **Hang** (`tables.py`'s `-l` renderer enumerating every element of an unbounded array
  dimension): the array-dimension check above closes the specific reported case (the real
  compiler rejects it before any table-building code would run at all), but as defense in depth
  for whatever this checker does not yet catch, `tables.py`'s `_explode_indices`/
  `render_table_list` now also caps how many index tuples a single field will ever enumerate
  (`_MAX_EXPLODE_COUNT`, comfortably above the largest single-field element count this session
  could get the real compiler to actually accept) -- silent truncation of the `-l` text for a
  pathological case, never a hang, and never observed to affect any table size this session
  could confirm the real compiler accepts.
- **Crash** (`tables.py`'s `_scan_weights` calling `float()` on a non-numeric `Scan` interval
  unconditionally, reachable from the smallest possible `Scan` block regardless of whether a
  table is even called inside it): now type-checked before the conversion; a non-numeric interval
  falls back to the same default weight an *absent* interval already used, and the fold this
  function does is unconditional and best-effort only for auto-sizing purposes, not a claim that
  the `Scan` interval is itself valid (`checker/instructions.py`'s own value-range check is the
  intended place for the real compiler's `Scan Interval parameter must be a non-negative integer`
  message; not implemented this session, out of this workstream's scope, `PLAN.md`).
- **Every checker pass is now wrapped** (`checker/__init__.py`'s `_run_pass`) so a bug in any one
  of them -- including modules this session does not own -- cannot take down the whole compile;
  a pass that raises is treated as having found nothing further, and every other pass still runs.
  `compiler.py`'s `compile_file` adds an outer safety net of the same shape around parsing, the
  whole checker call, and the program-signature computation, so no path through it can raise. No
  oracle text can ever exist for "opencrbasic itself broke" (the real compiler does not crash on a
  syntactically-loadable file, `pointervariable_2.CR6`'s known real-compiler-side crash under Wine
  is the one confirmed exception, "Known remaining gaps" below), so none of this invents a
  diagnostic to paper over an internal bug -- it only guarantees a `CompileResult` comes back
  instead of a Python traceback.

Regression tests for both bugs: `tests/test_tables.py`'s "robustness" section (a synthetic
`_explode_indices` call with a ~10^12 `dim`, and a `Scan("fuzz",...)` program through
`build_tables` directly); `tests/test_checker.py`'s "array dimension limits" section covers the
declaration-time fix that keeps the hang's specific reported input from ever reaching `tables.py`
at all.

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

## Pass model (authoritative, oracle-confirmed 2026-09-26)

The exe's diagnostics come from two passes (compile driver 0x424160, `docs/binary.md`):

- **Pass 1** parses every line into a record. It is lenient about statement *shape*: a line that
  is not a known instruction becomes an `Expr` record without error. Pass-1 errors are parse and
  declaration errors: parameter parsing by type code (`Invalid or missing parameter.`,
  `Too many parameters: <rest>.`), `Need '=' in units definition: <echo>.`,
  `Variable not named.`, `Const with no assignment.`, `Line exceeds max characters of 512.`,
  duplicate/invalid declarations, block `#If` errors, etc. All pass-1 errors in the file are
  printed, in line order.
- **Pass 2** runs only when pass 1 reported no error. It walks the records in order and calls
  each instruction's handler; `Expr` records are compiled here, so `Invalid, or out of place
  expression: <echo>.`, `Invalid symbol in expression: <echo>.` and `Invalid assignment in
  expression: <echo>.` are **pass-2** errors, as are instruction semantic checks (channel ranges,
  table rules). Errors accumulate record by record, **except** that a placement error
  (`%s illegally occurs before BeginProg.`, `%s is declared in the middle of an executing
  sequence`, `%s illegally occurs after EndSequence.`) ends the pass immediately.

Probes (CR6):

```
Public x / foo bar baz / Units x / BeginProg ...   ->  line 3: Need '=' in units definition: x
                                                       .            (line 2 never reported)
Scan body: foo bar baz / x = y / qux quux          ->  line 4 out-of-place, line 5 invalid symbol,
                                                       line 6 out-of-place (all three)
Public x / foo bar / x = 2 / zap zip / BeginProg   ->  line 2: Invalid, or out of place expression: foo bar.
                                                       line 2: Expr illegally occurs before BeginProg.
                                                       (stops; line 4 never reported)
```

Note the first probe: the `Units` echo includes the raw line's newline, so the closing `.` is
printed on the next line.
