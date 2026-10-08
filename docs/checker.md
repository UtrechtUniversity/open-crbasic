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

| Group | Round 1 start | Round 1 end | Round 2 end | Round 3 end | Round 4 end (cr6) |
|---|---|---|---|---|---|
| `crbasic/` (16 programs) | 14 exact, 2 verdict, 0 mismatch | **16 exact**, 0 verdict, 0 mismatch | 16 exact, 0 verdict, 0 mismatch | 16 exact, 0 verdict, 0 mismatch | 16 exact, 0 verdict, 0 mismatch |
| `examples/` (4 programs) | 0 exact, 0 verdict, 4 mismatch | 0 exact, 2 verdict, 2 mismatch | 0 exact, 2 verdict, 2 mismatch | 0 exact, 2 verdict, 2 mismatch | 2 exact, 2 verdict, 0 mismatch |
| `tests/corpus/chm/` (507 programs) | 198 exact, 0 verdict, 309 mismatch | 268 exact, 229 verdict, 10 mismatch | **310 exact**, 188 verdict, 9 mismatch | **311 exact**, 188 verdict, 8 mismatch (this row's own baseline re-measured 311/187/9 at round 4's start, a 1-verdict wobble from concurrent work between sessions, not a round-3 regression) | **336 exact**, 162 verdict, 9 mismatch |

Round 4's own full account (`expressions.py`/`symbols.py`'s general expression-tree resolution,
then a `structure.py`/`checker/__init__.py` pass-model correction): "Round 4" section near the end
of this document.

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

The stray `NextScan` is reported in pass 2 (CR6/CR1000X) or late (CR300/CR350); see "Scan
placement" below. Both one-word (`NextScan`) and two-word (`Next Scan`, parsed generically as
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

- **Explicit `Call name`, name resolves to no declared `Sub`**: see "Unresolved `Call`s" below.

### Unresolved `Call`s (oracle, 2026-10-02, all four loggers; `structure._unlinked_call`)

Fixture: `tests/fixtures/call_links.json` (`tests/test_call_links.py`). Two forms, chosen by
whether the program declares any `Sub` or `Function` before `EndProg` (one is enough, of either
kind; text after the first `EndProg` is not compiled at all, including an `Include` there; a `Sub` from an
included file before `BeginProg` counts, CR6 and CR300 probed).

- **No routine declared**: `line N: Sub <name> not defined.` (no `<file>` prefix) at the call,
  and the compile ends there: only the first such call is printed, and nothing later (a later
  `X = Q`, `Foo Bar`, `NextScan with no Scan.`, `Could not find DataTable`, unclosed `If`) is.
  Earlier pass-2 errors on earlier lines still print. Before `BeginProg` the same message replaces
  the placement error. CR300/CR350 name resolution is a pass-1 check there, so any name-class
  error anywhere (`Q is not defined.`, `Foo Bar`) hides the message (implemented as a deferred
  message, `commit_deferred_sub_error`). Pass-1 errors (`Variable not named.`) hide it everywhere.
- **Any routine declared**: no error at the call. At the end of the compile (the link step) every
  explicit `Call` whose name is not a declared **Sub** (a variable, built-in, `Function`, or
  `Sub` that the walk had not reached) prints `<name> is not declared as a Subroutine.` with **no**
  `line N:` prefix, once per call (duplicates twice), in **reverse** source order, after all
  line-numbered errors. It does not gate later checks: tier-C instruction errors, unclosed-block
  errors, `Call to S1 with 2 parameters, requires 1 arguments.` and `Could not find DataTable`
  are all still printed before it. Pass-1 errors (`Variable not named.`, `Dimension ... missing
  the ) character`, `Variable Type ... not supported`) hide it. A call before `BeginProg` still
  gets `Call illegally occurs before BeginProg.` and ends the walk, so a `Sub` defined after that
  point is unknown to the link step (`Call S1(1)` above `Sub S1` reports `S1`). CR300/CR350:
  hidden by name-class and parameter-shape errors, not by the pass-2 ones listed in
  `structure._LINK_TOLERATED`.
- **Included files and cutoff**: the immediate no-routines form and CR300/CR350's late link form
  use the statement's position in the expanded source stream, not its physical line number. With
  no earlier pass-1 error, an included `Call Nope` on line 11 that appears before a later main-file
  statement on line 5 ends the compile at `line 11: Sub Nope not defined.`. The checker records
  expanded `(file, line)` positions before applying either cutoff, so included-file line numbers
  cannot retain later diagnostics or discard earlier ones. Fixture:
  `tests/fixtures/include_deferred_sub.json`.
- **Name text**: the statement text after `Call`, all whitespace removed, up to the first `(`,
  case kept: `Call S1 1` -> `S11`, `Call  S2 (1)` -> `S2`, `Call Nope  Q` -> `NopeQ`. The same
  text rule applies to the no-routine form (`Sub S11 not defined.`). A bare statement whose first
  word is a declared `Sub` and whose remainder does not start with `(` uses it too (`S1 1,2` ->
  `S11,2`); a bare undeclared `Nope 1` is the generic fallback as before.

Known gaps: CR300/CR350 calls before `BeginProg` (the real compiler adds `Call illegally occurs
before BeginProg.` and stops; ours places that late, so the link line is missing or extra);
`Call S1(1) 2` / `Call` / `Call (1)` shapes; tier-C errors on lines before a no-routine `Sub ... not
defined.` (the real compiler prints them, ours skips tier C once an error exists).

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
name in a program without routines, "Unresolved `Call`s") and `Invalid, or out of place expression: %s.` (a bare unresolved name, or anything our
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

### Echo quirk: target parsed but `=` runs off the end of the line (fixed 2026-09-27)

`Alias`/`Units` both have a second, distinct "ran off the end of the line" shape from the fully-
empty one above: the target itself parses fine as a valid lvalue, but the `=` that should follow
it is on the *next* physical line (`realtime_1.CR6`: `Alias rTime(1)` / `= Year`, no `_`
continuation). Oracle-confirmed (two fresh scratch probes under Wine, `t.cr6`/`t2.cr6`, plus
`realtime_1.CR6` itself):

```
Public x / foo bar baz / Units x / BeginProg / EndProg
  -> line 3: Need '=' in units definition: x
     .                                            (closing '.' on its own output line)

Public rTime(9) / Alias rTime(1) / = Year / BeginProg / EndProg
  -> line 2: Need '=' in alias definition: rTime(1)
     .
```

The echo is the target's own source text followed by a **literal embedded `\n`** before the
closing `.` -- the same odd shape this session also saw on an unrelated message from a
`Data`-keyword/prose collision (`totalize_1.CR6` etc., see "Open questions", still unresolved).
Distinguished from the fully-empty case by a separate parser reason (`parser.py`'s `_parse_alias`
splits its `_parse_atom`/`expect_op("=")` calls into two `try` blocks instead of one; the `eq ==
-1` branch of `_parse_units_segment` tries to tokenise and parse an atom from the trailing text
before falling back to the plain reason): `"Alias target with no '='"`/`"Units target with no
'='"` vs the existing `"Alias without '='"`/`"Units without '='"`. `checker/structure.py`'s
`_check_unparsed_pass1` echoes the new reasons with an explicit `\n` before the final `.`. A
target that fails to parse (not attempted in the corpus or an oracle probe) falls back to the
plain, un-suffixed reason -- not claimed exact, no evidence either way.

Effect (`tools/difftest.py --logger <l> --show 0`, `chm`, before -> after this fix alone measured
against a tree that also had a concurrent, unrelated `instr_rules.py` change land mid-session, so
the deltas below are not attributable to this fix in isolation): `realtime_1.CR6`'s own first
diagnostic now matches the oracle exactly (`Need '=' in alias definition: rTime(1)\n.`); it stays
`verdict` rather than `exact` overall because its later lines depend on unrelated, unimplemented
messages (`Invalid, or out of place expression: = Year .`, `Too few parameters in instruction.`).
cr6 `chm` exact 336 -> 338, cr1000x 352 -> 354 (cr300/cr350 unaffected -- neither hits this shape
in the corpus); zero false rejects on all four loggers throughout, `python3 -m pytest -q` green
(1019 passed, 311 skipped). New tests: `tests/test_echo_quirk.py`.

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

#### A required argument cut off by an unterminated string (`_check_unterminated_string_param`)

Same message, a different pass-1 shape the count-shortfall check above cannot see: a required
argument that *opens* a string (`"`) but never closes it before the physical line ends never
becomes an `S.Instruction` at all -- `lexer.tokenize` raises `LexError("unterminated string")` on
the whole line first, so `parser.py` records it as `S.Unparsed(reason="lex error: unterminated
string")`, invisible to a check that only walks `S.Instruction` nodes. Oracle-confirmed
(`httpout_1.CR6`/`webpagebeginwebpageend_1.CR6`, CHM corpus, plus an unwrapped scratch probe --
`Public Commands As String * 10` / `BeginProg` / `HTTPOut("<title>CR1000X` / `Datalogger</title>")`
/ `EndProg` byte-for-byte): `Invalid or missing parameter.` at the statement's own opening line,
nothing at all for the continuation line the string spills onto -- for free, from the same pass-1/
pass-2 file-wide gate the count-shortfall check above already relies on (once this fires anywhere,
pass 2's generic `Unparsed`->`Expr` fallback, this project's own previous wrong output here, never
runs for the rest of the file).

**Deliberately keyed to the type-code `kind`, not "any known instruction"**: a same-shaped probe
against `IF X <= 29 Then X = "TEMP` / `WATCH"` (`call_1.CR6`, CHM corpus) gets a *different* real
message (`String without end quote`) for its own unterminated string, even though `IF` is a
`profile.instructions` entry too (`params=[7]`, kind `bool_expr`) -- so "known instruction" alone
is not the trigger. `docs/type-codes.md` documents `bool_expr` (7) and `expr_or_name` (26,
`HTTPOut`'s own single param) reaching the shared per-code parameter resolver through two
*different* switch cases (`_caseD_7` vs `_caseD_19`); `_UNTERMINATED_STRING_KINDS` fires only when
the statement's first declared parameter is `expr_or_name`, the confirmed case -- not generalised
to every other `kind` the shared resolver accepts (no oracle evidence covers them, and `String
without end quote` itself is unimplemented, out of scope here). CR6 CHM exact: 336 -> 338 (also
+2 on CR1000X, which shares `HTTPOut`; CR300/CR350 unaffected, no regression either way). Tests:
`tests/test_instr_rules_unterminated_string.py`.

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
`tcpopen_5.CR6` (implemented since: `docs/instructions.md` "SetSetting and SetStatus field names"), `portbridge_1.CR6` (a name inside a `Public`/`Dim` array initialiser,
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
- The `Alias`/`Units` half of the embedded-literal-`\n`-before-the-closing-period echo shape is
  now fixed -- see "Echo quirk" above. The *other* occurrence, an unrelated `Data`-keyword/prose
  collision (`Invalid symbol in expression: Tables and Store Data\n.` / `Illegal Data parameter
  Tables and Store Data\n.`, seen in `brfull_1.CR6`/`brhalf_1.CR6`/`totalize_1.CR6` and others),
  is a different message pair entirely (`expressions.py`'s undefined-symbol/Data-table checks, not
  `structure.py`'s declaration parsing) and is still not reproduced -- no mechanism found, low
  value.

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

## Round 4 (`expressions.py`/`symbols.py`, then `structure.py`/`checker/__init__.py`)

Two pieces of work, oracle-confirmed 2026-09-26, `tools/oracle.py`/scratch probes against all four
compilers unless noted CR6-only.

### General expression-tree name resolution (`expressions.py`, `symbols.py`)

Round 3 tried this and backed it out after finding `NAN`/the system `Status` table missing from
`known` (see that section above). Both gaps are now closed, and the walk (`_undefined_names`,
already written in round 3) is wired into `check` for real:

- **`known` additions** (`_known_names`, `expressions.py`; `symbols.system_table_names`,
  `symbols.py`): `NAN`/`INF` (oracle-confirmed accepted as float literals on all four loggers,
  despite being absent from every `data/<logger>/symbols.json` -- that table is the compiler's
  *named-constant* lookup, `docs/type-codes.md`'s `FUN_004ee4f0`, and `NAN`/`INF` evidently resolve
  through a separate, special-cased path this session did not trace to the decompilation, so they
  are added here rather than misrepresented as rows of that extracted table); `Status`/`Public`/
  `Settings` (system tables addressable as `Table.Field` unconditionally, on all four loggers, no
  program-shape precondition); `DataTableInfo`, same shape but **conditional** on the program
  declaring at least one user `DataTable` (oracle-confirmed, all four loggers: `DataTableInfo.
  SkippedRecord(1)` alone fails `Could not find DataTableInfo.`/`Could not find DataTable
  DataTableInfo.`; the same reference compiles once any `DataTable` exists anywhere in the
  program).
- **The walk itself** now recurses into `Unary`/`Binary`/`Index`/`Member.base`/`ArrayLiteral`, not
  just a direct bare-`Name` argument, at the same `_RESOLVED_KINDS` positions round 3 already
  restricted to.
- **Per-statement reporting order and shape is per-logger** (`_report_statement`), found by testing
  multiple-bad-argument shapes the corpus never happened to exercise (`WriteIO`/`CardTest`/
  `InternalTemp`/`PRT`-shaped scratch probes, not saved): CR6/CR1000X check every *constant-only*-
  family position (`_CONST_FAMILY_KINDS`: `const_expr`/`const_expr_only`/`datatype_kw`, codes
  1/39, 5/38, 20) ahead of every general-family position, **regardless of textual order**
  (`WriteIO(1,BazQux,FooBar)`, `params=[1,25,101]`, reports `FooBar` -- position 2, the later
  const-family slot -- never `BazQux` at position 1; `CardTest(GenBad,ConstBad)`, `params=[8,1]`,
  reports `ConstBad`, the const-family slot, even though it is the later argument), and report only
  the first bad position found (one whole-expression-echo diagnostic per statement). CR300/CR350
  have no such reordering (`InternalTemp(GenBad,ConstBad)`, `params=[8,20]`, reports the
  textually-first `GenBad`, then abandons the rest of the statement's own parameter list --
  `Too many parameters:  ConstBad.` -- rather than moving on to check position 1 at all) and echo
  *every* undefined name found inside one compound expression as its own separate line
  (`Randomize(X + FooBar + BazQux)`: one line on CR6/CR1000X, two on CR300/CR350). Modelled as a
  plain `profile.name` branch (`_WHOLE_EXPR_ECHO_LOGGERS`) to stay inside this session's two owned
  files.
- **The constant-only "rejects a variable" check is now implemented**, narrower than the type code
  alone would suggest on two axes that route around round 3's own found regression (`Sample`'s
  `DataType` argument, also coded `1`, legitimately accepting a `StructureType` name): `datatype_kw`
  (code 20) is excluded entirely (a keyword-validated slot with its own untraced per-instruction
  legal-value list, not this generic mechanism); the trigger is "references a declared runtime
  variable" (`symbols.declared_variable_names`: `Public`/`Dim`/`Static`/a `Sub`/`Function`
  parameter), not "fails to fold to a constant" -- a `StructureType` name is neither, so it is
  simply never reached by this narrower trigger, no `Sample`-specific carve-out needed. Oracle-
  confirmed uniform message text and whole-expression echo across all four loggers (`WriteIO(X,1)`
  -> `Variable illegal in Parameter: X.`; `Scan(X+1,Sec,0,0)` -> `Variable illegal in Parameter:
  X+1.`, identical on CR6/CR1000X/CR300/CR350), so no per-logger branch for it. **Ordering
  interaction, oracle-confirmed**: it runs as part of the *same* const-family-first pass as the
  undefined-symbol check above (not a separate later pass) -- `WriteIO(X,BazQux)` (`X` a live
  variable at the const-family position 0, `BazQux` undefined at the general-family position 1)
  reports `Variable illegal in Parameter: X.`, never reaching `BazQux`'s own undefined-symbol error,
  and the reverse shape (`PRT(GenBad,X,3,3,3)` on CR300, `GenBad` undefined at the general-family
  position 0, `X` a live variable at the const-family position 1) reports only `GenBad is not
  defined.`, confirming CR300/CR350 have no family reordering for this combined check either.
  Variable-required (the opposite direction, `_VARIABLE_REQUIRED_KINDS`) stays unimplemented: this
  round's own corpus scan for a literal at a variable-required position found a *third* confirmed
  override beyond round 3's `Move`/`ModBusClient` -- `MQTTPublishTable`'s optional `Longitude`/
  `Latitude`/`Altitude` arguments (code `148`, oracle-confirmed accepting a bare literal in
  isolation on CR6 and CR1000X, `mqttpublishtable_1.CR6` line 17) -- reinforcing that a blanket rule
  here is not safe without a per-instruction exception list wider than this session had time to
  build.

Measured in isolation during development (a scratch difference-only harness re-running just this
piece's own two files against the unchanged rest of the tree): improved `cr6`/`cr1000x`/`cr300`
each by a couple of `mismatch -> verdict`/`exact` moves with zero new false rejects. The single
authoritative before/after number for this round as a whole (both this piece and the pass-model
piece below combined, since the two were developed back to back against a live, concurrently-edited
tree and are not independently reproducible in isolation after the fact) is under "Pass model
correction" below.

Fixed-seed fuzz false-accept rate (`tools/fuzz.py --logger cr6 --count 300 --seed 42`, a scratch
`--out` directory, not committed): 77/300 (25.7%) -> 74/300 (24.7%), `false_reject=0` in both runs.
`tools/fuzz_replay.py --workers 2` (248+ reproducers): several `false_accept -> message_diff`
transitions on cr6/cr1000x/cr300 (closed false accepts), zero new `false_reject`.

### Pass model correction (`structure.py`, `checker/__init__.py`)

A coordinator-directed extension mid-round, once oracle probing (`docs/checker.md`'s own "Pass
model" section above, and further probes this round) established the real architecture more
precisely than round 2's "two-pass" finding had: pass 1 is lenient about statement *shape* (an
unrecognised line becomes an `Expr`/`Call` record without error at parse time), and the generic
"Expr"/implicit-`Call` fallback (`Invalid, or out of place expression: %s.`, `Sub %s not defined.`)
is itself **pass 2**, not pass 1 as this checker previously implemented it (`structure.check` used
to run this fallback unconditionally). Oracle-confirmed (`gate1.CR6`-shaped scratch probe): a
`Public` with no name at all (line 1) followed by `Randomize(FooBar)` (line 6) reports only
`Variable not named.` -- the undefined-symbol error is completely suppressed, exactly like
`instructions.py`'s channel check already was.

`structure.py`'s `check` is split into `check_pass1` (block matching, declaration/array-dimension
errors, `Units`/`Const`/`Alias` truncation -- unconditional, unchanged from before) and
`check_pass2` (the "Expr"/`Call` fallback, moved out of `check_pass1`, now only invoked when
`check_pass1` found nothing). `check_pass2` also implements the before-`BeginProg` placement
follow-up this checker previously deliberately did not attempt (round 2's "before BeginProg"
section, above): a bare statement resolving to a declared `Sub`/`Function` is normally silent, but
`Call illegally occurs before BeginProg.` if it precedes `BeginProg`; the same resolving to a
built-in *function* is `Expr illegally occurs before BeginProg.` (no preceding "Invalid, or out of
place expression" line -- the call itself compiles fine, only its position is illegal); resolving
to nothing is the usual fallback line plus the same `Expr illegally occurs before BeginProg.`
follow-up. All three stop pass 2 entirely once emitted (oracle-confirmed, `gate2.CR6`: `foo bar`
before `BeginProg` suppresses a later, unrelated `zap zip`); an **explicit** `Call NoSuchSub` gets
neither the follow-up nor the stop, before `BeginProg` or not (oracle-confirmed, `bp2.CR6`).
`check_pass2` returns this stop line (or `None`) so `checker/__init__.py` can truncate every other
pass-2 module's diagnostics to it without those modules coordinating the cutoff themselves.

**Two regressions caught and fixed during this round's own development, both real false rejects on
this repository's own `crbasic/` programs and one `chm` file, all caught by re-running the full
corpus + `pytest` immediately after the first version of this change (this session's own
methodological lesson, echoing round 3's)**:

1. "Before `BeginProg`" only means the *top level* of the program, never a statement lexically
   inside a `Sub`/`Function` body -- a `Function`/`Sub` is routinely defined above `BeginProg`, and
   a bare, no-`Call`-keyword built-in-function call inside its body (`SerialOpen(...)` inside
   `Function LogDebug(...)`, `crbasic/Main.CR6`) is completely ordinary CRBasic; the body only
   executes when the `Sub`/`Function` is later invoked from inside `Scan`/`NextScan`. The first
   version of this change flagged it as a placement error, false-rejecting `crbasic/Main.CR6` and
   several siblings. Fixed by tracking `in_body_block` (a flat pass over the statement list,
   toggled on `S.SubDef`/`EndSub`/`EndFunction`, `syntax.py`'s block structure being flat) and
   suppressing the before-`BeginProg` check entirely while inside one.
2. The same shape recurs for CRBasic's other named, `EndXxx`-terminated body blocks conventionally
   declared before `BeginProg` -- found via the corpus, not a hand-written probe:
   `modemhangupendmodemhangup_1.CR6`'s bare `SerialOpen(...)` inside `ModemHangup...EndModemHangup`.
   `_BODY_BLOCK_STARTS`/`_BODY_BLOCK_ENDS` extends the same tracking to `DialSequence`/
   `ModemHangup` (corpus-evidenced) and, on the documented pattern rather than individual oracle
   probes, `Menu`/`SubMenu`/`Voice`/`BurstTrigger`/`ApplyAndRestartSequence`/`ProcHiPri` -- safe
   either way, since including one only ever *suppresses* a placement diagnostic, never adds one.

**Cross-module interaction, oracle-confirmed** (`checker/__init__.py`'s own tiering): `structure.
check_pass2` and `expressions.check` freely accumulate with *each other* (an `expressions.check`
finding on one statement does not suppress a `structure.check_pass2` finding on another --
`Randomize(FooBar)` followed by a separate bare `qux quux` reports both), but *either* of them
finding anything at all suppresses `instructions.check`/`instr_data.check_table_structure`/
`instr_measure.check` entirely (`ExciteV(Vx1,...)` on an earlier line suppresses a later, otherwise-
invalid `TCDiff` channel argument -- this is the same round-2 "two-pass compilation" finding,
generalised to include `expressions.check`/`structure.check_pass2` as the gate, not just "any
pass-1 error"). Modelled as two sequential `if not diags.errors:` tiers in `checker/__init__.py`,
tier B (`structure.check_pass2` + `expressions.check`, with the cutoff-line truncation) then tier C
(`instructions.check`/`instr_data`/`instr_measure`).

Full corpus difftest (`tools/difftest.py --logger <l>`, `repo`+`examples`+`chm` summed), before
this whole round (round 3's end state, re-measured at this round's own start) / after both pieces
above (the authoritative before/after for this round as a whole):

| Logger | before | after | false rejects |
|---|---|---|---|
| cr6 | 327/189/11 | 354/164/9 | 0 -> 0 |
| cr1000x | 338/178/11 | 363/155/9 | 1 (pre-existing `structuretype_3.CR6`) -> 1 (unchanged) |
| cr300 | 122/379/26 | 122/379/26 | 0 -> 0 (unaffected -- CR300/CR350 never shared the pass-model mechanism, and the expression-resolution piece's own corpus effect here nets to zero in the final measurement) |
| cr350 | 126/374/27 | 126/374/27 | 0 -> 0 (unaffected, same reason) |

(`repo`/`examples` groups, not shown separately: `repo` briefly regressed to 2/0/14 on cr6 and
1/7/8 on cr1000x during development, from regression 1 above -- re-verified back at 16/0/0 and
8/5/3, unchanged from every prior session, once both regressions were fixed; `examples` improved
from 0/2/2 to 2/2/0 on cr6 and 1/1/2 to 3/1/0 on cr1000x as a side effect, not chased on purpose.)

`python3 -m pytest -q`: 949 passed throughout (0 failed), after both fixes above. `tools/
fuzz_replay.py --workers 2`: no new `false_reject` (the two `still false_reject` entries and the
three `message_diff -> false_accept` entries seen this round are all pre-existing, independently
root-caused to a `Variable Type %s not supported` type-name-validation gap and the pre-existing
`structuretype_3.CR6` bare-member parsing gap -- neither in `expressions.py`/`symbols.py`/
`structure.py`/`checker/__init__.py`, confirmed by replaying the same reproducer against this
round's *starting* `expressions.py`/`symbols.py` and finding it already broken there too).

Not implemented: the other two placement-error shapes (`%s is declared in the middle of an
executing sequence`, `%s illegally occurs after EndSequence.`) -- no oracle evidence gathered this
round, left as a documented gap rather than guessed at.

## Round 5 (`structure.py`, `expressions.py`, `symbols.py`, `instr_rules.py`)

Fuzz-driven false-accept reduction (`tools/fuzz.py --logger cr6/cr1000x`), method: group saved
`tests/corpus/fuzz/<logger>/false_accept/` reproducers by the real compiler's own message (line
numbers/echoed text generalised), implement the most frequent groups, oracle-confirm each rule and
its pass-tier behaviour, re-verify the whole corpus + all four loggers + `fuzz_replay.py` after
every change (this round's own methodological lesson, twice -- see "Reverted" below). All probes
via `python3 tools/oracle.py <file> --no-cache` against `CR6Comp.exe`/`cr1000xcomp.exe` under Wine,
2026-09-26.

### 1. Assignment placement before `BeginProg` (`structure.py`, `expressions.py`)

The largest single group by far (~70 of the ~300-mutant `false_accept` count before this round):
an `Assignment` statement (`X = 1`) was not subject to *any* placement or target-resolution check
at all, unlike `Call`/`Unparsed` (`_check_call_pass2`, already implemented). Oracle sweep (a dozen
minimal probes, target declared/undeclared x RHS clean/undefined x before/after `BeginProg` x
inside/outside a `Sub` body) establishes the exact three-way split, mirroring `_check_call_pass2`'s
own shape:

- **Target does not resolve** (`FuzzVar = 1`; an array base or index that doesn't resolve,
  `X(BadIdx) = 1`, works the same way): `Invalid, or out of place expression: <verbatim statement
  text>.` always (`show_file=False`, same exception as the `Call`/`Unparsed` fallback), **plus** a
  before-`BeginProg` placement follow-up + stop, regardless of whether the RHS is *also* undefined
  (`FuzzVar1 = FuzzVar2`, both undeclared, still gives only the one "Invalid, or out of place"
  line, never a second "Invalid symbol in expression" for the RHS too).
- **Target resolves, RHS has an undefined name** (`X = FuzzVar`, `X` declared): silent here,
  **including when before `BeginProg`** -- oracle-confirmed the placement follow-up does *not*
  fire in this shape (`X = FuzzVar` alone before `BeginProg` gives only `expressions.check`'s own
  `Invalid symbol in expression: X = FuzzVar.`, no `Expr illegally occurs before BeginProg.` at
  all). Modelled by having `expressions.check`'s pre-existing assignment-RHS loop (`docs/loggers.md`
  "CR300/CR350 agreement round") skip a statement entirely when its own target doesn't resolve
  (`_undefined_names(stmt.target, known)`, reusing the same walk a read-context argument uses), and
  by having `structure.py`'s new placement check do nothing when the RHS doesn't resolve either --
  each defers to the other's own diagnostic for the two disjoint failure shapes, matching the real
  compiler's own "one message per statement" behaviour exactly.
- **Target resolves, RHS is clean**: normally silent, but `Expr illegally occurs before
  BeginProg.` (no preceding echo line, same shape as a resolved bare function call) and a stop when
  before `BeginProg`.

`_check_assignment_pass2` (`structure.py`) implements this, called from the same per-statement loop
`_check_call_pass2` already runs in, with the same `in_body_block`/`beginprog_line` inputs (an
assignment inside a `Sub`/`Function`/other body block is exempt the same way a bare call already
was). Fuzz false-accept rate (`tools/fuzz.py --logger cr6 --count 300 --seed 42`): 67 -> ~44 (this
item alone was measured mid-round at ~54; see the round's combined total below).

### 2. `ESSVariables`-implied variable names (`symbols.py`, new `ess_variable_names`)

Caught by the mandatory full-corpus + `fuzz_replay.py` re-run after item 1 landed, not a hand probe:
`ESSVariables Dim` implicitly declares a whole family of predefined NTCIP `ess...`-prefixed
variables (`essNtcipSiteDescription`, `essBatteryStatus`, ...) this project has no extracted table
for (unlike a `DataTable`'s own fields). `ESSVariables Dim` / `essNtcipSiteDescription = "Main
Street"` compiles on `CR6Comp.exe`, but the new assignment-target check (item 1) treated the name
as unresolved, a genuine false reject (`chm/essinitialize_1.CR6`-derived fuzz mutant). Fixed
conservatively: `symbols.ess_variable_names(program)` returns every `ess`-prefixed name the program
*itself* references, only when it declares `ESSVariables` somewhere -- no attempt to enumerate the
real, complete name list, so a genuine typo is silently accepted too (under-reporting, the safe
side of AGENTS.md's priority). Wired into `expressions._known_names`, the single shared `known`
computation both `structure.py`'s and `expressions.py`'s checks already use.

### 3. `DataInterval`/`Scan` fractional-interval divisibility exemption (`instr_rules.py`)

A **pre-existing bug**, found while re-measuring a clean baseline for this round (not caused by
this round's own work): `check_data_interval`'s "table interval must be an exact multiple of the
scan interval" check (`docs/instructions.md`) computes both sides in whole microseconds and applies
unconditionally -- but oracle sweeps (`DataInterval(0,1.1..2.9,Sec,10)` inside `Scan(1,Sec,...)`,
and `Scan(1.5,Sec,...)` against a whole-number `DataInterval`) show the real compiler silently
**exempts** the check entirely whenever *either* side's own raw declared value is not a whole
number, regardless of the resulting divisibility in microseconds (`DataInterval(0,1.5,Sec,10)`
inside `Scan(1,Sec,...)` compiles clean, on all loggers this project has a `DataInterval`-value
check for, despite `1.5` not being an integer multiple of `1`). A whole-number **float** literal
(`5.0`) still counts as an integer and the check still fires for it -- this is about the folded
*value*, not whether the source token has a decimal point. Fixed with a new
`_divisible_interval_us` wrapper (`_interval_us` plus a `value == int(value)` guard) used only by
the two `check_data_interval` call sites that feed the divisibility comparison; `check_scan_params`
and the other `check_data_interval` call site (the non-negative check) are untouched. Found via a
`tools/fuzz.py --logger cr1000x` false-reject saved reproducer this round's own clean-baseline
measurement surfaced (`chm/goesstatus_1.CR6`-derived), confirmed on CR6 too (not logger-specific).

### 4. Malformed `As` clause: array declarations are exempt (`parser.py`)

Another false reject found the same way (a `chm/imp_1.CR6`-derived fuzz mutant, CR1000X):
`structure.py`'s existing "Invalid Variable declaration %s." rule (`docs/checker.md` above,
`parser.py`'s `_parse_vardecl`) fires for *any* unexpected token right after a variable's own
name/dims -- but oracle-confirmed side by side, `Public Result as_Fz Boolean` (scalar, no dims)
correctly raises `Invalid Variable declaration as_Fz.`, while the *exact same* malformed token
after an **array** declaration, `Public Result(4) as_Fz Boolean`, compiles clean (`Result` left at
its default type, as if the `As`-clause were never there at all; a later variable in the same comma
list, `Public Result(4) as_Fz Boolean, Y As Long`, still parses `Y` normally). `_parse_vardecl` now
only raises `_ParseError` here when `dims` is empty; an array declaration instead silently discards
tokens up to the next comma/end of line, the same "post-`As`-clause discard" quirk this function
already implements for a *different* shape.

### 5. `Too many parameters:  %s.` (`instr_rules.py`, new `check_too_many`)

Documented as unimplemented since `docs/fuzz-findings.md` (phase 4's first pass): the single
largest remaining group this round (~28 of ~300 mutants), an argument list running *past* an
instruction's declared parameter count. Pass 1 (oracle-confirmed: fires alongside an earlier,
unrelated `Variable not named.`), mirroring `check`'s own shortfall check. The echo is the *next*
token by the real compiler's own flat, non-depth-aware tokenizer (`docs/type-codes.md` section 1's
`FUN_004ba440`-style delimiter set, `,:() =\n\t>`) starting at the first excess argument's own raw
position (`Instruction.arg_spans`) -- oracle-confirmed: `Battery(X,X,X)` (2 extra bare `X`s) echoes
only the *first* one (`Too many parameters:  X.`), and `PanelTemp(PTemp,15000,(1+2))` (one extra,
parenthesised argument) echoes `1+2`, its own leading `(` consumed as a delimiter.

**Deliberately restricted to a narrow, corpus-verified-safe set of type-code `kind`s**
(`_TOO_MANY_SAFE_KINDS`): a 740-accepted-file corpus safety scan (every `repo`/`chm` file
`tools/difftest.py` shows `their_exit == 0` for, all four loggers, parsed and checked against this
rule before it was wired in) found several instructions that would otherwise misfire, all sharing
one root cause -- their declared codes capture raw text that may itself contain embedded commas as
*one* slot (`Units X = Volts` is one code-6 argument to the real compiler, never comma-split, while
this checker's own generic `stmt.args` is always comma-split): `Alias` (631 hits), `Units` (534),
`MenuPick` (35), `ConstTable` (17) from the `identifier`/`deferred_text`/`raw_line_text`/
`expr_marker` kinds, plus four instructions whose extracted `params` is simply **empty**
(`Alias`/`Units`/`Data`/`EssVariables`/`ReadOnly` -- meaning this project's own table has no
per-code model for them at all, real custom parsing outside the generic loop this check models).
`_too_many_eligible` requires every declared code's `kind` to be in the safe set (or `none`) *and*
`params` to be non-empty; the safety scan found zero false hits with this restriction across all
740 files. `Call`/`Sub`/`Function`/`FOR` (codes 12/13/35/3, inherently variable-length) are excluded
defensively rather than corpus-evidenced: they parse to `S.Call`/`S.SubDef`/`S.For`, never reach a
plain `S.Instruction`'s `args` list at all.

### 6. `Scan`/`Delay` illegally occur before `BeginProg` (`structure.py`) -- tried broader, reverted to two instructions

Fuzzing also found `Scan(...)`/`Delay(...)` statements placed before `BeginProg` compile clean here
but real-reject with `Scan illegally occurs before BeginProg.`/`Delay illegally occurs before
BeginProg.` (single line, no preceding echo -- the same shape a resolved bare function call already
gets). These are *recognised* `S.Instruction`s, so `_check_call_pass2`'s `Call`-only placement
check never reaches them at all -- a new, third check type was needed.

**A broad version was tried and reverted, twice, both times caught only by a full
`tools/difftest.py --group chm` re-run, not a hand probe** (this round's own repeat of the "Round
3"/"pass model" sessions' own methodological finding):

1. **"Any recognised `Instruction` before `BeginProg` is illegal"**: a 740-accepted-file corpus
   scan (same technique as item 5) found this false immediately -- dozens of instructions
   legitimately appear before `BeginProg` in real, accepted programs: everything inside a
   `DataTable`/`ConstTable`/`StructureType` body (`Sample`/`Average`/`Maximum`/.../`FieldNames`),
   and program-wide directives (`PipeLineMode`/`SequentialMode`/`ReadOnly`/`StationName`/
   `TimedControl`/`CH400Status`/`SDMBeginPort`/...). A `runtime_fn is not None` heuristic (the
   extracted instruction table's own field) looked promising -- it agrees with `Scan`/`Delay` and
   with every instruction the scan found legitimate -- but `CH400Status`/`SDMBeginPort` are
   counter-examples (non-null `runtime_fn`, legitimately used before `BeginProg`), ruling it out as
   a safe discriminator. **Kept narrow instead**: `_FORBIDDEN_BEFORE_BEGINPROG = {"SCAN", "DELAY"}`,
   the two instructions with both a real oracle transcript and zero counter-examples in the scan;
   every other instruction is left alone.
2. **Suppressing the check inside `DataTable`/`ConstTable`/`StructureType`/`ObjectType` bodies, by
   adding them to the shared `_BODY_BLOCK_STARTS`/`_BODY_BLOCK_ENDS` set** `_check_call_pass2`/
   `_check_assignment_pass2` already use for `Sub`/`Function`/`DialSequence`/...: looked safe (a
   `DataTable` body's own field instructions are legitimately there) and even improved `cr1000x`
   `chm` exact by 4 files during development -- but a full `tools/difftest.py --logger cr6 --group
   chm` re-run caught a real regression, `tests/corpus/chm/solarposition_1.CR6` (`exact` ->
   `verdict`): a CHM prose-wrap artifact (`'must be` / `in units of seconds when used in
   SolarPosition` split across two physical lines with no `'` on the second) **inside** a
   `ConstTable...EndConstTable` body still gets the real compiler's own placement follow-up
   (`Expr illegally occurs before BeginProg.`), unlike the same shape inside a genuine `Sub`/
   `DialSequence` body. The distinguishing property: `Sub`/`DialSequence`/... bodies are *deferred*,
   invoked-later code, while `DataTable`/`ConstTable`/`StructureType` bodies compile immediately in
   place -- an unresolved statement inside one is exactly as illegal as one at the true top level.
   Reverted entirely (the cr1000x improvement was a coincidental side effect, not independent
   evidence the change was correct, and was given up along with the fix).
   
   A **second** fuzz-found false reject then showed the opposite gap for the *narrow* `Scan`/
   `Delay` rule specifically: `Delay(0,1,Sec)` inserted inside a `DataTable...EndTable` body
   (`chm/structuretype_2.CR6`-derived) real-compiles clean, but the reverted-to-narrow version still
   flagged it (no body-block suppression left at all after the full revert). Fixed with a third,
   independent tracking flag, `in_table` (`DataTable`/`EndTable` only, toggled in the same
   `check_pass2` loop), used *only* by the `_FORBIDDEN_BEFORE_BEGINPROG` branch -- deliberately
   **not** merged into the shared `in_body_block` flag `Call`/`Assignment` use, since item 2's own
   counter-example shows `DataTable` and `ConstTable` do not behave identically for *that* check
   either kind of container is involved in.

### Measured impact

`tools/fuzz.py --logger cr6/cr1000x --count 300`, false-accept count (the task's own headline
metric), before this round's first commit / after all six items above, `false_reject = 0`
throughout every intermediate and final measurement:

| Logger | seed | before | after |
|---|---|---|---|
| cr6 | 42 | 67 | 44 |
| cr6 | 43 | 59 | 32 |
| cr6 | 44 | 62 | 36 |
| cr1000x | 42 | 57 | 39 |

(The task brief's own stated baseline, 74/300 on `cr6` seed 42, is this project's state as of
"checker round 4"/before the later "CR300/CR350 agreement round" landed a few more fixes
between sessions; 67/300 is this round's own freshly re-measured starting point on the same seed,
already lower than the brief's figure for reasons independent of this round's own work.)

`tools/difftest.py --logger <l> --show 0` (`repo`+`examples`+`chm`), before / after, exact counts
never lowered, zero false rejects (`their_exit==0 and our_exit!=0`) throughout:

| Logger | chm exact before | chm exact after | repo exact |
|---|---|---|---|
| cr6 | 336 | 336 | 16/16 (unchanged) |
| cr1000x | 352 | 352 | 9/16 (unchanged) |
| cr300 | 135 | 135 (verdict 356->359, mismatch 16->13, item 5's own effect there) | 0/16 (unchanged) |
| cr350 | 139 | 139 (verdict 353->356, mismatch 15->12, same) | 0/16 (unchanged) |

`python3 -m pytest -q`: 963 passed throughout (0 failed; the concurrent `checker/unused.py`/
`tables.py`/`compiler.py` workstream's own new tests are included in this count, not this round's
own). `tools/fuzz_replay.py --workers 2` (708 reproducers by the round's end, up from 644 at the
start as fresh false-accept saves accumulated): 193 fixed outright (up from 0), 60 changed class,
**zero new `false_reject`** at every checkpoint -- including three genuine, this-round-found false
rejects (items 2/3/4's own root causes, none pre-existing) that were each caught by the mandatory
full-corpus + `fuzz_replay` re-run *before* being treated as done, fixed, and re-verified fixed; the
two `cr350` `false_reject` entries present throughout are the pre-existing, already-documented
`ESSInitialize`/`SNMPVariable`-shaped gap (`docs/fuzz-findings.md` "4. False reject"), unchanged.

### Top remaining `false_accept` groups (this round's own end state)

By re-running `fuzz_replay.py --workers 2` and grouping the still-reproducing `false_accept`
entries by the real compiler's own (generalised) message text, most frequent first: `Scan %duSec
invalid must be at least %dmSec.` (a sub-millisecond-granularity check, boundary not pinned down,
`docs/instructions.md` "Known gaps"); general expression-tree undefined-name gaps not yet reached
by `expressions.py`'s deliberately narrow scope (`Invalid symbol in expression`, e.g. a name nested
inside a `SplitStr`/`FileReadLine`-style argument); `Illegally dimensioned aliased name` (an
`Alias` target's own array-shape validation, not yet implemented); `Multiple BeginProgs.` (no
duplicate-`BeginProg`/duplicate-declaration check exists yet, `docs/fuzz-findings.md` "8."); `Need
closing )` (a parser-level unclosed-paren tolerance gap, `docs/frontend.md`); `DataTable ...
EndTable not matched.` (an unclosed `DataTable`, `docs/instructions.md`'s own documented,
not-implemented gap); `IF or SELECT statement illegally has SCAN without NEXT SCAN.` (a `Scan`
opened inside an `If`/`Select` body without its own matching `NextScan`, a new placement shape
along the lines of item 6 above, not chased this round); `Duplicate Alias`/`MenuRecompile must be
used within DisplayMenu..EndMenu.` (single-digit-count, instruction-specific gaps).

## Round 6 (`structure.py`, `symbols.py`, `instr_rules.py`, `instr_data.py`, `warnings.py`,
`checker/__init__.py`)

Continues round 5's fuzz-driven false-accept reduction, same method (group saved fuzz reproducers
by the real compiler's own message, oracle-confirm each shape's exact rule and pass-tier
behaviour with `tools/oracle.py <file> --no-cache` scratch probes, re-verify the whole corpus + all
four loggers + `fuzz_replay.py` after every change). All probes CR6 via the oracle harness, 2026-09-
26; the two `DataTable`-related messages were also cross-checked directly against `cr300comp.exe`
under Wine (`tools/oracle.py`'s own CLI always drives `CR6Comp.exe`) and found byte-identical, so
implemented logger-unconditionally.

**Corrected (2026-10-03, "Interval truncation")**: the "exemption" was really truncation. The
compared value is `int(value) * unit_us` (the same truncation `Scan` min-interval uses, see
below), on both the `DataInterval` and `Scan` side, all four loggers identical. `DataInterval(0,1.5,
Sec,10)` against `Scan(1,Min,0,0)` fires `DataTable interval 1000000 uSec not integrally divisible
by scan interval 60000000 uSec.` (it is clean against `Scan(1,Sec)` only because it truncates to
1 s); `1.5,Msec` -> `1000 uSec`; `1000.5,Msec` -> `1000000 uSec`; `Scan(3.5,Sec)` -> `scan interval
3000000 uSec`; `Scan(3000.7,Msec)` -> `3000000`; `1.5,Min` against `Scan(1,Min)` clean (60 s).
Truncating to 0 skips the check (`0.5,Sec`). `_divisible_interval_us` now truncates instead of
returning `None`. Evidence: `tests/fixtures/interval_truncation.json`.

### 1. `Scan %duSec invalid must be at least %dmSec.` / `Scans must be a multiple of %dmSec.`
(`instr_rules.check_scan_min_interval`)

The single largest remaining false-accept group (9 of a ~300-mutant fresh sample) and the one
`docs/instructions.md` "Investigated, not implemented" flagged as unresolved: "the multiple-of-1ms
message fires for some sub-millisecond `uSec`-unit values but not for numerically equal `mSec`-unit
fractional values ... smells like float-precision artifacts". Pinned down with a dozen fresh
`Interval` x `Units` probes: there is no float-precision subtlety at all -- the declared `Interval`
is truncated toward zero **in its own declared unit** (`int(value)`) *before* being multiplied by
that unit's microsecond factor, not the real fractional microsecond value:

```
Scan(0.5,mSec,...)   -> int(0.5)*1000    = 0    uSec -> Scan 0uSec invalid must be at least 1mSec.
Scan(0.0015,Sec,...) -> int(0.0015)*1e6  = 0    uSec -> same message
Scan(1.9999,mSec,..) -> int(1.9999)*1000 = 1000 uSec -> compiles clean
Scan(1500,uSec,...)  -> int(1500)*1      = 1500 uSec -> Scans must be a multiple of 1mSec.
Scan(1.5,mSec,...)   -> int(1.5)*1000    = 1000 uSec -> compiles clean (numerically equal to the
                                                          uSec probe just above, but not truncated
                                                          the same way -- this is the "asymmetry"
                                                          docs/instructions.md flagged)
```

This single truncate-then-multiply rule (`raw_us = int(value) * us_per_unit`, then `raw_us < 1000`
-> the first message, `raw_us % 1000 != 0` -> the second) reproduces every oracle probe with no
per-unit branch: after truncate-then-multiply, an `mSec`/`Sec`-declared interval is *always*
already an exact multiple of 1000, so the second message can structurally never fire for those two
units. Both checks run independently of `check_scan_params`'s own non-negative check on the same
`Interval` argument, not mutually exclusive with it -- oracle-confirmed `Scan(-1,mSec,...)` fires
*both* `Scan Interval parameter must be a non-negative integer` and `Scan -1000uSec invalid must be
at least 1mSec.`, on the same line, in that order. Ungated (called from `warnings.check_early`
alongside `check_scan_params`/`check_data_interval`), CR6/CR1000X only (no evidence either way for
CR300/CR350, which don't have `check_scan_params`'s own non-negative check either).

### 2. `_check_background_calibration` gating correction (`warnings.py`, `checker/__init__.py`)

Found as a side effect of oracle-probing item 1, not chased on purpose: `When Main Sequence Scan %d
is running, background calibration will be disabled.` had lived in `check_early` (the "ungated,
fires even when the file has other errors elsewhere" bucket) since it was implemented, and
`docs/instructions.md`'s own "Two ungated call sites" section made that claim for *every* check in
that file. A fresh sweep this round shows it is not true for this one specific warning: an unrelated
undefined-symbol error placed either before or after an otherwise-calibration-warning-triggering
`Scan(1,mSec,...)` in the same file suppresses the warning **entirely**, while the same probe
re-run with `_check_reachability`'s own trigger shape (`Delay` after an infinite `Scan`) still
fires alongside the error exactly as documented. A warning-only file (no errors at all) still gets
both warnings together, confirming the gate is specifically "any error", not "any diagnostic".
Moved `_check_background_calibration` (renamed `check_background_calibration`, public) out of
`check_early` to `checker/__init__.py`'s own final `if not diags.errors:` tier (alongside
`warnings.check`/`tables_hook.attach`, the same "only on an otherwise-clean compile" gate already
used there); `_check_reachability`/`_check_fnotch` stay in `check_early`, unchanged. Pre-existing
bug, not a regression from anything this round changed -- `git blame`-independent confirmation:
dozens of already-saved `message_diff`/`false_accept` fuzz reproducers from earlier rounds already
showed the exact spurious-extra-warning-alongside-a-real-error shape
(`tests/corpus/fuzz/cr6/message_diff/ba333c2ee146.CR6` and about a dozen siblings).

### 3. Block-balance summary checks suppressed by an earlier declaration error (`structure.py`)

Also found while oracle-probing item 4 below, and, like item 2, pre-existing rather than caused by
anything this round changed: `structure.check_pass1`'s three end-of-file summary checks (`No
BeginProg in Program.`, `Scan without NextScan.`, `IF or SELECT without EndIf or EndSelect.`) were
unconditional, but oracle-confirmed (`Public` with no name at all, alone in an otherwise beginprog-
less/scan-imbalanced file) the real compiler suppresses all three once *any* other pass-1 error
(`Variable not named.`/`Illegally dimension: ...`/`Need '=' in units definition: ...`, swept
independently) already fired earlier in the same pass -- a minimal one-line `Public` alone reports
only `Variable not named.`, never a second `No BeginProg in Program.` alongside it. Fixed with one
guard, `if diags.errors: return`, right before the three checks (`check_pass1` is always the first
thing `checker/__init__.py` runs, so `diags.errors` at that point only reflects this same
function's own loop).

### 4. `Illegally dimensioned aliased name` / `Alias is illegally dimensioned.` (`structure.py`,
new `symbols.declared_dim0_sizes`/`_check_alias_dims`)

The second-largest group (8 of the fresh sample). `Alias <name>(<index>) = <target>`'s `<index>` --
only the *first* index, when `<name>` is itself multi-dimensional -- must be a compile-time constant
in `[1, <name>'s own first declared dimension size>]`; a dozen probes pin down the exact shape:

- A **scalar** (`Public y`, no dimensions at all) behaves exactly like a size-1 array: `Alias y(1) =
  Ts` compiles, `Alias y(2) = Ts` fails -- so `declared_dim0_sizes` maps every declared `Public`/
  `Dim`/`Static` variable, including scalars (mapped to `1`), not just array ones.
- **Only the first index is ever checked**, however far out of range a later one is: `Public
  grid(3,3)` / `Alias grid(2,4) = Ts` (second index `4` > `3`) compiles clean, while `Alias
  grid(4,2) = Ts` (first index `4` > `3`) fails.
- **Pass 1, unconditional**: accumulates with an unrelated, later `Variable not named.` in the same
  file, and, being pass 1, suppresses every pass-2 check the same way any other pass-1 error already
  does (no special "stop the file" behaviour of its own).
- **Empty parens are a distinct shape** (`Alias Flag() = WriteMessage`, `Flag` a declared array):
  our parser already accepts an `Index` node with an empty `args` list as ordinary (`y()` alone as a
  plain expression compiles clean, unrelated to `Alias`), so this needed its own branch rather than
  falling out of the "index out of range" logic above. Oracle-confirmed by a saved fuzz reproducer
  (`tests/corpus/fuzz/cr6/false_accept/2b628e5fb8f1.CR6`, cross-checked with a from-scratch probe):
  **two** diagnostics together, `Need closing )` then `Alias is illegally dimensioned.` (a different
  message text from the out-of-range case, also no trailing period), both at the `Alias`
  statement's own line, only when `name` is itself a declared variable.

### 5. `Multiple BeginProgs.` (`structure.check_pass2`)

A genuine nesting-depth check, not "has `BeginProg` appeared more than once in the file": a
*second*, complete `BeginProg...EndProg` pair (the first one properly closed first) compiles clean,
while a `BeginProg` encountered before the previous one's own `EndProg` real-rejects at that second
`BeginProg`'s own line, with no preceding echo. Tier B (pass 2, gated behind pass 1 being clean, but
accumulates freely with an unrelated `expressions.check`/`structure.check_pass2` finding either
side of it -- oracle-confirmed both orderings); a *third* `BeginProg` nested inside the second gets
its own second `Multiple BeginProgs.` line too (one diagnostic per excess `BeginProg`). Tracked with
a plain `in_beginprog` boolean in `check_pass2`'s existing per-statement loop, toggled by
`BEGINPROG`/`ENDPROG`, alongside the pre-existing `in_body_block`/`in_table` bookkeeping there.

### 6. `IF or SELECT statement illegally has SCAN without NEXT SCAN.` (`structure.check_pass2`)

Not "an `If`/`Select` contains a `Scan`" (a `Scan...NextScan` pair fully closed *inside* the same
block compiles clean) but "a `Scan` opened while this block was open is still open -- no matching
`NextScan` yet -- when the block's own `EndIf`/`EndSelect` is reached": a genuine nesting-depth
*comparison*, not a syntactic "contains" check. Modelled as a stack, `cond_stack`, of the value
`scan_depth` (a second counter added to `check_pass2`, mirroring `check_pass1`'s own) had when each
currently-open `If`/`Select` was entered; that block's own `EndIf`/`EndSelect` pops its own
remembered value and compares it against the *current* `scan_depth` -- greater means an unclosed
`Scan` from inside. Applies to `Select` too (reported at `EndSelect`'s own line), and to `If`
regardless of how many `ElseIf`/`Else` segments sit in between (reported at `EndIf`'s own line
either way, oracle-confirmed both shapes). Tier B, same accumulation/gating evidence as item 5
(an earlier pass-1 `Variable not named.` suppresses it; an unrelated tier-B `Invalid symbol in
expression` either side of it does not). Not attempted: the related "`Scan` nested directly inside
another `Scan`" shape (`Scan without NextScan or nested Scans.`, oracle-confirmed real but a
distinct message, and not among this round's own top fresh-fuzz groups) -- left undocumented
territory rather than guessed at.

### 7. `DataTable ... EndTable not matched.` / `Could not find DataTable %s.` (`structure.py`,
new `instr_data.calltable_targets`/`check_unclosed_calltable`)

An unclosed `DataTable` (declared, no matching `EndTable` anywhere in the rest of the file) gives
one of **two** different messages depending on whether anything later references it by name, found
by a dozen oracle probes after the fuzz corpus turned up the bare-message shape:

- **Never referenced** (no `CallTable <name>` anywhere in the program): the bare, no-line
  `DataTable ... EndTable not matched.` (oracle-confirmed verbatim, including the literal `...`,
  and printed with no `line N:` prefix at all -- `diagnostics.py`'s existing `d.line == 0`
  convention already renders this correctly once emitted with `diags.error(0, ...)`).
- **Referenced** (`CallTable <name>` exists): the unclosed table never gets "finalised" into the
  real compiler's table registry, so the later reference fails exactly like a genuine typo would --
  `Could not find DataTable %s.` at the `CallTable`'s own line, not the `DataTable`'s. **Tier B, not
  pass 1** (oracle-confirmed: accumulates with an unrelated tier-B `Invalid symbol in expression`
  either side of it, unlike the bare message above, which an earlier pass-1 error suppresses the
  same way item 3's other three summary checks are).
- **Lower priority than the other summary checks**: a co-occurring `Scan without NextScan.`
  (unrelated to the table at all) reports only that, never the bare `DataTable` message too --
  oracle-confirmed with an unclosed table *and* an unbalanced `Scan` in the same file.
- Not attempted: a *second* `DataTable` opened while the first is still unclosed (`DataTable
  without EndTable.`, a third, distinct message at the second `DataTable`'s own line, oracle-
  confirmed real but a different shape `DataTable` blocks nesting was never evidenced for) -- this
  project's own `instr_data.py` already documents `DataTable`/`ConstTable` bodies as never nesting,
  so this shape is left alone rather than guessed at; `in_table`/`table_name` in `check_pass1`
  simply stay pointed at the *first* table if it happens.

Implementation split across the pass-1/tier-B boundary the same way item 7's own evidence requires:
`structure.check_pass1` tracks a single (non-nesting) `in_table`/`table_name` flag and, at end of
file, checks `instr_data.calltable_targets(program)` (every upper-case name any `CallTable`
anywhere refers to) to decide whether to emit the bare message itself; `instr_data.
check_unclosed_calltable` (new, called from `checker/__init__.py`'s existing tier-B block) does its
own, independent open/close scan and reports `Could not find DataTable %s.` at each `CallTable`
site whose target is open-but-never-closed.

### Measured impact

Fresh false-accept groups (session-local `tools/fuzz.py` runs, saved reproducers grouped by the
real compiler's own generalised message, most frequent first, before any of items 1-7):
`Scan %duSec invalid must be at least %dmSec.` (9), `Illegally dimensioned aliased name` (8),
general expression-tree `Invalid symbol in expression` gaps outside `expressions.py`'s scope (~13,
not chased this round -- see `docs/checker.md`'s existing "deliberately narrow" scope note),
`Need closing )` (6, item 4's own empty-parens `Alias` shape accounts for at least one sub-case),
`Multiple BeginProgs.` (5), `IF or SELECT statement illegally has SCAN without NEXT SCAN.` (5),
`Duplicate Alias` (4, not attempted this round), assorted `DataTable`/`Could not find DataTable`
shapes (~7 combined), `Scan Interval/BufferOption/Count parameter must be a non-negative integer`
(already implemented, unaffected).

`tools/fuzz.py --logger cr6/cr1000x --count 300`, false-accept count, before this round's first
commit / after items 1-7 above, `false_reject = 0` throughout every intermediate and final
measurement (one long-standing pre-existing exception, unchanged: `false_reject=1` on a fresh
`cr6` seed-46 run, root-caused to `AddPrecise`'s first argument legitimately accepting an
auto-declared name this project's `expressions.py` does not yet special-case -- found this round
during baseline re-measurement, not caused by anything in items 1-7, and not attempted, since
`AddPrecise`'s own argument-type rule has no oracle evidence gathered yet):

| Logger | seed | before | after |
|---|---|---|---|
| cr6 | 42 | 44 | 36 |
| cr6 | 43 | 32 | 29 |
| cr6 | 44 | 36 | 27 |
| cr1000x | 42 | 39 | 31 |

(This round ran on a repository shared live with a concurrent session's own "Round 6b" work on
`checker/cr3xx.py`/CR300/CR350, whose commits landed in the same working tree mid-round; the
before/after pairs above are this round's own freshly-measured start/end points on the same
machine, not adjusted for or attributable to that other session's unrelated changes, which its own
log entry accounts for separately.)

`tools/difftest.py --logger <l> --show 0` (`repo`+`examples`+`chm`), before / after, exact counts
never lowered, zero false rejects (`their_exit==0 and our_exit!=0`) throughout: cr6 chm exact 336
(unchanged), cr1000x chm exact 352 (unchanged), cr6/cr1000x repo 16/16 and 9/16 (unchanged). `python3
-m pytest -q`: 963 passed throughout (0 failed). `tools/fuzz_replay.py --workers 2/4` (930
reproducers across cr6+cr1000x by this round's end): several hundred fixed outright, dozens changed
class, **zero new `false_reject`** -- the one `cr6` `false_reject` present throughout is the
pre-existing `AddPrecise` gap above (confirmed pre-existing, not newly introduced, by replaying the
same reproducer against this round's own *starting* tree and finding it already broken there);
three pre-existing `cr6` and two pre-existing `cr1000x` `false_reject` entries (older, independently
root-caused saved reproducers, not `AddPrecise`) were themselves fixed outright by this round's own
changes and are not newly broken by anything else. A handful of `--workers 4`
parallel-run `hang` classifications (`opencrbasic did not return within 8s`) did **not** reproduce
at all when the same files were replayed individually with `--workers 1` immediately after --
resource contention with the concurrent CR300/CR350 fuzzing session on this shared machine, not a
real infinite loop in any of items 1-7's own code.

### Top remaining groups (this round's own end state)

`Duplicate Alias`, remaining `Need closing )` shapes beyond item 4's own empty-parens `Alias` case,
general expression-tree `Invalid symbol in expression` gaps nested inside argument shapes
`expressions.py` does not walk (deliberately out of that module's scope, unchanged), `DataTable
without EndTable.` (item 7's own explicitly-not-attempted nested-table shape), function
argument-count/type checks from `data/functions.json`'s own `argtypes`, and `Sub`/`Function` call
argument-count checks (neither attempted this round).

## Duplicate Alias (oracle-confirmed 2026-09-27)

`_check_alias_dims`'s checks 3 (`Duplicate Alias`) and 4 (`Duplicate Alias Name: %s.`) --
`structure.py`'s own docstring above has the full account of both, from the "Round 7" session that
implemented them -- are re-confirmed here with fresh scratch probes (a small task queued in
`PROGRESS.md`'s "Small-task queue"), since that round's own write-up of this item was never landed
(`git log` shows its commit as "Round 7 (partial, cut off by quota)"). Probes: `<logger>.exe t.cr6`
under Wine (`CR6Comp.exe`/`cr1000xcomp.exe`/`cr300comp.exe`/`cr350comp.exe`, no option flags), a
scratch dir, not committed.

- **Same source, two target names** (`Public X As Float` then `Alias X = Foo` / `Alias X = Bar`):
  plain `Duplicate Alias` (no name, no trailing period), at the *second* `Alias` statement's own
  line (3 here) -- identical wording and line on all four loggers.
- **Two sources, one reused target name** (`Public X`/`Public Y`, `Alias X = Foo` / `Alias Y =
  Foo`): `Duplicate Alias Name: Foo.`, again at the second statement's own line (4) -- identical on
  all four loggers.
- Both are **pass 1**: CR300/CR350, run against the same probe files (whose bodies also trip an
  unrelated `Too many parameters:  Sec.` on the `Scan` line, a pre-existing CR300/CR350 `Scan`-
  grammar difference, not part of this item), print the duplicate-alias line *and* that later line
  together -- confirming the two accumulate with other pass-1 errors rather than one suppressing
  the other, matching every other `_check_alias_dims` check's own already-documented pass-1 shape.
- **Boundary, deliberately not implemented**: aliasing to a name that is already a declared
  `Public`/`Dim` variable is oracle-confirmed to give a *third*, distinct message (`Y already
  declared as a variable.`), and aliasing to a declared `Const` a *fourth* (`K is already declared
  as a CONST.`) -- both real `FUN_004b8e70` rows neighbouring `Duplicate Alias Name: %s.` in
  `docs/messages.tsv`, but not `Duplicate Alias`/`Duplicate Alias Name` themselves, so out of this
  item's scope. This checker stays silent on both (under-reports, no false reject) rather than
  misreporting either as one of the two implemented checks
  (`tests/test_checker.py::test_alias_name_equal_to_declared_variable_is_a_different_message`).

Full-corpus difftest (`tools/difftest.py --logger <l> --show 0`, unaffected by this session -- no
production code changed, verification and tests only): cr6 exact 336 (chm) + 16 (repo) + 2
(examples); cr1000x 352 + 9 + 3; cr300 157 + 0 + 2; cr350 161 + 0 + 2; zero false rejects on all
four (`{r['file'] for r in json.load(open(f'difftest-{l}.json')) if r['their_exit'] == 0 and
r['our_exit'] != 0}` empty for every `l`). `python3 -m pytest -q`: all green, including two new
`tests/test_checker.py` cases for the two messages above and one boundary case.

## Need closing ) shapes (oracle-confirmed 2026-09-27)

A small task from `PROGRESS.md`'s "Small-task queue": which statement shapes report `Need closing
)`, `No Matching, or out of place ) in expression: %s.`, `%s is missing the ) character.`, and
`Incomplete expression: %s.`. All probes below: `WINEPREFIX=$HOME/.wine-cr6 WINEDEBUG=-all wine
<CR6Comp.exe|cr1000xcomp.exe|cr300comp.exe|cr350comp.exe> t.cr6` in a scratch dir, no option flags.

**`Need closing )` itself** was already fully accounted for before this item started -- Round 6's
own `Alias name() = target` empty-parens shape (`structure.py`'s `_check_alias_dims`, above). This
item's own oracle sweep found no *further* shape that reports that exact text; every other
unbalanced-paren shape probed reports one of the three messages below instead.

**Four distinct shapes, by where the unclosed/extra `)` sits, all identical on CR6 and CR1000X**:

1. **Instruction call, `Call`, or a bare (no-`Call`-keyword) call's own outermost parens**: still
   tolerant of running off the end of the line, unaffected -- `PortSet(1,1` / `Call Foo(1,2,3` /
   bare `Foo(1,2,3` (nothing further on the line) all compile, oracle-confirmed, the trailing `)`
   silently supplied right there (pre-existing `expect_close`, `docs/frontend.md`). This is *only*
   the outermost parens of that one call: a bare call's own **argument**, parsed through the
   general expression grammar, is not tolerant (case 2's own nested-call probe below).
2. **Assignment RHS, or any nested call reached through the general expression grammar (an
   argument to any call, a bare grouping `(expr)`, `If`'s condition)**, left open to end-of-line:
   `No Matching, or out of place ) in expression: %s.`, pass 2. `%s` = `stmt.text` (the whole
   statement's own verbatim source, same echo convention as `Invalid, or out of place expression`)
   for the shape this item implements (an assignment: `x = Sin(1` -> `  x = Sin(1.`; a bare
   grouping paren: `x = (1+2` -> `  x = (1+2.`) -- oracle-confirmed exact on CR6/CR1000X. A call
   nested inside a *bare* statement's own arguments (`Foo(Sin(1` alone, `Foo` undeclared) also
   fails this way, but the real compiler echoes a narrower substring than `stmt.text` there (just
   `Sin(1`, not the whole `Foo(Sin(1`) that this item's implementation does not reproduce (a
   `docs/checker.md`-documented `verdict`, not a false accept -- not attempted; the exact rule the
   real compiler uses to pick that narrower span was not pinned down). `If`/`Select Case`/`Const`
   also echo their own narrower value/condition text rather than the whole line (also not
   reproduced, same reason -- these three additionally interact with pre-existing parser code that
   already wraps a failure in its own, differently-shaped `Unparsed`, e.g. `Const k = Sin(1`
   through this session's own change reports `Const with no assignment.` instead, itself a
   pre-existing, unrelated mismatch, not a new regression). CR300/CR350 report the same message and
   `%s`, but with the pre-existing, separately-tracked "echo quirk" (`PROGRESS.md`'s own queue
   item): the raw line's own trailing newline is echoed literally, so the closing `.` lands on its
   own line (`  x = Sin(1\n.`) -- not reproduced by this item either (out of its own scope).
3. **`Public`/`Dim`/`Static` dimension list**, left open to end-of-line: `%s is missing the )
   character.`, pass 1 (same `FUN_004baf00` family as `Illegally dimension: %s`/`Variable not
   named.`). `%s` = the variable's own name through the rest of the physical line, verbatim,
   *not* `stmt.text` and *not* including the `Public`/`Dim` keyword -- `Public x(5` ->
   `x(5 is missing the ) character.`; `Dim y(4` -> `y(4 is missing the ) character.`; multiple
   dimensions no different, `Public x(5,3` -> `x(5,3 is missing the ) character.` (all four loggers
   identical). Column-sliced from the segment's own raw text (`Token.col` indexes straight into it,
   oracle-confirmed exact). Only this exact run-off-the-end-of-line shape: a *non-EOL* malformed
   dimension list (`Public x(5 Then`) instead reports `x(5 Then is missing the ) character.`
   *plus* a second, unrelated `Invalid symbol in expression: 5 Then.` -- a different, more involved
   shape (the real compiler's own dimension-list scanner keeps consuming to end of line rather than
   stopping at the first bad token) this item leaves alone (pre-existing `expect_op` parse error,
   unaffected either way, `verdict` not a false accept). A **`Sub`/`Function`'s own parameter
   list**, unlike a declaration's dimension list, stays tolerant of end-of-line the same way an
   instruction call does (`Sub Foo(a,b` alone compiles, oracle-confirmed) -- not changed.
4. **Extra/unmatched `)`** after an otherwise-complete assignment (`x = 1+2))`): `Incomplete
   expression: %s.`, `%s` = `stmt.text` (the whole statement) -- oracle-confirmed exact on CR6/
   CR1000X (`  x = 1+2)).`), CR300/CR350 again with the same trailing-newline echo quirk as case 2,
   not reproduced. The pre-existing parser already turned this shape into `Unparsed(reason=
   "unexpected trailing input starting at ')'")` (used, unmodified, by a different, older `+=`
   corpus finding); this item only added the message-text branch for it. An extra `)` after a bare
   *instruction* call (`PortSet(1,1))`) is a separate, pre-existing mismatch (real: `Invalid port 1,
   ...`, i.e. the extra paren is itself silently tolerated for an instruction the same way a
   missing one is) that this item does not fix -- unaffected either way, not a new regression.

**Implementation** (`parser.py`, `structure.py`; `checker/expressions.py` untouched, owned by a
concurrent session): `_parse_arglist` and `_parse_atom`'s bare-grouping-paren case gained a
`strict: bool = True` parameter -- `False` only at the three lenient call sites (an instruction's
own argument list, `Call`'s own argument list, and `_parse_assignment_or_call`'s own initial
bare-call-or-lvalue atom parse); `strict=True` (every other caller, i.e. anything reached through
`_parse_expr`) raises a distinguishable `_ParseError` (`parser.UNCLOSED_PAREN_EXPR`) instead of
silently completing at end-of-line. `_parse_vardecl` gained `_close_dims_or_raise`, a narrower,
EOL-only check ahead of its own two `expect_close(")")` calls, raising `parser.UNCLOSED_DIM_PREFIX
+ <name-through-eol>` (needs the segment's own raw text, now threaded onto `_TokStream.seg_text`
at construction). `structure.py` imports these two markers from `parser` (no re-typed string,
verified no circular import: `parser.py` does not import `checker.*`) and adds three branches:
`_UNCLOSED_DIM_PREFIX` in `_check_unparsed_pass1` (pass 1, `%s is missing the ) character.`,
skipped by `check_pass2`'s own Unparsed branch the same way `Units`/`Const`/`Alias`-truncation
already are); `_UNCLOSED_PAREN_EXPR` and the pre-existing `_EXTRA_CLOSE_PAREN` reason in
`check_pass2`'s Unparsed branch (pass 2, `No Matching, or out of place )`/`Incomplete expression`
in place of the generic `Invalid, or out of place expression` fallback).

New tests: `tests/test_checker.py`, eight cases (unclosed assignment RHS, unclosed bare grouping
paren, unclosed instruction/bare-call still compiling, extra close paren, unclosed declaration
dimension single/multi-dim, unclosed `Sub` parameter list still compiling).

Full-corpus difftest, before this item / after (`tools/difftest.py --logger <l> --show 0`): no
change on any of the 507+16+4 `chm`/`repo`/`examples` files per logger (this item's four shapes are
fuzz-only syntax errors, not naturally occurring in either corpus) -- cr6 336+16+2, cr1000x
352+9+3, cr300 157+0+2, cr350 161+0+2, all unchanged; zero false rejects on all four throughout.
`python3 -m pytest -q`: 974 passed (966 before this item's own 8 new cases), 311 skipped, 0 failed.
One same-session run briefly showed a `cr350`-only false reject on four files (`SerialOpen`'s own
6th, optional argument, `examples/SBD_example_hirise*.CR6` and two `chm` files); re-running moments
later (no changes on either side of this item) already showed it gone, and `git stash`-isolating
this item's own changes at that same moment (concurrent session's `checker/expressions.py`/
`checker/__init__.py` work left in place) also showed it gone -- both point to a transient state in
the concurrent session's own in-flight `check_function_arity` work (`SERIALOPEN`'s `nargs=6` vs. a
5-argument call, oracle-confirmed the real compiler accepts) coinciding with this item's own
measurement, not an interaction this item's own changes caused; recorded here only so it is not
mistaken for a regression, and re-confirmed clean (see below) before this item was called done.

## Built-in function call arity (oracle-confirmed 2026-09-27, `checker/expressions.py`)

### Special parser-first function diagnostics (oracle-confirmed 2026-10-03)

The ordinary built-in arity messages do not cover every boundary. Across all four compilers,
strings in `SerialOpen` positions 4 or 5 and `Timer` position 2 produce `Parameter must be a
constant integer: <whole statement>.`; the same message occurs for a fourth string argument to
`TCPOpen`. `Abs` with a second argument and `MQTTPublish` with a fifth instead produce `Incomplete
expression: <whole statement>.`. CR300/CR350 put the period on a new line. `SerialFlush` keeps the
ordinary `No Matching, or out of place )` diagnostic, including in a one-line `If` body.

`WindVector`'s eight-argument form is accepted in a `DataTable`; a ninth argument yields `Too many
parameters:`. A normal dotted `SNMPVariable` OID remains accepted, but whitespace immediately
after a dotted OID segment resumes parsing at the type token and yields `Too many parameters:` for
that token. An OID used as the destination is separately invalid. These parser recovery cases are
implemented narrowly in `instr_rules.py`; fixture evidence is `tests/fixtures/function_specials.json`.

`Expression expects , : %s.` (too few arguments) and `No Matching, or out of place ) in
expression: %s.` (too many) for a call to a `data/functions.json` built-in *function* nested
anywhere inside an expression -- as distinct from an *instruction*'s own required/excess-parameter
checks (`Invalid or missing parameter.`/`Too many parameters:  %s.`, `instr_rules.py`, already
implemented) and from a user-declared `Sub`/`Function` call (a separate, not-yet-implemented
`PROGRESS.md` item, `Call to %s with %d parameters, requires %d arguments.`). All probes via a
scratch `.CR6` under `/tmp/claude-0/oracle/` against `CR6Comp.exe`/`cr1000xcomp.exe`/
`cr300comp.exe`/`cr350comp.exe` directly under Wine (no `tools/oracle.py`, to batch dozens of
independent probes into one compile -- pass 1's own "every error in the file, in line order"
behaviour makes this safe, confirmed below).

**Pass tier**: genuinely pass 1, not pass 2 despite superficially resembling the undefined-
symbol/constant-only checks in this same file (both of which are pass 2, gated behind `if not
diags.errors:` in `checker/__init__.py`). Two oracle probes settle it:

```
Public              (nameless -- an ordinary pass-1 "Variable not named." on line 1)
Public X As Float
BeginProg
  Scan(1,Sec,0,0)
    Round(X,1,2)     ' too many
  NextScan
EndProg
-> line 1: Variable not named.
   line 6: No Matching, or out of place ) in expression:     Round(X,1,2).
   (fires alongside an unrelated, earlier pass-1 error -- not suppressed)

Public X As Float
BeginProg
  Scan(1,Sec,0,0)
    Randomize(BazQux)   ' an ordinary pass-2 "Invalid symbol in expression", on its own
    X = Round(X)         ' too few
  NextScan
EndProg
-> line 5: Expression expects , :     X = Round(X).
   (line 4's own undefined-symbol finding is completely suppressed -- pass 1 found something
   anywhere in the file, so pass 2 never runs at all, same architecture as `instr_rules.py`)
```

Wired into `checker/__init__.py` as `expressions.check_function_arity`, unconditional, alongside
`instr_rules.check`/`check_too_many` (not inside the gated `expressions.check` in this same file).

**Echo shape depends on the containing statement**, matching this file's own existing echo
conventions rather than a new one:

- An `Instruction`'s own argument (`Randomize(Round(X))`, `Randomize` a real instruction): the
  *argument's own verbatim span* (`_arg_text`, already used by `_report_statement` above) --
  `Randomize(Round(X))` -> echoes `Round(X)`, not the whole statement, even though `Round(X)` is
  `Randomize`'s only argument. Nesting deeper doesn't change this: `Randomize(1 + Round(X))` ->
  echoes `1 + Round(X)`, the whole argument, not just the inner call.
- A bare `Call` (a built-in function used for its own side effect, or the whole statement being
  the call itself, `Round(Number,1,2)` alone) or a call *nested inside one of a `Call`'s own
  arguments* (`WatchdogTimer(0,Round(X))`, `WatchdogTimer` itself fine, `Round(X)` nested inside its
  2nd argument): the *whole statement's own verbatim text* (`stmt.text`) either way -- `Call` has no
  per-argument span to prefer, and the oracle uses the whole-statement span even for the
  nested-inside-an-argument case, unlike the `Instruction` case above.
- An `Assignment`'s RHS (`X = Round(X)`): the whole statement's own verbatim text, target and `=`
  included -- the same convention this file's `check` already uses for its own assignment-RHS
  undefined-symbol echo (`docs/checker.md` "Round 5").

**At most one diagnostic per statement**, oracle-confirmed both across two independent
`Instruction` argument positions and within one `Call`'s own argument list:

```
WriteIO(1,Round(X))              -> Expression expects , : Round(X).             (position 1 only)
WriteIO(Round(X),Round(Y))       -> Expression expects , : Round(X).             (position 0 only;
                                     position 1's own, independent problem is never additionally
                                     reported)
```
`check_function_arity` models this as "first argument position, ascending, plain textual order"
for an `Instruction` (**not** `_report_statement`'s own constant-family reordering -- arity is a
raw parse-time concern, not the semantic-family quirk that check is about; not independently
oracle-swept for a *textually-later* const-family position racing a *textually-earlier* general
one, since constructing a probe for that shape crashes the real compiler under Wine on every
instruction tried, `CardTest`/`CallTable`, "Known remaining gaps" below).

**`nargs` is not a safe universal minimum or maximum** (`checker/expressions.py`'s own
`_ARITY_MIN_SAFE`/`_ARITY_MAX_SAFE` docstring has the full account): a two-part oracle sweep --
every `data/functions.json` entry with a decoded `argtypes` list (92 functions), then every entry
with `argtypes: null` (31 more, the uniform-single-float-argument math functions) -- each tried at
`nargs-1` and `nargs+1` arguments, batched into a handful of scratch `.CR6` files (pass 1
accumulates every error in one file, so one compile confirms dozens of functions at once) found
real exceptions on both sides:

- `NetworkTimeProtocol`/`PingIP`/`HttpPost`/`HttpPut`/`Sprintf`/`Date` all accept *fewer* than
  `nargs` (an undocumented optional trailing argument each).
- `SerialOpen` accepts `nargs+1` (a genuine, undocumented 6th argument: `SerialOpen(ComC1,9600,0,0,
  1000,1)` compiles clean on CR6/CR1000X). `Sprintf` is genuinely variadic (2..9 arguments all
  compile clean; 10 hits `Expression nesting level exceeded, break up expression.`, unrelated to
  arity).
- `Abs`/`Ave`/`Timer` get a *different* message at the boundary this rule would otherwise flag
  (`Incomplete expression: %s.`/`Parameter must be a constant integer: %s.` -- their own later
  arguments are constant-integer-checked before arity would matter), so neither direction is
  modelled for them.
- A one-argument-short call to a zero- or one-argument function (`nargs` 0 or 1) is a *different*,
  inconsistent message family at zero arguments (most often the ordinary bare-call fallback,
  `Invalid, or out of place expression: %s.`; sometimes `Incomplete expression: %s.`; a few, e.g.
  `Sprintf()`/`PppOpen(1)`, silently accepted) -- the "too few" side is not modelled for any
  `nargs`-0/1 function at all, only "too many" where directly confirmed clean (most single-float
  math functions, plus `Rnd`/`ClockChange` at `nargs=0`).
- Argument-*kind* mismatches are **not implemented**: swapping a string literal where `argtypes`
  says a float/int expression and vice versa (`Round("x",1)`, `Left(1,2)`, `Mid(1,2,3)`,
  `Replace(1,"x","y")`, `InStr("x","x","x",1.5)`) all compile clean -- `argtypes`'s 0/1/2/3 codes
  evidently only select run-time coercion, not a compile-time type check, so there is no "wrong
  kind" diagnostic to reproduce.

Both directions are therefore an explicit, oracle-verified per-function allow-list
(`_ARITY_MIN_SAFE`/`_ARITY_MAX_SAFE`, `checker/expressions.py`) rather than a blanket rule over
every function with a decoded `argtypes`.

**Cross-logger data divergence, a false reject caught by the full-corpus gate**: `_ARITY_MIN_SAFE`/
`_ARITY_MAX_SAFE` were first written as plain name sets, calibrated against the default (`cr6`/
`cr1000x`, byte-identical) `functions.json`. `tools/difftest.py --logger cr350` immediately caught
a real false reject: CR350's own `functions.json` gives `SerialOpen` `nargs=6` (its own table
already bakes in the 6th argument CR6/CR1000X's table omits), so a name-only allow-list applied
`SerialOpen` to `_ARITY_MIN_SAFE` using *CR350's* `nargs` and rejected the ordinary 5-argument form
(`tests/corpus/chm/serialflush_1.CR6`, both `examples/SBD_example_hirise*.CR6`). `MqttSubscribe`/
`NetworkTimeProtocol` have the same shape (a smaller `nargs` on CR300/CR350 than CR6/CR1000X) with
no oracle evidence either way for whether their own true min/max still tracks it the way
`SerialOpen`'s doesn't. Fixed generally rather than per-function: both maps are `{name: nargs}`,
the exact `nargs` value this sweep tested, and `_call_arity_problem` only fires when the profile's
own `nargs` for that name still equals it -- a profile whose own table disagrees is silently left
unchecked for that one name, which is why CR300/CR350 lose coverage for those three names without
needing a special case. Re-verified clean afterward: zero false rejects on all four loggers,
`tools/difftest.py --logger <l> --show 0`.

`NetworkTimeProtocol` now has its observed bounds (oracle, 2026-10-03, all four loggers,
`tests/fixtures/ntp_arity.json`): 3..4 arguments on CR6/CR1000X (table `nargs` 5; a 5th argument
is rejected), exactly 3 on CR300/CR350 (table `nargs` 3). `_ARITY_RANGE` holds these, keyed by
the profile's `nargs` like the two maps above.

**CR1000X**: the same 92-function sweep re-run against `cr1000xcomp.exe` (both the `nargs-1` and
`nargs+1` batches) matched CR6 byte-for-byte on every one of the 92 functions' own two probes
except one unrelated line (`CheckPort`'s own port-range message, `Port must be C1..C4 or U1..U12.`
vs. `Port must be C1..C8.` -- a different, not-yet-implemented check, nothing to do with arity).

**CR300/CR350**: spot-checked on `Round`/`Instr` only (not the full 92-function sweep), both
directions. Behaviour matches CR6/CR1000X exactly except one formatting difference: the echoed
text is followed by a literal embedded newline, with the closing `.` alone on the next line,
where CR6/CR1000X put it immediately after the text on the same line --

```
CR6/CR1000X: line 6: Expression expects , :     X=Round(X).
CR300/CR350: line 6: Expression expects , :     X=Round(X)
             .
```
(raw bytes, `cat -A`: `...X=Round(X)^M$` then `.^M^M$` on CR300/CR350; one line, no embedded
newline, on CR6/CR1000X). Modelled as `_ARITY_TRAILING_NEWLINE_LOGGERS` (`{"cr300","cr350"}`),
reusing this file's own pre-existing per-logger split (`_WHOLE_EXPR_ECHO_LOGGERS`'s complement) --
not merged into that set directly since the two sets exist for conceptually different oracle
findings, even though they happen to be the same two loggers on each side for this rule too.

**Measured impact**: zero effect on `tests/corpus/{repo,examples,chm}` (`tools/difftest.py
--logger <l> --show 0`, before this item / after, all four loggers): `cr6` 336/162/9 (unchanged),
`cr1000x` 352/149/6 (unchanged), `cr300` 157/339/11 (unchanged), `cr350` 161/336/10 (unchanged) --
none of this corpus's own accepted-or-rejected examples happens to call a built-in function with
the wrong argument count, so this item's own coverage is exercised entirely by
`tests/test_function_arity.py` (16 new cases: too few/too many in an assignment, a bare call, and
nested inside a real instruction's own argument; the per-statement-once rule; the pass-1
tier-interaction probes above as unit tests; the `SerialOpen`/`Sprintf` never-reject guards; the
CR350/CR1000X message-shape checks) rather than corpus movement. `uv run pytest -q`: 990 passed (16
more than this item's own starting point), 311 skipped, 0 failed throughout. Zero false rejects on
all four loggers at every checkpoint, `tools/difftest.py --logger <l> --show 0`.

**Known remaining gaps, not chased further this session**: whether the constant-family reordering
`_report_statement` uses for a *different* check also applies to this rule when two independent
bad-arity positions race textually (no safe oracle probe found -- every instruction tried with two
simultaneously-bad-arity nested calls, `CardTest(Round(X),Round(X))`/`CallTable(Round(X))`, crashes
the real compiler under Wine instead of producing text, unrelated to this rule); a doubly-nested
bad-arity call (both an outer and an inner call wrong in the same argument) is not oracle-tested
either way, left as this rule's own documented "outer first" order; `Const`'s own value expression
is not checked for this (mirroring `check_consts`'s own narrow scope, no corpus signal either way);
an `Assignment`'s *target* is not checked (a function call is never a valid assignment target).

## DataTable/EndTable pairing (oracle-confirmed 2026-09-27)

A small task from `PROGRESS.md`'s "Small-task queue": which shapes give `DataTable without
EndTable.` and `EndTable without DataTable.` (`docs/messages.tsv` also lists a third string from
the same decompiled function, `EndTable without DataTable or nested DataTables.` -- no probe below
reproduced it; see "not reached" below). All probes: a scratch dir, `WINEPREFIX=$HOME/.wine-cr6
WINEDEBUG=-all wine <CR6Comp.exe|cr1000xcomp.exe|cr300comp.exe> t.cr6`, no option flags. Every shape
below is byte-identical (message text and line) on CR6, CR1000X and CR300.

- **A second `DataTable` opened while the first is still open** (`DataTable (A,...)` /
  `DataTable (B,...)` with no `EndTable` between them): `DataTable without EndTable.`, no `%s`, at
  the *second* `DataTable`'s own line -- fires unconditionally at the moment of nesting, regardless
  of what follows. Confirmed with two variants that differ only after that line: exactly two
  matching `EndTable`s (both tables fully, correctly closed and `CallTable`d) and three `EndTable`s
  (one extra beyond the pair) give the *identical* single-line result -- the extra `EndTable` adds
  no second diagnostic, so the compiler stops at the nesting error rather than continuing to track
  a stack of still-open tables.
- **A bare/orphan `EndTable`, no open `DataTable`**: `EndTable without DataTable.`, at the
  `EndTable`'s own line. Confirmed with two variants: no `DataTable` anywhere in the file at all
  (line 2, the file's first statement), and an extra `EndTable` immediately after a single,
  properly closed and `CallTable`d table (line 5) -- both give this same message, matching
  `checker/instr_data.py`'s `check_table_structure` docstring, which already implements and
  oracle-confirmed this shape in an earlier round.
- **Not reached**: `EndTable without DataTable or nested DataTables.` -- every shape probed above,
  including the nested-then-extra-`EndTable` variant (a natural place to expect the "nested"
  wording), reports the plain `EndTable without DataTable.` text instead. No further shape (`#If`-
  conditional `DataTable` declarations, deeper nesting) was probed this round; left as this item's
  own boundary rather than guessed at. `docs/messages.tsv`'s two `EndTable`-family strings share one
  decompiled function (`FUN_004f1af0`), so this may be dead text from an unreachable branch rather
  than a distinct oracle-visible shape.

Implementation: `structure.py`'s `check_pass1` already tracked a single (non-nesting) `in_table`/
`table_name` flag plus `nested_table_line` from an earlier, undocumented round (git history:
"Round 7 (partial, cut off by quota)"); this item re-confirmed both messages with the fresh probes
above and added the missing unit tests
(`tests/test_checker.py::test_nested_datatable_reports_datatable_without_endtable`,
`test_nested_datatable_extra_endtable_reports_only_one_error`,
`test_orphan_endtable_with_no_datatable_at_all`,
`test_extra_endtable_after_a_properly_closed_table`) and this write-up; no production code changed.

Full-corpus difftest (`tools/difftest.py --logger <l> --show 0`), unaffected by this session (no
production code changed, tests and docs only): cr6 exact 336, cr1000x 352, cr300 157, cr350 161;
zero false rejects on all four loggers. `uv run pytest -q`: all green, including the four new cases
above.

## Missing-parameter followup (oracle-confirmed 2026-09-27, `checker/structure.py`)

A small task from `PROGRESS.md`'s "Small-task queue": 34 `chm` `verdict` cases (`tools/
difftest.py --logger cr6 --show 0`) had the real compiler print one further line past our own
output's own last line, always `Invalid, or out of place expression: %s.`. Every one is the same
shape: an instruction's required-parameter prefix runs past the end of its own physical line with
no `_` continuation (`instr_rules.check`'s existing `Invalid or missing parameter.`, e.g.
`average_1.CR6`: `Average` alone, `(1,TCTemp,FP2,False)` on the next line) -- and the physical line
the wrapped arguments spill onto, which our parser already records as its own `S.Unparsed`
statement (it starts mid-argument-list: `(...)`/a bare `,...)`), gets its own `Invalid, or out of
place expression: %s.` line in the real compiler, `%s` the leftover's own verbatim text.

The surprise: this specific diagnostic is **not gated** the way `structure.check_pass2`'s generic
bare-statement fallback is (`checker/__init__.py`'s `if not diags.errors:`) -- two scratch probes
under Wine (`t.cr6`: nameless `Public` + unrelated `foo bar baz`; `t2.cr6`: same nameless `Public`
+ `Scan` / `(1,Sec,3,0)`) show the *generic* fallback fully suppressed by an earlier, unrelated
`Variable not named.` (`foo bar baz` reported nothing), while *this* leftover-continuation
diagnostic still fires right alongside it (`t2.cr6`: `Variable not named.` + `Invalid or missing
parameter.` + `Invalid, or out of place expression: (1,Sec,3,0).`, all three), matching
`cdmresistance_1.CR6`'s own real-compiler output (a `Variable not named.` earlier in the file does
not suppress it either).

A first version keyed only to "instruction shortfall, next statement `Unparsed`" over-fired: a
full-corpus difftest sweep (not just the 34 target files) found `getdatarecord_3.CR6` -- already
`exact`, no follow-up in the real output -- shares the identical shape (`GetDataRecord` / a fully
self-contained `(ResultTD,...)` leftover) with `getdatarecord_1.CR6`, which *does* get one. Bisection
under Wine (`tG`..`tK.cr6`, this session's own scratch dir) isolated the difference to a single
name: `getdatarecord_3.CR6`'s `ResultTD` is only ever the stranded name after an earlier nameless
`Public` (`Variable not named.`'s own shape, `_check_declaration` below) and was never actually
registered as declared; adding a real `Public ResultTD` to an otherwise-identical, otherwise-still-
undeclared probe flips the followup on, while adding an unrelated `Public Foo` (declaring a name
the leftover never references) does not. `cdmresistance_1.CR6`'s own two leftovers then narrowed
this further, from "every name in the leftover must resolve" to "only its first identifier": its
first leftover (`Tref,1 ,1,60)`, `Tref` undeclared and leading) gets no followup, but its second
(`Tblk1, 1,mV5000,1,TypeT,Tref,0,0,60,1.8,32 )`, `Tblk1` declared and leading, the same undeclared
`Tref` appearing later) does -- oracle-confirmed real output, both in the corpus file itself and
reproduced standalone.

Implemented as `structure.check_missing_param_followup` (new), called unconditionally from
`checker/__init__.py` right after `instr_rules.check` (same ungated tier, since it fires *because*
that call found something): re-walks `program.statements` for the same shortfall shape
`instr_rules.check` detects (reusing its `_real_params`/`_required_count`/`_TOLERANT_CODES`), and
for the immediately-following `S.Unparsed` statement, tokenises its own text and checks only the
first `IDENT` token found (`_leftover_first_name_known`) against `expressions._known_names` --
`True` (fires) when there is no identifier at all (`cosh_1.CR6`'s leftover is pure literals/
punctuation). Skips the same `Unparsed.reason` classes `check_pass2` already special-cases (a known
parser gap, an empty statement, or one `check_pass1` already reported for) so the two paths never
double-report the same line.

Full-corpus difftest (`tools/difftest.py --logger <l> --show 0`), before -> after this session:

| Logger | chm exact before | chm exact after | false rejects |
|---|---|---|---|
| cr6 | 338 | 372 | 0 -> 0 |
| cr1000x | 354 | 388 | 0 -> 0 (pre-existing `structuretype_3.CR6` false reject unrelated to this change, unaffected) |
| cr300 | 157 | 157 | 0 -> 0 (unaffected -- no corpus case hits this shape on CR300) |
| cr350 | 161 | 161 | 0 -> 0 (unaffected, same reason) |

`uv run pytest -q`: 1042 passed (0 failed), up from 1039 -- three new cases in `tests/
test_checker.py` (`test_missing_parameter_followup_reports_leftover_continuation_line`,
`test_missing_parameter_followup_suppressed_when_first_name_undeclared`,
`test_missing_parameter_followup_checks_only_the_first_name`).

## `Too few parameters in instruction.` vs `Invalid or missing parameter.` (oracle-confirmed 2026-09-27, `instr_rules.py`)

Another small task from the queue: 13 `chm` `verdict` cases had `instr_rules.check`'s shortfall
message (`Invalid or missing parameter.`, see "Required parameter missing" above) where the real
compiler instead prints `Too few parameters in instruction.` -- `docs/type-codes.md` section 4's
own open question about this message's trigger, previously unresolved ("every multi-parameter
instruction tried ... hit a more specific check first").

Four minimal oracle probes (bare instruction, no arguments, scratch dir) pinned it to the missing
parameter's own `type_codes.json` `kind`:

```
Battery                 (code 8, variable_or_array) -> Invalid or missing parameter.
Average    (in a DataTable) (code 1, const_expr)    -> Invalid or missing parameter.
CallTable                (code 6, identifier)       -> Too few parameters in instruction.
Histogram  (in a DataTable) (code 11, deferred_text) -> Too few parameters in instruction.
```

`identifier` (6) and `deferred_text` (11) are both captured via a raw `strtok` copy with no
per-code "missing" check of their own (`docs/type-codes.md`'s "Accepted forms" column for each);
every other kind's own handler reports its own message first. Cross-checked against the CHM
corpus's own `openinterval_1.CR6` (`DataTable` bare, code 6), `etsz_1.CR6` (`ETsz` bare, code 11)
and `writeio_1.CR6` (`CallTable` bare, code 6) -- all three now match. `instr_rules.check` now
looks up the missing code's `kind` and picks the message accordingly
(`_TOO_FEW_PARAMS_KINDS = {"identifier", "deferred_text"}`).

Not fixed by this change, still `verdict`: a handful of corpus files (`tablenamerecord_1.CR6`,
`realtime_1.CR6`, `tablenameeventend_1.CR6`, the `cdmvw300*`/`rainflowsample2_1.CR6` family) whose
divergence starts earlier in the file, for unrelated reasons (mostly the real compiler's multi-
line `{...}` array-literal initializer syntax, not implemented here at all) -- isolating
`RecData = Main.record(1,1)` alone (`tablenamerecord_1.CR6`'s own differing line) compiles clean,
confirming this is downstream cascade, not a second trigger for this message.

`tools/difftest.py --logger cr6 --show 0`: chm exact 372 -> 375, zero false rejects on all four
loggers (`cr1000x`/`cr300`/`cr350` chm exact unchanged at 392/157/161 -- no corpus case on those
loggers hits this exact shape). `uv run pytest -q`: 1042 passed, unchanged (no new test file this
round; the existing CHM-corpus regression sweep already covers the changed lines).

## `Data`/`DataLong` parameters (oracle-confirmed 2026-09-27, `checker/expressions.py`)

Another small task from the queue: ~20 `chm` `verdict` cases whose real-compiler diff starts with
`Invalid symbol in expression: ...` where this checker printed the generic `Invalid, or out of
place expression: ...` fallback or nothing at all. Diffing four (`am25t_1.CR6`, `brfull6w_1.CR6`,
`cdmbrfull_1.CR6`, `voltse_1.CR6`) found two unrelated causes bundled in the same difftest bucket:

1. Several (`am25t_1.CR6`'s own `VX1`, `brfull6w_1.CR6`'s own `Vx1`, `brhalf_1.CR6`'s own `P1`, ...)
   are an undefined symbol used as an *argument to a known instruction whose argument-position
   table this session did not extend* (`AM25T`, `BrFull6W`, `Totalize`, ... -- `instr_rules.py`'s
   own domain, out of this session's file scope, `checker/instr_rules.py`/`checker/cr3xx.py` are a
   concurrent workstream's files this round). Not fixed here.
2. A `Data`/`DataLong` instruction (`data/instructions.json`: both share one `compile_fn`,
   `0x406a32`) whose parameter is not a constant -- the actual "missing rule" this task's own
   wording pointed at, and the one implemented below. This is the single largest concrete win in
   the bucket: `cdmbrfull_1.CR6`, `cdmbrhalf4w_1.CR6` and `cdmbrhalf_1.CR6`'s *entire* remaining
   diff from this checker was exactly this shape, so fixing it alone flips all three straight to
   `exact`.

Oracle (a from-scratch scratch probe set, `/tmp/claude-0/oct/*.cr6`, no corpus siblings):

```
Data SomeVar          (SomeVar undeclared)     -> Invalid symbol in expression: SomeVar
                                                    .
                                                   Illegal Data parameter SomeVar
                                                    .
Data Batt_Volt        (Batt_Volt declared)     -> Illegal Data parameter Batt_Volt
                                                    .                          (no "Invalid symbol" line)
Data 1,2,3            (pure literals)          -> Compiled OK
Data 1,SomeVar,3      (undefined, not last)    -> Invalid symbol in expression: SomeVar.
                                                   Illegal Data parameter SomeVar.          (no embedded newline)
DataLong SomeVar                               -> byte-identical to Data's own (same compile_fn)
```

So: every `Data`/`DataLong` parameter must be a constant expression referencing **no name at all**
(defined or not -- a declared variable is just as illegal as an undefined one, only the first
line differs). The embedded raw `\n` in `SomeVar\n.`/`Tables and Store Data\n.` is a literal byte
in the real compiler's own message, not a rendering artifact: it appears only when the bad
parameter is the last one on its physical line (the compiler's own source buffer still holds the
line's terminating newline; a parameter followed by a comma stops before it instead). Implemented
as `expressions.check_data_params`, wired in as an ungated pass-1 step right after
`check_consts` (`checker/__init__.py`) -- same reasoning: `cdmbrfull_1.CR6` shows it firing
alongside an earlier, unrelated `Need '=' in units definition:` pass-1 error, not suppressed by
it, the same non-suppression `check_consts` already established.

A second, separate shape needed its own handling: the common CHM-corpus case is not a clean
`Data <args>` parse at all. `Data Tables and Store Data` (a `'`-less comment-continuation line,
the actual corpus artifact) has no comma anywhere in it, so `parser.py`'s `_parse_bare_arglist`
parses only `Tables` as parameter 0 and leaves `and Store Data` as trailing input, which turns the
*whole statement* into `S.Unparsed` (`Instruction.args` never populated at all) -- yet the oracle
still reports the same two-line shape, spanning from right after the keyword to end of line
(`Invalid symbol in expression: Tables and Store Data\n.`, not `Tables\n.` alone). Handled by
reconstructing that span textually from `S.Unparsed.text` (strip the leading `DATA`/`DATALONG`
keyword and its following whitespace) rather than teaching the parser to recover a partial
argument list for this one shape.

**CR300/CR350 are a real behavioural difference for this second shape, not just a message-template
swap** -- oracle-confirmed, cross-checked against `cr300comp.exe`/`cr350comp.exe`: the clean-parse
shape above (`check_data_instruction`) is identical to CR6/CR1000X except for the already-existing
per-logger `unresolved_symbol_message` split (`SomeVar is not defined.` in place of `Invalid symbol
in expression: SomeVar.`, `Illegal Data parameter`'s own line unchanged either way), reused as-is.
But the *unparsed* shape genuinely re-parses differently there: `and` is tokenised as the real
logical `AND` operator on CR300/CR350 (a reserved word this shape happens to expose), so `Data
Tables and Store Data` becomes a compound `Tables AND (Store Data)` parse attempt -- three separate
`%s is not defined.` lines (`Tables`, `Store`, `Data`, each its own undefined name) and `Incomplete
expression: %s.` (not `Invalid symbol in expression`) as the wrapper, before the same `Illegal Data
parameter %s.` line. Reproducing this needs a compound-expression walk this session did not build,
so it is deliberately not attempted (AGENTS.md: under-report rather than guess a false reject) --
the unparsed-shape reconstruction is scoped to `_WHOLE_EXPR_ECHO_LOGGERS` (CR6/CR1000X) only; CR300/
CR350 fall through unchanged to whatever later pass already handled that `S.Unparsed` statement.

`tools/difftest.py --logger <l> --show 0`, chm group, before -> after this round: cr6 375 -> 378,
cr1000x 392 -> 400, cr300 180 -> 219, cr350 184 -> 228 (cr300/cr350's larger jump is almost
entirely the clean-parse shape's per-logger message fix, which the existing corpus has many more
instances of than the unparsed/trailing-garbage shape) -- zero false rejects on all four loggers,
re-verified with `their_exit == 0 and our_exit != 0` after every change this round. `uv run
pytest -q`: 1052 passed (6 new, `tests/test_checker.py`'s own new "`Data`/`DataLong` parameters"
section), 311 skipped, unchanged otherwise.

Not fixed: the `AM25T`/`BrFull6W`/`Totalize`/... undefined-instruction-argument gap above (needs
`instr_rules.py`'s own argument-position tables, a concurrent workstream's file this round); the
before-`BeginProg` false positive some of these same files also showed (`brfull6w_1.CR6`'s own
line 2, a `'`-less comment continuation before `BeginProg`) turned out to be this session's own
gating working as designed -- `structure.check_pass2` is gated behind "no pass-1 error anywhere in
the file", and once `check_data_params` supplies that file's actual pass-1 error, `check_pass2`
(and its own false "Expr illegally occurs before BeginProg" line) no longer runs at all, with no
separate fix needed.

## Generic fallback echo newline: per-statement, not per-profile (2026-09-27)

`docs/loggers.md`'s "Generic fallback echo on CR300/CR350" implemented the closing `.` moving to
its own output line as a flat `Profile.fallback_echo_newline` switch, applied to every
`Invalid, or out of place expression: %s.` unconditionally on CR300/CR350. `difftest`'s own
first-differing-line showed this over-firing on corpus files where the echoed statement's segment
does *not* reach the true physical end of line -- a trailing `'` comment or a further
`:`-separated segment on the same line cuts it short first, and then CR300/CR350 print the `.`
right after, same as CR6/CR1000X, no newline. Oracle-confirmed (scratch probes, no option flags,
`cr300comp.exe t.cr6`): `Zzq1(1,2)` alone on its line -> `Zzq1(1,2)` then `.` on the next output
line; `Zzq2(3,4) 'comment` -> `Zzq2(3,4) .` (trailing space the stripped comment left behind,
`.` same line); `Zzq3(5,6):` -> `Zzq3(5,6).` (colon-truncated, `.` same line) -- all three
byte-identical to `checker/structure.py`'s new output.

Implemented as `syntax.Stmt.at_eol` (default `True`), set by `parser.py`'s `_parse_line` per
`:`-split segment (`True` only for the segment that is both last on its physical line and not
followed by a stripped comment -- `source.LogicalLine.had_trailing_comment`, new, tracks the
comment-strip on the *last* physical line a logical line's continuation-joining reached).
`checker/structure.py`'s `_out_of_place(profile, text, at_eol)` now emits the newline only when
`fallback_echo_newline and at_eol`; its 4 call sites pass `stmt.at_eol`/`nxt.at_eol`. The
assignment-statement variant (`check_pass2`'s target-undefined branch, `structure.py`) is
unaffected -- oracle-confirmed CR300 does not reach this generic fallback for that shape at all (a
bare `Zzq4 = 1` before `BeginProg` gives only `Zzq4 is not defined.`, no follow-up line), so it is
left untouched, matching this file's own pre-existing "not switched yet" note.

`tools/difftest.py --logger <l> --show 0`, chm group: cr6 378 -> 378, cr1000x 400 -> 400 (both
profiles have `fallback_echo_newline` unset, unaffected), cr300 219 -> 247, cr350 228 -> 256; zero
false rejects on all four loggers, re-verified after the change. `uv run pytest -q`: adds 3 tests
to `tests/test_cr3xx.py` (comment-truncated, colon-truncated, and true-end-of-line control), all
passing; full suite unchanged otherwise.

Not fixed, found in the same corpus scan but a different root cause (left alone, out of this
round's scope): (1) a CR300/CR350-only quirk where a bare parenthesised statement's own
echo drops its enclosing `(`/`)` (`average_1.CR6`: real compiler echoes `1,TCTemp,FP2,False.`, not
`(1,TCTemp,FP2,False).`); (2) 16 `chm` files on *both* CR6 and CR1000X (`calibrate_1.CR6`,
`cdmacpower_1.CR6`, `clockreport_1.CR6`, `fieldnames_1.CR6`, `fieldorigin_1.CR6`, `filemark_1.CR6`,
...) where the real compiler's first diagnostic lands on a *different* line and echoes different
text than ours entirely -- these files open with an unquoted documentation paragraph (no leading
`'`) that happens to contain a real keyword (`Calibrate`, `SlowSequence`, `DataTable`, ...)
mid-sentence; the real compiler evidently resynchronizes on that keyword without ever flagging the
preceding run of bare words, which our checker does not model. Needs its own oracle investigation,
not attempted here.

## Pass-1 fallback: statements that start with a known word (2026-09-27, not yet implemented)

This refines "Pass model" above and explains the 16-file CR6/CR1000X "keyword-resync" group (the
note just above; `calibrate_1.CR6`, `cdmacpower_1.CR6`, `sinh_1.CR6`, ...). There is no keyword
resynchronisation. Pass 1 syntax-checks a statement whose **first token is known**: a declared
variable/alias, a numeric literal, a built-in function name, or a reserved word (`To` in
`calibrate_1.CR6`'s prose line 2). A syntax failure there is a **pass-1** error, so it is printed
next to other pass-1 errors and it suppresses all of pass 2, including pass-2 errors on earlier
lines (an opening prose paragraph's `Expr illegally occurs before BeginProg.`). A statement whose
first token is an unknown identifier stays an `Expr` record for pass 2, as documented above.

Oracle probes, CR6 (CR1000X identical on the two marked `*`). Template: `Public Pi, X` / optional
`Dim Z banana` (pass-1 `Invalid Variable declaration banana.`) / `BeginProg` / probe line / `Scan
(1,Sec,0,0)` / `NextScan` / `EndProg`:

| Probe line(s) | With `Dim Z banana` | Pass |
|---|---|---|
| `Pi bar`*, `Pi(1) bar`, `Pi +`, `1 + 2` | `Invalid, or out of place expression: <echo>.` still printed | 1 |
| `Sin bar` | `Expression expects ( :   Sin bar.` printed | 1 |
| `Scan bar` | `Invalid symbol in expression: bar.` printed | 1 |
| `foo bar`, `foo` | nothing | 2 |
| `Pi = Q` (Q undefined) | nothing | 2 |
| `Pi = 4 * ATN( 1` | `No Matching, or out of place ) in expression: <echo>.` printed | 1 |
| `Pi.`, `X.Y`, `Pi.5` | nothing (alone: `Could not find Pi.` then the fallback, same line) | 2 |

Without `Dim Z banana`: `X = Q` / `foo bar` / `Pi bar`* on lines 3-5 prints only `line 5: ...
Pi bar.`; `X = Q` / `1 + 2` prints only the `1 + 2` line; `Pi = 4 * ATN( 1` then `X = Q` prints
only the `No Matching )` line.

A line made only of `(`, `)`, `,`, blanks and a comment (`)`, `))`, `(`, `((((`, `)(`, `( )`,
`),`, `)  'x`), in the body or before `BeginProg`, compiles with no message (ours rejects it with
the fallback). `) X banana`, `) + 3`, `,x` and `(X)` get the normal fallback. This is why
`atn_1.CR6`/`atn2_1.CR6` report only their `No Matching )` line.

CR300 prints every line instead (`Q is not defined.`, `foo is not defined.`, fallbacks with the
newline echo): its pass model differs and is not covered by this section.

Unclosed `(` (probes `Pi = (1`, `foo = 4 * ATN( 1`, `Pi = foo(1`, each with the pass-1 `Dim Z
banana` and, separately, with a pass-2 `X = Q` on the line before): known first token and every
name resolves -> pass-1 `No Matching )`; unknown first token -> pass-2 `Invalid, or out of place
expression: <echo>.`; known first token but a later unknown name -> pass-2 `Invalid symbol in
expression: <echo>.` (whole statement echo). Implemented (`structure.py` `_unclosed_paren_kind`).
An unclosed call statement is different: `Sin(1` -> pass-1 `No Matching )`, `foo(1` -> pass-2
fallback; our parser accepts both as a `Call` (not yet handled).

### The scan (implemented 2026-09-27, `structure.py` `_pass1_scan`, CR6/CR1000X)

Pass 1 reads the statement left to right. An identifier in operand position that is not a
declared variable/parameter/`Const`/`Alias` name or predefined symbol (`pass1_operands`; `Sub`,
`DataTable` and system-table names do not count) stops the scan: the statement is left for pass
2. A syntax error reached first is a pass-1 error. Messages, all echoing the whole statement:

| Shape | Message |
|---|---|
| statement-start operand then EOL/operand/non-`=` (`Pi`, `Pi bar`, `-1 bar`, `1 + 2`) | fallback |
| statement starts with `And`/`Or`/`Mod`/`Xor`/`Imp`/`Eqv`/`Is`/`To` | fallback |
| binary operator where an operand is expected, or EOL after an operator (`Pi = * 2`, `Pi = 1 +`) | fallback |
| operand after a complete operand inside an expression (`Pi = 1 bar`, `Not 1 2`) | `Incomplete expression: %s.` |
| extra `)` (`Pi = (1))`) | `Incomplete expression: %s.` |
| assignment ends with `(` open (`Pi = (1`) | `No Matching, or out of place ) in expression: %s.` |
| non-assignment ends with `(` open (`1 + (2`) | fallback |
| `,` outside a call's parentheses (`(-1,Second,Sunday)`, `Pi, 1, 2`, `(1,Undeclared,FP2)`) | fallback; `No Matching )` inside a group in an assignment (`Pi = (1, 2)`) |
| statement starts with `(` and a complete group is followed by an operand (`(Pi + 1) 2`) | fallback |
| function name without `(` that takes arguments (`Sin bar`, `X = Sin bar`) | `Expression expects ( : %s.` |

Pass 2 then reports a deferred assignment as `Invalid symbol in expression: %s.` (`Pi = Q bar`,
`Pi = Not bar`) and a deferred non-assignment as the fallback (`Pi + Q`, `foo bar`). A bare call
statement whose name is a pass-1 operand (`Pi`, `Pi(1)`) is a pass-1 fallback. Oracle-matched on
107 of 124 probes on both CR6 and CR1000X; the 17 others are the shapes below, which the scan
leaves unclassified (old behaviour). chm exact: cr6 381 -> 397, cr1000x 403 -> 419. The comma,
leading-`(` and function-name rows came second (CR6 134 of 149 probes; chm exact cr6 408,
cr1000x 430). This subsumes "Missing-parameter followup": a leftover `(args)` line is reported
exactly when the scan finds a syntax error before an unknown name (`(ResultTD,COMC1,...)`
silent, `(1,Undeclared,FP2,False)` reported -- oracle; an older unit test assumed silent).

Not done: `Could not find %s.` for `Pi.`/`X.Y` (pass 2, before the fallback); index
parentheses at statement start (`Pi(1) bar` fallback, `Pi(1 = 2` -> `Invalid assignment in
expression: %s.`, both pass 1); an unclosed call statement (`Sin(1` pass-1 `No Matching )`);
`Scan bar` (`Invalid symbol in expression: bar.`); `Exit bar` (`Illegal END or EXIT bar.`).

## Expression expects ( (bare function name)

A built-in function that takes arguments, written without an argument list, is the pass-1
`Expression expects ( : %s.` (`checker/expressions.py` `check_function_arity`,
`_is_bare_reference_problem`, `tests/test_expression_expects_paren.py`). Oracle, 2026-09-27
(`Public X, Y` / Scan body):

| Line | CR6 (CR1000X same) | CR300 (CR350 same) |
|---|---|---|
| `X = Sin` | `Expression expects ( :     X = Sin.` | same, closing `.` on the next line |
| `X = Exp + 1` | `Expression expects ( :     X = Exp + 1.` | same, `.` on the next line |
| `X = EmailRelay` (table `nargs` 0, `argspec` non-zero) | flagged | flagged |
| `X = Rnd` (`nargs` 0, `argspec` 0) | compiles | compiles |
| `Timer` (a whole statement) | `Expression expects ( :     Timer.` | same, `.` on the next line |
| `Randomize(Sin)` | `Expression expects ( : Sin.` (the argument only) | same, single line |

Echo: the whole statement for an assignment or call statement, the argument's own text for an
instruction argument. CR6/CR1000X permit a local `Dim` to shadow a function name; the
fresh local-`exp` arithmetic probe is accepted on these profiles. CR300/CR350 accept the local
declaration but report `Expression expects (` on those bare uses; that expression-resolution
difference remains a separate gap. Global function-name declaration collisions are now checked;
see [Built-in function name collisions](#built-in-function-name-collisions). Help corpus: 27 of the 33 `Expression expects (` lines now match on every logger;
the files keep other differences, so chm exact is unchanged. Not covered: statements our parser
leaves `Unparsed`, e.g. `Sin bar`, `X = Sin bar` (`docs/checker.md` "The scan").

## Pass-1 instruction arguments (2026-09-27)

Constant-family instruction parameters (`docs/type-codes.md` codes 1/39 `const_expr`, 5/38
`const_expr_only`, 20 `datatype_kw`) are folded while pass 1 parses the statement, so their
`Invalid symbol in expression: %s.` and `Variable illegal in Parameter: %s.` are pass-1 errors on
CR6/CR1000X (`expressions.check_const_family_args`); general positions stay pass 2
(`expressions.check`). Oracle, CR6, with a pass-1 `Dim Z banana` in the file:

| Statement | Reported? |
|---|---|
| `SDI12Recorder(X, Nope, "0", "M!", 1, 0)` (port, code 1) | yes |
| `PanelTemp(X, Nope)` (fnotch, code 20) | yes |
| `VoltSe(X,1,Nope,...)` (range), `VoltSe(X,1,mV5000,Nope,...)` (channel) | yes |
| `VoltSe(Nope2,1,mV5000,Nope,...)` | yes, `Nope` only (constant positions first) |
| `PortSet(X,1)` | yes, `Variable illegal in Parameter: X.` |
| `VoltSe(Nope,...)` (dest, code 8), `VoltSe(...,Nope,0)` (mult, code 2), `Randomize(Nope)`, `Delay(0,Nope,mSec)` (code 28) | no |

A parse error inside an argument (`Expression expects , :` arity, `Expression expects (`) owns
the statement: `WriteIO(Round(X),Round(Y))` reports only the arity error.

**Failed constants are undeclared** (all four loggers): `Const P = Nope` / `Const R = P + 1` /
`PortSet(R,1)` / `PanelTemp(X, P)` reports `Nope`, `P + 1` (CR300/CR350: `P is not defined.`),
`R`, then `P` (`expressions._failed_consts`, a fixpoint over the `Const` declarations).
`crbasic/AcclimaTDRLib.crb`'s `Const TDRComPort = U5` on CR1000X (no `U5` there) is this case.

**Messages print in expanded-source order**, not grouped by file (`compiler.py`
`CompileResult._position`): a library `Include`d above line 45 prints its own lines before the
main file's line 45 (`TDR_tester.CR6` on CR1000X; a `Lib.crb` probe matches byte for byte).
Together these make all 16 `crbasic/` programs exact on CR1000X (was 9) and CR6 chm exact
408 -> 422.

## Declaration expressions (2026-09-27)

Pass 1, `structure.py` `_check_decl_exprs`/`_check_alias_index_names`. Oracle, identical on
CR6/CR1000X; CR300/CR350 print `%s is not defined.` per name followed by `Variable illegal in
Parameter: <echo>.` instead of the `Invalid symbol` line:

| Declaration | Messages |
|---|---|
| `Public A(3, Nope)`, `Dim A( Nope )` | `Invalid symbol in expression: Nope.` / `... Nope .` (each bad dimension, echo left-trimmed) |
| `Dim A(Y)`, `Y` a variable | `Variable illegal in Parameter: Y.` (all four) |
| `Alias X( Nope ) = Q` | `Invalid symbol in expression:  Nope .` (raw), `Alias is illegally dimensioned.` |
| `Public B As Long = Nope * 2`, `Public B(2) = {1, Nope}` | `Invalid symbol in expression: <init>.`, `Variable initialization of B has bad syntax.` |
| `Public A As StringX * 50` | `Variable Type StringX not supported` (as written, no period) |

`Public`/`Dim` accept only `Float`, `Long`, `Boolean`, `String`, `Double`, `UInt1` or a declared
`StructureType` (case-insensitive); `UInt2`, `UInt4`, `Bool8`, `NSec`, `IEEE4`, `IEEE8`, `FP2`,
`Int4`, `Integer` and `ULong` are rejected on all four loggers. Not done: `Public C() As Boolean`
(`Invalid symbol in expression:  As.`). Fuzz replay: 105 saved false accepts now rejected, no
new false rejects.

## Unmatched block ends (2026-09-27)

Pass 2, all four loggers (CR6 and CR300 probed side by side, identical), `structure.py`
`check_block_closers`. One stack of open blocks (`If`/`Select`, `For`, `Do`, `Scan`/`SubScan`) and
a counter per kind. A closing `EndIf`/`EndSelect`/`Next`/`Loop` always pops the innermost block;
if that was a `Scan`/`SubScan` it first reports `IF or SELECT` / `FOR NEXT` / `LOOP statement
illegally has SCAN without NEXT SCAN.`; with no block of its own kind open it reports `END IF
without IF or END SELECT without SELECT.` / `NEXT without FOR.` / `LOOP without DO.`. An
`EndSelect` with no `Select` open reports only `EndSelect w/o Select` (no period). Examples:
`For I = 1 To 2` / `EndIf` / `Next` inside a Scan -> `END IF without IF...` on the `EndIf` (the
`For` is popped) and `FOR NEXT statement illegally has SCAN...` on the `Next`; two stray `EndIf`s
-> both messages on the first, only `END IF without IF...` on the second (the `Scan` was popped).
`NextScan` removes the innermost `Scan` wherever it is on the stack, so `If X Then` / `NextScan` /
`EndIf` compiles (a fuzz-found false reject in the first version, which popped the top). This
check replaces round 6's `If`/`Select` scan-depth comparison in `check_pass2`. Programs with
`While`/`Wend` are skipped. On one line the "never reached at run time" warning
prints after that line's errors (15 of 15 transcripts); other warnings keep their order.
Fuzz replay: 122 saved false accepts now rejected in total, 168 reproducers exact.

## Units targets, CallTable targets, redeclarations (2026-09-27)

All oracle-probed on CR6 and CR300, then checked on CR1000X and CR350.

- **`Units`** (`instr_data.check_units_targets`, after pass 2 with the table checks): the target's
  base name must be a `Public`/`Dim` variable of a plain type or an `Alias` name, declared
  anywhere; otherwise `Can't assign units to %s, it must be declared Public or referenced as a
  source in a Data Table.` (as written). A `Dim` no table uses is fine; a `Const`, an undeclared
  name, a `Sub` parameter, a `StructureType` variable and a table field (`P_Avg`) are rejected.
  `Units` inside a `StructureType` names a member and is skipped (`structuretype_1.CR6`).
- **`CallTable`** (`instr_data.check_unclosed_calltable`, tier B): any target that is not a
  closed `DataTable` (case-insensitive, quoted `DataTable ("HIST4D",...)` names included) gives
  `Could not find DataTable %s.`: `CallTable Nope`, a variable, `Status`, `CallTable("fuzz")` ->
  `fuzz`, `CallTable((1+2))` -> `1+2`.
- **Redeclaration** (`structure._check_duplicate_declaration`, pass 1, global scope only): a
  `Public`/`Dim`/`Const` name already declared as a variable or `Alias` name -> `%s already
  declared as a variable.`; a `Public`/`Dim` after a `Const` of that name -> `%s is already
  declared as a CONST.`. `Sub` parameters and `Dim`s inside a `Sub`/`Function` may shadow.
  CR300/CR350 then print their `Too many parameters:  Sec.` cascade on `Scan` (not modelled for
  these messages). Several unit tests had programs declaring `X` twice; they now use
  `wrapped_bare`.

Reachability (`warnings._check_reachability`, oracle CR6): the first unreachable `Do`, `Loop` or
`For` is named `Do`/`Loop`/`FOR` (was `Expr`); an unreachable `If` (block or one-line) or `Select`
silences the warning for the rest of the file, including later statements.

## Unclosed blocks (2026-09-27)

`Scan without NextScan.`, `FOR without NEXT.`, `DO without LOOP.` and `IF or SELECT without EndIf
or EndSelect.` are reported, in that order, on the last statement's line (`EndProg`) only when
passes 1 and 2 found nothing (`structure.check_end_blocks`, run before tier C). Oracle, CR6 and
CR300: an unclosed `Scan`/`For`/`If` plus an unrelated pass-2 `X = Q` (before or after it) prints
only `Invalid symbol in expression`; before this they were emitted in pass 1 and hid the real
pass-2 errors. `DataTable ... EndTable not matched.` moved with them (still only when no other
block message fires). Reachability: when an `If`/`Select` opened inside the `Scan` is still open
at `NextScan`, statements inside it are not flagged; the statement closing it is (`EndIf never
reached at run time.`). Not done: `Next SubScan without SubScan.` for a stray `NextSubScan`.

## If conditions, Sub arguments, table sources (2026-09-27)

Oracle probes on all four loggers unless noted (`tests/test_if_sub_table_checks.py`):

- **`If` condition, undefined name** (CR6/CR1000X, pass 2, `expressions.check`): `Invalid
  symbol in expression: <condition> .`, the echo being the text between `If ` and `Then`
  (`If Nope Then` -> `Nope .`, `If Foo(X) > 20 Then X = 1` -> `Foo(X) > 20 .`). CR300/CR350 print
  per-name `%s is not defined.` lines (not modelled). `ConstTable` names now count as declared
  (`ScanConstTable.ScanConst` in `applyandrestartsequence_1.CR6`).
- **`If` condition, built-in arity** (all four, pass 1, `check_function_arity`): `No Matching,
  or out of place ) in expression: TimeIsBetween(0,15,60,sec,1) .`, same echo, no CR300 newline.
- **`Sub` call argument count** (all four, pass 2, `structure._check_call_args`): fewer than the
  parameters without a default, including `Log1` and `Log1()` -> `Call argument required.`;
  more than the parameters -> `Call to Log1 with 2 parameters, requires 1 arguments.`
- **Table source** (CR6/CR1000X, pass 2, `structure.check_pass2`): an undefined name in a
  `deferred_text` (code 11) argument, e.g. `Sample (1,Nope,FP2)` -> `Invalid symbol in
  expression: Nope.`; it ends pass 2 (a later `X = Q` and a second bad source are not reported).
  CR300 differs (not modelled).
- **String `Alias` index** (all four, pass 1): `Alias W("fuzz") = WF` -> `Illegally dimensioned
  aliased name`.

Reproducer replay: 277 of 1469 exact, no false rejects.

## One-off rules (2026-09-27)

Oracle probes (`tests/test_oneoff_rules.py`), CR6 and CR1000X identical, CR300 where noted:

- A built-in function that takes arguments called with empty parentheses (`X = Int()`,
  `TCPOpen()`, `SerialFlush()`) is the generic fallback on the whole statement, pass 1, all four
  loggers (CR300/CR350 with the newline echo). An older unit test expected `Expression expects ,`
  for `TCPOpen()`; the oracle says otherwise.
- `DataTable (T,1,-1,"fuzz")` -> `Too many parameters:  "fuzz".`, pass 1.
- A second `DataInterval` in one table -> `Only one DataInterval allowed within
  DataTable..EndTable.`, pass 2 (also CR300).
- `MenuPick`/`MenuRecompile` outside `DisplayMenu`..`EndMenu` -> `%s must be used within
  DisplayMenu..EndMenu.`, pass 2 (CR300/CR350 have no menus).
- A stray `NextSubScan` -> `Next SubScan without SubScan.`, pass 2 (also CR300).

`No BeginProg in Program.` also moved after pass 2 (`check_end_blocks`): a program with no
`BeginProg` reports its pass-1/pass-2 errors instead (`Y=EXP()` -> the fallback and `Expr
illegally occurs before BeginProg.`), and the message is on `EndProg`'s line or else the main
file's last physical line, trailing blank lines included (`Public X` plus two blank lines ->
line 3; `program.last_line`). This alone made several hundred shrunk fuzz reproducers exact.

`Alias Target = Name, Name2`: each comma-separated item is a name (optionally indexed); text after
it up to the next comma is ignored (`= Spare2_1  3`, `= Sp + 1`, `= Sp As Long` compile; in
`= A1 B1` only `A1` is declared). `Alias X = 3` -> `Variable name 3 illegal, starts with a
number.` (pass 1, all four loggers). The first form was a fuzz-found CR1000X false reject.

## Stage 2a: instruction checks between pass 1 and pass 2 (2026-09-27)

CR6/CR1000X (`instr_data.check_stage2a`). Oracle: these are suppressed by a pass-1 error, and
they suppress pass 2's expression errors anywhere in the file, even on an earlier line (`X = Q`
on line 7 then `Battery((1+2))` on line 8 prints only the `Battery` error).

- A constant with no names where a destination variable is required (type code 8): `Battery(5)`
  -> `Invalid Variable or Array: 5.`, `Battery((1+2))` -> `... (1+2).`; `Battery(X+1)` is
  accepted. These accumulate. `AddPrecise`/`MovePrecise` keep their own pass-1 check.
- Table-output `Reps` below 1: `Sample (0,X,FP2)` -> `Illegal Reps 0` (no period); a string or
  out-of-range value reads as -2147483648. A source past its 1-D array: `Sample (3,A(4),FP2)`
  with `A(5)` -> `Variable A out of bounds.` and `Variable A, index 4, reps 3, out of bounds.`
  (a scalar counts as size 1). The first such table statement ends the stage.

CR300 differs (`Illegal destination variable type, expecting Float.`; no `Illegal Reps` before
its other errors) and is not modelled. `Public Q("fuzz")` -> `Illegally dimension: Public
Q("fuzz")` (pass 1, all four loggers). Replay: 647 of 1469 exact, no false rejects.

More declaration rules (pass 1, oracle 2026-09-27, all four loggers unless noted): a second `Const
K` -> `K is already declared as a CONST.`; `ReadOnly Nope` -> `Nope not yet declared.` (outside a
`StructureType`, where `ReadOnly` names members); `Public A() As Long` -> `Invalid symbol in
expression:  As.` (CR300/CR350: `As is not defined.`, `Variable illegal in Parameter:  As.`),
while a plain `Public A()` compiles. On CR300/CR350 these start the `Too many parameters`
cascade; a tainted `ReadOnly` keeps its own message and a tainted multi-variable declaration
reads `Too many parameters:  <second variable>.` (`Dim A(), B` -> `B`).
CR300/CR350 print the same `Illegal Reps` / out-of-bounds messages, but only when nothing else
failed, stopping at the first (`instr_data.check_table_reps_late`).

For the table-output instructions handled by that same Reps/source path (`Sample`, `Average`, and
`Maximum` independently probed), an expression source accepts only `Reps = 1`. A literal string
or `A+1` with `Reps` 2 or 24 emits, in order, `Reps must be 1 when the source is an expression.`
and `Variable Anonymous1, index 1, reps N, out of bounds.`; `Reps = 1` is accepted. The message,
ordering, and accepted boundary were observed on all four profiles on 2026-10-03. It follows the
same CR6/CR1000X stage-2a versus CR300/CR350 late-pass placement as the other table Reps checks.
Fixture: `tests/fixtures/table_allocation.json`.

`WindVector` is different: literal Reps `-1` and `-2` emit `Out of memory.` rather than the
generic illegal-Reps message, while Reps 1 is accepted. This was observed on all four profiles on
2026-10-03. The signed-minimum case has distinct diagnostics and is deliberately not folded into
this narrow allocation rule. Fixture: `tests/fixtures/table_allocation.json`.

## Scan placement (2026-09-27)

Oracle, all four loggers unless noted. `NextScan with no Scan.` is not a pass-1 error: a pass-1
error hides it. Only the first extra `NextScan` is reported.

`CallTable` outside `Scan..NextScan` (before `Scan`, after `NextScan`, or in a `Sub`):

- a table with `DataInterval` -> `Table with DataInterval must be called within Scan..NextScan.`
  (not reported from a `Sub`);
- a table with a negative size -> `Cannot autoallocate memory for DataTable T called outside of
  Scan, e.g., called by Sub.`, after the first message when both apply;
- a fixed-size table without `DataInterval` is accepted.

A literal `Scan(0,...)` still consumes its matching `NextScan`, but does **not** count as a Scan
for the auto-allocation placement check: a `CallTable` in that syntactic block gets the same
`Cannot autoallocate ... outside of Scan ...` message. CR6/CR1000X additionally retain their
ungated `Scan 0uSec invalid must be at least 1mSec.` diagnostic first; CR300/CR350 emit only the
auto-allocation message. `Scan(1,Sec,...)` is accepted. Oracle probes, all four profiles,
2026-10-03; fixture `tests/fixtures/table_allocation.json`.

When the two checks run:

- CR6/CR1000X: both run in the pass-2 walk (`structure.check_pass2`). A stray `NextScan` ends
  pass 2, so a later `X = Q` is not reported. A `CallTable` error accumulates with other pass-2
  errors.
- CR300/CR350: `structure.check_scan_placement_late` runs after the table `Reps` checks and the
  early warnings, and before the unclosed-block checks. An unrelated `X = Q` or `Sample (0,X,FP2)`
  hides both messages. Both messages print together. The fnotch and reachability warnings stay
  (`CallTable never reached at run time.` follows the autoallocate error). An unclosed `Do` is no
  longer reported. The CR300 early warnings (`profile.early_warnings_gated`) therefore run at
  that point, not with tier C: `Illegal Reps 0` hides an fnotch warning, but `DO without LOOP.`
  does not.

After a stray `NextScan`, no statement gets a reachability warning.

Replay: 771 of 1469 exact, no false rejects.

## Instructions before BeginProg (2026-09-27)

Decompiled placement loop (`FUN_00424160`, `docs/binary.md`): it walks the code records in order.
The first code-generating instruction outside a declaration block (`DataTable`, `Sub`,
`Function`, `DialSequence`, `WebPageBegin`, `ShutdownBegin`, ...) before `BeginProg` gets `%s
illegally occurs before BeginProg.` (`%s` = the table name, e.g. `Sw12`), and the walk stops. The
instruction's own errors on that line come first (`Variable Y out of bounds.`, `Table with
DataInterval must be called within Scan..NextScan.`). Instructions that generate no code are
accepted, for example `SetStatus`, `StationName`, `TimedControl`, and `SetSetting` with a
constant value. `SequentialMode`, `CH400Status` and `PreserveVariables` are exempt by index.

Which instructions generate code depends on each compile function, so the lists are measured.
`tools/probe_before_beginprog.py` copies lines from accepted chm examples to just before
`BeginProg`. It lists an instruction in `data/**/before_beginprog.json` only when every probe of
it is rejected. Counts: CR6 90 of 115 probed, CR1000X 132 of 162, CR300 38 of 50, CR350 40 of 52.
`ModbusServer` is rejected on CR6 but accepted on CR300. Unprobed instructions are not checked.

- `Call Foo` and `Foo` for a defined Sub -> `Call illegally occurs before BeginProg.`; a function
  call or assignment -> `Expr ...`. A `CallTable` of a table not yet declared and closed gets
  `Could not find DataTable %s.` instead.
- The comparison uses statement order, not line numbers, so an included file inside `Scan` is not
  flagged.
- CR6/CR1000X: pass 2 (`structure.check_pass2`); a later `X = Q` is not reported.
- CR300/CR350: the late walk (`check_scan_placement_late`), so an unrelated `Q is not defined.`
  hides it. Warnings after it are dropped (an fnotch warning further down is not printed). A
  stray `EndIf`/`NEXT` still gets its placement error right after its block message.

Other messages the probes found (not implemented):

- `PulseCount`/`Quadrature`/`TimerInput must be used within a Scan...NextScan.`
- `SDM instruction must be in Scan..NextScan, unless SequentialMode is used.` (`SDMCD16Mask`,
  `SDMTrigger`, `CSAT3B`)
- `%s not allowed outside of Scan..NextScan in PipeLine Mode.` (`CDM_*`, `AVW200`)
- `ContinueScan`/`ExitScan not within a Scan.`, `Exit Do without DO.`, `Else with no If.`
- `SPIOpen must precede SPIRead.`
- `BEGINPROG illegally occurs after EndSequence.`

CR300: `Foo(1)` with the wrong argument count gets both its own error and the placement error
(not modelled).

Replay: 790 of 1469 exact, no false rejects. chm exact CR300 269, CR350 279 (the other two
unchanged).

## Stray control statements (2026-09-27)

Oracle, all four loggers (`structure.stray_controls`, `tests/test_stray_controls.py`).

| Statement | Error when | Message | CR6/CR1000X |
|---|---|---|---|
| `Else`, `Case Else` | no `If` or `Select` open | `Else with no If.` | pass 2, stops |
| `ElseIf` | no `If` or `Select` open | `Case with no Select.` | pass 2, stops |
| second `Else`/`Case Else` | in the same `If` or `Select` | `If statement with duplicate Else.` | pass 2 |
| `Exit Do`, `Exit For` | no `Do`/`For` open | `Exit Do without DO.`, `Exit For without FOR.` | pass 2 |
| `Exit Sub`, `Exit Function` | outside any `Sub`/`Function` | `Exit Sub without Sub or nested subs.` | pass 2 |
| `ExitScan`, `ContinueScan` | no `Scan` open (also in a `Sub`) | `ExitScan not within a Scan.` | pass 2 |
| `Case` | no `Select` open | `Case outside Select.` | pass 1 |
| `EndSelect` | no `Select` open | `EndSelect w/o Select` | pass 1 |

- "Open" means anywhere on the stack. `Else` inside a `Do` inside an `If` is fine, and so is
  `Else` inside a `Select`. `Case` inside an `If` inside a `Select` is fine too.
- `Case Else` is matched as a prefix, so `Case Else = 3` and `Case Elsewhere` count.
- `ExitScan` outside a `Scan` gets no reachability warning. `ContinueScan` does.
- The two pass-1 messages come from the parser (decompiled `FUN_0049ce10`). The others come from
  pass 2 (`FUN_005089c0`).
- CR300/CR350 report the pass-2 ones in the late walk (`check_scan_placement_late`), so an
  unrelated `Q is not defined.` hides them. `Case outside Select.` prints next to it.

Not modelled: an unresolved name earlier in the same `Scan` (`X = Q`, `Battery(Q)`) hides the
parser's `EndSelect w/o Select`. Pass 2 then reports the generic `IF or SELECT statement
illegally has SCAN...` and `END IF without IF...` lines.

## Measurements that must be inside a Scan (2026-09-27)

`PulseCount`, `Quadrature` and `TimerInput` outside any `Scan` (before `BeginProg`, after
`NextScan`, or in a `Sub`) get `%s must be used within a Scan...NextScan.` (`PulseCount` spelled
so, not with the table's `Pulsecount`). Their compile function returns -1 (decompiled
`FUN_0050e960` and `PulseCount`'s own copy), which ends the placement loop. So CR6/CR1000X stop
pass 2 there, no reachability warning is printed for them, and CR300/CR350 stop the late walk.
`TimerInput` does not exist on CR300/CR350. Oracle, all four loggers
(`tests/test_scan_placement.py`).

Not implemented (conditional on the program's mode in the decompilation):
`SDM instruction must be in Scan..NextScan, unless SequentialMode is used.` and `%s not allowed
outside of Scan..NextScan in PipeLine Mode.` (`CDM_*`, `AVW200`), `No Measurements in SubScan.`

## One-line If statements (2026-09-28)

The statements after `Then` and `Else` in a one-line `If` get the same tier-B checks as ordinary
statements (`checker/__init__.py` `_with_inline`). Oracle, all four loggers
(`tests/test_inline_if.py`):

- `If X Then Y = 1` -> `Invalid, or out of place expression: Y = 1.` (CR300/CR350: `Y is not
  defined.`)
- `If X > 1 Then X=Q+1` -> `Invalid symbol in expression: X=Q+1.`
- `If X Then Battery(Q) Else Battery(Q2)` -> one line for `Q`, one for `Q2`.

The echo is the sub-statement's verbatim source text, with no leading space and no CR300
newline (`parser.py` `_parse_stmt_list`, which also re-bases `arg_spans`). The errors accumulate
with later pass-2 errors.

Replay: 818 of 1469 exact, no false rejects. chm exact CR6 428, CR1000X 437.

## Declaration details and CallTable arguments (2026-09-28)

Oracle, all four loggers (`tests/test_decl_and_calltable_args.py`):

- Dimensions written after the type, `Public A As Float(Nope)` and `Dim A As Long(2,Nope)`, are
  resolved like `Public A(Nope)` (pass 1).
- Index names inside a member reference are resolved: `X = Status.TimeStamp(Nope,1)` -> `Invalid
  symbol in expression:     X = Status.TimeStamp(Nope,1).` (CR300/CR350: `Nope is not
  defined.`). The member name itself is still not checked.
- `Warning: Attempt to initialize variable out of bounds.` is printed once per variable with more
  initializer values than elements (`Public A = {1,2}`, `Public A(2) = {1,2,3}`, `Dim A(2,2) =
  {1,2,3,4,5}`). An unresolved dimension counts as 1. Pass 1, next to other errors.
- `CallTable` gets the excess-argument check: `CallTable(T,X,Y)` -> `Too many parameters:  Y.`.
  CR350's `CallTable` takes one argument, so there `CallTable(T,X)` -> `... X.`.
- CR300/CR350 cascade: a tainted `Sub Foo (A, B)` line becomes `Too many parameters:  B.`.

Replay: 822 of 1469 exact, no false rejects.

## TimeIntoInterval arguments (2026-09-28)

`TimeIntoInterval`/`IfTime` with constant arguments are checked by the parser (pass 1, all four
loggers, `checker/intervals.py`, decompiled `FUN_00494780`). Both values are converted to whole
seconds plus microseconds in the given units. A negative `TintoInt` wraps into the interval.
Monthly intervals are not checked.

- `Interval (%d) must be > 0 and TimeInto Interval (%d) must be < Interval.` echoes the raw values
  unless `0 <= TintoInt < Interval`: `(3,1,Min)`, `(0,0,Min)`, `(-500,1,Sec)`.
- Inside a `Scan`: `Scan interval must be integrally divisible into interval.` unless both values
  are multiples of the scan interval. `Scan(7,Sec)` with `(0,15,Sec)`, `Scan(2,Sec)` with
  `(1,10,Sec)`, and `Scan(300,mSec)` with `(0,1,Sec)` all fail. The comparison is in 100 uSec
  units; for an interval of 3601 s or more it is in whole seconds, and a sub-second part gives
  `Illegal sub seconds in time into and/or interval when interval > 3600 secs.`.

Not done: a variable argument gives `Warning: Parameter will truncate to the nearest integer.`
twice. Replay: 825 of 1469 exact, no false rejects.

## Integral parameters (2026-10-02)

Oracle on all four loggers; fixture `tests/fixtures/integral_params.json` (188 cases, compiler
hashes and date inside), `tests/test_integral_params.py`, implemented in `checker/intervals.py`
(`not_integral`) next to the `TimeIntoInterval` checks. Pass 1, ungated, accumulating (one line per
bad argument, in source order with the other pass-1 errors; it hides pass 2 as any pass-1 error
does).

- Integer-typed constant arguments of three functions: `IfTime` and `TimeIntoInterval`, all three
  arguments (`TintoInt`, `Interval`, `Units`), and `SerialOpen`'s `BaudRate` (second argument).
  A scan of every `functions.json` entry with an integer or float argument type found no other
  function (CR6 and CR300), and a scan of every instruction in `instructions.json` found no
  `Parameter must be integral.` on CR6 (2 values x ~4000 calls). The other `SerialOpen`
  arguments are different (`Parameter must be a constant integer:`, `docs/loggers.md`).
- A constant argument draws `Parameter must be integral.` when it is typed as a float: a literal
  with a decimal point or exponent (`1.5`, `6.0`, `9600.`, `6E1`), any `/` or `^` (`6/2` is
  rejected, `KI/2` too), a named constant whose value is not whole (`Const KH = 7.5`; then
  `KH*4`, `KH+0.5` too), or arithmetic with any of those (`5 MOD 2.5`). A named constant with a
  whole value counts as an integer whatever its spelling (`Const K = 60.0`, `K*2`, `Const KD =
  KI/2` pass). Hex literals are always integers.
- Or when it is one numeric literal, optionally negated, outside the converted range: CR6/CR1000X
  accept `-2147483648..2147483647` and flag `2147483648`, `-2147483649`, `4294967295`; CR300/CR350
  convert through a 32-bit float, so the limits are `-2^31..2^31` after rounding to float (ties to
  even): `2147483776` and `-2147483776` pass, `2147483777` and `-2147483777` do not. A `Const`
  declared as one positive literal beyond that range (`Const KB = 999999999999`) carries the
  float type into every use (`KB`, `-KB`, `KB-1`); a computed or negated constant does not
  (`Const KC = 2147483647+1`, `Const KM = -2147483649` pass).
- Computed values past the range (`100000*100000`, `2147483647+1`, `-2147483647-2`) wrap
  silently and are never flagged; function calls (`Sqr(4)`) and variables give `Warning:
  Parameter will truncate to the nearest integer.` instead (not implemented).
- When any argument is flagged the interval and divisibility checks of that call are skipped.
  Otherwise, a value of exactly `-2147483648` (or a hex literal wrapping to it) in the first two
  arguments of `IfTime`/`TimeIntoInterval` is silent, a hex literal of more than 8 digits is
  silent, and on CR300/CR350 a single literal past `+-2147483647` saturates to it in the
  `Interval (%d)` echo.
- A negative `TintoInt` or `Interval` is reported only while no earlier-source error exists
  (observed: a negative after an integral error, an earlier negative, or a `(7,3)` error is
  silent; `(0,0)` and `(7,3)` are still reported). Implemented against errors already found at an
  earlier line; on CR300/CR350 an earlier pass-2-style error (`Undef1 is not defined.`) also
  silences it, which needs the cross-pass source order and is not modelled.
- CR300/CR350: the integral error, `Interval (%d) must be ...` and `Scan interval must be
  integrally divisible ...` all start the `Too many parameters` cascade (`cr3xx` declaration
  triggers): every later instruction written with two or more arguments gets `Too many
  parameters:  <its second argument>.` (oracle: `Delay(0,1,Sec)`, `Scan(1,Sec,0,0)` after one).
  Not modelled: a later `CallTable Nope` on CR300/CR350 is still reported by us but hidden by the
  real compiler after any earlier error (also true for `Undef1 is not defined.`, so not specific to
  this family).
- `SerialOpen`'s own baud message (`N is illegal baud rate.`) is separate: it is skipped when the
  literal is outside 32 bits, and not modelled for non-`ComRS232` ports or the CR300 saturation
  (observed `2147483648` -> `2147483647 is illegal baud rate.`).

Replay: reproducers `c2fa595b2867` (CR1000X), `14387ce6dd56` and `bb5ac63ec5c3` (CR300) now exact.

## Option arguments (2026-10-02)

Oracle on all four loggers; fixture `tests/fixtures/option_args.json` (368 cases, compiler hashes
and date inside), `tests/test_option_args.py`, `instr_rules.check_option_values` and
`checker/longconv.py`. Four instructions, found by scanning every instruction parameter of CR6
(`Delay`'s `Illegal option %d` and `FTPClient`'s `Invalid PutGetOption` were already known):

- `SplitStr` `Options` (fifth argument): below 0 -> `Invalid option %d` (the converted value).
  Every non-negative value to 2147483647 passes.
- `IPRoute` `Network` (second argument): outside 0..5 (CR300/CR350 0..4) -> `%d is illegal
  network`; then the optional third argument outside 0..1 -> `%d is illegal option`. Both bad
  gives only the network line. A variable in any argument silences both. A string option crashes
  `CR6Comp.exe` (page fault), so it is not modelled.
- `CDM_Delay` `Option` (third argument, CR6/CR1000X only): `Illegal option %d` with the value
  modulo 256 (`-1` -> 255, `K=2` -> 2) unless that is 0 or 1; 0 and 256 give `CDM_Delay
  instruction not supported by module NULL.` instead (not modelled). Like `Delay`'s option
  message, but accumulating with tier-B findings.
- `Matrix` `Option` (first argument): `Illegal option.` (no number) unless `(value & 0x7FFFFFFF)`
  is 1..5. So `-2147483647..-2147483643` pass, as the sign bit is ignored; on CR300/CR350
  `-2147483648` saturates to `-2147483647` and passes too. Dimension messages: next section.

## Matrix dimensions (2026-10-03)

Oracle on all four loggers, identical on each; fixture `tests/fixtures/matrix_dims.json` (84 cases),
`tests/test_matrix_dims.py`, `instr_rules._matrix_dimension_errors` (called from
`check_option_values`, so it shares the option error's gating and ordering by line). Shape of a
variable: `(rows, columns)`, a 1-D `A(n)` is `(n, 1)`, a scalar `(1, 1)`. Messages for one call, in
this order (no trailing period; line of the call):

1. `Matrix cannot be > 4X4` once per checked operand with either dimension above 4 (A, then B,
   then Result; options 4 and 5 do not look at B, it may be anything).
2. Options 1, 2: `Illegal Matrix dimensioned` unless A, B and Result all have the same shape.
   Options 4, 5: the same message unless A is square and Result has A's shape.
3. Option 3: `multiply A columns and B rows must be the same` when columns(A) differs from
   rows(B); then `multiply result illegal dimensioned, must be M,Q` (A is MxN, B is PxQ) only when
   rows(Result) differs from M *and* columns(Result) differs from Q. A Result sharing either
   dimension passes (`M23*M32 -> M23`, `D2`, `M12` all compile; `M33`, `M44`, `V`, `D3` do not).

Only modelled when every checked operand is a bare name declared once as a Float with at most two
dimensions and the option folds to 1..5; anything else stays silent. Seen but not modelled: a
3-D operand (`Illegal Matrix` per such operand, then the dimension message, with garbage
dimensions for option 3), `A()`/`A(1,1)` operand forms, `Long`/`String`/`Const`/undefined
operands (CR6/CR1000X add `Illegal Matrix`, CR300/CR350 `Illegal destination variable type`).

Conversion of the constant to a 32-bit `long` (`longconv.long_value`), shared by all three:
an integer-typed expression wraps (`100000*100000` -> 1410065408, `2147483647+1` and `Const KC =
2147483647+1` -> -2147483648, `-2147483647-2` -> 2147483647); a float-typed one (decimal point or
exponent, `/`, `^`, a literal beyond 32 bits, or a `Const` declared from one) truncates toward
zero. Out of range, a float gives -2147483648 on CR6/CR1000X; on CR300/CR350 a digits-only literal (or a `Const` declared as one) is first rounded to
a 32-bit float and gives -2147483648 beyond +-2^31 and +-2147483647 otherwise (`2147483776` ->
2147483647, `-2147483648` -> -2147483647); any other float expression gives -2147483648
(`2147483648*1.0`, `2^31`, `-2^31`; oracle 2026-10-03). The `const_ranges` rules and RunningAvg
use the same conversion (`instr_measure._long_arg`, `check_avgrun_options`), falling back to
`consteval.to_long` only for forms it does not evaluate (function calls, `TRUE`) A string, `NAN` or `INF` gives -2147483648 (`SplitStr`
and `Matrix`: `"2"` is an error). Hex literals wrap to 32 bits (`&HFFFFFFFF` -> -1); more than 8
digits is not modelled. Only a plain numeric expression is evaluated; function calls are not.
`True` is -1.

Gating: reported with pass 2 but not hiding the tier-C checks, like `AvgRun`'s options and the
index bounds (`bounds` in `checker/__init__.py`). They accumulate with a tier-B finding on CR6/
CR1000X (`X = Undef1` before or after) and with an unknown `CallTable`; a pass-1 error
(`Too many parameters`, `Parameter must be integral.`) hides them, and on CR300/CR350 so does an
undefined name. Two statements each report their own line.

Replay: reproducers `fde20a567f89` (CR350), `ef75644291d6` (CR6), `9b6ed6ff8f9d` (CR1000X) and
`881ad67a2b3f` (CR350) now exact.

## Sub call arguments (2026-09-28)

Undefined names in the arguments of a call to a declared `Sub` are reported for every argument,
unlike instruction arguments, where only the first bad position is reported. Pass 2, accumulating
(`structure._report_call_arg_names`, `tests/test_sub_call_args.py`, oracle on all four
loggers):

- CR6/CR1000X echo the argument's verbatim text: `Foo(Q+1)` -> `Invalid symbol in expression:
  Q+1.`, `Msg("a" + Nope)` -> `... "a" + Nope.`, `Two(Q, Q2)` -> one line each.
- CR300/CR350 name each undefined name (`Q is not defined.`), plus `Dimensions not declared:
  Nope(1).` for an indexed use.

Only calls written with parentheses are checked. Replay: 829 of 1469 exact.

## Duplicate Units, missing includes (2026-09-28)

Oracle, all four loggers (`tests/test_units_and_includes.py`):

- Each of a variable's n `Units` statements gets n-1 copies of `%s already assigned Units %s.`,
  echoing that statement's own name spelling and units text. Three `Units X` lines give two
  lines each. It is a late check, hidden by an unrelated `X = Q` (`instr_data.check_units_targets`).
  Indexed targets (`Units X(1) = ...`) were not probed and are not counted.
- A missing include warns `Cannot open include file  NoSuch.crb.` on CR6/CR1000X, without the
  `CPU:`/`USR:` drive. CR300/CR350 keep the drive (`parser._include_warning_drives`).

Code-4 arguments (`docs/type-codes.md`) accept only one integer token: `2`, `-1`, `&H1`, `(1)`,
or an integer constant name (`K`, `True`). `1+2` (echoed without its parentheses), `K+1`, `1.0`,
`1E0` and `- 1` (echoed `-`) are rejected. Replay: 836 of 1469 exact; chm exact CR6 429,
CR1000X 438.

## Display menus (2026-09-28)

CR6/CR1000X, following the decompiled menu builder (`FUN_004309c0`, `FUN_00430aa0`,
`FUN_00430b50`) and oracle probes (`instr_data.check_table_and_menu_placement`,
`tests/test_display_menus.py`). Pass 2 unless noted.

- `MenuItem`, `DisplayValue`, `DisplayLine`, `MenuRecompile`, `SubMenu`, `MenuPick`,
  `EndSubMenu` and `EndMenu` outside an open `DisplayMenu` -> `%s must be used within
  DisplayMenu..EndMenu.`.
- An empty `SubMenu` -> `Empty SubMenu ... EndSubMenu.`. It also closes the builder's insertion
  point, so the rest of that menu reports `must be used within` too.
- An empty menu -> `Empty DisplayMenu .. EndMenu.`. `EndMenu` with a `SubMenu` still open -> `SubMenu
  with no EndSubMenu.`.
- `EndSubMenu` with no `SubMenu` -> `EndSubMenu without SubMenu.`, which ends pass 2.
- `MenuPick` whose menu level's last item is not a `MenuItem` or `MenuRecompile` -> `MenuPick must
  follow a MenuItem instruction.`. Not modelled: a second `MenuPick` for the same item is
  sometimes rejected too.
- A second `DisplayMenu` before `EndMenu` -> `DisplayMenu without EndMenu.` on its line. A menu
  still open at the end gets the same message on the last line, late like the unclosed-block
  messages.

Replay: 841 of 1469 exact.

### Boolean picklists (2026-10-02)

Oracle on CR6 and CR1000X (CR300/CR350 have no menus); fixture `tests/fixtures/boolean_picklist.json`
(45 cases, compiler hashes and date inside), `tests/test_boolean_picklist.py`, implemented in
`instr_data._check_boolean_picklist` inside the menu pass, so it has the menu builder's gating:
it appears next to `Invalid symbol in expression`, `Scan without NextScan.` and `Could not find
DataTable`, but is hidden by the instruction-argument errors that stop pass 1 (`Invalid or missing
parameter.`, `Too many parameters`, `ComPort 0 illegal.`, `already declared`).

- Applies to every `MenuPick` that follows a `MenuItem` or `MenuRecompile` whose variable is
  declared `As Boolean` (scalar, array element or bare array name; `Dim` too). Float, Long and
  String items are not checked this way. A ConstTable-constant item keeps its own rule.
- The statement text is split at every comma, quotes ignored (`MenuPick("a,b")` has two tokens).
  Each rejected token prints `Boolean picklist must be True or False.` on the `MenuPick` line, so
  `MenuPick(1,2,3)` gives three lines. The check repeats for every `MenuPick` of the item.
- A token passes when it is a constant name whose value is 0 or -1: `True`, `False`, a `Const` or
  ConstTable constant (any case; a float `0.0` constant is fine; the string `"0"` and `1` are not;
  predefined symbols with value 0 such as `uSec`, `Until`, `If` pass, `Sec` does not), or a bare
  integer literal whose value modulo 2^32 is 0 or -1 (`0`, `-1`, `00`, `-0`, `+0`, `4294967295`,
  `4294967296`, `&H0`, `&HFFFFFFFF`, `&B0`; not `1`, `2147483648`, `999999999999`, `&H1`).
- Everything else is rejected: strings, `1+1`, `-1+0`, `(0)`, `0.0`, `1E0`, `- 1`, `NAN`, variables
  (the non-boolean `must be declared as a Const.` message is not printed), undefined names and
  empty tokens. A leading blank is skipped; a blank after a literal rejects it (`0 ,-1`, `0 )`)
  while a name tolerates it (`True ,False`).
- A final empty token (`MenuPick(0,)`) prints `Unable to parse MenuPick strings.` instead of a
  Boolean line, after the Boolean lines of the other tokens. A final blank token (`MenuPick(0, )`)
  is an ordinary rejected token. The same trailing-comma message also occurs for non-boolean items;
  that is not implemented.
- Not modelled: literals with more than 8 hex digits (observed: `&H1FFFFFFFF`, `&H1FFFFFFFE`,
  `&HFFFFFFFFF` pass; `&H100000000`, `&H200000000`, `&H1000000000` fail -- no rule fitted, so
  those stay silent) or more than 32 binary digits; `MenuPick()` on a Boolean item (real: the
  Boolean line only; ours: `Invalid or missing parameter.`); `0x0`/`&O0` spellings our parser
  already rejects differently. Numeric items have a separate unimplemented rule (`"x" must be
  declared as a integer Const.`).

Replay: reproducers `4c79f7dacefa` (CR6) and `99bf5c38e8cb` (CR1000X) now exact.

## Unterminated strings (2026-09-28)

A line cut short by an unterminated string is reported by its shape, in pass 1 on CR6/CR1000X.
Oracle on all four loggers (`instr_rules.unterminated_string_shape`,
`tests/test_unterminated_strings.py`):

| Shape | Messages |
|---|---|
| instruction, `Battery("abc`, `HTTPOut("<title>...` | `Invalid or missing parameter.` |
| `Sub` call, `Foo("abc`, `Call Foo("abc` | `Invalid or malformed function argument.` |
| assignment whose value starts with a quote, `S = "abc`, `S = "abc" + "def` | `String without end quote`, then `Invalid, or out of place expression: <raw line>.` with indentation and trailing spaces kept |
| one-line `If`, `If X Then S = "abc` | `String without end quote`, then `Invalid, or out of place expression: S = "abc:endif.` (the compiler's block-form rewrite) |

- A line starting with a declaration word (`Public</a></li></ul>")`, a wrapped `HTTPOut` string in
  `httpout_1.CR6`) is not reported.
- On CR300/CR350, `String without end quote` starts the `Too many parameters` cascade.
- `ESSInitialize` reads its argument raw: `ESSInitialize(public")` and `ESSInitialize("public)`
  compile on all four loggers (a fuzz-found CR350 false reject before this).

Not done: `S = abc"`, where an undefined name comes first, and `ESSInitialize(X)` -> `Community
Names parameter is of type string`. Replay: 847 of 1469 exact, no false rejects.

## Subscript counts (2026-09-28)

Oracle, all four loggers (`expressions._over_indexed`, `tests/test_subscript_counts.py`):

- CR300/CR350 reject more subscripts than a variable's declared dimensions (a scalar has none).
  An assignment target, `R(2,1) = 1` for `Public R(5)`, gets the fallback `Invalid, or out of
  place expression:     R(2,1) = 1` (accumulating). In an assignment value, `X = R(2,1)` gives
  `Dimensions not declared:     X = R(2,1)`. In an instruction argument, `Battery(R(2,1))` gives
  `Dimensions not declared: R(2,1).`. Fewer subscripts (`M(2)` for `M(5,5)`) are accepted.
- CR6/CR1000X accept up to three subscripts on any array. A fourth on an assignment target gets
  the fallback.
- String variables are not counted, because `Buf(1,1,I)` addresses a character
  (`SbdPayload_tester.CR6`). Neither are names declared with empty parentheses (`Dim A()`), which
  already have their own error.

Replay: 893 of 1785 exact, no false rejects.

## Function argument counts (2026-09-28)

A user `Function` called in an expression with fewer arguments than its non-`Optional` parameters
gets `Call argument required.` and then `Invalid, or out of place expression: %s.`. With more
arguments than parameters it gets `No Matching, or out of place ) in expression: %s.`. The echo
is the whole assignment (with the CR300/CR350 newline), the `If` condition (`One() > 1 `), or the
instruction argument (`One()`). Pass 1 on CR6/CR1000X: a later `X = Q` is not reported. Oracle
on all four loggers (`expressions.check_function_call_counts`,
`tests/test_function_calls_and_loops.py`). CR6 crashes on `Battery(One())`.

A bare statement call `F1(1,2)` (no `Call` keyword) to a user `Function` gets the same messages,
echoing the whole statement including its indentation (oracle 2026-10-02, all four loggers,
28/28 probe cases; `tests/test_function_statement_call.py`). Not modelled: `Call F1(1,2)` on a
`Function` reports `F1 is not declared as a Subroutine.` without a line prefix.

On CR300/CR350, `Call argument required.` starts the `Too many parameters` cascade. The cascade
rewrites a tainted `Sub`/`Function` header to the second token after its name: `Sub Foo (A, B)`
-> `B`, `Function F (x) As Long` -> `As`. Not modelled: the tainted function's parameters are then
undefined in its body.

`Do While`/`Loop Until` conditions are resolved like `If` conditions. CR6 echoes the text after
`While`/`Until`, trailing spaces included (`Invalid symbol in expression: (Nope <> 1)   .`), and
CR300 names each undefined name. Replay: 904 of 1785 exact.

## Statement and argument checks (2026-10-01)

Oracle-confirmed 2026-10-01.

- `For` headers: CR6/CR1000X `Invalid symbol in expression: <text>.` for an undefined start, end
  or step; the text is the raw text between keywords with one separating space dropped each side
  (`For X = A to 2` -> ` A`). An undefined loop variable is reported on the matching `Next` line
  (`j .`). CR300/CR350: per-name `is not defined.` on the `For` line, plus `Dimensions not
  declared: j(1) .`. A `For` loop does not declare its variable.
- `ElseIf` conditions are checked like `If`. `Case` values: CR6 echoes the text after `Case `,
  preceded by one `Case outside Select.` per item up to the first failing value (`Case 1, 2,
  Nope` -> three); none when an `Is` comparison or a `To` upper bound fails. CR300: per name; a
  `Select Case` with an undefined name opens no block (`Case outside Select.`, `EndSelect w/o
  Select`).
- Built-in function written as a statement (`Timer(Nope,Sec,0)`): CR6 `Invalid symbol in
  expression: <whole statement>.`; CR300 per name (plus `Dimensions not declared` for an
  undefined indexed name).
- Constant index out of bounds in instruction arguments: `Battery(A(9))` for `Public A(5)` ->
  `Variable A out of bounds.`. Only a plain Float-array element with number-literal indexes at
  parameter codes 2/8/28/41 (not `AddPrecise`/`MovePrecise`); index 0 or past a dimension.
  `A(-1)`, `A(6)+1`, Long arrays and assignments are not checked here; codes 27/29 (Long/String
  arrays too) are covered, for probed instructions only, in "Destination bounds" below. Pass 2 on CR6/CR1000X;
  CR300/CR350 only in an otherwise clean file.
- `ESSVariables`: the table has 217 entries per exe (CR6 `DAT_005fd008`, 20-byte records: type
  byte 0xb String / 0xff Long count x 16 / else Long, count, name pointer). Messages: `ESS
  variable %s must be declared as String|Long`, `ESS variable of type string must be of size
  255`, `ESS variable %s must be dimensioned as %d` (no trailing period). All failing entries
  are reported at the `ESSVariables` line; the statement declares the remaining names.
- `MenuPick`: a `MenuItem` on a ConstTable constant takes one `MenuPick` whose first parameter
  must be a ConstTable constant (`When using a ConstTable constant in MenuPick, the constant name
  must be the first parameter.`); an ordinary variable's item may repeat `MenuPick`. A `MenuPick`
  argument naming a variable -> `%s must be declared as a Const.`
- `Scan` units outside 0..5 (including any string) count as 0 uSec on CR6/CR1000X -> `Scan 0uSec
  invalid must be at least 1mSec.`
- Scan budget (CR6/CR1000X): `Delay(0,...)` calls in the main `Scan` (x n inside
  `SubScan(0,0,n)`) adding up to the interval -> `Scan interval too short for measurement by N
  <unit>.` at `NextScan`, N = floor((delays - interval)/unit) + 1 in the scan's own unit
  (`Scan(50,mSec)` + `Delay(0,1,Sec)` -> `by 951 mSec`).
- CR300/CR350 argument abort: the first undefined name at a runtime-expression parameter (codes
  2, 8, 24, 25, 27, 28, 29, 33, 40, 41, 47) is followed by `Too many parameters:  <next
  argument>.`; an undefined name at a code-20 slot also gives `Variable illegal in Parameter`.
- Measurement Reps (second argument): CR6/CR1000X `Invalid reps %d, must be > 0` for Reps <= 0
  and `Variable %s out of bounds.` when the Float destination has fewer elements from its start
  than Reps (negative Reps always); CR300/CR350 `Invalid reps 0, must be > 0.`; only instructions
  probed per logger (decompiled shared check `FUN_005163b0`).
- `SerialOpen` with a string or variable ComPort: `ComPort 0 illegal.` then `Parameter must be a
  constant integer: <statement>.` (pass 1, all loggers).
- A literal where a code-29 destination is required (`RealTime(5)`): `Invalid Variable or Array:
  5.`
- Table member reference with more than two arguments: `No Matching, or out of place )` with the
  statement.
- `Tb.EventCount` on a table without `DataEvent`: `DataTable Tb must have DataEvent() to request
  EventCount.` (pass 2 on CR6/CR1000X; CR300 only in a clean file).
- `Const Pi = 4*ATN()`: `Invalid, or out of place expression: 4*ATN().` (value up to any
  comment).
- Recursive Function call: `Cannot recursively call functions.` (pass 1).
- `DataTable (Ten Sec,True,-1)`: the name is read up to whitespace (`Ten`), the rest shifts into
  later parameters.
- Background calibration warning also when measurement delays leave under 3 mSec of the scan.


## Built-in function name collisions

The four supplied compilers reject a global `Public`, `Dim`, or `Const` whose name matches an
extracted built-in function (case-insensitively), preserving the declared spelling in
`<name> already defined as an internal function.`. `Public` and `Const` use this namespace even
inside a Sub/Function body. Local `Dim` and parameter declarations can use those names; this
does not imply that every profile resolves later bare uses to the local variable.

This is a pass-1 declaration rule: CR6/CR1000X suppress later pass-2 diagnostics after a
collision; CR300/CR350 start their normal `Too many parameters` cascade. CR6/CR1000X report every
colliding name in a multi-variable declaration. CR300/CR350 stop after the first collision and,
if another comma remains after the following name, report the next name: `Public Exp, Sin`
reports only Exp, whereas `Public Exp, Sin, Cos, Log` reports Exp then `Too many parameters:
Cos.`. Names that failed this check are not registered as new variables/constants.

Evidence: 78 observed cases in `tests/fixtures/function_name_collision.json`, replayed by
`tests/test_function_name_collision.py`, include case/spelling, global declarations, routine
locals and parameters, routine-local Public/Const, declarations after a closed routine, mixed
and all-colliding declaration lists, and following instructions. The two CR300/CR350 `Float`
probes exercise the separate [predefined constant rule](#predefined-constant-name-collisions) and
are explicitly excluded from this rule. The additional local-use probe remains scratch evidence in
`scratch/builtin-local-shadow-probes.json` rather than part of the 78-case fixture; CR6's accepted
source is also used by `tests/test_expression_expects_paren.py`.

Implementation: global checks are integrated per variable into
`structure._check_duplicate_declaration`, which calls
`symbols.report_function_name_collision` alongside duplicate and predefined-name checks in source
order. Routine declarations use `symbols.check_local_name_collision`, which checks both collision
families in source order while retaining the local `Dim`/parameter shadowing exception. Structure
members are skipped by the surrounding declaration scan.

## Predefined constant name collisions

The extracted `data/<profile>/symbols.json` table defines the reserved predefined-CONST namespace
for each logger. A declaration whose name is present in that logger's table is rejected,
case-insensitively, as `<name> is a predefined CONST.` with the source spelling preserved. This is
profile-specific: a name absent from a logger's extracted table is not rejected merely because a
different logger defines it. Observed accepted examples include `U1`, `mV5000`, and `ComC1` on the
CR300/CR350 profiles where those names are absent, and `U1CR1000X` where it is absent. `NAN` is
also accepted as a declaration name; it is a separately recognised expression constant and is not
an entry in the extracted symbol tables.

CR6 and CR1000X have an evidenced exception for these numeric type names even though they occur in
their symbol tables: `FLOAT`, `LONG`, `BOOLEAN`, `IEEE8`, `IEEE4`, `BOOL8`, `UINT1`, `UINT2`, and
`UINT4` may be declared. `STRING` and `FP2` remain reserved. CR300 and CR350 do not use that
exception: their per-profile extracted symbols are reserved as listed.

The implemented declaration coverage is:

- global `Public`, `Dim`, and `Const` declarations;
- `Public`, `Dim`, and `Const` declarations inside a Sub/Function; and
- Sub/Function parameters.

Members declared inside `StructureType`/`ObjectType` bodies are exempt because they occupy the
structure's member namespace; the mixed probes specifically confirm that a structure member named
`Exp` is accepted even though `Exp` is a built-in function. This evidence does not establish
complete coverage for every other declaration-like instruction or for `Alias`, so those are not
inferred into the rule.

The collision is a pass-1 finding. On CR6/CR1000X it suppresses later pass-2 diagnostics in the
usual way. A failed reserved `Public`/`Dim` name is nevertheless registered in the modern
declaration state, so a later declaration can report the ordinary duplicate-variable error. When
the same spelling appears in a constant-only instruction argument, the built-in predefined
constant still wins lookup over that failed variable declaration. A failed reserved `Const`, by
contrast, is not registered as a program constant.

CR300/CR350 stop a comma-separated declaration at the first predefined collision. If the collision
is at zero-based position *i* and an item at *i + 2* exists, that item supplies the accompanying
`Too many parameters:  <name>.` diagnostic; their normal later `Too many parameters` cascade also
applies. This is the same single-pass profile behavior for global declarations, routine locals,
and parameters.

Mixed function/predefined declarations follow source order. On CR6/CR1000X, `Public Sec, Exp`
reports the predefined collision for `Sec` and then the internal-function collision for `Exp`;
`Public Exp, Sec` reports those same two families in the reversed order. CR300/CR350 retain their
first-collision stop rule, regardless of which collision family appears first, with the same
ordinal *i + 2* `Too many parameters` behavior described above.

Evidence is the 276-case `tests/fixtures/predefined_name_collision.json`, drawn from the current
`scratch/predefined-namespace-probes.json`, `scratch/predefined-boundary-probes.json`, and
`scratch/predefined-order-probes.json`, plus `scratch/predefined-mixed-probes.json`. It covers the
four supplied compiler profiles, namespace boundaries, per-profile missing names, ordering,
duplicate interaction, pass suppression/cascade, constant-argument lookup, and mixed
function/predefined collision order. Implementation uses
`symbols.report_predefined_name_collision` and `report_function_name_collision`: global namespace
checks are integrated per variable into `structure._check_duplicate_declaration`, while routine
locals use `symbols.check_local_name_collision` for both families.
`Profile.declarable_predefined_names` holds the two modern profiles' explicit exception set.

## Duplicate field names

`Duplicate field name <name> in DataTable <table>.` has no `line N:` prefix. Observed on all four
supplied compilers (fixture date 2026-10-02, hashes in `tests/fixtures/duplicate_fields.json`).
This section supersedes the earlier "one message per repeated name" description in
`docs/instructions.md`.

Emission. It is a table-finalisation finding, so it appears only when no other error was found:
with any pass-1 or pass-2 error elsewhere in the file (including CR300/CR350 undefined-name
cascades) the duplicate messages are absent. Tables are reported in declaration order. It also
replaces the `Table X, is declared but never called.` warning: a file with any duplicate error
prints no such warning, for any table. Without a duplicate that warning is unchanged.

Pairing. Messages are per conflicting pair, not per name: a name on n conflicting fields gives
n(n-1)/2 messages (2 fields: 1, 3: 3, 4: 6, 12: 66). Messages are ordered by the later field of the
pair, one per earlier conflicting field, and print the later field's spelling (`FieldNames("Xy")`
then `"xY"` prints `xY`; a source variable prints its declared spelling). Matching is
case-insensitive.

What conflicts. Field names are the usual defaults (`<src>`, `_Avg`, `_Min`/`_TMn`, ...) or
`FieldNames` overrides, as derived by `tables.py`. Two fields with the same name conflict when
either (a) both were named by `FieldNames`, or (b) their source-element ranges overlap. Array
sources use element ranges: `Sample(1,w(1))` + `Sample(1,w(2))` is accepted; `Sample(3,w())` twice
gives one message, `Sample(3,w())` + `Sample(1,w(2))` gives one, and `Sample(2,w())` +
`Sample(2,w(2))` gives one. A rename that leaves a remainder (`Sample(3,w())` + `FieldNames("w")`)
is accepted: the renamed element covers w(1) and the remainder w(2..3). Two names from one
`FieldNames("n,n")` do conflict. Distinct 2-D element sources (`m(1,1)`, `m(2,1)`) are accepted.

FFT. `FFT(x,...)` contributes a field `x_FFT` for every option 0-5 (the two reproducers' option 5
on `y` gives `y_FFT`). `FieldNames` after an FFT renames its leading elements and leaves the
remainder as `x_FFT` (`FFT` + `FieldNames("p")` twice gives `p`, `x_FFT`; three times gives
`p,x_FFT,p,p,x_FFT,x_FFT`). A single FFT is accepted. Before this change `FieldNames` following an
FFT renamed the instruction before it in `tables.py`; FFT now owns it. FFT fields are name-only:
they are excluded from the TDF and record size, whose FFT layout is still unmodelled.

CH400Status (CR6/CR1000X only; CR300/CR350 reject the instruction). `Sample(n,CH400Status,...)`
yields n copies of 32 member names (list in `instr_data._CH400_FIELDS`, recovered from message
order); n=1 is accepted, n>=2 duplicates. This explains `chm/ch400status_2.CR6`.

Error-buffer cap. The message text is truncated by a buffer: output is cumulative messages
costing `len(message)+2` (CRLF) until 4095 bytes; a message that does not fit is skipped while a
shorter later one can still fit. Evidence: n=11..14 identical fields give 55..91 messages (all);
n=16 with table `T` stops at 102 messages (102*40=4080); table name length changes the count
(78 with a 13-character name, 71 with a long field name); `ch400status_2.CR6` reaches exactly 4095
and ends with one short `MaxBoardTemp` message after longer ones were skipped. The file name does
not count. Whether earlier non-duplicate diagnostics share the buffer is not observed (they cannot
co-occur with duplicates).

Implementation: `tables.py` records every field, including name-only ones, in
`TdfTable._dup_fields` (`_OutField.renamed`, `.rng`, `.name_only`, `.ch400`, `.placeholder`);
`instr_data.check_duplicate_fields` applies the conflict and buffer rules, and runs before
`warnings.check`, which is skipped when it reports an error.

`Anonymous` is skipped only when `_OutField.placeholder` marks a generated label for a source
without a variable name. The name itself is not a sentinel: the real compilers report one
`Duplicate field name Anonymous in DataTable T.` for either two `Sample` outputs sourced from a
public variable named `Anonymous` or two `FieldNames("Anonymous")` overrides. Observed on all four
profiles, 2026-10-02; both cases are fixture-backed in `tests/fixtures/duplicate_fields.json`.

Evidence: 358 oracle observations in `tests/fixtures/duplicate_fields.json`
(`tests/test_duplicate_fields.py`) over all four loggers; the fixture carries the probe
sources (the originals were in the git-ignored `scratch/dupfield/`). Not covered (not probed or not matched): `SampleMaxMin` without a preceding
`Maximum`/`Minimum` (the real compiler gives a different pass-2 error), FFT length checks
(`x must be of length >= 16.`, so `FFT(x(1),...)` still prints a duplicate where the oracle prints
that error), `CH400Status` used without the instruction (oracle: `Invalid symbol in expression`),
and other table instructions that `tables.py` does not emit (Histogram4D, LevelCrossing, Rainflow,
ETsz, GOES/MQTT fields).

## Destination bounds (2026-10-02)

`Variable %s out of bounds.` for a destination variable that is too small. Observed against all
four compilers (CR6Comp f6a6efca..., cr1000xcomp 9c327f8b..., cr300comp 755329ee..., cr350comp
741ed270...), 2026-10-02; fixture `tests/fixtures/dest_bounds.json`, `tests/test_dest_bounds.py`.

Elements needed from the destination's start (a literal-indexed element, row-major, or the whole
variable; a bare `X()` or scalar holds one):

| Instruction | Needed | Loggers |
|---|---|---|
| `RealTime` | 9 | all |
| `Move`, `CalFile`, `AvgRun` | Reps (argument 2) | all |
| `LI7700` | 5 * Reps | CR6, CR1000X |
| `Therm107/108/109` | Reps | CR300, CR350 only (CR6/CR1000X accept an overrun) |
| `SplitStr` | Reps > 1 overruns a scalar only | all |

Observed boundaries: an exact fit compiles; Reps 0 compiles; `CalFile` with negative Reps is out of
bounds (the others were not probed with negative Reps). Float destinations only for the Reps and
`RealTime` rules (a Long array is accepted by `CalFile(L,11,...)` on CR6/CR1000X, and `CalFile`
rejects it as a type on CR300/CR350). `SplitStr` accepted an array destination with Reps 20 against
5 elements, so only the scalar overrun is a rule (Float or String scalar; a structure-typed scalar takes Reps 3, `chm/splitstr_5.CR6`). An `Alias` name as destination was not checked
(`RealTime(RA)` with `Alias RT10(2) = RA` compiles, an exact fit, but no overrun was probed).

Spatial family (same day, all four loggers; fixture cases `*-avgspa-*`, `*-maxspa-*`, `*-sortspa-*`,
`*-sortspaindexed-*`; `SortSpaIndexed` exists on CR6/CR1000X only). `Instr(Dest, Swath, Source)`:

| Argument | Needs, from its start index (row-major) |
|---|---|
| Source (3rd), all seven | Swath elements (Float or Long; a negative Swath is out of bounds; Swath 0 compiles) |
| Dest of `AvgSpa`, `StdDevSpa`, `RMSSpa` | nothing beyond the index rule (`AvgSpa(A(5),2,B)` compiles) |
| Dest of `MaxSpa`, `MinSpa` | 2 whatever the Swath (even Swath 0 or 1, a scalar fails) |
| Dest of `SortSpa` | Swath (a negative Swath is out of bounds) |
| Dest of `SortSpaIndexed` | 2 * Swath (`A(3),2` into a 5-element array fails, `A(1),2` fits) |

Both arguments are reported when both overrun, destination first (`SortSpa(A(1),6,B)`). A
non-literal Swath is skipped. Exact fits compile. Only Float destinations are checked; a Long
destination also draws `Dest type must be Float.`, and String sources were not probed for overrun.

Code 29 index check (`ReadIO`, `PortGet`, `RealTime`) also covers Long arrays and Long scalars
subscripted past their size (`ReadIO(L(6),&B0001)`, `PortGet(L(0),C1)`, `ReadIO(R(2),..)` on
CR6/CR1000X; CR300/CR350 say `Dimensions not declared` for the scalar). A String array at code 29
is not checked (`ReadIO(S(6),..)`, `PortGet(S(6),C1)` compile). `MoveBytes` and `PortGet(A(5),C1)`
(an exact fit) compile.

Start-index rule (`expressions.check_instruction_index_bounds`): parameter code 27 (`Move`, `Erase`,
`GetRecord`, `SplitStr`, ... destinations) now also checks Long and String arrays, and code 29
(`RealTime`) Float arrays: a literal index of 0 or past the dimension is out of bounds (a negative
index compiles, as for the other codes). Only `Move`, `Erase`, `GetRecord`, `SplitStr` and
`RealTime` were probed; other code-27/29 instructions are assumed to share the check.

Order. CR6/CR1000X: appended in tier C after `check_reps`. CR300/CR350: inserted before the other
diagnostics of the same line (`Variable Temp out of bounds.` precedes the fnotch warning and
`Channel exceeds number of valid channels.`). Implementation: `instr_measure.check_dest_bounds`
(tables `_DEST_*`).

Reproducers fixed: `fuzz/cr6/false_accept/{e39c13d64518,f42c834f03b4}` (RealTime into a size-1 or
size-3 array), `2e18cd532da2` (AvgRun Reps), `6f3c11c5579c` (LI7700 Reps 3 on a 5-element array),
`fuzz/cr300/false_accept/{547e601c6c26,a71cffb33409}` (`SplitResult(0)`), `6c8fb0434bdb`
(`Therm109(Temp(),2,...)`), `fuzz/cr1000x/false_accept/49c0752ccf0e` (`CalFile(array2,25,...)`).
Not covered: negative Reps for instructions other than `CalFile`, CR300/CR350 measurement
instructions other than `Therm107-109` (not probed), Reps beyond 32 bits.

## Block structure (2026-10-02)

Fixtures: `tests/fixtures/block_structure.json` (`tests/test_block_structure.py`), real-compiler
outputs recorded 2026-10-02 with the compiler sha256 per logger in the fixture.

### Misplaced Sub / Function

`structure.first_misplaced_sub`. A `Sub`/`Function` declared while another is open prints, all at
the declaring line, in this order: `Sub declared within a Sub.` (also for Function in Function,
Sub in Function and the reverse; the text never says Function), `Sub declaration inside
If..EndIf.` (open `If`/`Select`), then `Sub is declared in the middle of an executing sequence`
(`Function is ...` for a Function; no period). The last message also appears alone for a
declaration inside an open `Scan`, between `BeginProg` and the first `Scan`, inside a `DataTable`
or inside an `If`/`For`/`Do` in the main program. Accepted: after the first `NextScan`, after a
`SlowSequence` header, after `EndProg`. An open `For` or `Do` remains an executing context even
after a completed `Scan`, so a following `Sub` is misplaced.

It is a pass-2 placement error: earlier pass-2 errors in line order still print, nothing later does;
a pass-1 error anywhere hides it. CR300/CR350 report it in the late walk
(`check_scan_placement_late`); any error elsewhere, including an unrelated `X is not defined.` after
it, hides it, and with no other error there is no `Too many parameters` cascade.

Routine names share one case-insensitive namespace. A second `Sub` or `Function` is `Duplicate Sub
<new spelling>` (also at top level); either routine kind sharing a global variable's name is `<new
spelling> already declared as a variable.`. When a nested declaration has more than one problem,
these messages follow `Sub declared within a Sub.` and precede `Sub declaration inside If..EndIf.`
and the executing-sequence message. The real CR300/CR350 compilers suppress the placement group
when a later undefined-name diagnostic occurs; fresh probes on 2026-10-02 did not reproduce the
previous wording that the group survived that condition. Fixture: `tests/fixtures/routine_names.json`.

### ConstTable count

`structure.check_pass1` (pass 1, line order with the other declaration errors, all four loggers).
Every `ConstTable` after the first one seen, closed or not, prints `Only one ConstTable allowed.`
at its line. `EndConstTable` with no open `ConstTable` prints `EndConstTable with no ConstTable.`.
The open count rises for every `ConstTable`, even the rejected one: `ConstTable`, `ConstTable`,
`EndConstTable`, `EndConstTable` prints only the first message, and a third `EndConstTable` after
two opens gets the second message. A leading stray `EndConstTable` does not count as a table, so
the next `ConstTable` is accepted. Both messages start the CR300/CR350 `Too many parameters:
Sec.` cascade (`cr3xx._DECLARATION_TRIGGERS`) and, being pass 1, do not stop the rest of pass 1
(`Variable not named.` after them still prints). A duplicate `Const` name in the second table
additionally prints `A is already declared as a CONST.`.

### Slow sequence limit

`structure.check_pass1`, pass 1, all four loggers (CR300/CR350 too). Every `SlowSequence` past the
fourth prints `Too many Slow Sequences: max = 4.` at its own line, with or without a following
`Scan` (five bare `SlowSequence` lines give one message at the fifth; six give two). Counting
runs from the start of the file to the first `EndProg`; `SlowSequence` lines after it are not
reported, and a second `BeginProg` section is not counted separately (5 + 5 gives only the first
fifth). Being pass 1 it hides pass-2 errors (`X = Q`, `NextScan with no Scan.`, unclosed `Do`) and
prints alongside `Variable not named.`; it starts the CR300/CR350 `Too many parameters:  Sec.`
cascade (`cr3xx._DECLARATION_TRIGGER_PREFIXES`).

### Task instructions in PipeLine Mode

`structure.pipeline_task_errors` (CR6/CR1000X only; CR300/CR350 accept everything below). After an
explicit `PipeLineMode` statement earlier in the file, and no `SequentialMode` before it, a
measurement/task instruction prints at its own line:

- `X not allowed outside of Scan..NextScan in PipeLine Mode.` when it is outside every `Scan`:
  before the first `Scan`, after `NextScan`, in a `SlowSequence` before its `Scan`, or in a `Sub`
  body (also when the `Sub` is called inside a `Scan`);
- `X not allowed inside of a conditional statement in PipeLine Mode.` inside a `Scan` but nested in
  an `If`, `Select`, `For`, `Do`/`While` (or a one-line `If`). A `Select Case` that is closed
  before the instruction does not count (unlike the auto-mode decision in `mode.py`, which has a
  quirk there).

`X` is the instruction's display name (`VoltSe`, `TCSe`). The profile data files
`data[/cr1000x]/pipeline_tasks.json` contain the measured list: `Delay` (constant-folded option 0 only: `Const Zero = 0`, `One-1`, `-0` all measured, 2026-10-03;
option 1 is accepted, option 2 prints `SDM instruction must be in Scan..NextScan, unless
SequentialMode is used.`, not implemented), `PanelTemp`, `Battery`, `VoltSE`, `VoltDiff`,
`PortGet`, `PortSet`, `Therm107`, `BrHalf`, `BrFull`, `TCSe`, `TCDiff`, `PeriodAvg`, `CS616`, and
`ExciteV`. `PortSet` is a measured exception on CR6: outside a Scan is accepted, but conditional
placement remains rejected; CR1000X rejects both. `tests/fixtures/pipeline_tasks.json` records an
accepted in-Scan call and both boundary placements for each member on each logger. Not implemented
because not probed or different: the other
`mode.json` task instructions (`CDM_*`, `CS7500`, `EC100`, ...), `SW12`, and
`SerialOutBlock`/`SerialInRecord` (accepted outside a `Scan`).

The error is pass 2 and accumulating: it does not end the pass, prints together with `x=q`
errors in line order, and is cut off by an earlier placement error. Unlike other tier-B errors it
does not hide the tier-C checks: it prints alongside the instruction's own `Invalid channel ...`
and the `Scan interval too short ...` errors, in front of them. Two same-line exceptions are
profile-specific: CR6 `ExciteV(1,...)` prints its first channel error, then the PipeLine Mode
error, then its excitation-channel error; CR1000X `PortSet(109,...)` prints the invalid-port error
before the PipeLine Mode error. Fresh real-compiler fixture cases
`pipeline-excitev-argument-first-cr6` and `pipeline-portset-argument-first-cr1000x` recorded both
on 2026-10-03. It does hide the
end-of-program block checks like any pass-2 error. `PipeLineMode` and `SequentialMode` are
declarations, never `never reached at run time.`. A dead `Delay` that gets this error prints no
reachability warning of its own, and the walk continues (the next dead statement is flagged); a
dead `PanelTemp` prints both.

### SlowSequence inside an open block

`structure.check_slow_sequence_blocks` (CR6/CR1000X; CR300/CR350 accept). At a `SlowSequence`
line, one message per kind of block still open, in this fixed order (not by nesting): `FOR .. NEXT
cannot span across sequences.`, `DO .. LOOP cannot span across sequences.` (`Do` and `While`),
`IF or SELECT cannot span sequences.` (`If` block and `Select`). Two open `Do`s give one message;
every later `SlowSequence` in the same open block repeats it. Depth is counted per kind, so `Do`,
`If`, `Loop`, `SlowSequence` reports only the `If`. A one-line `If` is not a block.

Pass 2, accumulating (prints with `x=q`); a pass-1 error hides it. Like the PipeLine Mode errors it
does not hide tier C (`Invalid port ...` still prints before and after it) but hides the end-of-
program block checks (`DO without LOOP.`), so both live in `check()`'s `pipe` list. Not
implemented: on CR300/CR350 the `Too many parameters:  x.` cascade into the `Loop While x=1` line
after an `x=q` error.

### DataTable placement

`DataTable is declared in the middle of an executing sequence` has no period. It is emitted at the
`DataTable` line when the declaration is between `BeginProg` and the first sequence, or inside a
`Scan`, `If`/`Select`, `For`, or `Do`; it stops the relevant placement walk. A table before
`BeginProg`, after `NextScan`, and directly after a `SlowSequence` header is accepted. On
CR6/CR1000X, `DO .. LOOP cannot span across sequences.` suppresses this table message after the
same `SlowSequence`; CR300/CR350 do not report that span error and report the table placement
instead. The CR300/CR350 check runs before table-body validation so `Sample` does not produce its
own conditional-placement diagnostic first. Fixture: `tests/fixtures/datatable_placement.json`.

## Expression-level false accepts (2026-10-02)

Fixture: `tests/fixtures/expression_false_accepts.json`, audited against all four real compilers
on 2026-10-02. The focused probes used `scratch/probe.py` with accepted boundary forms alongside
the failures.

- An empty call to a one-argument built-in is `Invalid, or out of place expression: %s.` rather
  than an arity-message when it occurs as an instruction argument or an `If` condition. `CHR()`
  inside `SplitStr` and `SerialInChk()` inside `If ... Then` have that result on all four loggers;
  `CHR(65)` and `SerialInChk(ComRS232)` are accepted. The instruction-argument echo is the
  argument only; the `If` echo is its condition, including the space before `Then`.
- `ESSInitialize` has one declared argument. A second argument yields `Too many parameters:
  %s.` on all four loggers. This remains true when its second argument starts an unterminated
  quote, so the raw `ESSInitialize((1+2),public")` line must be recovered after lexing. CR300 and
  CR350 then emit their ordinary later `Too many parameters:  Sec.` cascade.
- CR300 and CR350, but not CR6 or CR1000X, require a string literal, String variable, or String
  Const for the direct `MQTTPublish` payload argument: a numeric literal, Float variable, or
  Boolean variable reports `A string or string variable is required`. This checker intentionally
  leaves composite payload expressions unmodelled.
- A `ConstTable` member must be a `Const` declared while that table is open; a missing member
  reports `Could not find <Table>.<Field>(1).`. `SetSetting("<ConstTable>.<Field>",...)` then
  reports `Cannot find <Field>` for the same absent field. The complete DataTable field-naming
  grammar is not inferred: the independently observed misspelled `TableBytesX` member is checked,
  while `TableBytes` is accepted by CR6/CR1000X in the probe.

## Declaration and block-end diagnostics (2026-10-03)

Fixtures: `tests/fixtures/decl_*.json`, run by `tests/test_fixture_decls.py`; every expected output is
the real compiler's, taken on 2026-10-03 (compiler hashes in each fixture). The probes were made
with `tools/probe.py` against all four compilers, including accepted boundary forms.

### Stray `EndSub`/`EndFunction` and `Illegal END or EXIT`

- `EndSub`, `EndFunction`, `End Sub` and `End Function` with no `Sub`/`Function` open give
  `EndSub without Sub or nested subs.` (one text for all four spellings), all four loggers. The
  open `Sub` is closed by either `EndSub` or `EndFunction` (a `Function` closed by `EndSub`
  compiles), so only a closer with nothing open is reported. It is a pass-2 placement-style check
  that does not stop the pass (`structure.stray_controls`; CR300/CR350 report it in the late walk,
  with no `Too many parameters` cascade).
- `End <word>` / `Exit <word>` with a single following word the compiler does not know gives
  `Illegal END or EXIT <word>.` (the word as written), a pass-1 error that does not stop
  anything: `End Sequence`, `Exit Foo`, `End Structure`, `End Case`, `End Loop`, `End Type` in a
  `Scan`, a `Sub`, a `DataTable` body, before `BeginProg` and after `NextScan`. On CR300/CR350 it
  starts the `Too many parameters` cascade like the other declaration errors. On CR6/CR1000X a pass-1
  error hides the reachability warning (`End never reached at run time.` is not printed).
- Not modelled (left silent): a word that is a predefined symbol or built-in function. The compiler
  maps those through other tokens: `End Min` is `EndSub without Sub or nested subs.`, `End Day` is
  `EndTable without DataTable.`, `End Float` and `Exit Select` are `END ???`/`EXIT ???`, `Exit
  Float` is `Exit For without FOR.`, `End ASCII` is the ordinary `Illegal END or EXIT` (this
  project instead prints an unrelated function-arity line for it). Extra words after the first
  (`End Sequence 5`) also add `Too many parameters: 5.` and are not modelled.

### `ReadOnly` in structures and subs

`ReadOnly <name>` reports `<name> not yet declared.` (one line per unknown name, pass 1, all four
loggers) in a `StructureType` or `Sub`/`Function` body too, not only at top level. The name must be
a global variable declared before the `ReadOnly` or, in a structure, an earlier member of the same
structure. A `Const`, a global declared later, another structure's member, a `Sub` local or a
parameter is not declared (`ReadOnly Zq` after `Dim Zq` in a `Sub` fails). `ReadOnly A(1)` and `ReadOnly 5`
are not modelled.

The name list is split by commas or blanks and empty items are ignored (oracle, all four loggers,
2026-10-03): `ReadOnly A,`, `ReadOnly ,A`, `ReadOnly A,,B` and `ReadOnly A B` compile (a trailing comma
used to be a false reject). A list with no name at all (`ReadOnly`, `ReadOnly ,`) reports `Missing variable
name.` (pass 1, top level and in a `Sub`; it taints CR300/CR350 like the other declaration errors).
Evidence: `tests/fixtures/small_gaps.json` (`*-readonly-*`).

After `EndStructureType` the structure's members are no longer declared: a later `Sub` with
`ReadOnly A` (member `A` of the closed structure) fails with `A not yet declared.` on all four loggers
(oracle, 2026-10-03; `tests/fixtures/readonly_struct_scope.json`).

### Structure member types

A `StructureType` member whose `As` type is not a variable type and not a `StructureType` declared
earlier in the file (itself included) reports `Variable Type <type> not supported` (no period), all
four loggers: `A As String_Fz`, `A As Floatx`, `A As SU` before `SU` is declared. Each bad member
gets a line on CR6/CR1000X; CR300/CR350 stop at the first bad type in a statement
(`A As Foo, B As Bar` and `Public Z As Bad, Y As Worse` give one line), which is now also applied to
the existing `Public`/`Dim` check. `Sub`/`Function` headers: see below.

`Sub S(P As Bad)`, `Function F(P As Bad)` and `Function F(P) As Bad` report the same message on the
header line (oracle, all four loggers, 2026-10-03): every bad `As` type in order, parameters first
and then the return type, on CR6/CR1000X; only the first on CR300/CR350. A `StructureType` declared
earlier is valid, one declared later is not; `Integer`, `UInt2` and the like are bad, `UInt1` and
`String * 4` are fine, the written spelling is echoed (`bad`). Not modelled: `ByRef` parameters (real:
`Invalid Variable declaration P.`), `P() As Bad` and `P As Bad(3)` (extra lines), a `Sub` nested in
another `Sub`, and CR300/CR350 with three or more bad types (extra `Too many parameters:  As.`).
Evidence: `tests/fixtures/small_gaps.json` (`*-hdrtype-*`).

### `Alias` with fewer indices than dimensions

`Alias W(5)` on `Public W(4,5)` gives `Illegally dimensioned aliased name`, all four loggers.
Missing trailing indices count as 1 in the flat-offset test of "Alias dimensions": `W(1,4)` on
`W(2,3,4)` compiles, `W(2,4)`, `W(3)` and `W(1,0)` do not, while `W(4)` on `W(4,5)` does. More indices
than dimensions stay unchecked. `Alias W(5) = A1, A2` reports the message once per alias name, and an
`Alias W2(5), W1(2) = A1` source list is not modelled.

### Structure member and assignment checks

`checker/structs.py`, plain (non-compound) assignments only; a statement with an undefined name, a
nested structure member, a pointer or any other unmodelled operand is left silent.

- **Unknown member** (`inp.flagX = 1`, `G = sa(1).flagX`, `inp.value.x`): CR6/CR1000X report
  `In structure <name>, could not find .<member path>` for every access, left side first, as a
  pass-1 error (later pass-2 errors disappear). CR300/CR350 report `Could not find <name>.<path>(<n>).`
  (`n` is the index argument of the member, else 1); a bad left side ends the statement with
  `Invalid assignment in expression: <line>` and the right side is not examined. No cascade.
  Unknown members in `If` conditions and instruction arguments give the same lines but are not
  modelled, and combined with an undefined name the order differs, so those stay silent.
- **Structure against non-structure** (CR300/CR350 only; CR6/CR1000X accept every one of these): a
  structure target with a non-structure source (`inp = 5`, `inp = G`, `sa(1) = H(1)`, `inp = -inp`)
  gives `Structured Variable assignment illegal, wrong type`; a non-structure target with a
  structure source (`G = inp`, `H(1) = inp`, `G = sa(1)`, `G = (inp)`) gives `Illegal structure type
  assignment`. `name()` of a structure array adds `Invalid, or out of place expression: <line>`
  (`inp = sa()`, `G = sa()`), and as a target always gives the wrong-type line then the invalid line
  (`sa() = inp`, whatever the source). A scalar structure member as source (`inp = inp.value`) is
  accepted by every target except `sa()`. A variable declared with a parenthesised dimension
  (`Public s1(size,(1+2)) As ST`) is a Float array, so `s1(size) = inp` gives `Illegal structure type
  assignment`. Different structure types assigned to each other were accepted in the probe and are
  not checked. No cascade; the fnotch warnings are still printed.

### `Sub`/`Function` named like a `Const`

A `Sub` or `Function` whose name equals an earlier top-level `Const` (any type, case-insensitive) reports
`Subroutine name <name> illegal; cannot be numeric.` on its header line, all four loggers, pass 1
(oracle, 2026-10-03). The name is replaced by the constant's value first, so a value that is not a
whole number (or a whole one of 3e9 and up) adds `Parameter <value, printf %g> is integer type.` before
it (`1.5`, `1e-05`, `123457`, `3e+09`). The routine is not registered: a second `Sub A` reports the
same line again instead of `Duplicate Sub A`. A `Const` declared after the `Sub` does not count.
Parameters and the body are checked as usual (the statement still compiles on). CR300/CR350 taint
the rest like other declaration errors. Not modelled: a `Function` body assigning its own
const-named return value (real: `Invalid assignment in expression: A = 2`), a const-named `Sub`
nested in another `Sub` (real: only the numeric line; ours prints the placement lines), whole
values between 2^31 and 3e9 or below -2^31 (CR6 and CR300 disagreed on 2147483648).
Evidence: `tests/fixtures/small_gaps.json` (`*-constsub-*`).

### Undefined name in an assignment before `BeginProg`

`X = Nope` (undefined name on the right side, also inside a longer expression, an index, or `X = Y = 1`)
before `BeginProg` gives, on CR6/CR1000X, `Invalid symbol in expression: <line>.` followed by
`Expr illegally occurs before BeginProg.` on the same line, and compilation stops there (oracle,
2026-10-03): a later `X = Nope2` or `Nope3` is not reported. A one-line `If X = Nope Then X = 1`
gets `If illegally occurs before BeginProg.` after the symbol line. The placement line sorts after the
other messages of its line (`compiler.py`). CR300/CR350 give only `Nope is not defined.` and go on.
Evidence: `tests/fixtures/small_gaps.json` (`*-prebp-*`).

### Structure variable with more than one index

A structure variable reference takes at most one index (`check_function_arity`, pass 1; oracle,
2026-10-03, all four loggers). `s2(1,2)` is rejected even for `Public s2(3,4) As s`; a member's own
index is separate (`s1(1).value(2)` compiles). Destination (whole structure or member, also
compound `+=`): `Invalid, or out of place expression: <stmt>.`. In a value, `If` condition or
instruction argument: `No Matching, or out of place ) in expression: <stmt>.` (same text and CR300/
CR350 newline form as a bad built-in arity; instruction arguments echo the argument). On CR300/CR350
only, a whole-structure target with a whole `s1(10,2)` value adds `Structured Variable assignment
illegal, wrong type` first. CR300/CR350 drop all of these when the file has a call-arity error
anywhere. Pass 2's `Dimensions not declared` skips these statements.
Not modelled: output instructions outside a table keep `must be used inside a table declaration.`
(skipped); only the first error of a `DataTable` is reported (ours reports each); CR300/CR350
`Too many parameters` cascades after instruction arguments (same as for bad built-in arity).
Evidence: `tests/fixtures/struct_index.json`.

## Block placement order (2026-10-03, CR6/CR1000X unless noted)

Observed with the real compilers; evidence `tests/fixtures/{unknown_calltable_stop,
table_structure_order,datatable_body_expr,scan_value_errors,nested_scan,stale_closer,
return_placement}.json`, test `tests/test_block_placement.py`.

- **Unknown `CallTable` target** (`Could not find DataTable X.`) ends pass 2 like a placement error: a
  later `NextScan with no Scan.` or `x = 1` is not reported (CR6/CR1000X; CR300/CR350 order these
  late and differ, not modelled).
- **End-of-program checks come after tier C.** `No BeginProg in Program.`, `Scan without NextScan.`
  and the other unclosed-block messages are reported only when passes 1 and 2 *and* tier C found
  nothing; an unclosed `Scan`/`If`/`For` plus an orphan `EndTable`, a `Sample` outside a table or a bad
  channel reports only the latter. All four loggers.
- **Orphan `EndTable`** (`EndTable without DataTable.`) and an `EndTable` of a table with no output
  fields end pass 2 (a later `Sample` outside a table, bad channel or placement error is not
  reported); a `Sample` outside a table before it is still reported.
- **`DataTable` body**: an unresolved statement there gets only `Invalid, or out of place
  expression`/`Invalid symbol`, never `Expr illegally occurs before BeginProg.`, and a resolving
  assignment or built-in call is silent (a `ConstTable` body still adds the placement line).
- **`Scan` value errors** (`Scan Interval/BufferOption/Count parameter must be a non-negative
  integer`, `Scan %duSec invalid must be at least 1mSec.`, `Scans must be a multiple of 1mSec.`,
  `Maximum Scan interval is 1 Day.`, `DataInterval ... non-negative`) are pass-2 findings: hidden by
  any pass-1 error, but they gate neither the rest of pass 2, tier C, nor `Scan illegally occurs before
  BeginProg.` (reported after them on the same line); they hide the end-of-program checks and
  `Can't assign units`. Order on one line: Interval, its minimum/multiple message, BufferOption, Count.
  A negative interval at or below -1000000 uSec gets no minimum message (`Scan(-1,Sec)`,
  `Scan(-1000,mSec)`); `-1000001 uSec` gets `Scans must be a multiple of 1mSec.`; `-999999 uSec` the
  minimum message. A `CallTable` of a `DataInterval` table inside a literal-zero `Scan` gets neither
  placement message (auto-sized without `DataInterval`: `Cannot autoallocate...` as before).
- **Nested `Scan`**: a `Scan` while another is open (zero interval, `SlowSequence`, inside an `If`
  too) gives `Scan without NextScan or nested Scans.` at the inner `Scan`; pass 2 goes on, tier C is
  not gated, and the end-of-program `Scan without NextScan.` is replaced (CR6/CR1000X pass 2;
  CR300/CR350 print it late and only when nothing else is wrong).
- **Stale closer slot**: a closer that pops a `Scan` (`Scan` / `EndIf` ...) leaves the `Scan` in its
  block-stack slot, so every later block opened at that depth reports `<kind> statement illegally has
  SCAN without NEXT SCAN.` at its own closer (a one-line `If` at its own line); a stray closer at
  depth 0 reports only its `without` message. All four loggers.
- **`Return`** outside a `Function` is a pass-1 error, all four loggers: `Return outside of
  Function.` (any context except a `Sub`), and with arguments `Too many parameters:  <first argument
  token>.` (once, even for `Return 1,2`). In a `Sub`: `RETURN illegal inside Sub` (no period) with
  an argument, `Invalid or missing parameter.` bare. CR300/CR350 cascades after it are not modelled.

Follow-up (oracle, 2026-10-03, `tests/fixtures/struct_index_call.json`): a `Sub` call argument
(`Foo(A(1,2).V)`, `Call Foo(...)`) is rejected on all four loggers, echoing that argument alone
(leading blanks dropped, trailing kept). On CR6/CR1000X only the first DataTable output
instruction with such a reference is reported (not one per instruction or table), and none at all
once a statement outside a table has one.

### Instruction arguments with adjacent operands (CR6/CR1000X)

Oracle, 2026-10-03 (`tests/fixtures/instr_arg_adjacent.json`): `Keyword(a, b, ...)` whose argument
has two operands in a row (`Sample(1,x  y,FP2)`, `Scan(1,S  ec,3,1)`) is still recognised as the
instruction; each argument is read as an expression on its own, left to right. The echo is that
argument alone (leading blanks dropped, trailing kept). An adjacent known-operand pair is pass 1
`Incomplete expression: <arg>.` (every such argument is reported; inside a plain `( )` group it is
`No Matching, or out of place ) in expression:` instead, also for assignments). An unresolved name
reached first is pass 2 `Invalid symbol in expression: <arg>.`, which stops the scan; after it only
the `<Name> illegally occurs before BeginProg.` placement line follows, for instructions in
`profile.before_beginprog` (not `Scan`/`Delay`). Not modelled: `Variable illegal in Parameter`
after a malformed constant-only argument, `Delay`'s `Measurement delay must have constant
interval.`, and several unresolved arguments in one instruction (the real compiler reports them
all, in an order that is neither textual nor by one simple class split).

### Constant destination in a statement short of parameters (CR6/CR1000X)

Oracle, 2026-10-03 (`tests/fixtures/short_constant_dest.json`): `PanelTemp(15000)`,
`CDM_PanelTemp(CDM_A108,1,1,1,15000)`, `MovePrecise(.5)` print `Invalid Variable or Array: <arg>.`
for the constant at the destination position, then `Invalid or missing parameter.`, on the same
line. CR300/CR350 are unchanged. Not modelled: the real compiler also reports a later complete
statement's constant destination (`PanelTemp(3,15000)`) after a short one, which our pass-1
gating suppresses.

### Alias on a non-positive declared size; `Illegally dimension` newline

Oracle, 2026-10-03 (`tests/fixtures/alias_nonpositive_dim.json`, all four loggers): a declared size
of zero or below (itself `Illegally dimension`) counts as 1 for the alias index check, in every
dimension (`Public a(0)` / `Alias a(1)` passes, `a(2)` and `a(0)` fail). The `Illegally dimension:
<statement>` echo carries the source line's newline, so a blank line follows it whenever more
output does; the final output is right-trimmed (`compiler.py`).

## `Illegally dimension` echo and CR300/CR350 stop (2026-10-03)

Fixture `tests/fixtures/illegal_dimension_echo.json`, `tests/test_cr3xx_cascade.py` (52 cases, all
four loggers). `Illegally dimension: <statement>` echoes the statement through its line end: on
all four loggers a blank line follows it when another message comes next (none when a `'` comment
follows the statement). On CR300/CR350 it starts the `Too many parameters` cascade and stops the
declaration at its first bad variable (a variable with two bad dimensions still reports both,
`Variable A(..) dimension exceeds max of 3.` stops it too); the variable two positions on supplies
`Too many parameters:  <name>.` (`Public Tx(-1), Ty(2), Tz(0)` -> `... Tz.`; with only two variables
nothing more). CR6/CR1000X report every bad variable.

## `Dimensions not declared` (CR300/CR350, 2026-10-03)

Fixtures `tests/fixtures/dims_not_declared.json` (88 cases) and `tests/fixtures/dims_state.json`
(110 cases), CR300 and CR350, in `tests/test_cr3xx_cascade.py`; code in `expressions.DimsState`,
`dims_state`, `_dims_walk`, `_names_and_dims`. The compiler keeps one running "dimension count":
every reference to a declared variable, in text order and across statements, sets it to that
variable's number of dimensions (scalar 0, array its declared count, `String` one more than
declared; the *last* reference in text order wins, so `Rv(Sc)` leaves 0). Constants, predefined
names and undefined names leave it alone. An undefined name met with more subscripts than the
current count prints `Dimensions not declared: <arg or statement>` after the `is not defined.`
lines (`Nope()` never does; the count is compared at the name, before its own arguments). The
count at the start of the first statement is 0. Examples (`Public Sc, Rv(5), Mm(2,3)`):
`Sc = Nope(1)` and `ClockReport(Sc,Nope(1),..)` print it, `Rv(1) = Nope(1)` and
`ClockReport(Mm(1,1),Nope(1,1),..)` do not, `Rv(1) = 1` then `Battery(Nope(1))` does not (the count
carries over) but `Rv(1) = 1`, `Sc = 2`, `Battery(Nope(1))` does. It ends the walk: names after
the triggering name are not reported (`Battery(Nope(1)+Nope2(1))` names only `Nope`). At a
constant-only position it is followed by `Variable illegal in Parameter: <arg>.`
(`Delay(Nope(1),2,Sec)`), elsewhere by `Too many parameters:  <next argument>.`; code 26
positions (`SplitStr` arguments 2 and 3) also end the list on an undefined name. An arithmetic
expression at a Float destination (`Battery(Sa(1)+Nope)`) prints its names, then `Dimensions not
declared` if the rule fires, else `Illegal destination variable type, expecting Float.`.
Not modelled: declarations with an initialiser, `Alias` names, table members and `Case` items do
not move the count (unprobed); a variable declared with different dimension counts in two scopes
disables the line.

## CR300/CR350 cascade exemptions: `Alias`, `ConstTable` (2026-10-03)

Fixture `tests/fixtures/alias_consttable_cascade.json` (28 cases). After the cascade has started
(`Too many parameters`, `docs/loggers.md`) an `Alias X(2) = Y` line and a `ConstTable (T,0)` line
are not rewritten into `Too many parameters:  <second argument>.` (a `DataTable (T,1,100)` is);
a tainted `Alias` still registers its name. An `Alias` whose subscript names an undefined variable
(`Alias Q(2,Nope) = NetDir`) is not registered at all: a later `NetDir` is `NetDir is not
defined.` (`expressions._known_names`). Not modelled: an `Alias` naming an existing variable
(`Alias Q(1) = LevelFt` -> `LevelFt already declared as a variable.`) is accepted by us on all four
loggers.

`... not yet declared so cannot be aliased.` also starts the cascade (fixture
`tests/fixtures/alias_undeclared_cascade.json`: `Alias Nope(1) = Foo` -> a later `Scan` prints
`Too many parameters:  Sec.`).

## Undefined names around a built-in arity error (CR300/CR350, 2026-10-03)

Fixture `tests/fixtures/arity_name_order.json` (50 cases), `cr3xx.order_names_around_arity`. In an
assignment or `If` condition the single-pass parser prints undefined names as it meets them, left
to right, and the arity error of a built-in call after them (`Sc = Nope3+Round(Nope1)` -> `Nope3`,
`Nope1`, `Expression expects , :`; we used to print the arity line first). A call with too many
arguments stops the parse at its first surplus argument, so nothing later is named
(`Floor(Sd,Nope)` -> none, `Floor(Nope1,Nope)+Nope3` -> `Nope1`); too few arguments let the rest of
the expression be parsed (`Round(Nope1)+Nope3` -> both). A first `few` followed by a `many` keeps
the `Expression expects , :` text and stops at the surplus argument. An assignment whose target is
itself undefined still gets the arity line (`Nope4 = Round(Nope1)`). Not modelled: the same order
inside instruction arguments (`Battery(Floor(Nope1,Sd))`, `Delay(0,Floor(Nope1,Sd),Sec)`).

## Unmatched block closers hidden by other errors (CR300/CR350, 2026-10-03)

Fixture `tests/fixtures/block_closer_suppression.json` (128 cases, all four loggers),
`cr3xx.drop_block_closer_errors_after_errors`. On CR300/CR350 `END IF without IF or END SELECT
without SELECT.`, `NEXT without FOR.`, `LOOP without DO.`, the three `... illegally has SCAN
without NEXT SCAN.` companions and the `<closer> illegally occurs before BeginProg.` line are only
printed when the file has no other error, whether that error comes before or after the closer
(`Sc = Nope1` then `EndIf`; `EndIf` then `Battery(Nope1)`; a duplicate declaration; `Too many
parameters`). Closer errors do not hide one another. `EndSelect w/o Select`, `Case outside
Select.` and the CR6/CR1000X behaviour (always printed) are unchanged. A code-generating statement
before `BeginProg` ends the walk first (`Sc = 1` then `EndIf` -> `Expr illegally occurs before
BeginProg.` only), so on these loggers `check_block_closers` now feeds `leading` (printed ahead of
their line, outside the placement stop) instead of the main list; `leading` findings of one line
keep their own order.

## `Could not find DataTable` for `CallTable` (2026-10-03)

Fixtures `tests/fixtures/calltable_first_only.json` (all four loggers) and
`tests/fixtures/calltable_hidden.json` (CR300/CR350), `instr_data.check_unclosed_calltable`,
`cr3xx.drop_calltable_error_with_other_errors`. Only the first `CallTable` of a missing table is
reported, whatever later ones name (all four loggers). On CR300/CR350 it is also hidden by any
other error of pass 1 or tier B, before or after it (`Sc = Nope1`, `Too many parameters`); the
late checks (`SetSetting` field names, option arguments) still print beside it, as the existing
`file_drives`, `option_args` and `settings_fields` fixtures show. Not modelled: CR6/CR1000X end the
walk at that `CallTable` (a later `Sc = Nope1` is not printed there).

`C = Int()` with an undefined target `C` prints `C is not defined.` then the generic `Invalid, or
out of place expression: <statement>` (an empty argument list); like too few arguments, the rest of
the expression is still parsed, so its names come first (`Nope4 = Int()+Nope`: `Nope4`, `Nope`,
then the message). Fixture `dims_state.json`.

## Trailing tokens after an instruction call (oracle, all four loggers, 2026-10-04)

`Instr(a,b) tail` is not a syntax error: the real compilers append the tail as further
parameters, so `PortSet(C1,1) 1` behaves exactly like `PortSet(C1,1,1)` (silent on CR6/CR1000X,
`Too many parameters:  1.` on CR300/CR350 where `PortSet` takes two). Observed, all four loggers:

- A tail chunk without internal whitespace (`1`, `x`, `-1`, `+1`, `"s"`, `1+1`, `(1)`, `1)`) is one
  extra parameter; `PortSet(C1,1)1` too. Comma-separated chunks (`Delay(1) 1,mSec`) are several.
  `Delay(1,1) mSec` is valid.
- Stray `)`, `,`, `=` tokens (alone, or `1 )`) are ignored: `PortSet(C1,1) )`, `Delay(1,1,mSec) ) )`.
- Later whitespace-separated tokens are separate parameters (`PortSet(C1,1) 1 2 3` ->
  `Too many parameters:  2.`), but their exact splitting was not mapped: `parser.py`
  `_absorb_trailing` only consumes the gap-free single-chunk shapes above; anything else stays an
  unparsed statement. Not modelled: `((1)` (accepted), `PortSet(C1,1) 1 2`, `x y`, `a(1)` echo.
- Only recognised instructions were mapped (`SW12V(1) 1` stays rejected); Sub calls: see below.

Fixture: `tests/fixtures/trailing_after_call.json`.

## Alias name already declared (oracle, all four loggers, 2026-10-04)

`Alias Q(1) = LevelFt` with `LevelFt` already a `Public`/`Dim` variable (or `Q` itself, or `x`)
-> `LevelFt already declared as a variable.`; an already-declared `Const` -> `K is already
declared as a CONST.` (name as written, case-insensitive match). Only the first comma item, and only
as a bare name (`= LevelFt 3`, `= LevelFt As Long`, `= Fresh, LevelFt` compile). Ordering on the
line: `Illegally dimensioned aliased name` first, then this; an undeclared source
(`Nope not yet declared so cannot be aliased.`) pre-empts it. A colliding name is never
registered, so a repeat gives the same message, not `Duplicate Alias Name`. CR300/CR350: later
lines' pass-1 errors vanish after it in the probes (second `Alias`, `Public Zed, Zed`): not
modelled (message-only difference on already rejected programs). Fixture:
`tests/fixtures/alias_name_collision.json` (CR300/CR350 multi-line cases omitted).

## CR300/CR350 follow-ups (2026-10-04)

- `cr3xx._hides_nothing`: `EndSub without Sub or nested subs.` and built-in arity errors
  (`No Matching, or out of place )`, `Expression expects`) do not hide unmatched-closer or
  `Could not find DataTable` errors (`tests/corpus/fuzz/cr300/false_accept/ca5935f3a78b`,
  `a2f667cdbbc7`, saved real output).
- An `Alias` whose source was never declared does not register its name
  (`.../message_diff/829cfa80394a`). The cascade-dropped declaration case (`48f550e6a0a0`) is not
  modelled.

## CR300/CR350 round 4 (2026-10-04)

- **Dropped declaration + aliases** (`tests/fixtures/alias_dropped_decl.json`, 30 cases, CR300 and
  CR350 real output): once the cascade has started, a `Public`/`Dim` is not registered
  (`dropped`), so every later `Alias` of it prints `<name> not yet declared so cannot be aliased.`
  and nothing else for that line (it pre-empts `Illegally dimensioned`, `Duplicate Alias`,
  `already declared`); `cr3xx.check_too_many_cascade`. A `Duplicate Alias` / `Duplicate Alias Name:`
  line is itself a cascade trigger (`Duplicate Alias` in `_DECLARATION_TRIGGER_PREFIXES`):
  the next `Public R2` is dropped and a later `Scan` gets `Too many parameters:  Sec.`.
  Fixes `tests/corpus/fuzz/cr300/false_accept/48f550e6a0a0`.
- **Dimension count in an assignment with an unresolved target** (`tests/fixtures/dims_assign_target.json`,
  102 cases, CR300/CR350 real output): `Rv(1)=1` then `Nope4=Nope(1)` prints no `Dimensions not
  declared` (the existing `test_dimensions_not_declared` expectation is for a different program and
  still holds): `structure._check_assignment_pass2` used a count of 0 instead of the running count
  and now takes `expressions.dims_state(program, known)`. `dims_state` given `known` stops a
  statement's walk at the first triggering undefined name, like the real compiler (a later
  `SearchString()` in the same statement does not move the count). A target `Nope(1)=...` of an
  undefined array whose subscripts fit the count, or a declared array with an undefined subscript
  (`Rv(Bad)=Sc`), is not followed by the generic `Invalid, or out of place expression` line; the
  right side's undefined names and `Dimensions not declared` follow instead. With count 0 and an
  undefined array target (`Nope(1)=Sc`) the generic line stays.
- **First bad variable of a declaration** (`tests/fixtures/decl_first_bad.json`, 56 cases, CR300 and
  CR350 real output): the declaration stops at its first variable with an undefined dimension name
  or initializer name (or an illegal dimension, whichever variable comes first): later variables are
  not examined (`Public a(nv), b(nv)` names `nv` once) and the variable two positions on is named
  by `Too many parameters:  <name>.` (`Public a(nv), b(nv), c` -> `... c.`; `Public a(-1), b(nv),
  c, d` gets only `Illegally dimension: ...` and `... c.`). `structure.check_pass1` runs the
  illegal-dimension check and `_check_decl_exprs` side by side for CR300/CR350 and keeps the
  earlier one. `cr3xx.drop_too_many_after_variable_illegal` leaves a declaration's own `Too many`
  alone. After the cascade drops a declaration, the re-run name check places a newly undefined
  name in text order among the ones already reported (`For i = 1 To nv`, `i` dropped: `i` before
  `nv`), `cr3xx._report_unregistered`. `Need closing )` (an `Alias R1() = B`) also starts the
  cascade. Not modelled: `Alias R1(1 = B` (`Need '=' ...`), `Dim R3(` (`R3( is missing the )
  character.`), a dimension name declared later in the file (`Public a(Sc)` then `Public Sc` is
  `Sc is not defined.` on the real compilers, we say only `Variable illegal in Parameter`).
- **Redeclared variable stops a declaration** (`tests/fixtures/decl_dup_first_bad.json`, 46 cases,
  CR300/CR350 real output): `already declared as a variable.` / `is already declared as a CONST.`
  on variable i stops the statement and names variable i+2 with `Too many parameters`
  (`Public P, Q, R` twice -> `P already declared ...`, `Too many parameters:  R.`; Q and R are not
  reported). On one variable the order is dimension error, then the redeclaration, one `Too many`.
  The first bad variable of dimension/name/redeclaration wins (`Public X(-1), Q(nv), R, S` with Q
  declared -> only `Illegally dimension: ...` and `... R.`); `structure.check_pass1` merges the
  three results.
- **`Do`/`Loop` conditions in the cascade** (`tests/fixtures/cascade_do_loop.json`, 90 cases, CR300
  and CR350 real output): after the cascade starts, `Do While/Until <cond>` and `Loop While/Until
  <cond>` lines are rewritten like a second argument to `Too many parameters:  <first token>.`
  (`X=-1` -> `X`, `X+Y=1` -> `X+Y`, `X<>5` -> `X<`, `Not X` -> `Not`, `X(1)=1` -> `X`; same
  tokenizer as instruction arguments), replacing the line's own undefined-name lines. A bare `Do`,
  `While`/`Wend` and `If` are untouched; an empty `Do While` loses its `Incomplete expression:
  While.` line. `Too few parameters in instruction.` (`CallTable()`, `Histogram()`) starts the
  cascade. `cr3xx.check_too_many_cascade`. Closes the "not implemented" note under PipeLine Mode
  for the `Loop While x=1` line only in the generic cascade form.

## Trailing tokens, round 4 (oracle, all four loggers, 2026-10-04)

Fixture `tests/fixtures/trailing_after_call2.json` (`parser.py` `_absorb_trailing`,
`_absorb_sub_tail`).

- **Instruction tail, next slot decides.** The tail lands in the next parameter slot
  (`len(args)`). A constant-integer slot (type 4/104) or one past the last slot splits the tail at
  whitespace: each gap-free token is a parameter (`PortSet(C1,1) 1 2` -> `Too many parameters:  2.`
  on CR6, `1` on CR300/CR350 which have two slots; `1 x 2` -> `x`; `-1 -2` -> `-2`; `1 ) 2` drops
  the `)`). Leading `(` are stray there too: `PortSet(C1,1) ((1)`, `(1`, `((1` compile (CR300:
  `Too many parameters:  1.`). An expression slot reads the whole tail as one expression
  (`Delay(1,1) 1 +2` compiles) and `Delay(1,1) (1` is `No Matching, or out of place ) in
  expression: (1.`. Whitespace-separated words in an expression slot are
  `Incomplete expression: <text>.` (`Delay(1,1) 1 2`, `PortSet(C1) 1 2`; CR6 adds `Invalid or
  missing parameter.` when the slot is `Delay`'s units): not modelled, they stay rejected with our
  generic message. `PortSet(C1,1) 1 + 2` (`Too many parameters:  +.`) likewise unmodelled.
- **Sub tail.** `Call S1(1) 2`, `S1(1) 2`, `S1(1)2`, `S1(1) x` append one argument (so
  `Call to S1 with 2 parameters, requires 1 arguments.` for a one-parameter Sub, silent for
  `S3(a,b)`). The tail is a comma list of full expressions, gaps allowed (`S3(1) 1 + 2`, `- 2`,
  `(2)`, `"s"`); one trailing `)` is tolerated (`S3(1) 1)`); a final empty piece is dropped
  (`S3(1) 2,`), others count (`S3(1) 2,,3` -> 4 parameters). A lone `)` or `,` is one empty
  argument: `S3(1) )` -> `Call argument required.`, `S4(1) )` (Optional second) compiles,
  `S1(1) )` -> 2 parameters. An empty argument in a required position reports `Call argument
  required.` once each (also `S3(1,)`, `S3(,2)`, which were silent before).
  `S()` followed by anything swallows the whole tail (`S2() 1 2`, `((1)`, `) 1` compile); a Sub
  with required parameters then reports `Call argument required.` twice.
  Not modelled (stay rejected): `S3(1) 2 3` (`Incomplete expression: 2 3 | .`), `S3(1) (1`
  (`No Matching ...`), `S3(1) =`, `S3(1) 2 ) )` / `2) 3` (counted parameters), `S3(1) ) 2`, `S2() =`.
  Known false accept: an undefined name in the tail (`Call S3(1) nope` reports `Invalid symbol in
  expression: nope | .`) is not reported.
- **Unary plus.** `PortSet(C1,1,+1)` compiles on CR6/CR1000X (type-4 slot accepts `+<decimal>`;
  `+&H1`, `++1`, `-+1`, `+x` stay `Illegal parameter`). CR300/CR350 had no issue (`Too many
  parameters:  +1.`). `+` in assignments, `Abs(+1)`, `Call S1(+1)`, operands, `Sample`, `Dim`
  sizes etc. already matched (probed, no change).

## Round 4 follow-ups (2026-10-04)

Oracle-confirmed fixes from the gpt-6.1-sol review of round 3 and the trailing-token leftovers.
Fixtures: `closer_arity.json`, `alias_known.json`, `subcall_tail.json`, `portset_tail.json`.

- CR300/CR350 closer suppression (`cr3xx._hides_nothing`): a built-in arity error hides the
  unmatched-closer errors unless a `Public.<name>` reference occurs before the call's first `)`.
  The function does not matter: `Round(Public.x,1,2)` hides nothing; `Round(x)+Public.Timestamp`,
  `Floor(x,1)`, `FormatLong(x)` and `SecsSince1990(x,1,2)` hide the closers (probed too few and too
  many arguments). Not modelled: `CallTable Nope` before `Round(Public.x,1,2)` prints only the
  CallTable error.
- CR300/CR350 failed `Alias` (`expressions._known_names`): names registered by a declaration or by
  another valid Alias stay known (`Public x` / `Alias Nope = x`: `x` stays defined).
- Sub-call tails: `S2() =` and `S2()=` are a Sub call with the tail ignored. Undefined names in a
  tail are reported (CR6/CR1000X: the last tail piece keeps its trailing blanks plus the
  trailing-newline quirk; CR300/CR350: `nope is not defined.` plus `Dimensions not declared`).
  Name errors precede `Call to S with N parameters` / `Call argument required.` on all loggers,
  also for parenthesised arguments; CR300/CR350 hide the count error when a name error prints.
- CR6/CR1000X: an earlier `Illegal parameter` or `Variable illegal in Parameter` on the same
  statement hides `Too many parameters` (PortSet, Delay; CR300/CR350 already matched).

## Round 4 (CR6/CR1000X diagnostics): pass-2 expression errors do not gate tier C

Observed 2026-10-04 with CR6Comp/cr1000xcomp (`tests/fixtures/tier_b_no_gate.json`, test
`tests/test_tier_b_no_gate.py`). This corrects the earlier "either `structure.check_pass2` or
`expressions.check` finding suppresses tier C" statement under "Pass model":

- The generic fallback (`Zork`, `Zork = 1`, `foo bar`, `SWVX(Vx1,1,1)`), `X = Zork`, an `If`
  condition and an undefined name in a *general* instruction position (`Randomize(Zork)`,
  `ExciteV(U1,Zork,0)`) are pass-2 findings that do **not** hide the instruction channel/port/
  range checks (`Invalid channel 2, must use U1..U12.`, `Excitation exceeds ...`, `Sample must be
  used inside a table declaration.`); everything prints in source order.
- What hid `ExciteV(Vx1,...)`'s successor is that the channel position is constant family, a
  pass-1 finding (`check_const_family_args`). `X = 1 +` (parse error) and `Call Zork` (link step)
  still print alone.
- A placement stop (`Scan illegally occurs before BeginProg.`) still cuts later tier-C findings.
- `Can't assign units` (`check_units_targets`) stays hidden by these findings
  (`test_duplicate_units_is_late`). A `DataTable` body whose field statement does not parse
  (`Sample(1,x,FP  2)`) is not reported as `declared with no output fields` (inference, safe side).
- CR300/CR350 keep the old gate (not probed).

Measured: cr6 chm exact 438 -> 443; cr1000x unchanged.

### Malformed `For` header (all four loggers)

Observed 2026-10-04 (`tests/fixtures/for_missing_parameter.json`): a `For` with no `=` after its
variable (`For more information ...`, `for communication.`), or nothing after `=`, no `To`, or
nothing after `To` (`For I = 1 To`, `For I =`, bare `For`) is the pass-1 error `Invalid or missing
parameter.` at its line; the block still opens (a later `Next` is not reported), pass-2 findings
are hidden as for any pass-1 error, and a `Dim Z banana` finding accumulates. Not done (belongs
with the trailing-token work): `For I = 1 To 3 Foo` / `... Step` / `Step 1 2` print `Incomplete
expression: <To- or Step-expression and trailing text>.` (CR300/CR350 add `Foo is not defined.`
before it) and `For 5 = 1 To 3` prints `5  is not a variable`.

Measured: chm exact cr6 443 -> 447, cr1000x 449 -> 453, cr300/cr350 420 -> 422.

### `SerialOpen` with an undefined ComPort name (CR6/CR1000X)

Observed 2026-10-04 (`tests/fixtures/serialopen_comport.json`): an undefined name anywhere in
argument 1 (`ComC5` on CR6, `Zork`, `Zork+1`, `ComC1+Zork`) gives only `ComPort 0 illegal.`, pass 1
(accumulates with `Dim Z banana`), replacing every `Invalid symbol in expression` for the statement
(an undefined name in a later argument too); unlike a variable or string port there is no `Parameter
must be a constant integer` line. A bare `SerialOpen` / `SerialOpen()` prints `ComPort 0 illegal.`
before its usual line. Not modelled: literal ports (`0`, `-1`, `U5` -> `ComPort 205 illegal.`) which
add `No Matching, or out of place )`, and an undefined name in argument 2 with a valid port
(`CommsMode Option 100 not supported on ComC1.`). Implemented in `cr3xx.check` / `expressions.check`.
Measured: chm exact cr6 447 -> 450, cr1000x 453 -> 454.

### `EmailSend` destination (CR6/CR1000X)

Observed 2026-10-04 (`tests/fixtures/emailsend_destination.json`): `EmailSend` behaves like
`EmailRecv` (`docs/instructions.md` "EmailRecv destination"): argument 9 missing or a non-String
variable/literal gives `A string variable destination is required` ahead of the arity line. Also
for the bare `X = EmailSend` / `X = EmailRecv` (arguments wrapped to the next line, the CHM shape),
and when the call is nested in a larger expression (`L = EmailSend(...,L)+1`, with 9+ arguments).
An unresolved assignment target keeps only the generic `Invalid, or out of place expression`.
Not implemented: `If EmailSend(...) Then` (same message), `X = EmailRelay(S,S)` / 8-argument
`EmailSend` arity (`Expression expects ,`), `X = SMSSend` (`Invalid symbol`).
Measured: chm exact cr6 450 -> 452, cr1000x 454 -> 456.

### CR6 negative `VoltSe`/`VoltDiff` channel constants

Observed 2026-10-04 (`tests/fixtures/cr6_negative_channel.json`): a small negative channel
(`-1`..`-199`) is not a burst code; it is rejected and shown signed (`Invalid channel -5, must use
U1..U12.`), where the burst model printed its absolute value. `-201`..`-212` stay accepted (diff:
odd rule). `-200`, `-213` and below behave irregularly in the real compiler (accepted, `Invalid
start channel...`, `Invalid port, must be C1..C4 or U1..U12.`; the value seems to be taken modulo
200) and are left unchecked (previously a false reject for `-200`/`-213`). Positive `>= 213` prints
`Channel <v mod 200> exceeds number of valid channels.` (observed 213..1199; 0 = accepted for SE,
odd message for diff), not implemented. CR1000X is unchanged. `terminals._cr6_burst_kind`.

### Bare calls of a user `Function` (CR6/CR1000X)

Observed 2026-10-04 (`tests/fixtures/function_stmt_undefined_arg.json`, `function_bare_name.json`):
a statement-level call of a declared `Function` (`Fn(Zork)`, no `Call`) with an undefined name in
any argument gives one `Invalid symbol in expression: <whole statement text>.` (like `X = Fn(Zork)`),
however many arguments are undefined, where a `Sub` call echoes each bad argument separately. Tier-C
errors on later lines still print. A bare `Fn` without parentheses is accepted for a `Function`
(was a false reject `Call argument required.`); `Fn()` still gives it, and a bare `Sub` with a
required parameter still gives it. Not done: `Call Fn(Zork)` (adds `Invalid symbol in expression:
Zork.` before the link message), `Fn(1) + Zork` / `Fn(Zork).y` (`Invalid symbol`, whole statement),
`Fn 1` (`Incomplete expression: <text>.`).

### Several undefined arguments in one call, and their order (CR6/CR1000X)

Observed 2026-10-04 (`tests/fixtures/multi_report_order.json`; all-positions-undefined sweep of every
instruction with two or more parameters on CR6 and CR1000X, then random 1-3 position subsets of the
instructions below, 114 cases, all agreeing): most instructions report only the first bad
position (existing rule), but these report every undefined position, each as its own `Invalid symbol
in expression: <arg>.` line, in a compile-function-specific order (1-based positions):
`Move` 2,1,4,3; `RMSSpa`/`StdDevSpa`/`MaxSpa`/`MinSpa`/`AvgSpa` 2,3,1; `CPIFileSend` 1..5; `GOESSetup`
1..7,10,8,9; `GOESGPS` 1,2; `CH400SendOS` 1,2; `MoveBytes` 1,2,4,5,3 (when its optional
sixth, constant argument is undefined only that one is reported, pass 1); `ReadIO` 2,1; `IPRoute` 1..3;
`MQTTPublishMeta` 1..6. The order is not derivable from the type codes (`Move`
and `MoveBytes` share their first codes and differ), so it is the `_MULTI_REPORT_ORDER` table in
`expressions.py`. Not done: `GOESData`
(`Could not find DataTable <arg 2>` after the symbol lines), `AddPrecise`/`MovePrecise`, `RainFlowSample`.
Also: after an undefined name at a const-family position, the later `Illegal parameter ..., expecting
constant integer.` (`PortSet`, `PortGet`, `SW12`, `CDM_SW*`, `SwVx`) is no longer printed
(`check_const_int_args`).

## Round 5 CR6/CR1000X help-corpus families (`tests/fixtures/r5cr6_*.json`)

Fixtures are real-compiler output (`scratch/mkfixture.py`); `tests/test_r5_cr6_families.py` runs them all.

- **Indexed target in the pass-1 scan** (`r5cr6_indexed_target`): `A(2) = Y +` is a pass-1 `Invalid,
  or out of place expression` like `X = Y +` (it is listed beside a later pass-1 error, e.g. a wrapped
  `1` on the next line, while pass 2 is hidden). `_pass1_scan` skips a clean subscript and scans on;
  `A(2)(1) = ...` and an unclean subscript stay unmodelled. CR6/CR1000X only.
- **Condition statements, pass 1** (`r5cr6_condition_echo`): for `If`, `ElseIf`, `While`, `Do While|Until`,
  `Loop While|Until`, `Select Case` and a simple `Case`, the pass-1 expression messages (`Invalid, or out
  of place expression`, `Incomplete expression`, `No Matching, or out of place )`) echo the *condition
  only* (keyword and a closing `Then` removed; `Select Case` echoes the raw remainder, leading blank
  included; an `If` echo keeps the blank before `Then`). `If X = Then` / `ElseIf Y + Then` (parsed with
  `Then` as operand) and an unparsable one-line `If c Then X = Y +` are pass-1 errors too. Unknown name
  in the condition stays pass 2. `structure.condition_part`, `_check_if_pass1`. `Select Case Nope +`
  (a lone symbol error) and `Case ... To|Is` are not modelled.
- **Placement name of an unparsable condition** (`r5cr6_cond_placement`, `r5cr6_select_symbol`): before
  `BeginProg`, an `If`/`While`/`Do While|Until`/`Select Case` whose condition has an unknown name (or is
  prose: `If using this instruction ...`) reports `Invalid symbol in expression: <condition>.` then
  `<If|WHILE|Do|SELECT> illegally occurs before BeginProg.`, not `Expr ...`. An unresolved `Select Case`
  still opens its block (no `Case outside Select.`/`EndSelect w/o Select`). Not modelled: `ElseIf`/`Case`
  there (`Case with no Select.`) and `Loop` (`LOOP without DO.`).
- **Include file extension** (`r5cr6_include_ext`, CR6/CR1000X): the text after the last `.` of an
  `Include`/`IncludeSection` name (empty with no `.`) must start with `crb` or the logger's own
  extension (`cr6`/`cr1x`), else the error `Include file <name as written, drive included> must have
  valid program file extension.` replaces the missing-file warning; every such include is reported and
  compilation goes on. CR300/CR350 follow a different rule (a name without `.` passes) and are not
  modelled. `parser._include_extensions`.
- **FileManage / RunProgram file names**: see `docs/instructions.md` "FileManage drive and DNP ComPort
  constants" (extension rule, `RunProgram` drive, first bad name only).
- **Pass-1 scan knows only earlier declarations** (`r5cr6_decl_order_*`, `r5cr6_cond_comma`): the
  CR6/CR1000X pass-1 statement scan resolves a name only if its `Public`/`Dim`/parameter declaration
  comes before the statement. A statement whose first name is declared further down is a pass-2 finding
  (hidden by any pass-1 error; with none, its message is the same pass-1-style text plus the
  `Expr illegally occurs before BeginProg.` follow-up when it precedes `BeginProg`). Corpus: prose such
  as `If TCTemp is <26, ...` above `Public TCTemp`. In a condition a top-level comma after an operand is
  `Incomplete expression`, and one where an operand is expected is the generic fallback. An `If ... Then`
  or `Do While|Until` whose condition does not parse still opens its block for the closer checks.
- **Trailing `.` on a name** (`r5cr6_trailing_dot`, CR6/CR1000X): `junk.` is one (unknown) name, not a
  member access with a missing member, so `Histogram instruction.` is `Invalid or missing parameter.`
  like `Histogram junk`. Parser: a `.` not followed by an identifier stays on the preceding plain name.
  Still unmodelled: an instruction followed by adjacent-word junk (`PanelTemp the instruction`,
  `Battery the end`, `Randomize junk.`), where the oracle reads each comma-separated chunk as an
  expression (arity first, then `Invalid symbol in expression: <chunk>.`).
- **Statement starting with an operator** (`r5cr6_leading_operator`, CR6/CR1000X): `+ 1`, `= -1`, `* 2`,
  `<> 1`, `^ 2`, `, x`, `) x`, `. x`, `; x` followed by anything is a pass-1 `Invalid, or out of place
  expression: <text>.` (a later bare word such as `successful` on the next line is then hidden, which is
  the wrapped-continuation shape of `X = Y` / `+ 1 'comment` in the help corpus). A leading `+`/`-` with
  an expression is a pass-1 error when every name resolves, pass 2 when one does not. Not modelled: a
  lone `+`/`=`, `&`, `%`, `!`, `Not`.
- **Bare variable after `Then`** (`r5cr6_inline_bare*`): `If c Then Y` (a declared variable, no
  assignment) is a pass-1 `Invalid, or out of place expression: Y.`, so later pass-2-only findings are
  hidden. Gaps seen, not modelled: `If c Then Battery` (inline instruction arity) and the `declared but
  never called` warning for a table named in an inline `CallTable`.
- **Stray `)` and `,`** (`r5cr6_stray_paren_comma`, CR6/CR1000X): `X )`, `X ) + 1`, `X ) )` are pass-1
  `Invalid, or out of place expression` (not `Incomplete expression`); in `Y = 1, 2` / `Y = Z, W` /
  `Y = Int(1), 2` a top-level comma after an operand is pass-1 `Incomplete expression`, and one where an
  operand is expected (`Y = , 1`) the generic fallback (a name before the comma that does not resolve is
  still pass 2). An indexed target (`A(1) = 1, 2`) keeps the pass-2 fallback.
- **Statements before `BeginProg` with an undefined name** (`r5cr6_fn_stmt_before_bp`): a built-in
  function written as a statement (`FileClose(Nope)`, `Timer(Nope,1,0)`) reports `Invalid symbol in
  expression: <statement>.` before its own `Expr illegally occurs before BeginProg.` (it was hidden by
  that placement error); not when an earlier statement of the line owns the placement error.
- **`If` without `Then`** (`r5cr6_if_no_then*`): the condition runs to the end of the statement (a
  trailing comment or `:` leaves the blank), so an unknown name gives `Invalid symbol in expression:
  <condition>.` (the missing-`EndIf` message is then not printed); an `If` whose condition does not parse
  still opens its block. **Bare condition keywords** (`r5cr6_bare_cond`): `If`, `ElseIf`, `Do While|Until`,
  `Loop While|Until`, `Case` alone are pass-1 `Invalid or missing expression.`; `If Then`, `If 'c` and
  `Select Case` alone echo an empty condition. Not modelled: bare `While` and `Select`.

## CR300/CR350 round 5: prose and leftover lines (2026-10-08)

Fixture `tests/fixtures/cr3xx_prose_words.json` (156 cases, all four loggers, real output). The help
corpus puts example prose into the program, so these shapes are measured by `difftest --group chm`.

- **Table names are not values.** A bare statement `Tb`, `Tb X`, `Tb (X,1)` naming a `DataTable`
  prints `Tb is not defined.` first on CR300/CR350 (`structure._check_call_pass2`), and a table name
  inside prose is reported as a word (`cr3xx.prose_known` removes `KnownNames.tables`). CR6/CR1000X
  unchanged.
- **Parenthesis after the first word.** An undefined first word followed by `(` opens a second walk
  of the same "chain plus one peek word" shape, which ends at the first `)`; nothing after it is
  examined (`device (8 channels)` -> `device`, `channels`; `device (foo bar baz)` -> `foo`, `bar`;
  `device (foo bar) baz` -> not `baz`). A first word that is a predefined constant (`C1`, `True`,
  `Sec`) opens no walk. Not modelled: a declared variable as first word (`X (foo bar)` reports
  `foo`, `bar` on the real compilers), and a built-in function as first word (`Sin (A B)` ->
  `No Matching, or out of place ) in expression: ...`, `Round (A B, 1)` -> `Expression expects ,`).
- **Operators with surrounding blanks** join the chain like adjacent ones (`foo & bar baz qux`
  names `foo`, `bar`, `baz`; also `+ - * / ^ < > <>`; `=` with blanks is an assignment shape and is
  not modelled, the real message there is `Incomplete expression`).
- **`:`-separated prose.** `Note: In this example` is two statements; the second used to be dropped
  because the first statement's echo on the same line was taken for a pass-1 leftover
  (`structure.check_pass2`, all loggers: CR6/CR1000X print the second `Invalid, or out of place
  expression:  In this example...` too), and the leading-word lines were applied to both echoes
  (`cr3xx.check_unknown_line_words` now matches the echo text with the statement).
- **Leftover `(Name,...)` after a cascade-dropped declaration** (`cr3xx.check_too_many_cascade`): the
  follow-up line of a missing-parameter instruction is preceded by `Name is not defined.` when
  `Name` was a declaration dropped by the cascade; a name that was never declared keeps no extra line.
- Skipped (seen, not modelled): a trailing call tail after an undefined call (`device (foo) bar`
  reports no `bar`, we do); `X = 1 2`-style assignments with junk on CR300/CR350 print
  `Incomplete expression: <statement>` (`No Matching ...` inside parentheses), we print the generic
  `Invalid, or out of place expression`, because CR300/CR350 do not run `structure._pass1_scan`.

- **Bare `SerialOpen` and `EmailSend`/`EmailRecv` on CR300/CR350** (fixtures
  `cr3xx_bare_serial_email.json`, `cr3xx_email_names.json`): bare `SerialOpen`, `SerialOpen()`,
  `SerialOpen(`, `SerialOpen ()` print `ComPort 0 illegal.` first on all four loggers (it was
  CR6/CR1000X only; an explicit `Call SerialOpen` does not: `Sub SerialOpen not defined.`, not
  modelled). `A string variable destination is required` (`instr_rules.check_emailrecv_destination`)
  now runs on CR300/CR350 too, with the same argument rule plus: an undefined name in the
  destination slots counts as bad there and the statement's `X is not defined.` lines come first
  (`cr3xx.order_email_destination`). Not modelled: more than nine arguments (`Expression expects ,`,
  also wrong on CR6), an undefined assignment target (`Nope = EmailSend`).

- **Names declared later or dropped** (fixtures `cr3xx_prose_dropped.json`, `cr3xx_prose_dropped2.json`):
  prose words are resolved against the declarations that precede the line (`foo X bar` above
  `Public X` reports `X`; `cr3xx.declared_later`) and, once the cascade has started, against the
  declarations it did not drop (`cr3xx._reword_prose`: a prose line after the first error that
  mentions a dropped `Current` reports `current is not defined.`).
- **Built-in function as first word** (`Sin foo`, `Timer 1`, `COS of angle`, `Round foo`): the
  fallback is `Expression expects ( : <text>` (CR6/CR1000X already did this); the leading-word lines
  stay. A leading `Is` ends the walk with no word lines. Operator runs in the chain are the valid
  operators only (`<> <= >=` or one of `+ - * / ^ & < > =`), so `Datalogger</title>` stops after
  `Datalogger`. Not modelled: `Timer is bar baz` reports `bar`, `baz` (`Sin is ...` reports none);
  `Not foo bar` is `Incomplete expression`.

- **`If` without `Then`, and condition-before-body order** (fixture `if_condition_names.json`, all
  four loggers): `If Nope = True` (no `Then`, accepted when the names resolve) reports like the
  `Then` form with the rest of the line as the echo (`Invalid symbol in expression: Nope = True.`
  on CR6/CR1000X, `Nope is not defined.` on CR300/CR350; lines with a comment or continuation are
  left alone). On a one-line `If c Then stmt` the condition's findings precede the inline
  statement's (`expressions.check`).

- **Too-few-arguments message with an undefined name** (fixture `cr3xx_shortfall_names.json`,
  CR300/CR350): `Invalid or missing parameter.` / `Too few parameters in instruction.` is dropped
  when the same statement has an `X is not defined.` line (`cr3xx.drop_shortfall_with_names`;
  `PanelTemp(Nope)`, `Delay(1,Nope)`, `VoltDiff (X,1,mV200,1)`). The same hiding applies on
  CR6/CR1000X only for constant-family argument positions (`VoltDiff (X,Nope)`,
  `Delay(Nope,1)` print `Invalid symbol in expression: Nope.` alone; `PanelTemp(Nope)` keeps
  `Invalid or missing parameter.`): not modelled, those CR6/CR1000X cases still print both. Not
  modelled: `VoltDiff (Nope,1)` (`Too many parameters:  1.` after the name).

- **System tables are not values** (fixtures `system_table_values.json`, `cr3xx_system_table_prose.json`):
  a bare `Status` or `Public` used as a value (`X = Status`, `Battery(Public)`, prose `Status foo`) is
  undefined on all four loggers, `Settings` too on CR300/CR350 (CR6/CR1000X accept `X = Settings`);
  `Status.Battery` still resolves. They join the user `DataTable` names in `KnownNames.tables`
  (`expressions._value_less_tables`); the `Member` walks of `_names_and_dims` and
  `cr3xx._arity_walk` no longer report a table base. Prose after an alias of a cascade-dropped
  declaration (`Alias AC_Pwr(1) = Real_Power` with `AC_Pwr` dropped) sees the alias name as undefined.

- **Invalid `As` clause still registers names** (fixture `decl_bad_as_names.json`):
  `Public Q, Flag A s Boolean` registers `Q` and `Flag` (the variables up to and including the bad
  one; later ones are not registered), so later uses do not print `is not defined`
  (`symbols._names_through_bad_as_clause`). Surfaced by the `If`-without-`Then` check above.
  Not modelled: `Public Flag As Banana, R` should leave `R` unregistered.

- **Function-named assignment targets and CR300/CR350 local shadows** (probed 2026-10-08 on all four
  loggers, `tests/fixtures/function_assign_target.json`): `Sin = 1`/`Timer = X` -> `Expression expects ( : <stmt>`;
  `Rnd = 2`, `Sin(X) = 3`, `Round(1,2) = 3` -> `Invalid assignment in expression: <stmt>`;
  `Sin(1,2) = 3` -> `No Matching ...`; `Round(1) = 2` -> `Expression expects ,`; `Sin() = 3` ->
  generic (`expressions._function_target_message`). On CR300/CR350 a local `Dim exp`/parameter named
  like a built-in function does not shadow it: bare uses print `Expression expects (`, `exp(1) = 2`
  prints `Invalid assignment in expression`, and these start the `Too many parameters` cascade.
  A scalar local indexed as the target (`Dim exp` then `exp(1) = 2`) gets only the `Invalid assignment`
  line (no over-indexed fallback after it). Not modelled: an instruction name used as a call in an
  expression (`X = Battery(X)`, `X = Delay(1)`, `X = PulseCount(1)`): the real compilers print
  `Invalid symbol in expression: <stmt>.` (CR6/CR1000X) or `<name> is not defined.` plus
  `Dimensions not declared: <stmt>` (CR300/CR350), but `X = SerialOut(1)` is `Expression expects ,`
  and ours accepts these silently (instruction names sit in `_known_names`); family left open.
  Unfinished: bare function in `If`/`Do` conditions (`If Timer > 1`), CR6 scoping of a Sub-local.

- **One dimension-count state for every CR300/CR350 consumer** (probed 2026-10-08, fixture
  `tests/fixtures/dims_state_shared.json`): the first undefined name with more subscripts than the
  running count stops a statement's walk (`Nope4=Nope(1)+S()` ignores `S()`), so the next statement
  still reports `Dimensions not declared`. `expressions.program_dims` is the single entry point;
  `structure.check_pass2`, `expressions.check`, the `For`/`Case`/`ElseIf` checks and
  `cr3xx.check_float_destinations` all use it (CR6/CR1000X keep the declared-variable simulation).

## Round 6 parse fixes (oracle, 2026-10-08, all four loggers)

- A bare call of a declared `Function` is one expression statement, not a `Sub` call with a tail:
  `F0()+Nope` -> CR6/CR1000X `Invalid symbol in expression:   F0()+Nope.`, CR300/CR350 `Nope is
  not defined.`; `F0()+1`, `Fn(1)+1`, `Fn(1)*2` compile. (`Fn(1) 1` / `F0() Nope`, a juxtaposed
  tail, report `Incomplete expression: <stmt>.`; not modelled yet.) Fixture `func_expr_stmt.json`.
- A `Sub` tail splits arguments at top-level commas only: `Call Two(1) Round(2,0)` and
  `Two(1) Round(2,0), 3` (the latter: `Call to Two with 3 parameters, requires 2 arguments.`).
  Fixture `sub_tail_nested.json`.
- The CR6/CR1000X "earlier parameter-kind error hides `Too many parameters`" suppression is
  per statement: `PortSet(C1,1,x):PortSet(C1,1,1,2)` -> `Illegal parameter x, expecting constant
  integer.` then `Too many parameters:  2.` (also with `Variable illegal in Parameter`). Modelled
  on `:`-joined lines by attributing a diagnostic to the statement whose text contains its echoed
  argument. Not modelled: when the too-many statement comes first the real output lists it first
  (statement order); ours lists per pass. Fixture `stmt_scope_suppress.json`.
