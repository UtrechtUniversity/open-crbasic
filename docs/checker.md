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
  exists to match. Values below it whose storage is too large to allocate instead get `Out of memory.`, reported at the
  program's last line (`docs/tdf-rules.md` "CR6/CR1000X `Out of memory.`", implemented in
  `checker/storage.py`).
- The check is **per-dimension**, not per total element count: `Public X(100000,100000) As Long`
  (10^10 elements) is *not* `Illegally dimension` (each individual dimension, 100000, is legal) --
  it is the `Out of memory.` rule above.

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

Round 51: the pass-1 constant-family check also covers the statements of a one-line `If`
(`If x Then SDI12Recorder(fan,C1,1,"XLs!",1.0,0,Undecl)`, also after `Else`); before, an undeclared
seventh `SDI12Recorder` argument there was a false accept on CR6/CR1000X (fuzz seed 442, GEUS seed
`GIOS_WAT_R_V4_0`). Fixture: `tests/fixtures/r51_inline_const_family_args.json`, 14 observations.


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

- Originally modelled integer-typed constant arguments: `IfTime` and `TimeIntoInterval`, all three
  arguments (`TintoInt`, `Interval`, `Units`), and `SerialOpen`'s `BaudRate` (second argument).
  The original scan of `functions.json` entries with an integer or float argument type missed
  `Timer`'s custom resolver; [round 46](#timer-number-and-nonfinite-integral-arguments-round-46)
  adds its first argument. A scan of every instruction in `instructions.json` found no
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
- Instructions whose first parameter is optional (`Calibrate`, `ConstTable`, `SequentialMode` and
  `CPISwSyncMode` on CR6/CR1000X, per `profile.instructions`) drop the cut-off string and read the
  rest as the instruction (oracle, 2026-10-16, all four loggers): `Calibrate(")` compiles,
  `Calibrate(A,")` is `Variable A out of bounds.`, before `BeginProg` it is `Calibrate illegally
  occurs before BeginProg.` (`parser._parse_text_segment`; fixtures `r17_optfirst_unterminated*.json`).
  CR300/CR350 `SequentialMode`/`CPISwSyncMode` keep their own messages.
- `SlowSequence` (round 18, oracle 2026-10-16, all four loggers, fixture `r18_slowseq_timeout.json`,
  `tests/test_r18_slowseq_timeout.py`): `SlowSequence(")` and `SlowSequence("abc` drop the string
  (same rule as above; inside `BeginProg` they compile). `SlowSequence(A,")` and `SlowSequence(1,")`
  are `Too many parameters:  ".` (the cut-off string is the excess argument; `parser._parse_text_segment`
  puts a `0` in its place so the echo keeps the raw quote). A clean `SlowSequence` before any
  `BeginProg`/`EndProg` opens the program (`structure.check_pass2`, `program_marker_seen`): the next
  `BeginProg` is `Multiple BeginProgs.` (bare, `SlowSequence(1)`, `SlowSequence(")`, `Sub`/`EndSub`
  before it), a second `SlowSequence` before it changes nothing, and `EndProg` clears the state.
  An erroneous `SlowSequence` (`SlowSequence(A B)`, `SlowSequence(1,2)`) gives no `Multiple`. On
  On CR300/CR350 the argument must resolve without error: a literal, empty, or a declared `Public`
  name opens the program (`SlowSequence(Q)`, `(A)`, `(Z)` with `Public A, Z, Q`: `Multiple`), while
  `SlowSequence(Undef)` is `Undef is not defined.` and no `Multiple`. CR6/CR1000X never resolve the
  argument. (Expression and `Const` arguments: see below.)
  A clean `SlowSequence` before any `BeginProg` is the start of the program for the real compiler
  (oracle 2026-10-16, all four loggers, fixture `r18_slowseq_endprog.json`,
  `tests/test_r18_slowseq_endprog.py`): `SlowSequence` / `EndProg` compiles (also bare `SlowSequence`
  with no `EndProg`, `SlowSequence(1)`, `(A)`; CR6/CR1000X `(Nope)`), nothing after the first `EndProg`
  is read (garbage or a second program with errors), and what follows the `SlowSequence` is in-program:
  `A = 1`, `Scan`/`NextScan` and `Sub`/`EndSub` are clean, while the unbalanced forms report at `EndProg`
  (`Scan without NextScan.`, `IF or SELECT without EndIf or EndSelect.`, `Sub ... EndSub not matched.`)
  and a bad statement gives only its own message, never `... illegally occurs before BeginProg`. A bare
  `EndProg` is still `No BeginProg in Program.`. On CR300/CR350 any argument expression over declared names
  and built-in functions opens the program (`(1+1)`, `(A+1)`, `(K*2)`, `(Abs(-1))`, `(-A)`, `("a"&"b")`, `(Z=1)`, with or
  without a later `BeginProg`: `Multiple BeginProgs.`); an undefined name (`(Nope+1)`) or `A(1)` on a scalar is an
  error and does not (`structure._slowsequence_resolves`; declared-array references are not probed). `structure.open_program_at_slowsequence` inserts a
  `BeginProg` ahead of the opening `SlowSequence` when the statements hold no `BeginProg`. Not covered:
  `SubScan (1,Sec,0)` in such a block (real `SubScan must have >0 number of scans.`, a separate rule),
  and a `SlowSequence` with a `BeginProg` after it and `Scan`/`NextScan` between (real: `Multiple
  BeginProgs.` plus `Warning: BEGINPROG never reached at run time.`; ours `Scan illegally occurs before
  BeginProg.`); on CR300/CR350 a `NextScan` with no `Scan` between such a `SlowSequence` and `BeginProg`
  (real: `NextScan with no Scan.` alone).

Not done: `S = abc"`, where an undefined name comes first, and `ESSInitialize(X)` -> `Community
Names parameter is of type string`. Replay: 847 of 1469 exact, no false rejects.

### Unterminated string reached by the scan (CR6/CR1000X, 2026-10-10)

Oracle (`unterminated_string_reached.json`, 14 cases x 2 loggers): any statement whose pass-1 scan
reaches the unterminated string with no earlier syntax error gets `String without end quote`, then
the fallback echo, whatever the first word is: `X + "abc`, `X(1) "abc`, `1 "abc`, `Len("abc`,
`S = Left(S, "abc`, `SprintF (S, "abc, X`, `X = 1 + "abc`. Not when an unknown name (`Y + "abc`,
`X * (Y + "abc`) or an operand pair (`X here "abc`, `X, "abc`) comes first (plain fallback), and a
call that already has more arguments than the function takes before the string (`Sin(X, "abc`,
`Len(S, "abc`) is `No Matching, or out of place ) in expression: <stmt>.` alone
(`structure.string_reached`). CR300/CR350 read other shapes (`Foo "abc` also gets the string line
after `Foo is not defined.`, `X(1) "abc` does not) and are not covered.

### Junk after an expression: string operands, a stray `.` (CR6/CR1000X, 2026-10-10)

Oracle (`string_operand_junk.json`, 36 cases x 2 loggers): the expression a pass-1 statement starts
with is type-checked before the junk is met, so `"a" and "b" .`, `X and "b" and "c" .` (one per
operator), `("a" and "b") .` and `"a" and "b" foo` give `String is illegal here` first
(`parser.partial_expression`, `structure._unparsed_string_messages`); with an unknown name later
on the line (`"a" and foo .`) the string line still comes first but the fallback stays pass 2
(`X = Q` before it is still reported). A statement starting with an unknown name (`foo and "b" .`)
has no string line. Also: a lone `.` after a complete assignment value is `Incomplete expression`
(`X = 1 .`), after a statement starting with an operand the fallback (`X and X .`); and every junk
shape after an operand-led non-assignment is the fallback, never `Incomplete`/`No Matching`
(`X + 1 foo`, `X + 1 )`, `X and (X foo)`, `1 + 1 foo`). CR300/CR350 also print the string line,
after the `is not defined.` lines; not modelled. Unrelated: CR6 accepts `X = 1 ;`.

### Statement left open inside a built-in function call (CR6/CR1000X, 2026-10-10)

Oracle (`unclosed_call_count.json`, 29 cases x 2 loggers, pass 1): by the argument count `n` of the
innermost open built-in call against its `nargs` N: no trailing comma and `n < N` (`Round(X`,
`FileOpen (S, "r"`, `Round(Left(S`) is `Expression expects , : <stmt>.`; a trailing comma with
`n <= N` counting the missing argument (`Round(X,`, `FileOpen (S,`, `Left(S,`) is the fallback
`Invalid, or out of place expression: <stmt>.`; every other count stays `No Matching, or out of
place )` (`Sin(X,`, `Left(S, 1,`, `FileOpen (S, "r", 0`). `Abs` is left alone (`X = Abs(X` compiles,
`Abs(X, 1` is `Incomplete expression`). `structure._unclosed_call_message`.

### `_` continuation keeps the blanks in the echo (all four loggers, 2026-10-10)

Oracle (`continuation_echo.json`): a continued line echoes the text before the `_` with its blanks
(the `_` itself dropped) followed by the next physical line verbatim, leading blanks kept:
`foo bar _` / `      baz` -> `foo bar       baz` (7 blanks), `Y = foo bar _` / `    baz _` /
`      qux` -> `Y = foo bar     baz       qux`. Earlier code joined with one blank
(`source.py`). Fixes the `xml_attribute_namespace,         xml_element_name,` echo of
`xmlparse_1.CR6`.

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
- Scan budget (CR6/CR1000X): the time the instructions add to the main `Scan` (100 us units; `Delay`,
  `PortSet`, `SW12`, `PulseCount`, `PanelTemp`, `VoltSe`/`VoltDiff`/`TCSe`/`TCDiff`, `Therm107-109`,
  `CS616`, `Battery` on CR1000X; a `SubScan(...,n)` body counts n times) above its interval ->
  `Scan interval too short for measurement by N <unit>.` at `NextScan`; model and evidence in
  docs/instructions.md "Scan time budget" (`checker/scan_time.py`).
- CR300/CR350 argument abort: the first undefined name at a runtime-expression parameter (codes
  2, 8, 24, 25, 27, 28, 29, 33, 40, 41, 47) is followed by `Too many parameters:  <next
  argument>.`; an undefined name at a code-20 slot also gives `Variable illegal in Parameter`.
- Measurement Reps (second argument): CR6/CR1000X `Invalid reps %d, must be > 0` for Reps <= 0
  and `Variable %s out of bounds.` when the Float destination has fewer elements from its start
  than the required output count; CR300/CR350 `Invalid reps 0, must be > 0.`; only instructions
  probed per logger (decompiled shared check `FUN_005163b0`). Round 24 (fixture
  `r24_dest_counts.json`, Reps 3 against 2, 3, 5 and 6 elements) adds `BrHalf3W`, `BrHalf4W`,
  `BrFull`, `BrFull6W`, `PulseCount`, `CS616` on CR6 and CR1000X and `Resistance` on CR6, each needing
  Reps elements in the probed options. Round 25 identifies the optional extra-output parameters
  and adds CR6 `Resistance2W`/`Resistance3W`: see [optional measurement outputs](#optional-measurement-outputs-round-25).
  Not modelled: CR1000X `Invalid PulseCount reps.` (Reps past the last pulse channel) and the CR6
  4W/6W channel overlap message.
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
their symbol tables: `FLOAT`, `LONG`, `BOOLEAN`, `IEEE8`, `IEEE4`, `BOOL8`, `UINT1`, `UINT2`,
`UINT4` and `DOUBLE` may be declared (`DOUBLE` as `Public`, `Dim`, `Const`, parameter and `Alias`
name, oracle 2026-10-24, round 33). `STRING` and `FP2` remain reserved. CR300 and CR350 do not use that
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
| `LI7700` | Reps times a per-mode count (round 24, see below) | CR6, CR1000X |
| `Therm107/108/109` | Reps | CR300, CR350 only (CR6/CR1000X accept an overrun) |
| `SplitStr` | Reps > 1 overruns a scalar; a whole-array destination overruns past its element count (2026-10-11, `docs/instructions.md`) | all |

Observed boundaries: an exact fit compiles; Reps 0 compiles; `CalFile` with negative Reps is out of
bounds (the others were not probed with negative Reps). Float destinations only for the Reps and
`RealTime` rules (a Long array is accepted by `CalFile(L,11,...)` on CR6/CR1000X, and `CalFile`
rejects it as a type on CR300/CR350). `SplitStr` accepted an array destination with Reps 20 against
5 elements (superseded 2026-10-11: a whole-array destination with Reps above its element count is out of bounds, `docs/instructions.md`); the scalar overrun stands (Float or String scalar; a structure-typed scalar takes Reps 3, `chm/splitstr_5.CR6`). An `Alias` name as destination was not checked
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

### Builder destination counts (round 24, 2026-10-19)

Fixture `tests/fixtures/r24_dest_counts.json` (all four loggers; `instr_measure._builder_counts`).
The CR6 instruction builders pass an element count to one shared destination resolver, which reports
`Variable %s out of bounds.` when the variable has fewer elements from its start (row-major) than the
count. The counts below were read from the decompiled builders (`FUN_0043b5c0` SDMGeneric,
`FUN_0043ba40` SDMSW8A, `FUN_00436d60` CSAT3, `FUN_00437090` CSAT3B, `FUN_00438a20` CS7500 family,
`FUN_00438190` TGA, `FUN_00438460` EC100, `FUN_0043a080` SDMCD16AC, `FUN_00452980` PeakValley,
`FUN_00452be0` HydraProbe, `FUN_00428ea0` GOESGPS, `FUN_00452490` RectPolar) and every one was
confirmed at its boundary on CR6 and CR1000X; CR300/CR350 lack these instructions. A value the
builder keeps in a byte counts modulo 256 (`SDMSW8A(Z,265,...)` needs 9).

| Instruction | Argument and elements | Types checked |
|---|---|---|
| `SDMGeneric` | Dest: `ValuesIn` (6th); a negative count is out of bounds | Float, Long, Boolean (a String destination has its own `Destination out of bounds` rule, not modelled) |
| `SDMSW8A` | Dest: Reps mod 256 | Float |
| `CSAT3` | Dest: 5 * Reps (signed byte; a negative Reps is `Out of memory.`, not modelled) | Float |
| `CSAT3B` / `CSAT3BMonitor` | Dest: 5 / 4 | Float |
| `CS7500`, `LI7200`, `LI7700` | Dest: Reps mod 256 times a per-command count (4th argument mod 256, 0..7): CS7500 2,2,4,3,11,3,4,4; LI7200 2,5,10,12,10,5,1,5; LI7700 3,5,9,13,17,3,7,9 | Float |
| `TGA` | Dest: table by DataList (1..5) and ScanMode (1..3): 2,3,4 / 5,7,9 / 8,10,12 / 19,25,31 / 21,27,33 | Float |
| `EC100` | Dest: by output option 0..3: 8,12,13,15; option 2718: 36 | Float |
| `SDMCD16AC` | Source (1st): 16 * Reps mod 256 (a Long source takes another path) | Float |
| `PeakValley` | DestPV, DestChange and Source (1st, 2nd, 4th): Reps (3rd) | Float |
| `HydraProbe` | 1st: 11; 2nd: 4 | Float |
| `GOESGPS` | 1st: 6; 2nd: 7 | Float |
| `RectPolar` | 1st: 2; 2nd: 2 | Float |

The previous `LI7700` rule (5 * Reps) held only for commands 1 and 5. Not modelled: options outside
the tables (`EC100` option 4..255 reads past its table after `Invalid output option; range 0..3.`),
`SolarPosition`, `SerialBrk`, `AVW200`, `SysInfo`, `CDM_*` and the analog measurement helpers.

### Table output source with `X()`, a multi-dimensional source, or an expression subscript (round 24)

Same fixture (`instr_data._check_stage2a`), identical on all four loggers. A table-output source
(`Sample`, `Average`, ...) is checked over the whole variable, row-major:

- `Variable %s out of bounds.` when the flat offset of the source plus Reps exceeds the element count;
  printed only when every subscript is a literal (`Sample(8,A((1+2)),FP2)` prints only the next line).
- `Invalid, or out of place expression: ().` after it for an empty subscript `A()` (index 1).
- `Variable %s, index %d, reps %d, out of bounds.` when `index + Reps - 1` exceeds the element
  count, where the index is the subscript of a one-dimensional source and 1 for a multi-dimensional
  one (`N(4,4)`: `N(2,1)` with Reps 13 prints the first line only; Reps 20 prints both with index 1).
  This line ends the table checks; a first line alone does not (a later table statement is checked).

Not modelled: a first line alone does not gate pass 2 on CR6/CR1000X (`X=Q` after it is still
reported); a partial subscript of a multi-dimensional source is skipped.

### `FieldNames` argument (round 24, all four loggers)

Fixture `tests/fixtures/r24_dest_counts.json` (`instr_data.check_fieldnames`, tier C). The output
instruction's builder reads a following `FieldNames` record (decompiled `FUN_004ec300`) and resolves
its argument as a string: a numeric constant (`1`, `(1+2)`, a numeric `Const`) or a variable is
`FieldNames cannot be a variable.`; an empty string is `FieldNames canot be NULL` (sic); a String
variable gives both. A string literal, string `Const` or constant string expression compiles. The
line is that of the output instruction that owns the `FieldNames` (see "FieldNames owner line (round
54)" below). CR6/CR1000X report it alongside pass-2 expression errors; CR300/CR350 only in an
otherwise clean file. An undefined name as the argument is modelled in round 54.

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
explicit `PipeLineMode` statement earlier in the file, and no `SequentialMode` before it, or in a
program without `PipeLineMode` whose automatic decision (`mode.py`) is Pipeline, a
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
`PortGet`, `PortSet`, `Therm107`, `BrHalf`, `BrFull`, `TCSe`, `TCDiff`, `PeriodAvg`, `CS616`,
`ExciteV`, and (round 48) `SW12`. A string `Delay` option (`"1"`, a string `Const`, `"0"+0`)
also prints it, in both modes; the automatic decision reads such an option as neither 0 nor 2,
so the program stays Pipeline and the conditional `Delay` is rejected. `SW12` prints `Sw12 not
allowed inside of a conditional statement in PipeLine Mode.` unless its option is 1; with
automatic mode an omitted option is already sequential. Like `PortSet`, CR6 accepts it outside
a Scan (a `Sub` body) and CR1000X rejects it there. Before `BeginProg`, CR1000X prints only the
PipeLine Mode error; opencrbasic adds `Sw12 illegally occurs before BeginProg.` after it, as it
already did for CR1000X `PortSet` (same verdict and first error; strict xfail in
`tests/fixtures/r48_mode_string_arguments.json`). `PortSet` is a measured exception on CR6: outside a Scan is accepted, but conditional
placement remains rejected; CR1000X rejects both. `tests/fixtures/pipeline_tasks.json` records an
accepted in-Scan call and both boundary placements for each member on each logger. Round 49 added
(`tests/fixtures/r49_pipeline_task_instructions.json`, 484 observations, all four profiles, all
match): on CR1000X `BrHalf3W`, `BrHalf4W`, `BrFull6W`, `Therm108`, `Therm109`, `AM25T` and
`PulseCount` (display name `Pulsecount`, conditional placement only: outside a `Scan` it prints
`PulseCount must be used within a Scan...NextScan.`); on CR6 and CR1000X `PulsePort`; all only
after an explicit `PipeLineMode` (automatic mode is sequential for them). CR6 rejects the first
group outright. After `NextScan` the CR1000X group keeps the `never reached` warning, while
`PulsePort` and `SerialInRecord` do not (`hides_unreached` in the data file).
`SerialInRecord` (CR6/CR1000X, explicit `PipeLineMode`) prints `SerialInRecord illegally used
inside conditional statement.` in a conditional and `SDM instruction must be in Scan..NextScan,
unless SequentialMode is used.` outside a `Scan` (`messages` in the data file). After a `SlowSequence` the rule does not apply (`main_sequence_only`; reference compiles it, and
in automatic mode a conditional one there gives SequentialMode, `SerialOutBlock` likewise). `MuxSelect` and
`SerialOutBlock` are accepted everywhere. Round 50 (same fixture, 352 more observations): `Calibrate` follows the standard rule on CR6 and
CR1000X. `CS7500`, `CSAT3`, `LI7200`, `LI7700`, `SDMInt8`, `SDMSw8a` and `SDMX50` print `SDM
instruction must be in Scan..NextScan, unless SequentialMode is used.` outside a `Scan` and
`<NAME> illegally used inside conditional statement.` in a conditional, with the name in upper
case as the reference prints it (`SDMINT8`); none prints `never reached` after `NextScan`. All of them, like `SerialInRecord`, are accepted in a conditional after a `SlowSequence`
(`main_sequence_only`, 24 observations, Codex-review follow-up).
Round 54 (below) added `EC100`, `ExciteI`, `Resistance`, `SetDAC`, `VibratingWire` and a probed
`CDM_*` subset. Not implemented because not probed: the other `CDM_*` instructions (`CDM_BridgeFilt`
needs a scan of 1 s or faster, `CDM_CalRead`, `CDM_CANOutCfg`, `CDM_FactCal`, `CDM_FFTFilt`,
`CDM_OffsetRd`, `CDM_VoltFilt`, `CDM_VW300*`), `SDMTrigger`, `TGA` and the other `mode.json` task
instructions.

**Round 54 (fixture `tests/fixtures/r54_pipeline_task_instructions.json`, 1274 observations, 32
`not_modelled`; probes `scratch/r54/`, 2026-10-28).** Same matrix as rounds 49-50 (explicit
`PipeLineMode` and automatic mode: in a Scan, in an `If`, before the first `Scan`, after `NextScan`,
in a `Sub`, plus a conditional and a plain call in a slow sequence, and `SequentialMode`) on CR6 and
CR1000X. CR300/CR350: `Calibrate` (needs an array of at least ~100 elements there) and
`VibratingWire` (`VibratingWire(Tm(),1,1,1,1,1000,3000,10,1,1,1,1,1,0)`, a different instruction
from the CR6 one) accept every placement (`Compiled OK.`, only the `never reached`
warning after `NextScan`); `EC100`, `SetDAC`, `ExciteI` and `Resistance` are undefined there (one
row each). Only data files changed, except the `setup` flag and the slow-scan warning below.

- `SetDAC` (display name `SetDac`, CR6 and CR1000X): the standard rule; like `PanelTemp` it keeps
  `SetDac never reached at run time.` after `NextScan` (no `hides_unreached`). In automatic mode it is
  a sequential trigger (it is in `mode.json`).
- `ExciteI`, `Resistance`, `VibratingWire` (CR6 only; the CR1000X compiler rejects them as
  undefined): the standard rule with `hides_unreached`. `VibratingWire` needs a string variable as
  its 8th argument (`Public Str As String * 100`; any numeric or array argument prints
  `Invalid diagnostic parameter.`, found with Codex research on `FUN_00510f40`, confirmed on the
  compiler); the call used is `VibratingWire(Tm(),1,U1,1000,3000,1,1,Str,60)`.
- `EC100` (CR6 and CR1000X): the SDM messages of round 50 (`SDM instruction must be in
  Scan..NextScan, unless SequentialMode is used.`, `EC100 illegally used inside conditional
  statement.`), `hides_unreached`, accepted in a conditional after a `SlowSequence`
  (`main_sequence_only`). The round-50 control failed only because `Ux(10)` is too short: the
  destination needs 13 elements.
- `CDM_*` (CR6 and CR1000X, identical): the standard rule with `hides_unreached` for `CDM_ACPower`,
  `CDM_Battery`, `CDM_BrFull`, `CDM_BrFull6W`, `CDM_BrHalf`, `CDM_BrHalf3W`, `CDM_BrHalf4W`,
  `CDM_CurrentDiff`, `CDM_PanelTemp`, `CDM_PeriodAvg`, `CDM_PulsePort`, `CDM_Resistance`,
  `CDM_Resistance3W`, `CDM_TCComp`, `CDM_TCDiff`, `CDM_TCSe`, `CDM_Therm107/108/109`,
  `CDM_VoltDiff`, `CDM_VoltSe`, `CDM_SW12`, `CDM_SW5`, `CDM_SWPower`, `CDM_Delay`. The rule applies in
  automatic mode too wherever the program stays Pipeline: `CDM_ACPower`, `CDM_Delay`,
  `CDM_Resistance`, `CDM_Resistance3W` and `CDM_TCComp` are not in `mode.json`, so a conditional or
  `Sub` call is an error even without `PipeLineMode`. `CDM_SW12`/`CDM_SW5` (5th argument) and
  `CDM_SWPower` (4th) are exempt with option 1 and rejected for 0, 2 or an omitted option (like
  `SW12`); `CDM_Delay` is exempt with option 1 (3rd argument) and rejected with option 0; option 2
  prints `Illegal option 2` (not modelled, outside the rule).
- **New rule shape, the `setup` flag.** `CDM_ExciteV`, `CDM_ExciteI` and `CDM_MuxSelect` are
  accepted everywhere before the first `Scan` of a sequence (between `BeginProg` and `Scan`,
  in an `If` there, in a `Sub` or `Function` body, in a program without a `Scan`, inside a
  `SubScan`) and rejected in only two places: after a closed `Scan` (`not allowed outside of
  Scan..NextScan`, also between two scans and after a slow scan; a `SlowSequence` header resets it,
  so before the slow `Scan` they are accepted) and in a conditional inside a `Scan` (`For`, `If`,
  `Select`, ...). `closed` in `pipeline_task_errors` tracks this. `CDM_ExciteI` with a constant
  excitation stays Pipeline in automatic mode (it is not in the `mode.json` task list), so the
  placement errors also show without `PipeLineMode`; the CHM form with a variable excitation makes
  the program sequential (`nonconstant_arg`), which `mode.py` already did.
- **`Slow Seq %d, Scan %d, may skip scans if running with Scan %d.`**, the open "Calibrate in a slow
  scan" candidate, is a general scheduling warning, not a `Calibrate` rule
  (`warnings.check_slow_scan_skips`, `FUN_004684a0`): at the last statement's line, for each slow
  sequence, each of its scans that holds a time-consuming instruction, and each main-sequence scan
  whose interval is not shorter than the slow scan's (`1 Min` against `60 Sec` warns; `2 Sec`
  against `1500 mSec` does not; two equal main scans give two lines, numbered by scan). Order: slow
  sequence, slow scan, main scan, after any `never reached` warnings. It is gated like the
  background-calibration warning (nothing with errors elsewhere, nothing in SequentialMode) and
  needs a constant interval on both. The instruction list is `slow_scan_tasks` in
  `pipeline_tasks.json`, confirmed per logger with a 3 s/3 s probe: CR6 `PanelTemp`, `VoltSE`,
  `VoltDiff`, `PortSet` (also with a variable state), `Therm107`, `BrHalf`, `BrFull`, `TCSe`,
  `TCDiff`, `PeriodAvg`, `Delay` (option 0; option 1 does not), `PulsePort`, `MuxSelect`,
  `Calibrate`, `Resistance`, `VibratingWire`; CR1000X the same without the CR6-only ones, plus
  `Battery`, `Therm108/109`, `BrHalf3W/4W`, `BrFull6W`, `CS616`. These do not warn (so they
  are not in the list): `Battery` on CR6, `ExciteV`, `SW12`, `SetDAC`, `EC100`, `ExciteI`,
  `SerialOutBlock`, all `CDM_*`. Unprobed (not listed, no warning): `AM25T`, `SDI12Recorder`,
  `CS616` on CR6, the SDM group. A main scan placed after a `SlowSequence` is a slow scan.
- Same-line and remaining gaps, all `not_modelled` strict xfails in the fixture: before `BeginProg` in
  explicit `PipeLineMode` the reference prints only the PipeLine Mode error (no `illegally occurs
  before BeginProg.` line) for the `hides_unreached` instructions (15 cases; `SetDac` and
  `PanelTemp` print both) and accepts `CDM_ExciteV`/`CDM_MuxSelect` outright (4 cases); the
  `before_beginprog.json` lists gained `SETDAC`, `EXCITEI`, `RESISTANCE`, `VIBRATINGWIRE` and
  `CDM_THERM107` for the automatic-mode `illegally occurs before BeginProg.`; reachability after an
  endless scan inside a slow sequence is not analysed (12 cases, existing limitation); CR1000X
  `PulseCount` in a slow sequence prints `Pulsecount not allowed in Slow Sequence` (1 case).

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
Round 31 narrows "only the first comma item", "never registered" and the CR300/CR350 note: see
[Alias name collisions, Const-first assignment and String lengths](#alias-name-collisions-const-first-assignment-and-string-lengths-round-31).

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
  compilation goes on. CR300/CR350 follow a different rule ("Round 9, CR300/CR350: include extensions"). The rule applies to an *existing* file too (`r7_include_existing_ext`): it is rejected the same way and contributes nothing. `source._Loader._bad_extension`.
- **Include diagnostics are positional** (`r7_include_cutoff`, all four loggers): a missing-include
  warning or extension error is reported only if the compiler reads the line -- an `Include` after the
  first `EndProg` or inside an inactive `#If` branch is silent. The loader leaves an `include_warning`
  marker line and `parser._Parser.run` emits it when active.
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
  hidden. Gap seen, not modelled: `If c Then Battery` (inline instruction arity). The `declared but
  never called` warning for a table named in an inline `CallTable` matches the oracle on all four
  loggers (round 18, `r18_inline_calltable.json`).
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
  when the same statement, or any *later* statement (same line after a `:`, or a later line), has an
  `X is not defined.` line; a shortfall of an earlier statement stays (`r7_cr3xx_shortfall_order`:
  `PanelTemp(X) : X=Nope` keeps it, `X=Nope : PanelTemp(X)` and `X=Nope`/`PanelTemp(X)` on the next
  line drop it; a statement written with no argument at all (`X=Nope`, then `PanelTemp`) is never hidden; prose lines (`S.Unparsed`) hide only their own line's; `cr3xx.drop_shortfall_with_names`;
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

- **Array dimensions do not end the registration scan** (`r7_bad_as_array_dims`, CR300/CR350): the declaration is split on top-level commas only, so `Public Q(2,3), R, Flag A s Boolean` registers `Q`, `R` and `Flag`.
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
  line (no over-indexed fallback after it). Instruction names as values: see "Round 6 parse fixes".
  Unfinished: bare function in `If`/`Do` conditions (`If Timer > 1`), CR6 scoping of a Sub-local.

- **One dimension-count state for every CR300/CR350 consumer** (probed 2026-10-08, fixture
  `tests/fixtures/dims_state_shared.json`): the first undefined name with more subscripts than the
  running count stops a statement's walk (`Nope4=Nope(1)+S()` ignores `S()`), so the next statement
  still reports `Dimensions not declared`. `expressions.program_dims` is the single entry point;
  `structure.check_pass2`, `expressions.check`, the `For`/`Case`/`ElseIf` checks and
  `cr3xx.check_float_destinations` all use it (CR6/CR1000X keep the declared-variable simulation).

## Round 6 parse fixes (oracle, 2026-10-08, all four loggers)

- **Instruction names are not values** (every `profile.instructions` name probed on CR6, CR1000X,
  CR300; `X = N(1)`, `X = N`, `If N(1) Then`, `Delay(1,N(1),mSec)`): CR6/CR1000X `Invalid symbol in
  expression: <stmt>.`, CR300/CR350 `N is not defined.` (+ `Dimensions not declared: <stmt>` for the
  call form). Exceptions: `Randomize` (also a function; bare gives `Expression expects (`) and the
  ten block keywords (`Case Do For Function If Scan Select Sub SubScan While`): bare compiles, the
  call form is `Incomplete expression: <stmt>.` (not modelled; still accepted). `SerialOut` and
  friends are functions, not in `profile.instructions`, so they keep `Expression expects ,`.
  `_known_names` therefore admits only the block keywords from `profile.instructions`, plus
  `CH400Status` when the program has that instruction (it names the table `Sample` reads). Fixture
  `instr_name_in_expr.json`.

- A bare call of a declared `Function` is one expression statement, not a `Sub` call with a tail:
  `F0()+Nope` -> CR6/CR1000X `Invalid symbol in expression:   F0()+Nope.`, CR300/CR350 `Nope is
  not defined.`; `F0()+1`, `Fn(1)+1`, `Fn(1)*2` compile. Fixture `func_expr_stmt.json`.
- A juxtaposed tail after a `Function` call statement (`Fn(1) 1`, `Fn(1) Zork`, `F0() Nope`,
  `Fn(1),1`, `Fn(1) (2)`, `Fn(1) "a"`, `Fn(1) 1, Zork`) is `Incomplete expression: <stmt>.` on all
  four loggers (CR300/CR350 echo the raw line, `<stmt> | .`). Names: CR6/CR1000X print `Invalid
  symbol in expression: <stmt>.` instead when an *argument* is undefined (`Fn(Zork) 1`) and never
  name tail operands; CR300/CR350 print `<name> is not defined.` first for an undefined argument
  and for a tail that starts with a bare name (`Fn(1) Zork, 1`), not for a tail starting `,` or
  `(`, nor beyond the first operand. Not modelled (still generic `Invalid, or out of place`, real
  is `Incomplete expression`): `Fn(1) 1 +`, `Fn(1)+1 2`. Fixture `func_juxtaposed_tail.json`.
- A `Sub` tail splits arguments at top-level commas only: `Call Two(1) Round(2,0)` and
  `Two(1) Round(2,0), 3` (the latter: `Call to Two with 3 parameters, requires 2 arguments.`).
  Fixture `sub_tail_nested.json`.
- The CR6/CR1000X "earlier parameter-kind error hides `Too many parameters`" suppression is
  per statement: `PortSet(C1,1,x):PortSet(C1,1,1,2)` -> `Illegal parameter x, expecting constant
  integer.` then `Too many parameters:  2.` (also with `Variable illegal in Parameter`). Modelled
  on `:`-joined lines by attributing a diagnostic to the statement whose text contains its echoed
  argument. Not modelled: when the too-many statement comes first the real output lists it first
  (statement order); ours lists per pass. Fixture `stmt_scope_suppress.json`.
- CR300/CR350: the `Public.<name>` exemption of the unmatched-closer drop (`_hides_nothing`) reads
  the statement text outside string literals: `X=Round("Public.x",1,2)` then a stray `EndIf`
  reports only the arity error (oracle, 2026-10-08). Fixture `closer_public_lexical.json`. Known
  gap, observed: the real rule is positional, not "before the first `)`" --
  `Round(X,"b",Public.X,1,2)` and `Round(X,"(",Public.X)` (`Public.` in the third argument)
  also hide the closer, while `Public.` within the first two arguments does not.

## Round 7 fuzz false accepts (oracle, 2026-10-10; fixtures `tests/fixtures/r7_*.json`)

All rules observed on the real compilers; "all four" means CR6, CR1000X, CR300, CR350 agree.

- **Adjacent literals in a string size** (`r7_decl`): `Public S As String * 1  0` -> `Incomplete
  expression: 1  0.`, all four. The echo is everything after the `*` minus one leading blank,
  cut at an `=` (`* 1 0 = "a"` echoes `1 0 `), kept to a trailing `as long`. CR6/CR1000X report
  every variable, CR300/CR350 stop at the first (then `Too many parameters:  <var two on>.`).
  `1 Nope` is a different message (undefined-name family) and is not modelled.
- **Empty dimensions with an initializer** (`r7_decl`, `r7_struct`): `Dim A() = {1,2}` ->
  `Invalid, or out of place expression:  =.` (echo: the blanks after `)` plus `=`) before the
  out-of-bounds warning, all four; CR300/CR350 report the first variable only and carry on with
  the initializer checks. The dup-name ordering with this message is not modelled.
- **StructureType members** (`r7_struct`): `FuzzVar = Nope` / `FuzzVar = FuzzVar + 1` resolve
  like a declaration initializer (`Invalid symbol in expression: ...` + `Variable initialization
  of FuzzVar has bad syntax.`; CR300/CR350 `X is not defined.`, `Variable illegal in Parameter:`
  ...); members of any structure are not known names inside the initializer.
- **`SNMPVariable` argument count** (`r7_snmp`): dotted-OID statements (parsed as `Unparsed`) take
  `Name, OID, Type[, Access]`; a Type that is not an SNMP type ends the list at 3 and the next
  argument is `Too many parameters:  <arg>.`; `RWRITE` access also takes a fifth (Valid)
  argument, `RONLY` does not (`Float,RONLY,1` -> `Too many parameters:  1.`).
- **`ModemCallBack` baud** (`r7_baud`, CR6/CR1000X): `<n> is illegal baud rate.` on `COMSDC7`
  unless the truncated value is 0 or 600..460800; outside 32 bits the value prints as
  `-2147483648`. Only `COMSDC7` is modelled (`ComC1,300` passes; `COMRS232,300` fails; string
  and `Hr` arguments also fail, not modelled).
- **Unclosed `Sub`/`Function`** (`r7_sub`): `Sub ... EndSub not matched.` on the last line
  (`EndProg`, or the last line without one), after the unclosed-block messages and after
  `No BeginProg in Program.`; a `BeginProg` inside the open Sub is accepted.
- **`HTTPPut` argument count** (`r7_httpput`): 4, 8 or 9 arguments compile; 1-3, 5-7 are
  `Expression expects , :`, 10+ `No Matching, or out of place )`.
- **`TCPOpen` third argument** (`r7_tcpopen`): a string, `NAN`, a float literal (`1.0`) or an
  integer beyond 32 bits is `Parameter must be a constant integer:`; `Hr`, `-1`, `1+1`, `True`
  compile. A declared variable or string constant here is also an error (not modelled).
- **CR300/CR350 over-indexed declared variables** (`r7_dims`): `Public A()` counts as a scalar,
  so `A(1)` is `Dimensions not declared`; the message now also covers `If`/`ElseIf`/`Do While`
  conditions (`Dimensions not declared: Q(1) .`, `If` echo keeps the blank before `Then`), and it
  starts the `Too many parameters` cascade like the other declaration-class errors.

Not done: the CR300/CR350 `Cannot autoallocate memory for DataTable` that appears when a scan
interval overflows (timing family, owned elsewhere). The CR300/CR350 storage limit
(`Unable to allocate enough data table space`) is `checker/storage.py`, `docs/tdf-rules.md`
"Storage limit" (round 15).

## Round 7 message families (oracle, 2026-10-10; `tests/fixtures/r7_cr3xx_*.json`)

- **Adjacent literals in an initializer** (all four): `Const X = 1 2` -> `Incomplete expression:
  1 2.`; `Public X = 1 2, Y` / `Dim A(2) = {1 2}` / `Public X As Long = 1 2` -> that line then
  `Variable initialization of X has bad syntax.` The echo is the trimmed value (the first bad
  `{...}` item). CR6/CR1000X report every variable, CR300/CR350 the first, and none of them
  follows with the generic fallback (pass 1).
- **`Const X =`** (nothing after `=`) -> `Invalid, or out of place expression: .` (empty echo, no
  newline, all four); `Const X` / `Const X As Long` add `Too many parameters:  X.` after `Const
  with no assignment.` A `Const = 1` line is `Const name 1 illegal, starts with a number.` (not
  modelled).
- **`Public S As String *`** with no size (end of line or a comma follows) -> `Size of string
  missing.` (all four, pass 1, once per statement; CR300/CR350 only the first statement).
- **`#` statements**: a statement starting with `#` -> `Unknown instruction: <raw text>` + the line
  end as a newline + `.` (all four, even CR6) and the compile stops there: no later statement
  is reported, earlier errors stand.
- **CR300/CR350 assignment syntax verdicts**: for an assignment or a `Not ...` statement the
  pass-1 verdict words of CR6 apply after the undefined-name lines (`X = 1 2` -> `Incomplete
  expression:`, `X = (1 2)` -> `No Matching, or out of place ) in expression:`); prose lines stay
  generic. A built-in call with juxtaposed operands in its parentheses (`Sin (foo bar)`,
  `ArrayIndex (Array Index)`) is `No Matching, or out of place )` after its names.
- **CR300/CR350 names in the generic fallback**: `X (foo bar)` names `foo`, `bar` when `X` is a
  declared variable or a function (not a `Const`: `C1 (COM1 TX)` names nothing); `Not foo bar`
  names both; a parenthesis-less function reads as many operands as it has parameters (`Timer
  is bar baz` -> `bar`, `baz`; `COS foo bar` -> `foo`; `Sin is foo` -> nothing).

Open (CR300/CR350 chm, not established): `Dim A(8)` followed by a lone `=` line (the declaration
continues across the line), `Function ... As` header cascade, `Loop While I>0 and`,
`For ... Step-1`, `X (foo) bar` (names `foo` only when text follows the parenthesis).

## Round 7 CR6/CR1000X help-corpus families

### `Public`/`Dim` initializer line pulling (CR6/CR1000X; `source._pull_decl_initializer`)

Oracle, 2026-10-10 (CR6 and CR1000X identical; CR300/CR350 differ and are untouched):

- A `Public`/`Dim` line ending in `=` pulls the next line into the declaration. A pulled line that
  is not blank is read as the initializer: `Dim D As Long =` / `5`, `Public D As String * 4 =` /
  `"ab"`, `Public D(2) As Long =` / `1, 2` compile. **Every pulled line is consumed without
  advancing the reported line counter**: every later diagnostic in the file is reported one line
  earlier per pulled line (`source.py` subtracts a running `shift`).
- A brace list whose line ends in `,` or `{` keeps pulling while the braces are unbalanced
  (`{ 1,` / `1,` / `1 }` and `{` / `1,` / `2 }` compile; `{ 1, 2` / `}` does not pull, the `}` is a
  stray statement).
- Exception: `=` at end of line followed by a line starting with `{`, on an **array** variable
  (`D(2)`), is `Incomplete expression: <that line>\n.` (the echo is the first top-level comma
  segment; the `\n` only when it is the whole line and no comment was stripped; a bare `{` gives
  `Invalid, or out of place expression: {\n.`), then `Variable initialization of <var> has bad
  syntax.`, both on the declaration line. The brace line does not keep pulling, so its continuation
  lines are ordinary statements (`Invalid, or out of place expression: 1,.`). A scalar
  (`Public D As Long =` / `{ 5 }`) compiles.
- Not modelled: a blank pulled line (`Invalid, or out of place expression: \n.`), a pulled line
  that is a statement (`Dim D As Long =` / `Public X` -> `Invalid symbol in expression: Public
  X\n.`, ours has no `\n`), and a comment-only pulled line (real compiler page-faults).
- A stray `}` (`}`, `3 }`) is `Invalid, or out of place expression: <stmt>.` in pass 1 on
  CR6/CR1000X (`_pass1_scan`); `3 ]` is too, not modelled.

### `Const <name> =` with an empty or operator-ending value

All four loggers: `Const A =` (also at end of line, value on the next line), `Const A As Long =`,
`Const A = 5 +` (also `-`, `/`, `*`, `^`, `2 + 3 *`) -> `Invalid, or out of place expression:
<value>.` (value from its first character, trailing blanks kept, comment stripped), not `Const with
no assignment.`, which stays for a missing `=`. Not modelled: `Const A = *`. Fixture
`init_pull.json` (CR6/CR1000X).

### Pass-1 fallback: predefined symbol with a subscript, name pair before an unterminated string

CR6/CR1000X, oracle 2026-10-10 (`pass1_symbol_paren_string.json`; a preceding pass-2 `X = Nope + 1`
is hidden exactly when the statement is pass 1):

- A statement starting with a **predefined symbol** (`C1`, `U1`) followed by `(`... whose contents
  do not scan clean (`C1 (COM1 TX) to C4 (COM3 RX)`, `U1 (a b)`, `C1 (COM1 TX)`) is a pass-1
  `Invalid, or out of place expression`. A declared variable in the same position (`X (COM1 TX) to
  C4`) stays pass 2 (`_pass1_scan`).
- A statement `<known operand> <name> ... "` ending in an unterminated string (`string here " + X`,
  `X here " + X`) is pass 1 because the scan fails at the adjacent name before the string; with an
  unknown first name (`foo here "`) it stays pass 2. Block keywords, functions and instructions as
  the first word are excluded (`If X = 1 Then X = "abc`).

### Brace-led statements; `Do`/`Loop` with a parameter

- CR6/CR1000X (oracle 2026-10-10, `brace_statement.json`): a statement starting with `{` (`{ 1 }`,
  `{ 1, 2`, `{`, `{ 1 } + 2`) is a pass-1 `Invalid, or out of place expression: <stmt>.`; `X = { 1 }`
  and a bare `=` are silent. After `Dim A(2)` / `=` / `{ 1, 2 }` only the `{` line is reported.
- All four loggers (`do_loop_param.json`): `Do`/`Loop` followed by anything but `While`/`Until`
  reads the word as an instruction parameter: an identifier or string (also behind one `(`), `Do X`,
  `Do X = 1`, `Do "abc"`, `Do (X)`, `Loop X` -> `Illegal parameter <word>, expecting constant
  integer.`; a lone number or negative number (`Do 5`, `Do -1`, `Loop 5`) -> `Invalid or missing
  expression.`. Not modelled: `Do 5, 6` (real `Invalid, or out of place expression: , 6.`).
  CR300/CR350 `Do While` / `Do While X X` echo differently (existing gap).

### `#`-led statements: `Unknown instruction`, and the compile stops

All four loggers (oracle 2026-10-10, `hash_unknown_instruction.json`): a statement starting with `#`
that is not a preprocessor directive (`#1 abc`, `#Foo`, `# 1 abc`, `#Define X 1`, a bare `#`)
is `Unknown instruction: <statement as written, leading blanks kept>\n.`, with the `\n` dropped
when a `'` comment was stripped off the line. It ends the compile: **nothing after its line is
reported**, pass-1 errors included (`checker.check` drops later diagnostics of the file). Errors
before it stay. Not modelled: `#If 1 abc` (`Invalid symbol in expression: 1 abc.`) and `X = 1 #1`
(`Invalid symbol in expression: X = 1 #1.`).

### A lone `_` continuation line (CR6/CR1000X)

A physical line consisting only of `_` (blanks allowed before it) joins the next line **verbatim**:
the blanks before the `_` plus the next line unstripped, no separator (`    _` / `X Y` echoes
`    X Y`; `  _` / `  X Y` echoes eight blanks). After another lone `_` line the earlier blanks are
dropped (`_`/`_`/`X Y` echoes `X Y`). Other continuations (text before the `_`) are unchanged.
Fixture `lone_underscore_join.json` (`source._Loader.load_text`). Fixes the leading blank in the
`CDM_VW300Config(...)` leftover-line echo of `rainflowsample2_1.CR6`.

### `Alias <target> =` with nothing after it, and a target followed by junk (all four loggers)

Oracle 2026-10-10 (`alias_eq.json`, CR6/CR1000X; CR300/CR350 print the same `Alias` text but have
other, existing gaps in that file's other lines):

- `Alias gd(1) =` (also with a comment, blanks, or the name on the next line) ->
  `Nothing after '=' in alias definition: <target>.`: an indexed target is echoed as written up to
  the `=`, blanks included (`gd(1) .`, `gd(1)  .`), a plain name trimmed (`Alias X =` -> `X.`).
- `Alias gd(7) uSecond to rTime(7)`, `Alias rTime(7) uSecond` (no `=` at all, junk after the
  target) -> `Need '=' in alias definition: <rest of the line>\n.`, like the target-alone form.
  (The parser used to let the "unexpected trailing input" reason override it.)

### `Invalid assignment in expression` (CR6/CR1000X)

Oracle 2026-10-10 (`invalid_assignment.json`): a statement whose first top-level `=` follows a
literal (`6 = X`, `"a" = X`, `-1 = X`, `1.5 = X`, `6 = X = 3`) or a known variable plus a binary
operator (`X + 1 = 2`) is pass-1 `Invalid assignment in expression: <stmt>.`, which hides pass-2
errors. Not modelled: `X(1) + 1 = 2` (real: invalid assignment; ours: generic), `(X = 1) foo`
(real: `Incomplete expression`), `(X) = 1` (real: accepted), and the help-corpus sentence
`(Double = Double + Double) Results in Double-Precison` (`DoublePrecArith_1.CR6`).

### Block keywords used as functions (CR6/CR1000X)

Oracle 2026-10-10 (`block_keyword_call.json`; CR300/CR350 give the same text plus their
`Too many parameters:  Sec.` cascade and are not modelled): `Case Do For Function Scan Select Sub
SubScan While` followed by `(` inside an assignment value or an `If`/`ElseIf` condition
(`Y = Scan(1)`, `Y = 1 + SubScan(1)`, `Y = Scan (1)`, `If X = Scan(1) Then Y = 1`) is a pass-1
`Incomplete expression: <stmt>.` (an `If` echoes its condition text up to `Then`, trailing blank
included), so it hides every pass-2 finding; the bare keyword (`Y = Scan`) and the other block words
(`NextScan(1)`, `Loop(1)`) keep their old results (`Invalid symbol in expression`). Not modelled:
`Y = If(1)` (real `Incomplete expression`), the unclosed `Y = Scan(`. Implemented as
`structure._block_keyword_call`, called per statement from `check_pass1`.

### `If c Then :` is a one-line If; unmatched `Loop`/`ElseIf` conditions are not resolved (CR6/CR1000X)

Oracle 2026-10-10:

- `If X Then : Y = 1`, `If X Then :`, `If X Then : Y = 1 : Y = 2` compile with no `EndIf`: a colon
  after `Then` makes it a one-line If, and a bare `Else` / `EndIf` / `End If` segment after it is
  part of it (`If X Then : Y = 1 : Else : Y = 2`, `If X = 1 Then : EndIf` compile). `If X = 1 : Y = 2`
  (no `Then`) is unchanged (`IF or SELECT without EndIf or EndSelect.`). `parser._parse_line`;
  fixture `colon_if.json`.
- A `Loop While|Until Nope` with no open `Do` gives only `LOOP without DO.` (plus its placement
  companion), no `Invalid symbol`; likewise an `ElseIf Nope Then` with no open block-form `If`
  gives only `Case with no Select.`. Matched ones still report the symbol. Fixture
  `unmatched_loop_elseif.json`. Not modelled: a stray `Case Nope` is `Case with no Select.` in the
  real compiler (ours `Case outside Select.`).

### `For` header with a bad `To`/`Step` half (CR6/CR1000X)

Oracle 2026-10-10 (`for_header_incomplete.json`): the text after `To` splits at a blank-delimited
`Step`; each half is an expression, and one with adjacent operands is a pass-1 `Incomplete
expression: <half>.` (trailing blanks kept; one line, hides pass 2): `For I = 5 To 1 Step-1`
(`1 Step-1`), `Step(-1)`, `... To 12 Step` (`12 Step`), `Step 2 3` (`2 3`), `Step -1 X` (`-1 X`),
`To 1 Stepper`. `Step -1` and `Step - 1` compile. `structure._for_header_incomplete`. Observed, not
modelled here, see "`To` as a binary operator" below.

### `String *` with no size

All four loggers, oracle 2026-10-10 (`string_size_missing.json`, CR6/CR1000X; CR300/CR350 print the
same line plus their usual `Too many parameters` cascade, not modelled for this shape): a `Public`/`Dim`
whose text ends at `String *` (also `A, B As String *`, a following blank, or the size on the next
physical line) is pass-1 `Size of string missing.` and the next-line number is its own statement
(`Invalid, or out of place expression: 100.`). With a `'` comment after the `*` the real message is
`Invalid, or out of place expression:  .` (the blanks after the `*`, one dropped; modelled). The
declaration still registers its variables (`parser._parse_vardecl` flags `size_missing`), so a later
`X = MyString +` reports. `String * X` -> `Variable illegal in Parameter: X.`;
`String * 5 5` -> `Incomplete expression: 5 5.` (not modelled).

## Scope-aware lookups and statement identity (round 8, 2026-10-10)

Fixtures `scope_portset.json`, `scope_fft.json`, `scope_gps.json` (`tests/test_scope_shadow.py`).
- FFT source length and the GPS NMEA-string size resolve the declaration through
  `symbols.scoped_view(program, stmt)`: top-level declarations plus the instruction's own routine,
  a local shadowing a global. `Public x(32)` with a Sub-local `Dim x(16)` and `FFT(x,...,32,...)`
  in a table compiles on all four loggers; a short global with a long local is still rejected.
- Statement-scoped suppression of `Too many parameters` matches by statement identity
  (`Diagnostic.stmt`) where the emitter records it, else by echo substring:
  `PortSet(C1,1,opt):PortSet(C1,1,1,opts)` reports both lines (CR6/CR1000X). Not modelled: the
  reversed order (`Too many parameters` statement first) prints in statement order.

### `Function` names for call statements are collected from read code only

Oracle 2026-10-10, all four loggers (`function_names_active.json`): a `Function Two(A)` after the
first `EndProg`, or inside an inactive `#If`, does not make an earlier `Two(1) Round(2,0)` a
`Function` call (the program defines `Sub Two(A,B)`; it compiles). `parser._Parser.run` finds the
names with a throwaway pass over the lines, so only definitions the real pass reads count.

### Word operators after a `Function` call statement

Oracle 2026-10-10, all four loggers (`func_word_operator_tail.json`): `Fn(1) MOD 2`, `AND`, `OR`,
`XOR`, `EQV`, `IMP` (any case) continue the call as one expression and compile; `Fn(1) NOT X` and
`Fn(1) Y` stay `Incomplete expression`; a dangling `Fn(1) MOD` is `Invalid, or out of place
expression`. The parser sends a leading word operator to the expression path, not the juxtaposed
tail (`_WORD_BINARY_OPS`). `Fn(1) AND X Y` (real `Incomplete expression`) is still not modelled.

### `Function` call statement continued as an expression is checked like an assignment value

Oracle 2026-10-10, all four loggers (`func_expr_tail_checks.json`): `Fn(1)+Sin` (`Expression
expects (`), `Fn(1)+Sin(1,2)` and `Fn(1)+Fn(1,2)` (`No Matching, or out of place ) in expression`),
`Fn(1)+Sin()` and `Fn(1)+Fn()` get the same message as `X = <same expression>`. CR300/CR350 also
print `Dimensions not declared` for `Fn(1)+X(1,2)`, `Fn(1)+X(1)` (scalar `X`) and an undefined
indexed name (`Fn(1)+Zork(1)`, after `Zork is not defined.`). `Call.tail_expr` is therefore used
as the checked expression in the bare-function/arity pass, the user-Function count pass,
`_stmt_exprs` (dims simulation), `_report_call_arg_names` and the over-index loop. Not modelled:
`Fn(X(1,2))` / `Fn(Zork(1))` argument forms on CR300/CR350 (real adds `Dimensions not declared` with the
whole-statement echo).

### `To` as a binary operator

Oracle 2026-10-10, all four loggers (`to_operator.json`): outside `For` headers and `Case` lists, `To`
is a binary operator in assignment values (`=` and compound), `Select Case`, parentheses, call
arguments and subscripts of a value (`I = 1 To 12`, `X = (1 To 12)`, `X = Sin(1 To 2)`,
`X = A(1 To 3)`, `Y = 1 To NOT 2` compile); it binds between comparison and `+`/`Mod` (`Y = X Mod 2 To
3 ^ 2` compiles). Rules: at most one `To` per parenthesis level and argument (`1 To 2 To 3` ->
`Incomplete expression`, inside parentheses or a call `No Matching, or out of place )`); no comparison
operator at the same level on either side (`X < 1 To 2`, `1 To 2 = 3`, `Y = X = 1 To 12` ->
`Incomplete expression`; `(X = 1) To 2` and `X = 1 And 2 To 3` compile); a dangling `X = 1 To` is
`Invalid, or out of place expression`; an undefined operand is reported as usual. `If`, `While`,
`Do While` conditions reject it (`Incomplete expression: <cond>.`; ours still rejects with other
wording for `If ... Then` and `Do While` on CR300/CR350). Implemented by `parser._parse_relational`
(`_to_ok`, set only while parsing an assignment value or `Select Case`) and `structure._scan_expr`
(`to_ok`). `Y = TO` (a bare `TO` value, real `Invalid`) is still accepted.

Round 9 (2026-10-10, fixtures `to_args`, `to_scan`, `to_tables`, all four loggers except
`to_scan`: CR6/CR1000X): `_to_ok` is also set for instruction arguments (`Scan(1 To 2,..)`,
`Sample(1,X To Y,FP2)`, `PanelTemp(X To Y,250)`, `Move(A(1),1 To 2,..)`), an assignment target's
subscripts (`A(1 To 2) = 3`), a `Const` value, and `If`/`ElseIf` conditions (`If X To 2 Then`
compiles; `If X = 1 To 2` and two `To` are `Incomplete expression: <cond> .`, block form only: the
inline form still prints the generic message). A `To` of two constants is the constant 0
(`Scan(1 To 2,Sec,..)` -> `Scan 0uSec invalid`, `Scan(1,Sec To Min,..)` -> `Scan 1uSec invalid`,
`PortGet(X,5 To 2)` -> `Invalid port 0`, `Const K = 5 To 2` is 0, `consteval._binary`); with a
variable operand the argument is not a constant (`Variable illegal in Parameter: X To 2`). A String
on the *right* of `To` is `String is illegal here` (alone: `"a" To "b"`, `3 To "b"`, `S To S`,
`Len("a" To "b")`; `var_types._string_operand_messages`); a String on the left compiles
(`"a" To 3`, `S To 3`).

## Round 8 CR6/CR1000X: instruction followed by junk (2026-10-10)

Fixtures `instr_junk_args.json`, `instr_junk_top.json`, `tests/test_instr_junk.py` (CR6 and CR1000X
identical; CR300/CR350 untouched).

- **Statement form is the paren form.** `Kw a b, c d` (no `(`) is read as `Kw(a b, c d)` by
  `_instruction_arg_findings`: adjacent operands are `Incomplete expression: <arg>.`, a first
  unresolved name is `Invalid symbol in expression: <arg>.` (then the `<Name> illegally occurs
  before BeginProg.` line). Skipped for instructions with a raw-text parameter (`plain_expression_params`)
  and for `If`/`For`/`Call`/... (`_STATEMENT_FORM_EXCLUDED`).
- **Arity before names, except for destinations.** A statement short of required parameters whose
  unresolved name sits in a destination argument (codes 8/27/29) is only `Invalid or missing
  parameter.` (`PanelTemp the instruction`, `PanelTemp(the instruction)`); with the name in an
  expression/constant argument it is only the `Invalid symbol` (`Delay(junk)`, `Scan junk`; the
  shortfall line is dropped, `instr_rules.check`). `SlowSequence junk` compiles (its argument is
  never resolved). Short by one with a stage-1 `Incomplete` on a two-argument call also prints the
  shortfall (`Delay(1, 2 3)`); one argument gives only the `Incomplete` (`Delay 1 2`).
- **Constant-only arguments** (codes 1/5/20/38) resolve every name first: `15000 junk` is `Invalid
  symbol`, not `Incomplete`; a declared variable in a malformed constant argument adds `Variable
  illegal in Parameter: <arg>.` (`Delay X Y`, `PanelTemp(X, X Y)`).
- **Argument starting with `To`/`Is`/a binary word** (`Scan to x`, `Randomize and 3`,
  `Calibrate(to x)`): pass 1 `Invalid, or out of place expression: <arg>.` (the keyword is not
  echoed; no before-`BeginProg` line). This hides an earlier prose line's pass-2 error
  (`calibrate_1`).
- **Parameterless instructions** (`instr_rules.ZERO_PARAM_INSTRUCTIONS`: `BeginProg`, `EndProg`,
  `NextScan`, `ExitScan`, `Restore`, `Wend`, ... probed one by one, both `Name junk` and `Name(1)`):
  any argument is pass 1 `Too many parameters:  <first simple token>.` (`BeginProg, EndProg (Begin
  Program, End Program)` -> `EndProg`). Not `EndIf`/`EndSelect`, whose closer messages interact.
- Not modelled: `Delay(1, junk)` (`Invalid or missing parameter.` instead of the symbol),
  `Randomize X, to x` (`Too many parameters:  to.`), `Calibrate X, to x` (`Illegal parameter to,
  expecting constant integer.`), a lone `to`/`or` argument (`Calibrate to`), and the unreachable-code
  warning name for these pass-1 statements.

### `name.` statement: `Could not find <name>.` (CR6/CR1000X, 2026-10-10)

Fixture `could_not_find_dot.json`. A statement that starts `<name>.` where `<name>` is no table (`times.`,
`variables.`, `Timer.`, `X.` with `X` a plain `Public`, `X.Y.Z`, `X. Y`) prints pass 2 `Could not find <name>.`
before its `Invalid, or out of place expression: <text>.` (and the `Expr illegally occurs before
BeginProg.` line). `Tab.`/`Status.` (tables, system tables) and `X .` (blank before the dot) do not. A
pass-1 error elsewhere hides it. Not modelled: `Tab.Foo` -> `Could not find Tab.Foo(1).`, `X.1`, the
`X.Y = 3` assignment (accepted by us; the oracle also says `Invalid assignment in expression`).
Also seen and left alone: the oracle accepts `I = 1 To 12` (one `To` between two valid operands, scalar
target, `+=` too; a second `To` is `Incomplete expression`, a trailing `To` is `Invalid, or out of place
expression`, a string after `To` is `String is illegal here`), which we reject as `Incomplete expression`
(`fornext_1`, the `To` owner's area).

### `ACPower` channel series (CR6, 2026-10-10)

Fixture `acpower_channels.json`. With `RepsI` 1 or 2 and constant channels, a bad `ChanV` `v` (4th) prints
`Invalid channel` for `v, v, -v, v`; then per rep `k` a bad `v` adds `v` (from rep 2) and a bad `ChanI` `i`
(7th) adds `-(i+k)` (so `ChanI` alone is `-i`; `0` prints `0`, `-1` prints `1`). Larger `RepsI` add a `Scan
interval too short` line (timing, not modelled) and a variable `RepsI` is a different message; both stay
on the old two-line output. Replaces the "known incomplete" note above (CR6 only; CR1000X unchanged).

### `AvgRun` instance number given by a `Sub` parameter (2026-10-10)

Fixture `avgrun_subparam.json`: an untyped `Sub` parameter as the number of distinct instances is a
variable, `Optional number of distinct instances must be a constant of type long.` precedes `Missing
instance number` (`avgrun_4`).

A `Sub` parameter named like a `Const` declared *after* the `Sub` is local to it: the global constant is
still accepted as that instance number (fixture `sub_param_const.json`, all four loggers; a `Const` declared
first gives `N is already declared as a CONST.`, see "`Sub` names shadowing an earlier `Const`" below).
`declared_variable_names` skips such names.

## `Sub` names shadowing an earlier `Const` (oracle 2026-10-11, all four loggers; `tests/fixtures/const_shadow_sub.json`)

A `Sub`/`Function` parameter, a `Dim` or a `Const` inside its body whose name is a global `Const` declared
*earlier in the file* prints `N is already declared as a CONST.` at that line, one per name (`Sub Foo(N,N)`
gives two lines on CR6/CR1000X), when the `Sub` comes before the `EndProg`: before `BeginProg`, or inside the
program body (`BeginProg`, `Sub Foo(N)`, `EndSub`, `EndProg` reports the same line only). A `Const` declared
after the `Sub`, or a `Sub` after `EndProg`, is not a conflict. CR300/CR350 print only the first such line in
the file (`Sub Foo(N)` then `Sub Bar(N)` gives line 3 only); a later duplicate global `Const` still prints its
own message. With `AvgRun(..., N, ...)` the CR300/CR350 `Too many parameters` cascade follows as before.
Implemented in `structure.check_pass1` (`_local_const_conflicts`, `const_shadow_reported`). Not modelled:
`Const N=2` then `Sub Foo` with `N=1` in its body prints `Invalid assignment in expression: N=1.` (a different
family, still silent here).

### `R` (double-precision) number suffix; assignment inside a leading `( )` group (2026-10-10)

Fixtures `double_literal_r.json` (all four loggers), `paren_assign.json` (CR6/CR1000X).

- A decimal literal followed directly by `R`/`r` (`2.15R`, `2R`, `.5R`, `1e3R`, `2.R`) is one number token
  and compiles; `5RR`, `5R5`, `2Rx`, `2 R` stay `Incomplete expression`. (`&HFFR` is `Invalid symbol` in the
  oracle: not modelled.) The lexer previously split it, so valid double-precision programs were rejected.
- A statement starting with `(` whose group holds `lhs = ...`: a number, string, `-number`, or predefined
  symbol/type word (`Double`, `Long`, `Float`, `TRUE`, `NAN`, `C1`, `FP2`; not `Single`, `Integer`, `ULong`,
  `Int4`, `PI`, which are plain unknown names) on the left is pass 1 `Invalid assignment in expression:
  <stmt>.`; a declared variable on the left keeps `Incomplete expression: <stmt>.` for words after the group
  (`(D = D) x`). Not modelled: `(D = D) + 1` compiles; `(D(1) = 1) x` is `Incomplete`.

### `CallTable <word> <rest>` statement form (CR6/CR1000X, 2026-10-10)

Fixture `calltable_statement.json`. Without parentheses the first word is the table and the rest is a second
argument, `CallTable(Tab, <rest>)`: `CallTable instruction is used to transfer` -> `Invalid, or out of place
expression: is used to transfer.` (`calltable_1`), `Tab junk` -> `Invalid symbol in expression: junk.`,
`Tab 1 2` -> `Incomplete expression: 1 2.`, a third comma part -> `Too many parameters:  <its first token>.`
(`_instruction_arg_findings`; the table word is never resolved as a variable). Not modelled: `CallTable
Tab(junk)` (symbol error for the subscript).

### `ExitScan` in a one-line `If` ends the scan for reachability (all four loggers, 2026-10-10)

Fixture `exitscan_inline_if.json`. `If F Then ExitScan` / `... Else ExitScan` inside an infinite `Scan(...,0)`
counts as an `ExitScan` in the body: the statement after `NextScan` gets no `never reached` warning
(`warnings._scan_span` only looked at top-level statements; `scannextscan_3`).

### Predefined symbol as an assignment target; `Const X As` (2026-10-10)

Fixtures `symbol_assignment.json`, `const_as_assign.json` (CR6/CR1000X), `const_as_only.json` (all four).
- `Sec = 3`, `C1 = 3`, `TRUE = 1`, `NAN = 1`, `FP2 = X`, `String = "x"`, `Long = 3`, `Long += 3` (a plain
  target that is a predefined symbol or type word, not a declared variable) is pass 1 `Invalid assignment in
  expression: <stmt>.` We used to accept it. `PI`, `Status`, `Single` are plain unknown names
  (`Invalid, or out of place expression`). Not modelled: `Long == 3` (also `Invalid assignment`), `Long(1) = 3`,
  `Long.Y = 3`.
- `Const A As` with nothing after `As` also prints `Too many parameters:  A.` after `Const with no assignment.`
  (all four loggers), like `Const A As Long` (`sprintf_4`).

## Round 8 CR300/CR350 families (oracle, 2026-10-10; `tests/fixtures/r8_cr3xx_*.json`)

- **Statement ending at an open `(`** (all four loggers; `r8_cr3xx_openparen.json`): `X = Sin(`,
  `X = Y(`, `X = Sin(1)+Sin(` are the generic `Invalid, or out of place expression: <stmt>.`, not `No
  Matching, or out of place )`; a trailing comma (`X = Sin(X,`) or an unclosed argument (`X = (X`) stay
  `No Matching`. `structure.py` (pass 1 and pass 2 `_UNCLOSED_PAREN_EXPR`): text ending in `(` -> fallback.
- **CR300/CR350 `While`/`Do While|Until`/`Loop While|Until` conditions** (`r8_cr3xx_cond.json`): the
  CR6 pass-1 verdict applies to the condition alone (`X >`/`X =` -> `Invalid, or out of place
  expression: X >.`, `X Y` -> `Incomplete expression: X Y.`), echoed without the line-end newline, names
  of the condition first. They no longer take the "instruction reads the prose" path
  (`Incomplete expression: While X >.`). `cr3xx.check_unknown_line_words`. An empty condition keeps the old path.
- **`Too many parameters` on a `Sub`/`Function` header after the cascade started** (CR300/CR350,
  `r8_cr3xx_funchdr.json`): with two or more parameters the echo is the *second parameter's name*
  (`(a As Long!, rst As Long) As Long` -> `rst`, `(a, b, c)` -> `b`); with one parameter it stays
  the second token (`(a As Long!) As Long` -> `As`); with no parameters (`F( ) As Long`) nothing is
  printed. `cr3xx.check_too_many_cascade`.
- **Leftover lines starting with a signed number or a string** (CR300/CR350, `r8_cr3xx_signed_leftover.json`):
  `-1 Then`, `"a" Foo`, `-1 Foo bar` start the undefined-name chain like a plain number (`Then is not
  defined.` then the generic fallback); `-1 X` with `X` declared names nothing. `cr3xx._leading_unknown_words`.
- **Conditions naming a cascade-dropped variable** (`r8_cr3xx_dropped_cond.json`): `If X =`, `While X >`
  after the cascade dropped `X`'s declaration print `X is not defined.` before the verdict
  (`cr3xx._reword_prose` also matches the condition-only echo). Not modelled: `Do Until X >` in a
  cascade is `Too many parameters:  X.`; `If X Y` omits `X`.
- **`SerialOpen`/`ModemCallBack` baud from a constant** (oracle 2026-10-10; `r8_cr3xx_baud*.json`,
  `r8_cr3xx_modemcb.json`): the baud is evaluated as a constant expression: `Hr` -> `4 is illegal baud
  rate.`, `Sec` 2, `mSec` 1, `Min` 3, `Day` 5, `Hr+1` 5, `True` -1; `uSec`/`False` (0) pass. A
  `ModemCallBack` string baud is `-2147483648 is illegal baud rate.` (SerialOpen accepts a string).
  `ModemCallBack` with other than 9 arguments reports only the count error (the literal baud line used
  to be added). CR300/CR350 after the `Too many parameters` cascade started: a negative or computed
  `SerialOpen` baud is swallowed (`-115200`, `Hr+1`), `Hr`, `12345` still print.
- **`For` header with a bad `To`/`Step` half on CR300/CR350** (`r8_cr3xx_for_header.json`; the CR6/CR1000X
  rule above applies): the `From` expression's undefined words, then the bad half's leading
  undefined words (`1 Step-1` -> `Step`, `1 X` -> `X`, `Step-Nope` -> `Step` only), then `Incomplete
  expression: <half>.` with no line-end newline. `cr3xx.check_for_header`. Not modelled: the `To`
  keyword inside an assignment value (`I = 1 To 12` compiles; ours still rejects it, `fornext_1.CR6`).
- **A line holding only `=`** compiles silently on all four loggers (`r8_cr3xx_bare_eq.json`), alone or as a
  `:` segment (`X = 1 : =`); `= 5` is still `Invalid, or out of place expression`. `parser._parse_line`.
  After `Dim A(8)` this is the `Dim A(8)` / `=` / `{ ... }` help-corpus shape: only the `{` line is reported.
- **Initializer pulling on CR300/CR350** (`r8_cr3xx_pull*.json`): `Public`/`Dim` ending in `=` pulls the next
  line, with the same line-counter shift as CR6/CR1000X (`source._pull_decl_initializer`), **unless an error
  was already reported ahead of the declaration** (any earlier error except `... illegally occurs before
  BeginProg.`: `Too many parameters`, `Invalid Variable declaration`, prose names, ...): then the next line
  is an ordinary statement and the declaration is silent. `compiler.compile_file` compiles again with
  `pull_limit` set below the first affected declaration (the loader logs the pulls it made in
  `Program.pull_log`). Not modelled: whether an error inside an earlier pulled declaration also stops
  later pulls; `Public D(2) As Long =` / `1, 2` is fixed in "Round 9, pulled initializer lines".
- **`For` without `=` and short instructions on a shared line** (CR300/CR350, `r8_cr3xx_for_missing.json`,
  `r8_cr3xx_shortfall_line.json`): `For more: foo` keeps `Invalid or missing parameter.` ahead of the
  names of the following segment (names never hide it; it prints after names of an earlier segment:
  `X = Nope : For example`). `Invalid or missing parameter.` is itself a cascade trigger: a later
  instruction with exactly one argument that is short (`Delay(1)` then `PanelTemp(X)`) loses its
  shortfall, and on one line only the first shortfall prints (`PanelTemp(X) : Delay(1)` -> one line;
  `PanelTemp(X) : Delay(1,Nope)` -> the shortfall, then `Too many parameters:  Nope.`). `X=Nope :
  PanelTemp` prints `Nope` before the shortfall. `cr3xx.check_too_many_cascade`, `drop_shortfall_with_names`.
- **`Const` with a numeric name, no name or no `=`** (all four loggers, `r8_cr3xx_const_name.json`,
  `r8_cr3xx_const_comma.json`): with an `=` somewhere, a first name token starting with a digit (`Const 1 = 1`,
  `Const 1X As Long = 2`, `Const = 1`, `Const =1`, `Const = = 1`, `Const = 1, 2`, `Const 2X, Y = 3`) is `Const
  name <tok> illegal, starts with a number.` and nothing else; without any `=`, `Const with no assignment.`
  then `Too many parameters:  <first item>.` (`Const 5`, `Const 1 2 3`, `Const X Y`, `Const "a"`, `Const
  -1`); `Const =` alone is `Const with no assignment.` (a cascade trigger on CR300/CR350). After `Const A =
  <value>` a top-level comma ends the statement and the rest is ignored (`Const A = 1, B = 2` compiles, `B`
  stays undefined). Not modelled: `Const = X` (`CONST needs assignment.`), `Const = "a"` / `-1` / `1.5`
  (`Invalid variable or constant name: ...`), `Const = X = 1`, `Const = X Y`.

## Round 8 review 7: initializer pull is applied only where text is read (oracle, 2026-10-10; `tests/fixtures/init_pull_unread.json`)

- CR6/CR1000X: `Public A(2) =` / `{1,2}` inside an inactive `#If` or after `EndProg` is accepted and
  pulls nothing (so `Public A(2) = {` cannot swallow a following `#EndIf`). The loader still pulls
  tentatively, but the `LogicalLine` keeps the lines as written (`unpulled`) and the bad-initializer
  messages (`bad_init`); the parser reports them only for a line it reads, and otherwise restores
  the written lines and the line numbering (`parser._Parser.run`).
  The restored line count is per file: an include's inactive pull must not move the main file's line
  numbers (fixture `r8_include_unshift.json`, all four loggers).
- CR300/CR350 pull gating (an earlier error disables later pulls, `compiler.compile_file`) compares
  positions in the expanded source, not bare line numbers: an error in an include counts as at its
  `Include` line followed by its own line (`source._Loader.site`), and pulls inside includes are gated
  the same way (fixture `r8_include_pull_gate.json`). Not modelled: the real `Invalid, or out of place
  expression: ...` for a line of an include carries the `<file>` prefix (CR6 and CR300), which
  `show_file=False` suppresses, so cases that print it are not in the fixture.
- A scalar `Dim D As Long =` whose pulled `{` line is unbalanced and ends in `,` or `{` is the same
  bad-syntax pair as the array case (`{` -> `Invalid, or out of place expression`, `{5,` ->
  `Incomplete expression`); the continuation lines are not folded in (`5}` is reported on its own).
  A complete `{5}` or `{ 5` is accepted.

## Round 8 review 7: SNMPVariable Valid constraint commas (oracle, 2026-10-10; `tests/fixtures/snmp_valid_constraint.json`)

All four loggers: with `RWRITE`, a fifth argument starting `RANGES `/`VALUES ` owns every remaining
comma (`RANGES 0,65535`, `VALUES 1,2,3,4,5,6,...`, even `RANGES 0,65535,7`) and is not counted as
extra parameters. With `RONLY` the word itself is the excess parameter (`Too many parameters:  VALUES.`).

## Round 8 review 7: inline `If` bodies in pass 1 and the `#` cutoff (oracle, 2026-10-10; `tests/fixtures/inline_block_keyword.json`, `tests/fixtures/hash_cutoff.json`)

- CR6/CR1000X: `If True Then X=Scan(1)` (also after `Else`, after `:`) is the same pass-1 `Incomplete
  expression: X=Scan(1).` as the standalone statement (`_check_if_pass1` runs `_block_keyword_call`
  on inline bodies). CR300/CR350 print it for the standalone form too and that is not implemented
  (`known_first_word_pass1` is false there; reproducer still differs).
- All four: `If True Then #Foo` (also after `Else`/`:`) is `Unknown instruction: #Foo.` (no newline echo,
  the body is not at the line end) and ends the compile; `syntax.hash_statement` finds it.
- The cutoff also drops the parser findings of later lines (`#Foo` / `#Else` / `Include "bad.txt"`
  reports only `#Foo`): `Program.hash_cut` is the parser's diagnostic count when the `#` statement
  was parsed -- after the whole line, so `#Foo : Public X` then `#Else` also reports only `#Foo`.
- Inline statements after the `#` one are dropped too (`If True Then #Foo Else X=Scan(1)` reports only
  `#Foo`, `syntax.cut_after_hash`). Fixture `r8_hash_cut.json`, all four loggers. The one-line
  `#If True : #Foo : #EndIf` case (`#If with no #Endif`) is in "Unclosed `#If`" at the end of this file.

## Round 8 review 7: bad-As recovery splits at top-level commas (oracle, 2026-10-10; `tests/fixtures/bad_as_nested_dims.json`)

`Public Q(1,(2)), R, Flag Bad, Z`: the names up to the invalid `As` clause (`Q`, `R`, `Flag`) are
registered whatever the nesting depth of the dimensions (CR300/CR350; same text on CR6/CR1000X). Not
done: a call inside the dimensions (`Q(1,Fn(2,3))`) has its own message family (CR6: `Invalid symbol in
expression: Fn(2,3).`; CR300/CR350: `Dimensions not declared`, `Variable illegal in Parameter`), and
`Public R As Bad, Z` registers `Z` here but not on CR300/CR350.

## Round 8 review 7: the `-t` gate is the checker's pass-1 result (oracle, 2026-10-10; `tests/fixtures/timing_pass1_gate.json`)

`cli._pass1_ok` no longer re-runs a subset of passes; `checker._check` records
`CompileResult.pass1_errors` (the error state the pass-2 gate itself uses). Real `-t` prints no
`[Times]` entries for `X=TCPOpen("192.168.4.23",6785,"fuzz")`, `X=Sqr(1,2)`, a `ModemCallBack` with
`COMSDC7,599`, and `If True Then X=Scan(1)`, and full entries for a pass-2-only error. Known
remaining difference (pre-existing): `X = Y` with `Y` undefined prints `Scan: 0` on the real
compiler; ours prints none (`_P1_HIDERS`).

## Round 8 review 7: predefined constants as Alias names (oracle, 2026-10-10; `tests/fixtures/alias_predefined_name.json`)

All four loggers: `Alias X = Sec` (the alias name after `=`, case-insensitive, first comma item) is
`Sec is a predefined CONST.` using the declaration namespace (`symbols.is_reserved_predefined_name`:
`U1`, `String`, `FP2`, `mV5000`/`ComC1` on CR6/CR1000X only, `Long`/`Float` on CR300/CR350 only; `NAN`,
`Month` and CR6/CR1000X `Long`/`Float` are accepted). It follows the dimension error and precedes
`Duplicate Alias` / `Duplicate Alias Name`; unlike an `already declared` collision the name is still
registered (a second `Alias Z = Sec` adds `Duplicate Alias Name: Sec.`). It is a pass-1 error: on
CR6/CR1000X it hides a later `Invalid symbol in expression`, on CR300/CR350 it does not hide the
`... is not defined.` line.

False rejects found on the way and fixed: a built-in function name on either side of `Alias` / in
`Units` (`Alias X = Sin`, `Alias Sin = X`, `Units Sin = X`) is not an `Expression expects (` error.
Not done (still differ): `Alias X = Y(2)` / `Sec(2)` (`Alias is illegally dimensioned.`), comma
junk after the alias name (`Alias X = Sin, Z` -> `Illegally dimensioned aliased name`), and the order of
`Duplicate Alias` against `already declared as a variable` for a repeated source.

## Compile-stage `Member` index error (2026-10-10, message tracer)

`x = Status.TimeStamp(1,1,"fuzz")`, `Tb.x(1,1,1)`, `Public.y(1,1,1)` (a table or status field with
three or more indices) print `No Matching, or out of place ) in expression: ...` from the statement's
pass-2 compile (tracer frames `0x5c8435` in cr300, `0x4b5d34` via the compile driver in CR6), whereas
`Sin(1,2)` fails in pass 1 (frames in the statement parser) and leaves no record. Consequences, probed
on all four loggers (`tests/fixtures/member_index_compile_stage.json`):

- the statement still gets its `X illegally occurs before BeginProg.` (`Expr`, `Delay`, ...) and no
  `Too many parameters` cascade follows on CR300/CR350;
- any pass-1 error elsewhere hides it (`x = Sin(1,2) + Status.TimeStamp(1,1,1)`, or a second
  statement `x = Sin(1,2)`); on CR300/CR350 any non-placement error hides it;
- it is only reached up to the first placement stop; the statement's own undefined-symbol finding is
  not printed (CR6/CR1000X; CR300/CR350 with an undefined argument still differ, not modelled);
- `Sample(1,Status.TimeStamp(1,1,1),FP2)` outside a table prints only `Sample must be used inside a
  table declaration.`; a second statement on the same line (`x = 1 : x = Status.X(1,1,1)`) prints no
  index error (not modelled).

Implemented by holding these diagnostics aside after pass 1 (`held_member` in `checker/__init__.py`).

## Round 9, CR300/CR350: block keyword used as a function (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_block_call.json`)

`Y = Scan(1)` (also `Case`, `Do`, `For`, `Function`, `Select`, `Sub`, `SubScan`, `While`, any nesting:
`Y = Abs(Scan(1))`, `1 + Do(1)`, `If Scan(1) Then`, an inline `If c Then Y = Scan(1) Else ...`) is the same
`Incomplete expression: <stmt>.` as CR6/CR1000X, with the CR300/CR350 echo: a standalone statement keeps the
raw line terminator (`...Scan(1)` newline `.`), an inline `If` branch or a condition does not. The CR300/CR350 name
resolution stops at the call: undefined names before it, target included, are reported first
(`Q = Scan(1)`, `Y = Q + Scan(1)`, `If Q = 1 Then Y = Scan(1)`: `Q is not defined.` then the
Incomplete line); names inside or after it (`Y = Scan(Q)`, `Y = Scan(1) + Q`, `Scan(Zork(1))`) are not
reported, and neither is `Dimensions not declared`. An indexed target that is not a declared array
(`Q(1) = Scan(1)`, `X(1) = ...` on a scalar) is the generic `Invalid, or out of place expression:` with no
Incomplete line (`A(1) = Scan(1)` on a declared array is Incomplete). Code:
`structure.check_pass1` (ungated for `single_pass`), `cr3xx.order_block_keyword_calls`.
Not done (CR6 differs too): `X = If(1)` and `X = Until(1)` (Incomplete), `X = To(1)`, `X = Public(1)`.

## Round 9, CR300/CR350: include extensions (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_include_ext.json`)

Same two messages as CR6/CR1000X, `Include file <name as written> must have valid program file extension.` vs
the missing-file warning `Cannot open include file  <name>.`, for `Include "x"` and `IncludeSection("A", "x")`
(the paren-less `IncludeSection "A", "x"` is a different shape, not modelled), existing file or not (a rejected
file contributes nothing). The extension is the text after the last `.` (none: empty), case-insensitive:

- three characters other than `crb` (`txt`, `cr6`, `dat`, `abc`, `cr3`, `bat`, `c r`): the extension error;
- five characters starting `cr3` that are not the logger's own: the extension error. Own: `cr300` and `cr310`
  on CR300, `cr350` on CR350 (`cr300`, `cr310`, `cr301`, `cr351`, `cr35x` all fail on CR350; `cr350`, `cr351`,
  `cr301`, `cr35x` fail on CR300; `cr310` passes there);
- anything else (no `.`, `inc.`, 1, 2, 4 or 6+ characters, five not starting `cr3`: `cr1x`, `crbx`, `cr`, `cr200`,
  `cr3x`, `cr3000`) passes the check, but only `crb` and the logger's own extensions actually open: an existing
  `inc.cr1x` / `inc` / `inc.cr` is `Cannot open include file` and contributes nothing.

`source._Loader._bad_extension` / `_unloadable`, `parser._CR3XX_INCLUDE_EXTS`. The pass-through and 5-character
rule are fitted to the probed extensions; the real compiler's table was not read.

## Round 9, pulled initializer lines are cut at the first top-level comma (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_pull_comma.json`)

All four loggers: when a `Public`/`Dim` ending in `=` pulls the next line, only the text before that line's first
top-level comma (outside parentheses, braces and strings) is the initializer; the rest of the line is dropped
unread. `Public D(2) As Long =` / `1, 2` and `1, 2, 3`, `1, X`, `1,`, `1, (` compile clean; the names after
the comma are not declared (`... =` / `1, E` then `Public E` is not a redeclaration, `E = 1` is not defined); a
trailing comma does not pull the next line (`1,` / `2` -> `Invalid, or out of place expression: 2.`).
A same-line `Public D(2) As Long = 1, 2` is a different rule (`Variable name 2 illegal, starts with a number.`)
and is not modelled, nor are a pulled line starting with a comma, a parenthesised `(1, 2)` and the newline
in the `1 2` echo. `source._pull_decl_initializer`.

## Round 9, `Const = X`: a leading `=` (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_const_lead_eq.json`)

All four loggers (the CR300/CR350 undefined-name wording aside, which the normal paths already give). `Const =`
skips the `=`s and reads the next item as the name:

- `Const = X` (a name, nothing after) -> `CONST needs assignment.` after the usual name checks (`X already
  declared as a variable.`, `X is already declared as a CONST.`, `Sec is a predefined CONST.`, `Sin already
  defined as an internal function.`); the name is not registered. `Const = X = 1`, `Const = X Y`,
  `Const = X, Y`, `Const = X "a"`, `Const = X(1)` read the rest as the value as if the `=` were not there
  (`Y is not defined.` / `Invalid symbol in expression: Y.`, clean).
- A string, `-1`, `1.5`, `.5`, `&H10`, `*`: `Invalid variable or constant name: <item>.`, then `CONST needs
  assignment.` when nothing follows; for a digit-first item (`1.5`) `Const name 1.5 illegal, starts with a
  number.` instead, also with text after it. Text after a non-digit item gives only the first line
  (`= "a" + 1`, `= -1 = 3`, `= * 3`). `= ,` / `= , X` is `CONST needs assignment.`; `= =` is `Const with no
  assignment.`. A plain integer or `1e5` is the existing `Const name 1 illegal` path.

Not modelled: `Const = (1)` / `(X)` (the parentheses are stripped), `Const = X As Long`, `Const = a b c` /
`X Y Z` (two juxtaposed items), `Const = $`. `parser._parse_const`, `structure._check_duplicate_declaration`,
`_check_unparsed_pass1`.

## Round 9, CR300/CR350: the variables after a stopping variable are not registered (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_decl_stop.json`)

A `Public`/`Dim` statement stops at its first bad variable (an unsupported `As` type, an illegal or undefined
dimension, a bad initializer, `A() As Long`, a redeclared name); the variables after it are never registered, so
`Public R As Bad, Z` then `Z = 1` is `Z is not defined.`, as are `Public P(-1), Q`, `Public A(Nope), B` and
`Public Z` / `Public Z, Q` (Q). The stopping variable itself is registered (`Public R As Bad` then `R = 1` is
clean), and an alias of an unregistered name is `Z not yet declared so cannot be aliased.`. The unsupported type
now behaves like the other stops: its own dimensions and initializer are still checked (`R(Q) As Bad`,
`R As Bad = Q`: type message, then the name messages, then `Variable initialization of R has bad syntax.`), then
`Too many parameters:  <variable two on>.`; a redeclared name in an earlier position wins (`Public Z` then
`Public Z, R As Bad` -> only `Z already declared`), at the same position the type message comes first, then
`R already declared as a variable.`. `Public A() As Long, B, C` also names `C`.

Applied only to global declarations when no error precedes the statement: after earlier errors the declaration
cascade (`cr3xx.drop_*`) owns the statement and the real compiler keeps registering the later variables
(`fornext_1.CR6`: `Dim I, J, K, L` after `Dim I, J` names `J` only and `K`, `L` stay defined). A structure-typed
variable after the stop is kept registered (`input.value` of `input As s` after a bad `s2() As s` is not
reported; fuzz reproducer cr300 e564ca45d2c6). Not modelled: `Public R As Bad(Q), Z` (no type message there),
declarations inside a `Sub`. `structure._check_decl_exprs`, the single-pass block of `check_pass1`.

## Round 9, CR300/CR350: a bare `Function` call with subscripted arguments (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_fn_call_dims.json`)

`Fn(X(1,2))` (a scalar or an over-indexed array in an argument), `Fn(Zork(1))`, `Fn(Zork(1,2))`, `Fn(Fn(Zork(1)))`,
`Fn(1+Zork(1))` as a statement are checked like `Y = Fn(...)`: the undefined names up to the subscripted one, then
`Dimensions not declared:` echoing the **whole statement** (trailing newline unless a comment follows), not just the
argument (`Zork(1)`) as for a `Sub` call. With defined names only the dimension line is printed. `Fn(A(1))` on a
declared array and `Fn(Zork)` are unchanged. CR6/CR1000X are unaffected (`Invalid symbol in expression:
<statement>.` already matched). Not modelled: an argument-count mismatch together with a subscripted undefined name
(`Fn(Zork(1), 2)` prints no arity line, `Fn(1, Zork(1))` only the arity line) and `Call Fn(...)`.
`structure._report_call_arg_names`, `expressions.check`.

## Round 9, CR300/CR350: unreadable `Do`/`Loop`/`If` conditions (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_do_if_cond.json`)

- Inside the cascade (an undefined-name error earlier in the file), `Do Until X >`, `Do While X >=`,
  `Loop Until X >`, `Do Until X <> `, `Do Until (X >` are `Too many parameters:  X.` like a parsable condition
  (`Loop Until X=-1`); before, the unparsable ones kept the generic out-of-place line. `cr3xx.check_too_many_cascade`.
- A one-line `If c Then stmt` with an unreadable condition reports the condition alone, as a block `If` does
  (`If X Y Then Y = 1` -> `Incomplete expression: X Y .`; likewise `X = `, `X + `, `X, Y`, `X ) `), with the
  undefined names of the condition before it (`If Nope Y Then`, `If X Nope Then`) and the one-line body still
  checked after it (`Y = Nope` / `Nope = 1` / `Y = Nope(1)` / `Y = 1 Else Y = Nope`: `Nope is not defined.` etc.).
  The body is parsed as if the condition were fine (`parser._inline_body_after_bad_condition`); the condition
  text is `structure.inline_if_condition`. CR6/CR1000X print the same condition-only message but their
  one-line forms are still wrong (not touched).
- A block `If X = Then` / `If X + Then` (the `Then` read as an operand) is `Invalid, or out of place expression:
  X = .` instead of `Then is not defined.` (`cr3xx.fix_then_operand`).

Not modelled: `ElseIf X Y Then Y = 1` (one-line `ElseIf`: an extra `ElseIf is not defined.`).

## Round 9, CR300/CR350: `Invalid assignment in expression` (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_invalid_assign.json`)

The CR6/CR1000X pass-1 rule (a predefined symbol, a number or a string as the assignment target) holds on
CR300/CR350 with their usual echo: `Sec = 3`, `String = "x"`, `Double = Double + Double`, `True = 1`, `Float = 2`,
`NAN = 1`, `uSec = 1`, `6 = 1`, `"a" = 1`, `-1 = 2`, `1.5 = X` -> `Invalid assignment in expression: <statement>`
with the line-end newline before the `.` (none for a `:` segment, a one-line `If` body or a trailing comment).
The undefined names of the statement come first (`Sec = Nope`, `6 = estimated`). Only the symbols the logger itself
defines count (`mV1000 = 1` and `Pi = 3` are `... is not defined.` there). `Sec(1) = 3` is still wrong on every
logger. `structure.check_pass1`, `cr3xx.order_invalid_assignments`.

## Round 9, CR300/CR350: an undeclared target with a bare function value (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_target_bare_fn.json`)

`Nope = Sin`, `Nope = TCPOpen`, `Nope = Sin + Nope2`, `If X = 1 Then Nope = Sin`, `Nope = Sin` then `Nope2 = Cos`:
the undefined names, then `Expression expects ( : <statement>` (line-end newline unless a `If` body or comment)
-- the message was missing when the target was undeclared, and out of order (after the names) in `Socket =
TCPOpen` / `EmailSuccess=EMailSend` of comportisactive_1 / ethernetpower_1. `Nope(1) = Sin` stays the generic
out-of-place line. Not modelled: `Nope = EMailSend` also prints `A string variable destination is required` between
the two. `structure._check_assignment_pass2`, `cr3xx.order_target_name_first`.

## Round 9, all loggers: a stray `EndStructureType` and words before a dimension (oracle, 2026-10-10; `tests/fixtures/r9_cr3xx_endstructure.json`, `r9_cr3xx_decl_junk_paren.json`)

- `EndStructureType` (CR6/CR1000X also `EndObjectType`) with no `StructureType` open is `EndStructure without
  Structure` (no period) at its line, a pass-1 error like the others (it stops CR6/CR1000X pass 2; on CR300/CR350
  it starts the declaration cascade, so a later redeclaration is silent there). `structure.check_pass1`,
  `cr3xx._DECLARATION_TRIGGER_PREFIXES`.
- `Public A B(10)`, `A B (10)`, `A B.C(1)`, `A B C(1)`, `A 1(2)`, `A Sin(10)`: the words between the name and the
  `(` are skipped and the parenthesis is the dimension of `A` (`Public A B(Nope)` names `Nope`, `Public Resul
  tString() As String * 20` is the `A() As` error, `Public A B(2)` then `B = 1` has no `B`). Without a `(`
  after the words it is still `Invalid Variable declaration B.`; `Public A B(` (unclosed, `A B( is missing the )
  character.`) is not modelled. `parser._parse_vardecl`, `structure._check_decl_exprs`.

### `CallTable` before its table is closed (all four loggers, round 9, 2026-10-10)

Fixture `calltable_open_table.json`. `Could not find DataTable %s.` is reported at a `CallTable`
whose target is not a table that was *closed* (`EndTable`) before that statement, in source order:
the table's own still-open body (`CallTable T` before or after its `Sample`), a table declared
later (inside another table, or in a `Sub` above the table), also under `If .. Then CallTable T`.
Tier B like the unknown-name case: it prints in line order beside an earlier `Invalid symbol`, and
only the first such `CallTable` is printed. `unknown_calltables` now walks in order instead of
using the file-wide closed set. Not done: `CallTable T()` (real prints the same, we accept) and
`Y = T.X` inside its own open table (`Could not find T.` on CR6/CR1000X).

### Index bounds placement and CR6 port values (CR6/CR1000X, round 9, 2026-10-10)

Fixture `portget_order_wrap.json`.

- `PortGet(A(11),1)`: `Variable A out of bounds.` precedes `Invalid port ...` (argument order;
  `PortSet(1,A(11))` keeps the port first, as before). `checker._place_index_bounds` inserts the
  index-check finding ahead of the port error for `PortGet` only.
- `VoltSe(A(11),1,..,13,..)`: the destination bounds message printed once, before `Invalid
  channel`; the index check no longer repeats a message the destination check already printed on
  the line.
- CR6 port kind (`port_c1c4_u1u12`, `port_wrap`; `PortSet`, `PortGet`, `CS616`): besides
  101..116 and 201..212 any `v >= 201` with `v mod 200` in 0..12 compiles (400, 412, 1000, 1001,
  3012, 2147483612; not 200, 213, 413, 1100, 3013), a fraction truncates (`400.5`), and a value
  outside Long is shown as `-2147483648` (`3000000000`). Previously false rejects. Probed for
  `PortGet` 95..109, 196..221, 396..415, 596..615, 1196..1215 and singles; `PortSet`/`CS616` at
  400/412/413/1000. `SPIOpen`/`TimerInput`/I2C/SDI-12 port kinds are not touched. CR1000X unchanged.

### Alias name lists (all four loggers, round 9, 2026-10-10)

Fixture `alias_name_lists.json` (the cases ours does not match are dropped from it, see below).
`Alias Var = n1, n2(3), ...` aliases consecutive elements of `Var`: `N` is its element count and
`cur` the element a name starts at (`Var`'s own flat index for the first, then the previous names'
sizes added; a bare name is 1, `n(k)` is `int(k)`). Per name, in order: `Illegally dimensioned
aliased name` when `cur` is outside 1..N (the first name's was already reported; several names
each report), then `Alias is illegally dimensioned.` when `k < 1` or, for `cur >= 1`, `k > N + cur
- 1` (so `Y(3)`: `Y = Zed(4)` fails, `Y = Zed, Q(4)` compiles, `Q(5)` fails); that message ends
the statement (the earlier names stay registered, a failing first name registers nothing, so no
`Duplicate Alias` follows). `Alias Foo = Zed,` (a trailing comma) is one more empty name. Later
names also report `X is a predefined CONST.` in name order (`Alias Y = Zed, Sec`); an existing
variable as a later name compiles. `Duplicate Alias Name:` echoes the written name with its
dimension and the blanks after the comma (`Duplicate Alias Name:  Zed.` for `Zed, Zed`), and a
dimensioned name registers its base name. Order of the first name's findings: its start/dimension
messages, `X is a predefined CONST.`, `Duplicate Alias`, `X already declared as a variable.`,
`Duplicate Alias Name`. `Alias is illegally dimensioned.` now starts the CR300/CR350 `Too many
parameters` cascade (`cr3xx._DECLARATION_TRIGGER_PREFIXES`). `structure._check_alias_dims`,
`_alias_dim_ok`. Not modelled (items with text after the name, `Zed(2) = 5`, `Zed(2)(3)`, `Zed.W(2)`,
`Zed(Q)`, `Alias Foo = 2Zed`) and the pre-existing CR300/CR350 difference that real prints no
`Too many parameters` after `already declared as a variable.`.

## Round 8 review 8: `Not` right of `To` (oracle, 2026-10-10; `tests/fixtures/r8_to_not.json`)

All four loggers: the operand after `To` may be preceded by any number of `Not`, but it is a shift-level
operand, not a relational: `X = 1 To Not 2 To 3`, `1 To Not 2 < 3` and `1 To Not 2 = 3` are `Incomplete
expression`, while `1 To Not Not 2`, `1 To Not 2 + 1`, `1 To Not 2 << 1`, `1 To Not 2 And 3` compile.
Not modelled (pre-existing false reject): `X = 1 + Not 2 To 3` compiles.

## Round 10: `CHR` of a zero low byte is the empty string (oracle, 2026-10-11; `tests/fixtures/chr_nul_const.json`)

All four loggers: a NUL byte contributes nothing to a constant string. `CHR(256)` (low byte 0) is
`""`: `LEN(CHR(256))` is 0, `CHR(256)=""` is true, and `CHR(256)+"AB"` equals `"AB"` with length 2,
`"A"+CHR(256)+"B"` has length 2. `#If CHR(0)=""` is true. `consteval._fn_chr` returns `""` for a zero
low byte; the earlier `"\x00"` made `LEN` 1 and failed the comparisons.

### Constant `To` with a string left operand (oracle, 2026-10-11; `tests/fixtures/const_string_to.json`)

All four loggers: `"a" To 3` (and `"" To 3`) is a constant `0`, as `1 To 3` is: `Const K = "a" To 3`
makes `#If K=0` true, `K=""` and `K="a"` false, and `Public Y(K)` fails with `Illegally dimension`.
Only the right operand must be numeric in `consteval._binary`. A string right operand (`1 To "a"`,
`"a" To "b"`) is `String is illegal here` at line 1 on the real compilers; ours reports no line 1
finding for it (not modelled, pre-existing; the fixture omits those cases).

### `MenuItem` with a non-finite ConstTable constant (CR6/CR1000X, oracle, 2026-10-11; `tests/fixtures/menuitem_nonfinite.json`)

`Const F=1e999` (and `-1e999`, `1e308*1e308`) in a ConstTable is a float item: `MenuPick(F,2.5)` is
accepted, as for a fractional constant, while `Const F=2` rejects `2.5` (`integer Const`). A finite
integral constant (`1e300`) stays unclassified, as before. Before this, the classification called
`int()` on infinity and the compile ended as `Internal error`. `checker/instr_data.py`, `item_kind`.
Not modelled (pre-existing, not part of this fix): a second `DisplayMenu` gives `Only one DisplayMenu
allowed.` on the real compilers; ours reports nothing.

### `Member` index error in an `If` condition is held like the other statements (all four loggers, oracle, 2026-10-11; `tests/fixtures/if_member_hold.json`)

`If Status.TimeStamp(1,1,1) Then X=1` reports `No Matching, or out of place ) in expression` as a
compile-stage (held) error, as `X=Status.TimeStamp(1,1,1)` already did. With an earlier pass-1
error (`X=Nope`), the real compilers print only that error and the `Expr illegally occurs before
BeginProg.` line, not the `If` line. Without one, the held error prints at its line as before.
`checker/expressions.py`, `check_function_arity`, `If` branch (`_hold`). Not modelled: `If
Status.TimeStamp(1,1,1)+Nope2 Then`, which on CR300/CR350 hides the `Nope2` finding that ours still
reports (the same family as the assignment case `X=Status.TimeStamp(1,1,1)+Nope`, pre-existing).

### Held `Member` error does not hide another statement's findings on the same line (oracle, 2026-10-11; `tests/fixtures/same_line_member.json`)

`X=Nope : X=Status.TimeStamp(1,1,1)` (CR6/CR1000X): the real compilers print both `Invalid symbol in
expression: X=Nope .` and the `No Matching, or out of place )` line for the `Member` statement. The
suppression in `checker/__init__.py` (held `Member` errors) removed every undefined-symbol finding on
the same `(file, line)`. Now the held statement is identified by `Diagnostic.stmt` (the `id()` of its
statement, the `instr_rules` convention): the undefined-symbol finding of the whole-echo branch
(`expressions.check`, CR6/CR1000X) and the arity error of an assignment carry it. Untagged findings
keep the line-based rule. Not modelled: the order of these findings when the `Member` statement comes
first (`X=Status.TimeStamp(1,1,1) : X=Nope`, CR6 prints the `Member` line first; ours prints `Nope`
first), and the position of a later `Y=2` finding (CR6/CR1000X print it last; ours first).

## Round 10, CR300/CR350: string literals are not code in the block-call ordering (oracle, 2026-10-11; `tests/fixtures/r10_cr3xx_block_quotes.json`)

`cr3xx.order_block_keyword_calls` finds the first block keyword call (`Scan(`, `Sub(`, ...) in the
statement text with the quoted literals blanked. Before, `LEN("Scan(") + Nope + Scan(1)` cut the
name scan at the quoted `Scan(` and dropped `Nope is not defined.`, and `LEN("Nope") + Scan(1) +
Nope` kept the later `Nope`, because the quoted name counted as an earlier name. Both match the
real compilers now, as does `If LEN("Scan(") = Nope Then X = Scan(1)`. CR6/CR1000X are not part of
this family: `X = Nope + Scan(1)` gives `Invalid symbol in expression` there, which ours does not
model (pre-existing).


`Not` after any other operator (oracle 2026-10-11, all four loggers, `tests/fixtures/not_operand_binary.json`):
`Not` is a prefix on the operand of any binary operator (`+ - * / Mod \ & << >> ^ = < >`) and of unary `-`,
so `X = 1 + Not 2`, `X = 1 + Not 2 To 3`, `X = 1 = Not 2`, `X = -Not 2`, `X = 2 ^ Not 2 To 3`,
`X = 1 + Not 2 To 3 Mod 4` and `X = 1 + Not 2 To Not 3 Mod 4` compile. Still rejected by the same rules as
above: `1 + Not 2 To 3 < 4`, `1 + Not 2 < 3 To 4`, `1 = Not 2 To 3` (`Incomplete expression`), `1 Mod Not`,
`1 + Not 2 To Not` (`Invalid, or out of place expression`). Implemented in `parser._parse_unary` and
`parser._parse_power_operand`; the `Not` at the start of a value is still the lowest-precedence logical `Not`.

## Unclosed `#If`: `#If with no #Endif` (oracle 2026-10-11, all four loggers; `tests/fixtures/unclosed_if_oneline.json`)

A `#If` still open at the end of the read lines prints `#If with no #Endif` (no line prefix, no period). The
rest of a directive line is not read, so `#If True : #Foo : #EndIf` and `#If True : #EndIf` leave the `#If`
open, while `#If True : #Foo` followed by an `#EndIf` line closes it, and `#If True : #EndIf` followed by
`#EndIf` on its own line is accepted. Lines after the first top-level `EndProg` are never read, so
`BeginProg`/`EndProg`/`#If True` compiles. Modelled only for a file with no other finding (`parser.run` sets
`Program.unclosed_if`; `compiler._compile_once` adds the message when `diags` is empty). Not modelled: with
other findings the real compilers differ (CR6/CR1000X print only `#If with no #Endif` even with `X = Nope`
in the body, `No BeginProg` is suppressed there too; a `Public Q(` before the `#If` keeps its own message;
CR300/CR350 print `Nope is not defined.` alone). Our `Unknown instruction` and `No BeginProg` findings still
print in those mixed cases.

### Excess arguments on deferred-text instructions (2026-10-10)

`SampleMaxMin`, `Moment`, `Histogram`, `FFT`, `Covariance`, `RainFlow`, `LevelCrossing`, `ETsz`,
`SendData`, `AcceptDataRecords` and similar take their arguments as `deferred_text`, which ends at
the next top-level comma, so an excess argument is left over and reported as `Too many
parameters:  <rest>.` (`instr_rules._TOO_MANY_KINDS`). On CR300/CR350 a `SampleMaxMin` with an
excess argument drops the `Maximum() or Minimum() must precede SampleMaxMin().` error that
CR6/CR1000X still emit. Evidence: `tests/fixtures/too_many_deferred.json` (84 cases, all four
compilers, audited with `tools/fixture_audit.py`: 0 differing).

### Undeclared dotted names, CR300/CR350 (2026-10-11)

In an expression (assignment value or subscript, `If`/`ElseIf` condition, function argument, ...)
`Undeclared.member` (`Tmp.dat`, `TempConv.dat`, `Tmp.001`, `Tmp.a.b`) is not `Tmp is not defined.`
but `Could not find DataTable Tmp.`, one line per such name, in source order with the plain
undefined names (`X = Tmp.dat + Zed` -> both, `X = Tmp.dat * 2 + Tmp.dat` -> two). Once a plain
`is not defined.` was reported for the statement, later dotted names are silent (`X = Zed + Tmp.dat`
names only `Zed`; `Nope = Tmp.dat` names only `Nope`). Implementation: `profiles.DottedName`
(returned by `_undefined_names` for a `Member` whose base is undeclared, CR300/CR350 only) and
`profiles.DottedAwareMessage`, the `unresolved_symbol_message` those profiles carry. The lexer reads
`ident.digits` as `.` plus a member name, not a number. Fixture: `tests/fixtures/dotted_undeclared.json`
(52 cases). Not done: CR6/CR1000X report `Could not find Tmp.` for the same shapes (plus the whole-
expression `Invalid symbol` echo only when a plain undefined name follows); `Declared.nothing` on a
data table (`Could not find T1.dat(1).`) is not checked on any logger.

### `Return` after the CR300/CR350 cascade (2026-10-11)

A `Return <expr>` after the cascade has started (`Dim W banana` earlier) is checked like any other
statement against the names the cascade left unregistered: `Function F2(N1)` / `Return N1*3` ->
`N1 is not defined.` (the tainted header lost its parameter), `Return Q` for a dropped `Public Q`
-> `Q is not defined.`, `Return F2(N1)` -> `N1 is not defined.` then `No Matching, or out of place )
in expression: F2(N1).`. `cr3xx._report_unregistered` no longer skips `Return`. Fixture:
`tests/fixtures/return_tainted.json`; corpus: `chm/functionendfunction_4.CR6`.

### Table names starting with a digit (2026-10-11)

`DataTable(15Min,True,-1)` (also `("15Min")`, `(1)`, `(15)`) is a named table whose name is
rejected: `Table name 15Min illegal, starts with a number.` at the `DataTable` line (message
`docs/messages.tsv`), the body is then compiled normally and `CallTable 15Min` resolves (a
`CallTable 15Min` with no such table is `Could not find DataTable 15Min.`). The parser reads
digits-then-word after `DataTable(` / `CallTable` as one name.

- CR6/CR1000X: one message per such table, in tier B (so a pass-1 error such as `Invalid Variable
  declaration` hides it; body errors, pass 2 errors and a second illegal table do not).
- CR300/CR350: only the first illegal table is reported, and only when nothing else was found
  earlier: any error outside a table body, or a non-name error inside one (`Foo(1)` unknown,
  `Invalid or missing parameter.`, `FieldClassify is not defined.`), hides it; an undefined name
  inside a table body (`Zed is not defined.`) races it in file order and the first of the two is
  the only error printed; `CallTable T9` / unclosed-table errors lose to it. Modelled as
  `instr_data.finalize_illegal_table_names`; it is a fit to the probed shapes, not a traced
  mechanism. A table with no fields (`DataTable(15Min..)` `EndTable`) additionally gets `Table
  15Min declared with no output fields.` on CR6; not implemented.

Fixture: `tests/fixtures/digit_table_names.json` (164 cases, four loggers).

### Function calls after an undefined target (2026-10-11)

`Q(3) = F2(1,2)`, `Q(3) = F2()`, `Q(Z) = F2(1,2)` with `Q` undeclared and `F2` a user `Function`:
the target is reported (`Q is not defined.` + the out-of-place fallback on CR300/CR350; the
fallback alone on CR6/CR1000X) and the value's call is never checked, so neither `No Matching, or
out of place )` nor `Call argument required.` appears (all four loggers, 2026-10-11).
`expressions.check_function_call_counts` skips such assignments. Not done: a bare undefined target
on CR300/CR350 (`Q = F2(1,2)`) prints `Q is not defined.` before the `No Matching` line (ours prints
it after), and the same shapes after the cascade has started (`_report_unregistered` re-runs the
checks, and the original pass still sees the dropped name as declared). Fixture:
`tests/fixtures/undefined_target_call.json`.

### Stray close parenthesis, and `Alias A(1) =` after the cascade (2026-10-11)

A statement with an unmatched `)` that is not an assignment (`Z)`, `Z + 1)`, `West)`, `Z > 1)`,
`1)`, `Z, 1)`) is `Invalid, or out of place expression: <text>.` on all four loggers (CR300/CR350
first name undefined words: `West) ` -> `West is not defined.` then the fallback); an assignment
(`Z = 1)`, `Z = (1))`) and an `If` condition keep `Incomplete expression:`. `structure._ASSIGNMENT_HEAD`
and the stray-`)` verdict of `_scan_expr`. Fixture `tests/fixtures/stray_close_paren.json`.

After the CR300/CR350 cascade has dropped `Public gps_data(15)`, `Alias gps_data(1) =` with the
alias name on the next line (an `Unparsed` statement) is `gps_data not yet declared so cannot be
aliased.`, not `Nothing after '=' in alias definition` (`chm/gps_1.CR6`; cr3xx cascade loop).

### Parenthesised assignment with a constant target, CR300/CR350 (2026-10-11)

A leftover line starting `(` is re-read as an expression and its inside echoed
(`structure._leftover_message`). When the inside is `<constant|string|predefined symbol> = ...`
(`(Double = Z) x`, `(1 = 1) x`, `(Sec = 1) x`) the message is `Invalid assignment in expression:
<inside>.` instead of the generic fallback (`chm/DoublePrecArith_1.CR6`). With a variable target
(`(Z = Z) x`) the real compiler accepts it (`Expr illegally occurs before BeginProg.` before
`BeginProg`, nothing after); not implemented, we still print the fallback. Fixture
`tests/fixtures/paren_assignment.json`.

### `word.` line before `BeginProg`, CR300/CR350 (2026-10-11)

`foo.` / `foo.bar` outside any body before `BeginProg` is `Could not find DataTable foo.`, the
fallback, then `Expr illegally occurs before BeginProg.` (as on CR6/CR1000X); after the cascade
has started the line is silent, placement line included (`cr3xx._loose_before_beginprog`).
Not done: `foo. bar` also prints `bar is not defined.` first. Fixture
`tests/fixtures/table_line_placement.json`; corpus `chm/const1_1.CR6`.

### Logical-led assignment with a string literal, CR300/CR350 (2026-10-11)

`X AND S = "a" AND X =` on CR300/CR350 prints `String is illegal here` before the fallback, as on
CR6/CR1000X (`structure._logical_led_string`; the CR300/CR350 pass 2 had no string line for an
unparsable statement). Only a name, `AND` and a string literal was probed: `X AND S =` (no
literal), `X = 1 AND S = "a" AND X =` and `X AND "a" AND X =` shapes were checked for no new false
string line. Not done: an assignment-led shape with a string operand (`X = "a" AND X =`,
`S = "a" AND X =`, `X = "a" AND`) also gets the string line on CR300/CR350 and is still missing
there. Fixture `tests/fixtures/logical_assign.json`.

### Missing include warnings come first (CR6/CR1000X, 2026-10-11)

A missing main-file `Include` warning (`Cannot open include file  X.`) is printed before every other
message of the main file, whatever line those are on: `Foo Bar` on line 2 with `Include "NoSuch.crb"`
on line 3 is `line 3: Warning: ...` then `line 2: Invalid, or out of place expression: Foo Bar.`, and
the same inside a `Scan` body (`Battery (Q)` then `Include`: warning first). Warnings keep their line
order among themselves. Measured on CR6 and CR1000X (`Profile.include_warning_first`,
`CompileResult._sort_key`); the reader stops at the first `EndProg`, so an `Include` after it is still
silent. CR300/CR350 keep line order (`Foo Bar` / `Include` prints the warning last there). An extension
error (`Include "x.txt"`) hides the other errors as before and is not reordered. Pre-`BeginProg` and
in-body shapes are in `tests/fixtures/include_warning_pre.json` and `tests/fixtures/include_warning_body.json`
(all four loggers, 0 differing). Fixed `chm/include_1.CR6` on CR1000X.

### `CallTable` inside a single-line `If` counts as a use (CR6/CR1000X, 2026-10-11)

`Table %s, is declared but never called.` is not printed when the table's only `CallTable` is a
single-line `If` branch, whatever the condition: `If Batt > 1 Then CallTable (Msgs)`, `If False Then
CallTable (Msgs)`, the `Else` branch, nested `If`. The multi-line `If ... EndIf` form was already
counted. `warnings._with_inline_branches` walks the `If` branches for the `called` set. Evidence:
`chm/emailrecv_1.CR6` (CR1000X printed the warning before; CR6 was exact only because of its unrelated
channel error). Fixture `tests/fixtures/called_inline_if.json` (56 cases, four loggers, 0 differing).
CR300/CR350 unchanged (same fixture).

Not done: `If Batt > 1 Then CallTable (Msgs) : Battery (Batt)` (a colon-separated statement after an
inline `If`) prints no warning on CR6/CR1000X in either, but the real compiler's mode is Sequential and
ours is Pipeline (`mode.decide`; inference: the parser keeps `: Battery` outside the `If`, the real one
puts it inside, and a conditional `Battery` in a `Scan` is sequential). Left out of the fixture.

### Shortfall after an earlier parameter-kind error (CR6/CR1000X, 2026-10-11)

On CR6/CR1000X the same rule that hides `Too many parameters` (above) also hides a shortfall
(`Too few parameters in instruction.` / `Invalid or missing parameter.`) on a statement that already has
`Variable illegal in Parameter: X.` or `Illegal parameter X, ...`: `Average Strain`, `Average (Strain)`,
`Average Strain, 1`, `Sample (Strain)`, `Sample (Strain,1)`, `PortSet (X,1)` print only the variable
line (`chm/straincalc_1.CR6`; the `MQTT QoS` shape of `chm/mqttpublishtable_1.CR6` is a different
pass-1 question, below). `Average (Strain,1)` likewise prints only the variable line. Without a variable
or illegal parameter the shortfall stands: `Average Foo`, `Sample (1)`, and `Average (1,Strain)` (the
variable is a destination, no variable line) keep `Invalid or missing parameter.`. Implementation: `instr_rules.check`, the same
`_earlier_error_in_stmt` gate as `check_too_many`. Fixture `tests/fixtures/shortfall_after_param_error.json`
(78 cases, CR6 and CR1000X, 0 differing). `chm/mqttpublishtable_1.CR6` still differs (its `MQTT QoS`
argument: real `MQTTPublishTable(MQTT QoS, 5)` is a pass-1 error that hides the fallbacks of earlier
lines in the table, `Foo Bar` before it included; ours reports them. Not fixed: the pass-1/pass-2
placement of that argument is unidentified).

### Trailing empty arguments count as absent (CR6/CR1000X, 2026-10-11)

For the shortfall check (`instr_rules.check`), a trailing empty argument does not count: `Sample (9,)`
and `Sample (9,` (line ends after the comma) are short like `Sample (9)` (`Too few parameters in
instruction.`, the missing parameter is an identifier), `Sample (9, X, )` like `Sample (9, X)`
(`Invalid or missing parameter.`), `Sample (9, rTime( ),)` (`Invalid or missing parameter.`; ours
accepted it before). Extra trailing empties still do not matter: `Sample (9,X,FP2,)` and
`Sample (9,X,FP2,,)` keep their own messages, and `Sample (1,X,FP2,)` is accepted. Evidence:
`chm/realtime_1.CR6` line 53 (`Sample( 9,` ending a line, `Too few`). Fixture
`tests/fixtures/trailing_empty_args.json` (44 cases, CR6 and CR1000X, 0 differing). CR300/CR350
not changed (the rule is gated to CR6/CR1000X; not probed there).

## Round 12, CR300/CR350 help-corpus prose diffs (2026-10-11; `tests/fixtures/r12_cr3xx_*.json`)

The 20 remaining `difftest --group chm` diffs per logger (same 20 files on CR300 and CR350) are
CHM prose lines that start with an instruction, a block keyword or a function. Each family below
was probed with `tools/probe.py` on both loggers; the CR300/CR350 pass-1 scan is not run, so the
CR6/CR1000X verdicts were reused where the probes agreed.

### Block keyword with no condition (`chm/sgn_1.CR6`, fixture `r12_cr3xx_bare_cond.json`)

A bare `If`, `ElseIf`, `Case`, `Do While|Until`, `Loop While|Until` (nothing after the keyword) is
`Invalid or missing expression.`; with a trailing blank (`If `, `Case `) and for `Select Case` it
is `Invalid, or out of place expression: .` (empty echo). These are the CR6/CR1000X pass-1
verdicts (`structure._pass1_scan` on a blank `condition_part`), applied in
`cr3xx.check_unknown_line_words` to the generic fallback of an `S.Unparsed` statement; they were
`Invalid, or out of place expression: If` or an `Incomplete expression: While.` from the
instruction-prose branch. An error on an earlier line does not hide them for `If`/`ElseIf`/`Case`
(the corpus has three bare `Case` lines after prose errors), but a bare `Do While`/`Loop Until`
after an earlier error stays silent (`cascade_do_loop`), so `Do`/`Loop` are excluded then. Not
fixed: a bare `While` (`Invalid or missing parameter.` here, `Invalid or missing expression.`
real, all four loggers), `Case Is`, `Case To 3`.

### Built-in function as an expression statement (`chm/strcomp_2.CR6`, fixture `r12_cr3xx_fn_expr_stmt.json`)

A statement that starts with a built-in function call is one expression, like a user `Function`
call (`parser._parse_assignment_or_call`, now also for the CR300/CR350 built-ins): `Sin(x) > 0`
and `StrComp (s(1),s(2)) > 0` compile (the generic fallback was a false reject), `Sin(x) y` is
`Incomplete expression: <stmt> | .`, and a complete expression followed by an operand
(`StrComp (a,b) > 0 Then`) names that operand (`Then is not defined.`) before the `Incomplete
expression` line. Undefined names of the arguments and the expression come first; an undefined
subscripted name over the running dimension count ends the walk with `Dimensions not declared:
<stmt>` and no `Incomplete expression` (`StrComp (Arr(I),Arr(I+1)) > 0 Then`; the count is
`DimsState`, so `StrComp (s(1),Arr(I))` does not print it). The first tail operand is named by
its undefined names even when indexed (`Sin(x) Arr(1)`). A built-in call with an arity error
keeps only its arity message (`Round(x) y`, `Sin(x,1) y`). Not done: other tails (`Fn(1) 1 +`).
CR6/CR1000X are untouched (`StrComp (...) > 0 Then` there is `Invalid symbol in expression`; not
modelled).

### Instruction followed by prose (`chm/calibrate_1`, `calltable_1`, `histogram_1`, `ProgramStructure_1`; fixture `r12_cr3xx_instr_tail.json`)

The instruction reads the text with its own parameter codes (`docs/type-codes.md`), so the
prose messages follow the first parameter's code (`cr3xx.check_unknown_line_words`):

- **Code 11 (deferred text: `Histogram`, `Histogram4D`, `FFT`, `RainFlow`, `LevelCrossing`,
  `ETsz`, `ETClearSky`)** takes the whole text unread, so nothing is named; the next required
  parameter is missing: `Invalid or missing parameter.` (no top-level comma in the text).
- **Code 6 (a name token: `CallTable`, `DataTable`, `FileMark`, `ResetTable`, `StationName`)**
  takes the first token unchecked (`foo` is never `not defined`). With no second parameter
  (`FileMark`, CR350 `CallTable`) the next token is the surplus: `Too many parameters:  bar.`.
  Otherwise the rest is the second parameter's text (CR300 `CallTable`'s optional expression,
  `DataTable`'s code 2): `bar is not defined.`, `Incomplete expression: bar baz..`.
- **Leading binary operator word.** A parameter text that starts `is`/`mod` is `Invalid, or out of
  place expression: <text>.` with nothing named and nothing after it; `to`, `and`, `or`, `xor`,
  `imp`, `eqv` name the next operand if it is a word, then the same message, then (constant-only
  parameter) `Variable illegal in Parameter:` only when that operand was a word
  (`SlowSequence to calibrate the datalogger`, `Delay and 3 baz`). Symbol-led texts (`+ - * / ^ \ <
  > = <> << >> & (`) have their own shapes, probed but not modelled (`Delay * bar baz` -> invalid
  alone, `Delay & bar` names `&` first, `Delay (bar baz` drops the parenthesis from the echo).
- **Instructions without parameters** (`BeginProg PipeLineMode SequentialMode EndIf WEND
  OpenInterval NextScan NextSubScan EndTable FillStop ExitScan ContinueScan ExitSub ExitDo ExitFor
  Restore AngleDegrees ShutdownBegin ShutdownEnd EndSequence ExitFunction AbortScan SampleFieldCal
  WaitTriggerSequence PulseCountReset WebPageEnd TableHide DHCPRenew Restart PreserveVariables
  ApplyAndRestartSequence EndApplyAndRestartSequence`, listed from a CR300 and CR350 sweep with
  `<name> foo bar baz.`): the first token after the name, delimiters `,:() =` skipped, is the
  surplus: `BeginProg, EndProg (Begin Program, End Program)` -> `Too many parameters:  EndProg.`.
  `EndProg` ignores its tail, `Alias`/`Const`/`Data`/`ReadOnly`/`Include*`/`ESSVariables`/
  `SnmpVariable` read it themselves. Not done: the same instruction written without a `.`
  (`ExitScan foo`) is accepted by us and rejected by the real compilers.
- **After the cascade has started** (`check_too_many_cascade`): the deferred-text line prints
  nothing (its shortfall goes, like a short instruction's), and a name-token instruction with a
  second parameter prints only the second parameter's first token (`CallTable instruction is used
  to ...` -> `Too many parameters:  is.`, CR300). CR300 `CallTable` itself differs from CR350
  only by that optional second parameter (`profile.instructions`).

### Statements with a string: `String without end quote` and `String is illegal here` (`chm/call_1`, `sprintf_1`, `straincalc_1`, `getdatarecord_2`; fixture `r12_cr3xx_string_stmts.json`)

- **Unterminated string** (`cr3xx.order_unterminated_string`, after every other pass): an unparsable
  statement whose text up to the opening quote is a well-formed expression prefix prints its
  undefined names, then `String without end quote`, then the final message
  (`Foo" bar baz`, `x" bar`, `Nope + "abc`, `Len("abc`, `Nope = "abc`, `If Nope <= 29 Then Nope2 =
  "TEMP`: both names, then the string line, then `Nope2 = "TEMP:endif.`). We printed the string line
  only for assignment/`If` shapes, and before the names. The prefix check (`_string_reached`) is
  what keeps `Datalogger</title>")` free of the line: it fails at the `/` operand first. A
  built-in function call that the string leaves open is the generic fallback, not `No Matching`
  (`SprintF ( s, "The current air temp`). Names the cascade left unregistered are undefined here too
  (`diags.cascade_gone`, `call_1`). Left alone, still wrong or unprobed: `Sin(x) "abc` (real
  `Incomplete expression`), `y = Sin(x, "abc` (real `No Matching`), `Battery(x, "abc` (real `Too
  many parameters:  "abc.`), `Public z = "abc` (real: accepted, we reject), a declared variable
  with a subscript before the string (`x(1) "abc`, no string line, left as it was), a juxtaposed
  operand before the string (`x y "abc`).
- **String operand** (`cr3xx.add_string_illegal`): an unparsable statement that starts with a
  well-formed expression with a string operand of a logical/numeric operator prints `String is
  illegal here` before its final message, as on CR6/CR1000X (`"BattV" and "Counter".`, `x =
  "BattV" and "Counter".`; `"BattV"` alone, `x "BattV" and "Counter".` print none).

### `&` in a call's arguments and the argument items (`chm/clockset_1`, `sdmcd16mask_1`; fixture `r12_cr3xx_call_items.json`)

A prose line that starts with a call (`cv(4, 5, & 6) with GoesTime values`, a declared variable
or an undefined name) reads its parenthesised arguments as comma-separated items
(`cr3xx._paren_items_words`, called from the first-word parenthesis walk): the first three items
are walked like a line (words, chain and one peek word), the fourth only for its first name, the
rest is not read, and a juxtaposed peek word ends the whole walk (`foo (a b, c d, ...)` names `a`,
`b`; `cv(4, zz yy, 6)` names both; `cv(zz, yy, 6, ww)` names all three; `cv(4, 5, 6, zz, 7)` names
`zz`). `&` is a hex literal prefix only before `h` and hex digits: `&h0F` in the first three items
is a literal, in the fourth it is the name `h0F` (an unresolved call's fourth `&h0000` argument
too, `_call_arg_undefined_names`); in operand position any other `&` makes a name of the `&` and
what follows up to a blank (`& 6` -> `&`, `&6` -> `&6`, third item or earlier; a fourth `&5` names
nothing). Not done: `foo (a, & b) text` (real also names `b`), the `text` that follows a plain
`foo (a, b) text` is named by us and not by the real compilers, `x = & 6` (real `& is not defined.`
and `Incomplete expression`), `&h0000 text more` (real names `text`).

### Trailing comma, `Alias` without `=`, leading `=` (`chm/realtime_1`; fixtures `r12_cr3xx_trailing_comma.json`, `r12_cr3xx_alias_eq.json`)

- **A trailing comma makes the next parameter the missing one, all four loggers**
  (`instr_rules.check`, `given_count`): `Sample( 1,` is `Too few parameters in instruction.` (the
  missing second parameter is deferred text), `Sample(1,x,)`, `PanelTemp(x,)`, `Average(1,x,FP2,`
  are `Invalid or missing parameter.`; `Sample(1,x,FP2,)` (optional fourth) compiles. We counted the
  empty last slot as given: the wrong message for the first, and no error at all for the others
  (false accept on CR6/CR1000X/CR300/CR350). After an earlier error with undefined names the
  shortfall is hidden as usual (`cr3xx.drop_shortfall_with_names`, the cascade branch for a short
  instruction, both now use `given_count`).
- **`Alias` without `=`** (`Alias rTime(1)` with the `=` on the next line): the line prints
  `Need '=' in alias definition: ...` alone; the prose fallback and its names were extra.
- **A line that starts with a comparison operator** (`= Year`, `< x`, `<> a b`, `>= 3 b`) names the
  first following word and stops (`_leading_unknown_words`), then the generic fallback.

### `TCPOpen` handle argument, CR300/CR350 (`chm/tcpopen_4`; fixture `r12_cr3xx_tcpopen_handle.json`)

The fifth argument (the connection handle) must be a Long variable or Long array element: a Float
or String variable, a constant, an expression, an undefined name (also a declaration the cascade
dropped) is `Parameter must be a variable of type long`, printed after the statement's `X is not
defined.` lines and before `Dimensions not declared` (`cr3xx.check_tcpopen_handle`, run after the
cascade). Same message on CR6 (`TCPOpen("",3000,30,1000,hf,3)`), where it comes first, ahead of
`Max number of concurrent connections must be 1..1` (a scalar Long handle with 3 connections):
not done. A non-constant fourth argument stops the list with `Parameter must be a constant
integer: <stmt>` instead and is not modelled (the check is skipped then). The first three
arguments' checks are the existing `_CONST_INTEGER_BAD_ARGS` ones.

### `Instruction(a b, ...)`: adjacent operands in the first argument (`chm/mqttpublishtable_1`; fixtures `r12_cr3xx_call_junk.json`, `r12_cr3xx_call_junk_table.json`)

A line `Name(first, ...)` whose `Name` is an instruction and whose first argument holds adjacent
operands (`MQTTPublishTable(MQTT QoS, Number of records, ...)`) names the first argument's
undefined words, prints `Incomplete expression: <first argument>.` and, for a constant-only first
parameter, `Variable illegal in Parameter: <first argument>.`; the rest of the line is never read
(`cr3xx.check_unknown_line_words`, the echo drops the call's closing parenthesis). The instruction
name is not reported as undefined (it was, when the line was not parsed as a call). Skipped: texts
with a word operator, deferred-text and name-token first parameters (their own rules above),
`Alias`/`Units`/`Sub`/`Function`/`Data`. Not done: the `Too many parameters:  <next argument>`
that follows for destination-first instructions (`PanelTemp(foo bar, 15000)`, `VoltSe(foo bar, 1, ...)`)
and a bad second argument (`Delay(1, foo bar)`).

### `Name As Type` outside a `StructureType`, CR300/CR350 (`chm/not_1`; fixture `r12_cr3xx_member_decl.json`)

A bare `Flag As Boolean` line (also after a nameless `Public`, whose `Variable not named.` does not
take the next line on CR300/CR350) is an expression, not a member declaration, outside a
`StructureType`: `Flag is not defined.`, `As is not defined.` (an already declared first word
reports only `As`), the generic fallback, and later uses of `Flag` are undefined too. The parser read
every `Name As ...` as a structure member; it now does so only inside the structure on CR300/CR350
(`parser._parse_assignment_or_call`). CR6/CR1000X also reject the line (`Invalid, or out of place
expression`, plus `Expr illegally occurs before BeginProg.` before `BeginProg`) but a nameless
`Public` there swallows the next line, so the parser is left as it is for them.

### Statement left open inside a function call, CR300/CR350 (`chm/xmlparse_1`; fixture `r12_cr3xx_open_call.json`)

An assignment cut off inside a built-in call (the CHM wraps arguments onto the next line) follows
the CR6/CR1000X rule (`structure._unclosed_call_message`, now also enabled for CR300/CR350): no
trailing comma and fewer arguments than the function has is `Expression expects , : <stmt>`
(`x = Round(x`, `x = Left(s`); a trailing comma with at most as many arguments as the function
takes, counting the missing one, is the generic `Invalid, or out of place expression: <stmt>`
(`x = Round(x,`, `x = FileOpen (s, "r",`, `x = XMLParse( s, s, s,`); anything else stays `No
Matching, or out of place )`. The echo carries the CR300/CR350 line-end newline. Still wrong, all
four loggers: a bare call statement open at a trailing comma (`Round(x,`, `FileOpen (s, "r",`:
real the generic fallback, accepted here), `x = Abs(x,` (real accepts), `x = XMLParse( s, s, s,
s, s, s,` (real generic, `nargs` of the function in our table).

### Adjacent operands inside a call's arguments; strings in the word chain (`chm/udpdatagram_1`; fixtures `r12_cr3xx_call_juxt.json`, `r12_cr3xx_string_chain.json`)

- A built-in call with adjacent operands in an argument (`Round (A B, 1)`) is `Expression expects ,`
  when that argument is not the function's last, otherwise `No Matching, or out of place )` (`Sin (A
  B)`, `Round (A, 1 B)`, `Left (s, 1 2)`); `nargs` of the function decides (`IIF (A B, 1, 2)` and
  `UDPDataGram("a",UDP Port, ...)` expect a comma). The text after the call's `)` is not read:
  `Sin (A B) tail` is the same message with the whole line echoed. Seen before only for a call
  that is the whole statement and always `No Matching` (`cr3xx.check_unknown_line_words`).
  Not done: `UDPDataGram(a, b, c, d, e, f g) - tail` also names `f` and `g` (we stop at the fourth
  argument).
- A string literal is an operand of the word chain like a number (`IPAddress <> "" and IPAddress <>
  "0.0.0.0" then` names `then`): the operator lookahead accepted only a letter or digit after an
  operator, so the walk ended at the first string (`_LINE_OP_RUN_RE`, `_LINE_OP_WORD_RE`,
  `_LINE_OPERAND_RE`). Not done: `st <> "" and x = 2 then` is `Incomplete expression` on the real
  compilers (the `=` after a string-led chain), we print the generic fallback.

### `Could not find DataTable` for a table field is hidden by other errors (`chm/tablenameeventend_1`; fixture `r12_cr3xx_table_field_hidden.json`)

CR300/CR350: `x = Nope.Field(1,1)` and `If Nope.Field(1,1)` print `Could not find DataTable
Nope.` alone, but any other error in the file, before or after, removes it (`Zork = 1` undefined,
`PanelTemp(x)` short, `PanelTemp(x,1,2,3)` too many; two unknown tables in a row print both when
nothing else is wrong). Only the `CallTable` form was hidden before
(`cr3xx.drop_calltable_error_with_other_errors`, now every statement but a prose line).

### Signed table-field line (`chm/tablenamefieldname_1`; fixture `r12_cr3xx_signed_table.json`)

A wrapped concatenation leaves a line `+ Test.TCTemp(1)`. CR300/CR350 treat `+`/`-` before
`Name.Field` like the plain `word.` line (`_SIGNED_TABLE_RE`): an unknown table gets `Could not find
DataTable Nope.` then the generic fallback (`+ Nope.F(1)`; `+ Public.x(1)`, a system or user table,
only the fallback), and once the cascade has started the line is silent. A signed plain variable
(`+ x`) keeps the fallback after errors. Not done: names inside signed lines (`+ Nope` also names
`Nope`, `+ x y` names `y`, `- Nope + x` names `Nope`).

Follow-up to the two table-field sections and the `TCPOpen` handle section above: an error on the
same line does not hide a table field's `Could not find DataTable` (`X = Tmp.dat + Zed` prints both,
`tests/fixtures/dotted_undeclared.json`), and an earlier argument's `Dimensions not declared` ends the
`TCPOpen` walk before the handle (`chm/tcpopen_2.CR6`).

## Round 13 review fixes (CR300/CR350 `TCPOpen` handle, trailing empties, `&h` argument)

Oracle runs of 2026-10-12 (fixtures `r13_cr3xx_tcpopen_handle_types`, `r13_trailing_empties`,
`r13_cr3xx_call_hex_declared`); each case below was probed on the real compiler.

- `TCPOpen` handle (CR300/CR350): the handle must be a Long or Boolean variable. `As Boolean` (scalar,
  array element, `Dim`, or a Sub parameter `H As Boolean`) compiles; Float, String, the default type
  and constants are still `Parameter must be a variable of type long`. The same holds for the
  enclosing Sub's parameters: `H As Long` and `H As Boolean` compile, an untyped `H`, `H As Float`,
  `H As String` and `Optional H = 1` are bad (`Optional H As Long = 1` compiles). `ByVal`/`ByRef`
  parameters and array parameters are other families and are not judged. The error is a `Too many
  parameters` cascade trigger: after a bad handle in a Sub, the following `Scan (1,Sec,3,0)` prints
  `Too many parameters:  Sec.` and a following `Delay(1,1,Sec)` prints `Too many parameters:  1.`.
  `check_tcpopen_handle_early` adds the line before the cascade pass; `check_tcpopen_handle` still
  runs afterwards (names the cascade dropped, lines the cascade rewrote).
- Trailing empty arguments: all four loggers drop every trailing empty argument (`Sample(1,x,,)`,
  `Sample(1,,,)`, `PanelTemp(x,,,)`, `Totalize(1,x,FP2,,)`: the shortfall message of the last real
  argument; on CR300/CR350 each starts the `Too many parameters` cascade). `given_count` is one loop.
  Not fixed (all four loggers, older): `Sample(1,,x)` prints `Invalid or missing parameter.`, we print
  `Variable illegal in Parameter: x.`.
- A fourth `&h0F` argument of an unresolved call names `h0F` only when `h0F` is not a declared variable
  (a `Public`/`Dim`/`Const`/`Alias` name is silent; a Sub or table of that name is still reported).
## Two-word routine closers (`End Sub`, `End Function`)

Oracle, 2026-10-12, all four loggers (`tests/fixtures/r13_two_word_routine_end.json`, 56 cases;
`-t` cases in `tests/fixtures/timing.json`, CR6; `-t` also probed on the other three, 9 programs):
`End Sub` / `End Function` (any case or spacing, trailing comment) end a routine exactly like
`EndSub` / `EndFunction`. The parser gives them as `Instruction("END", [Name("Sub")])`, which
several passes did not recognise, so everything after the routine was treated as still inside it:
`-t` labelled the main `Scan` `(Subroutine)` (real: `Scan: 0`); `PulseCount` in a main `Scan` got
`must be used within a Scan...NextScan.` (false reject); `X = 5` or `Delay` between the routine and
`BeginProg` was not reported (`Expr`/`Delay illegally occurs before BeginProg.`); the
`Delay never reached at run time.` warning was lost; a routine-local `Dim` shadowed a global in
`GPS`'s NMEA-string check. All now use `syntax.ends_routine` (one definition, also behind
`bindings.closes_routine`). Closer-independent and fixed separately: a variable declared inside a
`Sub` and used in the main program ("Routine-local names", below).

### Digit-led members of a declared name (2026-10-12)

`ident.digits` lexes as a member (`Tmp.001`, see "Undeclared dotted names"), so `X = X.5`,
`A.1`, `T.1`, `Status.1` used to compile clean. No variable, data table or `Status` has a member
starting with a digit; the real compilers (all four, oracle 2026-10-12) report, one line per access
in source order, in assignment values, subscripts, `If` conditions and function arguments:
`Could not find X.` for a declared variable (CR300/CR350 `Could not find DataTable X.`) and
`Could not find T.1(1).` for a data table or `Status` (the index echoed as for an absent member:
`T.1(2)` -> `(2)`, `T.1(X)` -> none). An assignment target (`X.5 = 1`, `T.1 = 1`) adds
`Invalid assignment in expression: <line>.`. Implementation: `expressions.check_digit_members`;
statements that also name an undefined symbol and structure-typed bases are not touched. Fixture:
`tests/fixtures/r13_digit_members.json`. Not done: a letter-led member of a plain variable, table or
`Status` (`X.dat` -> `Could not find X.`, `T.dat` -> `Could not find T.dat(1).`) is still
accepted; CR6/CR1000X `Undeclared.digits` (`Could not find Tmp.`) still prints the `Invalid symbol`
echo.

### Logical-prefixed assignment targets (2026-10-12)

`X AND Y = 1` reads `=` below the logical words (`A AND B = C AND D` is `A AND (B = (C AND D))`;
parser `_parse_logical`, oracle all four loggers 2026-10-10) and compiles when every name resolves
and each nested assignment target is assignable. Targets (oracle, all four loggers, 2026-10-12;
`tests/fixtures/r13_logical_assign_targets.json`): a declared variable; an element of a declared array
(`X AND A(1) = 1`, an out-of-range `A(4)`, `A(0)`, `A(X)`, `A()` are accepted, no bounds check);
`Status.Field` and a data-table member `T.X`, with or without a subscript (member existence is not
modelled: `X AND Status.Nope = 1` really is `Could not find Status.Nope(1).` plus `Invalid
assignment in expression`, still accepted here); the enclosing `Function`'s own name; and a `Sub`
parameter even when a later `Const` has the same name. Subscript count: CR6/CR1000X accept any
count on any declared variable (`A(1,1)`, `X(1)`, `M(1)`, `M(1,1,1)`); CR300/CR350 accept 0..n
subscripts for an n-dimensional array (`M(1)` on a 2-D array too) and reject a scalar or an extra
subscript (`A(1,1)` on a 1-D array, `X(1)`, `M(1,1,1)`, `S(1)` on a scalar string) with the
fallback. Undefined names: CR300/CR350 print only `Y is not defined.` per name (no fallback;
`X AND Y = 1`, `Y AND X = 1`, `X AND X = Y`, `X AND A(Y) = 1`), except an undefined indexed-target
base (`X AND Y(1) = 1`) which also prints the fallback. CR6/CR1000X print the fallback when a name
is undefined before the first `=` (target, left operand, target subscript) and otherwise `Invalid
symbol in expression: <statement>.` (`X AND X = Y`, `X AND A(1) = Y`, `X AND X = 1 AND Y = 2`).
Implementation: `structure._logical_assign_state` and `_logical_assign_undefined_messages`;
`parser._at_assign_target` also takes `name.member` targets. Not done: `X AND Y.a = 1` (`Could
not find Y.` + `Invalid assignment in expression`), `X AND X MOD ...` (`Invalid assignment`), any
statement with a string literal, an undefined `Y(1) = Z`.

## Round 13: `SampleMaxMin` order error and argument count on CR300/CR350 (2026-10-12)

`Maximum() or Minimum() must precede SampleMaxMin().` was already dropped on CR300/CR350 for an excess
argument (5e454911). Oracle, CR300/CR350 (`tests/fixtures/r13_samplemaxmin_order_arity.json`): it is
dropped for every wrong argument count: `()`, `(1)`, `(1,X)`, `(1,X,FP2)`, `SampleMaxMin 1,X,FP2`, and an
empty fourth argument `(1,X,FP2,)`; kept for `(1,X,FP2,False)` and for a trailing empty fifth
`(1,X,FP2,False,)`. CR6/CR1000X keep it for all of these. Not modelled: an empty argument before the
last (`(1,X,,False)`, `(1,,FP2,False)`) gives only `Invalid or missing parameter.` on all four loggers;
ours still prints the order error (both reject).

## Round 13: `;` in assignments (2026-10-12, review r13-A item A10)

Oracle, fixture `tests/fixtures/r13_semicolon.json` (probes `scratch/r13/c6.py`..`c9.py`).

- CR6/CR1000X skip a `;` like a blank inside an assignment statement (`=` or compound operator, array
  or member target, any right side): `X = 1 ;`, `X = 1;`, `X = 1 ;;`, `X = 1 ; ;`, `X = 1 ; 'c`,
  `X += 1 ;`, `A(1) = 2 ;`, `Y = Len(S) ;`, `; X = 1`, `X =; 1`, `X = 1 ; + 2` (the `;` is just
  absent), `X = 1 ;: Y = 2` and the body of a one-line `If` (`If X = 1 Then Y = 2 ;`) all compile.
  What follows the `;` is judged as if the `;` were absent: `X = 1 ; Y = 2`, `X = 1 ; 2`, `X = 1 ;)`
  are `Incomplete expression` (we print the generic fallback: same verdict, message not modelled) and
  `X = (1 ;` is `No Matching ...`. `Const K = 1 ;`, `For Y = 1 To 3 ;`, `Next Y ;`, `While c ;`,
  `Select Case X ;` also compile (trailing `;` only; `; Next Y` is rejected). `parser._drop_semicolons`
  removes the `;` tokens for exactly these shapes.
- CR300/CR350 accept `Next ;` and `Next Y ;` (also `Next Y ; ;`) and reject a `;` in every other
  statement above with `Invalid symbol in expression: <statement>` (newline echo as for the fallback;
  `X = "a" AND "b" ;` keeps its `String is illegal here` line first). Done for plain assignments
  (`structure._out_of_place` / `add_string_illegal`); a leading `; X = 1` stays the generic fallback.
  Not done: CR300/CR350 `For`/`While`/`Const`/`Select Case` headers with `;` (`Invalid symbol in
  expression: 3 ;.`, the value or condition only), `ElseIf ... Then ;`.
- Unchanged, rejected on all four loggers with their own text, not modelled: an instruction call
  `Delay (1,1,Sec) ;` / `PanelTemp (Y,60) ;` is `Too many parameters:  ;.`, `Do ;`, `Else ;` and
  `Loop ;` are `Illegal parameter ;, expecting constant integer.`, `EndIf ;` is `Too many parameters:  ;.`
  (CR6/CR1000X), `CallTable T ;` is `Invalid, or out of place expression: ;.` (CR300/CR350: `Invalid
  symbol in expression: ;.`) and `If X = 1 Then ;` is the fallback on `;` alone.

## Round 13: `#If with no #Endif` with warnings (2026-10-12, review r13-A item A6)

The message used to be added only when the file had no diagnostics at all, so a file whose only
findings were warnings (`Table T, is declared but never called.`) compiled. Oracle, all four loggers
(`tests/fixtures/r13_unclosed_if_warning.json`): the compile fails and prints `#If with no #Endif`
alone, the warnings are not shown. `compiler.py` now tests for no errors and drops the warnings when
it adds the line. With errors present the real output was already left unmodelled (CR6/CR1000X print
the `#If` line alone even then, CR300/CR350 the errors alone).

## Round 13: builtin argument counts from the host sweep (2026-10-12, PROGRESS "Compiler items" 5)

The CR6 sweep `git show 8c359f49:opencrbasic/docs/lsp-rounds/host-tests/2026-10-11-e77ee5cc/function-arity-fixture.json` (T-08, audited
0 differing) was repeated on all four loggers from the same 130 sources; the result, with
`TCPOpen-5` left out on CR6/CR1000X, is `tests/fixtures/r13_function_arity.json` (518 cases, our output
identical). Accepted counts, now in `expressions._ARITY_ALLOWED` (fewer than the largest is
`Expression expects , :`, more is `No Matching, or out of place )`; identical on the four loggers):
`EmailSend` 9, 13, 14; `EmailRecv` 9-13; `UDPDatagram` 6-8; `XMLParse` 8; `HTTPGet` 3-4; `UDPOpen` 3-4;
`TCPOpen` 3, 4, 6; `HTTPPost` 4, 8, 9; `CheckSum` 3, 4, 6. Signature help and hover follow these tables
(two tests that pinned the old minimum-only behaviour now pin the new one).

The `A string variable destination is required` rule (`instr_rules.check_emailrecv_destination`) was
wider than the evidence: it required every argument from the ninth on to be a String variable. Oracle:
`EmailSend` checks argument 9 only (`..., S1, 0, 0, Sec, 8, 100` compiles), `EmailRecv` arguments 9-12 (the
13th is a number). The old per-rule `Expression expects ,` for 6-8 arguments is now the ordinary arity
message.

Not done: CR6/CR1000X `TCPOpen` with a handle and no connection count prints
`Max number of concurrent connections must be 1..N` first (N = the handle array's size, 1 for a scalar;
also a count above N, `TCPOpen(..., H1, 3)` with `H1(2)`, which we accept: a false accept). CR300/CR350
print neither.
### Array bounds are resolved by the scope of the use site (round 13, 2026-10-12)

A `Sub`-local `Dim P(5)` shadows a `Public P(20)` inside that `Sub` only; main code and other routines
see the global. Oracle, all four loggers (fixture `tests/fixtures/r13_bounds_scope_shadow.json`, cases
`*-shadow*`): with `Public P(20)` and a `Dim P(5)` in `Sub S2`, `S1(P(15))`, `Battery(P(15))`,
`Move(P(1),20,r,1)` and `SplitStr(P(),s,",",15,0)` in main code compile; `P(25)` there is out of
bounds; inside `S2`, `P(3)` compiles and `P(0)`, `P(6)`, `Reps` 6 are out of bounds. The reverse layout (one
`Sub` with `Dim Q(20)` and `S1(Q(15))`, a later `Sub` with `Dim Q(5)`) compiles. The array-shape
checks (`check_instruction_index_bounds`, `check_subcall_element_bounds`, `check_splitstr_whole_array`,
`check_reps`, `check_dest_bounds`, `check_portbridge_array`, the `Reps`/source checks of `instr_data` and the
`NSEC` array-size check) looked up shapes with the flat, last-declaration-wins `declared_all_dims`, which
gave false rejects (`Variable P out of bounds.`); they now call `symbols.scope_tables`, which gives the
declarations visible at the statement (a routine's own locals over the globals, cached per routine). A
routine parameter hides the name, so the check is skipped (a scalar parameter indexed `P(15)` is out of
bounds on the real compiler, `Dimensions not declared` on CR300/CR350; not modelled).
`_matrix_shapes` (declared exactly once only) and `structure.py`'s alias check were left as they are.

`SplitStr(S(),src,",",1/0,0)` (a whole-array destination whose `Reps` divides by zero) crashed the
checker (`ZeroDivisionError`, printed as `Internal error`); `consteval` now raises `DivideByZero`
(a `NotConstant`) for a division by a zero divisor. Oracle (same fixture, `*-divzero*`): CR6/CR1000X print
`Divide by zero in expression: <Reps text>.` for `1/0`, `1 / 0`, `(1/0)`, `1/0+1`, `-1/0`, `0/0`,
`K/(K-3)` (`Const K = 3`); with a decimal point (`2.5/0`, `1/0.0`, `-2.5/0`) it is a plus or minus
infinity: `Variable S out of bounds.` when positive, accepted when negative, on all four loggers.
CR300/CR350 treat every integer form as infinity too (`1/0`, `5/0`, `K/(K-3)`: out of bounds; `0/0`,
`-1/0`: accepted). Not modelled: `1/0+1` on CR300/CR350 (out of bounds on the real compiler, accepted
here) and `SplitStr(S(1),...,1/0,...)` / other instructions' constant arguments (`x = 1/0` compiles).
`5 MOD 0` page-faults the real compiler (no verdict).
### Stray `)` after a compound assignment or a nested-paren target (round 13, A3)

A statement that starts like an assignment and then has an unmatched `)` is `Incomplete
expression: <statement>.`, otherwise the generic fallback. "Starts like an assignment" includes the
compound operators (`X += 1)`, `-=`, `*=`, `/=`, `\=`, `^=`, `&=`) and a target subscript with
nested parentheses (`A((1)) = 1)`, `A(B(1)) += 1)`, `A(Sqr(1)) = 1)`, `A(((1))) = 1)`); oracle,
all four loggers, 2026-10-12 (`tests/fixtures/r13_stray_paren_compound.json`). Exceptions: a nested
subscript naming an undefined variable stays the fallback on CR6/CR1000X (`A(Len(S)) = 1)`, `S`
undeclared; CR300/CR350 print `S is not defined.` + `Incomplete expression`, not modelled), and on
CR300/CR350 a call-like name that is a declared scalar in the subscript (`A(X(1)) = 1)`) is the
fallback. Implementation: `structure._assignment_head`.

### Junk after a logical-led assignment (round 13, A2)

`X AND X = 1 foo`, `X AND S = "a" junk`, `X AND A(1) = 1 )`, `X OR X = 1 .`, `X XOR X = 1 foo`,
`X AND X = 1 AND X = 2 foo`, `X AND X = 1 2` and `S AND X = 1 foo` (after its `String is illegal
here`) are `Incomplete expression: <statement>.` on all four loggers: the compiler reads the
statement as an assignment (the tail after the first top-level `=`) and the leftover is the
assignment's. An unclosed `(` in the tail is `No Matching, or out of place ) in expression`. CR300/
CR350 name the undefined junk first (`foo is not defined.`, only when the target is a plain name:
`X AND A(1) = 1 foo` lacks that line, not done). Oracle, 2026-10-12,
`tests/fixtures/r13_logical_assign_junk.json`. Implementation: `structure._pass1_scan` (operand-led
branch, `_logical_assign_eq`), and for CR300/CR350 the `_pass1_scan` call in
`cr3xx.check_unknown_line_words`. Not done: `X AND X = 1 ;` (CR6/CR1000X accept it; CR300/CR350
`Invalid symbol in expression`), `X AND X = Y foo` on CR6/CR1000X (`Invalid symbol`), CR300/CR350
`X AND X = (1 foo` lacks its `foo is not defined.` line.

### `String is illegal here` count with a dangling operator (round 13, A7)

Oracle, all four loggers, 2026-10-12 (`tests/fixtures/r13_string_illegal_dangling.json`). A logical
word (`AND OR XOR IMP EQV`) whose right operand is a String leaves its result String-typed, so the
next logical word reports again: `X AND "a" AND X .` and `"a" AND "b" AND` print two `String is
illegal here`, `"a" AND X AND X .` and `X AND X AND "a" .` one (counted per logical node, the chain
is left-associative). A missing right operand counts like a number: `X = "a" AND`, `S AND`,
`X OR "a" OR`, `X AND "a" AND X =` (and `X AND X = "b" AND "c" AND X =`) print the same lines as the
complete expression. An unclosed `(` group is typed too: `X = (S AND 1` prints `String is illegal
here` before `No Matching, or out of place )` (CR300/CR350 after the `Y is not defined.` line).
CR300/CR350 order: the arithmetic `Illegal String operand` (`"a" * 2 foo`, `S * 2 foo`, `"a" MOD 2
foo`) comes before `foo is not defined.`, the logical `String is illegal here` (`X AND "a" foo`)
after it. Implementation: `var_types._string_typed`, `parser.partial_expression` (placeholder
operand, closed groups), `cr3xx._order_arith_string_lines`. Not done: junk inside an unclosed group
(`X = (S AND 1 foo` lacks its string line), CR300/CR350 `X AND "a" * 2 .` (we print
`Illegal String operand` twice), CR6/CR1000X `X = 1 AND "b" AND X .` (we print `Invalid symbol`
instead of the strings and `Incomplete expression`).

## Round 13, CR6/CR1000X: a wrapped line that starts with an operator or `%`, after an unterminated string (`chm/sprintf_1`; fixture `r13_unterminated_leading_op.json`)

Oracle runs of 2026-10-12, CR6 and CR1000X (the tracer shows the generic `%s: %s.` message of the
continuation line emitted by the same pass as the `Invalid or missing parameter.` lines, i.e. pass 1).
A line that follows an unterminated-string line and starts with `%`, `[` or `]` (lexer-level
failures), or with one of `, . ) ^ * / < > = \` (a leading operator, with or without a quote later in
the line: `,x", x)`, `)"x`), is a pass-1 error: its `Invalid, or out of place expression: <line>.`
is printed after `String without end quote` (`SprintF ( s, "...` / `%3.2f %c", x,10)`). We printed
nothing for it because the scan (`structure._pass1_scan`) gave up at the lex error and left the line
to pass 2, which an earlier pass-1 error hides. Lines that start with `?`, `@`, `$`, `` ` ``, `~`, `|`,
`{`, `+`, `-`, `(`, `&`, `:` or a letter stay silent (and `#` is `Unknown instruction`, `!` a pointer
error). Not done: `;a x` and `{a x` print the line here but are silent in the real compiler after an
unterminated string; a line starting with a number then a name and a quote (`.5 x", x)`) is also
a real pass-1 error that we do not print; the echo of a line that fails at an unrecognised character
keeps its indentation and trailing blanks in the real compiler on all four loggers (`   %3.2f x   `)
and ours strips them.

## Round 13, CR6/CR1000X: `&` in operand position names an unknown symbol (`chm/clockset_1`; fixture `r13_amp_operand.json`)

Oracle runs of 2026-10-12, CR6 and CR1000X. In the pass-1 scan an `&` where an operand is expected
(`x = & 6`, `cv(4, & 6) text`, `x = 1 + &`, `cv(4, 5, &)`, `&hZ6`) is a name that does not resolve, so the
line is left to pass 2 like any unknown name and is silent after an earlier pass-1 error. A hex literal
(`&h6`) is an operand; `&h` directly followed by more tokens (`cv(4, 5, &h) text`, `&h 6`) is still the
pass-1 fallback, and `&` after an operand (`x = 4 & 6 y`) is the binary operator (`Incomplete
expression`). The subscript scan of `name(...) text` stopped at the first comma and never saw a later
argument; `_later_arg_unknown` scans each later argument for an unknown symbol. Not done: the same `&`
inside an `If` condition (`If x = & 6 Then`, `If cv(4, & 6) Then`) is silent in the real compiler and
still printed here.

## Round 13, CR6/CR1000X: an unresolved name in a constant-family argument of an unparsable instruction is pass 1 (`chm/mqttpublishtable_1`; fixture `r13_const_symbol_pass1.json`)

Oracle and message-tracer runs of 2026-10-12, CR6 and CR1000X. The tracer shows one `%s: %s.` message
for `MQTTPublishTable(MQTT QoS, 5)` and nothing else, so the CHM example's following prose line
(`Note: Longitude, Latitude, ...`, two pass-2 fallbacks for us) is hidden because the first line is
a pass-1 error. The rule is the one of `expressions.check_const_family_args`, applied to the
unparsable form (`_instruction_arg_findings`): when the first argument whose name does not resolve
sits at a constant-family parameter (`const_expr`, `const_expr_only`, `datatype_kw`: `MQTT QoS`, the
second argument of `PanelTemp(x, Zork Y)`, the datatype of `Sample(1,x,FP  2)`, the units of
`Scan (1,S  ec,3,1)`), `Invalid symbol in expression: <arg>.` is reported in pass 1 (stage 4,
printed with the stage 1 and 3 findings) and hides the generic fallback lines before and after it.
At any other position (`PanelTemp(Zork Y, 15000)`, `Battery(Zork Y)`) it stays a pass-2 message
that does not hide earlier or later fallbacks. A table-output source (code 11, stage 6) such as
`Sample (1,Zork Y,FP2)` inside a `DataTable` ends pass 2 like the existing `_undefined_table_source`
(a later `Foo Bar` is silent; an earlier one is printed). A shortfall (pass 1) hides a pass-2
symbol at any position, not only at a destination (`Sample (1,Zork Y)`, `Minimum (1,Zork Y,FP2,False)`
are `Invalid or missing parameter.`); a const-family symbol still wins over the shortfall
(`MQTTPublishTable(Zork Y)`). Not done: `MQTTPublishTable(1, Zork)` (plain name, an instruction the parser
accepts) prints the symbol, real prints the shortfall; an extra argument with adjacent operands
(`PanelTemp (x, 15000, Zork Y)`, `3 4`) is `Too many parameters:  <first token>.` in the real compiler;
`PanelTemp (x y, 15000, 3 4)` page-faults the real CR6Comp (no verdict).

## Round 13, all loggers: a `Sub`/`Function` header cut off at the end of the line is accepted (`chm/strcomp_3`; fixture `r13_sub_header_cut.json`)

Oracle runs of 2026-10-12, all four loggers. The CHM wraps `Sub SortStr(SrcPtr As Long,Swath As Long,Order As` /
`Long)`. The header line itself is silent whatever the cut: after `As` (`b As`), after a comma
(`Sub S1(a As Long,`), after the opening parenthesis (`Sub S1(`); the parameters read so far are
declared (`x = b` in the body after `b As`-cut is fine, after `a As Long,` the missing `b` is
`Invalid symbol in expression: x = b.`). The continuation line is an ordinary statement
(`Long).`, `b).`, `10).` generic fallback; a lone `)` compiles). The parser rejected the header at
the cut and the generic fallback printed the header line (and `Function` headers lost their body:
`Return outside of Function.`). Fixed in `parser._parse_subdef`/`_parse_param`. Not done: a header cut
after `As String *` (real `Size of string missing.`), inside `a(` (real `a( is missing the ) character.`), after
`a =` (real `Required parameter a cannot be initialized.`, CR300 prints `p`) or after `Optional` stay as before.

## Round 13, CR6/CR1000X: a condition statement cut off inside an open parenthesis (`chm/strcomp_3`; fixture `r13_cond_open_fallback.json`)

Oracle runs of 2026-10-12, CR6 and CR1000X. The CHM wraps `If (StrComp` / `(str1,str2) > 0 AND ...) Then`.
- `If (x +`, `If x > 0 And (x +`, `ElseIf (x +`, `While (x +`, `Do While (x +`, `Select Case (x +`: a condition that ends
  on an operator while a parenthesis is still open is the pass-1 generic fallback echoing the
  condition only (`(x +.`, no keyword, `Select Case` keeps its leading blank). `_scan_expr` returned
  "unknown shape" for it (`open_fallback`), so the line was silent and only the continuation line was
  reported. Plain assignments (`x = (x +`) are unchanged (whole line, the existing unclosed paren rule).
- `Expression expects ( :` for a bare built-in function in an unclosed condition echoes the condition as well
  (`If (StrComp` -> `(StrComp.`, `If x > 0 And (StrComp` -> `x > 0 And (StrComp.`), not the whole line.
Not done (older, all probed this round): `If StrComp Then` (real `Expression expects ( : StrComp .`, we
accept the line), `If StrComp` + wrapped argument list (real prints the `Expression expects (` line, we
only the continuation), `While (StrComp` (we echo `StrComp`, real `(StrComp`), `Select Case StrComp` +
`EndSelect` (real adds `EndSelect w/o Select`), `For x = y To (y +` (real echoes only the `To`
expression, `(y +`, we the whole line), and the CR300/CR350 versions of all of these (`No Matching` instead
of `Expression expects (`; no verdict for `strcomp_3` there, the real CR300/CR350 compilers fault on it).

## Round 13, CR6/CR1000X: the broken expression of a `For` header is its own message (`chm/strcomp_3`; fixture `r13_for_header_halves.json`)

Oracle runs of 2026-10-12. A `For v = init To limit [Step step]` header reads its three expressions
separately, and every broken one prints its own pass-1 message echoing that expression as written
(`structure._for_header_findings`, replacing the `Incomplete expression` only version): adjacent
operands `Incomplete expression:`, a trailing operator `Invalid, or out of place expression:` (the limit
`(y +`, `3 +   `, the init ` y + `: the text after `=`, one blank before `To` removed), an unmatched parenthesis
`No Matching, or out of place ) in expression:` (`(y 5`, ` (y 5)`). Several broken expressions give several lines
(`For x = 1 To (5 Step 1)`: `(5` and `1)`). A `For v = expr` with no `To` anywhere is `Invalid or missing
parameter.` whatever the expression (`For x = y +`, `For x = (y`). Not done: the CR300/CR350 headers
(`structure._for_header_incomplete` stays as it was), `For x = (y +` followed by a continuation line is
`Invalid or missing parameter.` in the real compiler and was already fixed by the no-`To` rule above.

## Round 13, CR6/CR1000X: expression statements that are not assignments (`chm/strcomp_3`; fixture `r13_expr_stmt_fallback.json`)

Oracle runs of 2026-10-12, CR6 and CR1000X.
- `Invalid assignment in expression:` needs a well-formed arithmetic left side of the first `=`
  (`x + 1 = 2`, `1 + x = 2`, `x ^ 2 = 2`). A comparison on the left (`x < 2 = 2`, `x > 2 Then y = 1`) or junk
  (adjacent operands: `x + 1 y = 2`, `Swath > 2 Then Done = false`) is the generic
  `Invalid, or out of place expression:` (`_invalid_assignment`).
- `(a < 0 AND b = 1) Then` and the like: a parenthesised group that holds a comparison or logical word and an
  `=` inside it is `Incomplete expression:` even when it contains a function call
  (`(StrComp (s1,s1) < 0 AND x = 1) Then`); the group without an `=` stays generic
  (`(Sin(x) < 0) Then`), and an `=` after the group does not count (`(x > 2) Then y = 1` generic).
Not done, still wrong (probed): pre-existing FALSE REJECTS on all loggers, a logical operator after a
longer left operand than a lone name: `x > 2 And y = 1`, `x + 1 And y = 1`, `x <> 2 Or y = 1`,
`(x > 2) And y = 1`, `1 And y = 1`, `-x And y = 1`, `Not x And y = 1`, `x And y(1) = 1` all compile on
the real compilers (the parser only tries the logical-assignment reading when the statement starts with a
name followed by the logical word, `parser._parse_assignment_or_call`); we print `Invalid assignment` /
the generic message. Also `(Sin(x) = 1) Then` is `Invalid assignment in expression:` (we: generic),
`x(1) + 1 = 2`, `x Mod 1 = 2`, `x + (1 = 2` are `Invalid assignment` (we: generic), `(x(1) = 1) y`,
`(x) = 1 y`, `(x = 1)(y = 2) z` are `Incomplete expression:` (we: generic).

## Round 13 analysis, CR6/CR1000X: `!name` on a non-pointer variable (`chm/strcomp_3`; implemented in round 14, see the end of this file)

The last remaining difference of `strcomp_3` (lines 49, 51, 61, 62). Oracle runs of 2026-10-12, CR6; we accept all of
these (false accepts). `Public x As Long, s1 As String * 10, p As Long!, f As Float`:
- `s1 = !x`, `x = !x`, `!x = 1`, `!x`, `x = 1 + !q`, `x = Len(!q)`, `s1 = !x + 1`: `x must be declared as a pointer type,
  e.e., AS Float!, Long!, Double!, String!,...` followed by the generic fallback echoing the whole statement with its
  indentation (`If !x = 1 Then ...`: the generic echoes the condition `!x = 1 `). `!x y` and a bare `!x` too.
  A pointer variable (`p As Long!`) is silent. The name is the one after `!`, whatever the other side is.
- `!nope` (undeclared): `Illegal ! operation`, then `<destination> must be declared as a pointer type...` (the other
  variable of the statement, `s1` in `s1 = !nope`), then the generic; `!nope = s1` is `Illegal ! operation` alone;
  `Call !x` is `Sub !x not defined.`.
- Ordering: every such statement is reported, in line order (`s1 = !x` twice gives two pairs), but a
  statement before or after it that has any other pass-1 or pass-2 error is silent (`Foo Bar`, `x = Zork`, `y = 1 +`,
  `Call Zork`, `Sample` outside a table), including earlier ones. Inside `strcomp_3` the pointer errors sit between
  the `For`/`If` pass-1 errors in line order, so the stage is not a pure pre-pass; which stage hides the `y = 1 +` of
  the probe is unresolved. This needs the tracer on a pointer program and a model of the statement-compile stage
  before it can be added; left for a later round.
- Everything else of `strcomp_3` is now exact on CR6 and CR1000X; CR300/CR350 page-fault in the real compilers.

## Unclosed built-in call inside a condition

`If Round(X Then`, `ElseIf X = Round(X Then`, `If X + Round(X Then` (a call with fewer arguments than its
count, left open in an `If`/`ElseIf` condition): all four loggers print `Expression expects , : <condition>.`
with the condition echoed without the keyword and `Then` (oracle, 2026-10-12, `r13_unclosed_call_condition.json`),
the same rule as the statement form (`_unclosed_call_message`). Not covered: a trailing comma (`If Round(X, Then`,
real `Invalid, or out of place expression: Round(X, .`), `Round(Abs(X`, the one-line `If c Then stmt` on
CR6/CR1000X, and `While`/`Do While` on CR300/CR350.

### Table names with a dot, `CallTable(15Min)` (2026-10-12)

Follow-up to "Table names starting with a digit". A table name that starts with a digit or a dot and
contains a `.` (`DataTable(1.5,...)`, `1.5e3`, `.5`, `1.`, `1.5x`, `"1.5"`) is `DataTable name 1.5 is
illegal.` on all four loggers (the string form is echoed without quotes), in the same model as the
`Table name ... illegal, starts with a number.` message (the CR300/CR350 first-error rule is
shared, checked with two illegal tables). `CallTable(15Min)` (parenthesised) and `CallTable 1.5` /
`CallTable(1.5)` read the digit-led name as one name like `CallTable 15Min`. Fixture:
`tests/fixtures/r13_digit_table_names_paren.json`.

### `TCPOpen` connection count, CR6/CR1000X (2026-10-12, fixture `tests/fixtures/r13_tcpopen_connections.json`)

`TCPOpen(..., Handle, Count)` with a `Long` variable handle and a constant integer `Count` outside
`1..N` prints `Max number of concurrent connections must be 1..N` at the statement's line, `N` being the
total element count of the handle variable (scalar 1, `H1(2)` 2, `H1(2,2)` 4; any subscript form of the
handle, `H1`, `H1()`, `H1(1)`, gives the same `N`). Count 0, negative, above `N`, and a missing count
(5 arguments, read as 0: the message precedes the usual arity line) are all out of range; `Const` counts
fold. Also inside a one-line `If` and as `R = TCPOpen(...)`. A `Boolean` handle prints nothing on
CR6 (probed). CR300/CR350 print nothing (unchanged). Implemented in `instr_rules.check_tcpopen_connections`
for 5-7 arguments, a once-declared `Long` handle and a constant integer count. Not done (all probed on
CR6): a non-constant or non-integer count (`2.5`, `3.0`, a variable) adds `Parameter must be a constant
integer: <stmt>` (and `1..1` for a variable count), 8 arguments, trailing empty arguments.

### User-function call messages after an undefined name, and their count (2026-10-12, fixture `tests/fixtures/r13_call_arity_counts.json`)

Follow-up to "Function calls after an undefined target".
- CR300/CR350 skip the value's call only when the target's base name is undefined (`Q(Z) = F2(..)`,
  `Q = ..` is different, below). `A(Z) = F2(1,2,3)` with `A` declared reports `Z is not defined.` and then
  the call's messages (`No Matching ...` / `Call argument required.` + fallback); CR6/CR1000X print the
  fallback only, as before. `cr3xx.order_names_before_call_arity` puts every `X is not defined.` of such an
  assignment ahead of the call's messages (also the bare `Q = F2(1,2,3)`: `Q is not defined.` first).
- `Call argument required.` is printed once per missing required argument, on all four loggers: `F2()` with
  two required parameters twice, `F3()` three times, `F2(1)` once; `Optional` parameters do not count. A call
  nested in the arguments adds its own (`X = F2(F3())`: 1 + 3 = 4), a later sibling call adds nothing
  (`F3() + F2()`: 3). Also for `If` conditions and a call used as a statement.
- Not done: `A(Z,1) = F2(1,2,3)` (a two-subscript target on a one-dimensional array: real prints the
  generic fallback instead of `No Matching`); on CR6/CR1000X an undefined name in the *value* also hides the
  call (`X = Zz + F2()` -> `Invalid symbol in expression:` only; ours still prints `Call argument required.`).


## Prefix by message kind in an included file (oracle, 2026-10-12, all four loggers)

Supersedes the "two exceptions" of "Errors from an included file: the `<file>` prefix" above: the
real compiler prints `<file> line N:` from the file being *read* when the message is emitted.
A message emitted while the line is read (pass 1, name resolution on CR300/CR350) carries it; a
message emitted after the include was closed (block pairing, placement before `BeginProg`, the
table/scan/array checks, `Could not find DataTable`, pass-2 symbol resolution on CR6/CR1000X)
prints no prefix at all. When the *included file contains the `BeginProg`/`EndProg`* the deferred
messages print a garbled name (`<SS@>`, `<>`, bytes from freed memory; not modelled, any output
differs there), so the rule was measured on programs split around `BeginProg`: 2,390 corpus
programs (fuzz + CHM, 4 loggers), the declarations before `BeginProg` as the include (variant A) or
the body between `BeginProg` and `EndProg` as the include (variant B). Prefix mismatches against the
real compiler fell from 1,043 of 10,615 compared lines to 62 (the rest are the families below left
alone and pairing artefacts of the harness); fixtures `tests/fixtures/r13_include_prefix.json`
(128 cases: statements before `BeginProg` in an include, and the include inside a `Scan` body).

- Read-time, always prefixed: `Invalid, or out of place expression` for a syntax error
  (`(1,2)`, `X = 1 +`, a quote, `ACOS()`), `Incomplete expression`, `Expression expects (`,
  `Invalid or missing expression/parameter`, `Invalid assignment in expression`,
  `No Matching, or out of place ) in expression`, declarations. The old `show_file=False` on these
  call sites was wrong (it came from a probe where the include was the last thing in the compile).
- `Invalid, or out of place expression` for an *unresolved name* statement (`Foo`, `Foo Bar`,
  `X += 1` with `X` undeclared): CR6/CR1000X deferred (no prefix), CR300/CR350 read-time
  (prefixed, next to `Foo is not defined.`). Modelled as `show_file="cr3xx"` (prefix only where
  `drive_prefix` is true).
- Deferred, no prefix (`diagnostics._DEFERRED`, one regex list per logger family, only families
  with no prefixed observation): `<X> illegally occurs before BeginProg`, `NEXT/LOOP/END IF/Else/
  Case/EndSub ... without`, `NextScan with no Scan`, `Scan without NextScan`, `Duplicate Sub`,
  `Could not find ...`, `EndTable without DataTable`, `Table ... declared with no output fields`,
  `Sample must be used inside a table`, `Variable ... out of bounds`, Scan interval/parameter
  messages, `Invalid channel/port/reps`, `Can't assign units to`, `... already assigned Units`,
  an undefined name as a table-field source (`Sample(1,Nope,IEEE4)`: `Invalid symbol in
  expression: Nope.` / `Nope is not defined.`) and similar instruction checks (see the list).
  A new family is added to the list only after a probe shows it unprefixed.
- Still mixed and left as before: `Invalid symbol in expression` in a statement body (prefixed in
  some positions, not in others), `Warning:` lines (never prefixed in `cr6comp`).

## Duplicate declaration inside an include (oracle, 2026-10-12)

PROGRESS item 13 (CR300/CR350): no divergence. All
  loggers print `<file> line 2: X already declared as a variable.` (CR3xx also the genuine
  `Too many parameters:  Sec.` cascade for a `Scan (1,Sec,0,0)` in the program and the table
  field cascade), matching `analyze`; fixture `r13_include_dupdecl.json` (56 cases). The item was
  not a defect of `crbc`. `Duplicate Sub A` in an include prints without the prefix on all four.


## Routine-local names and routine-body `Public`/`Const` (2026-10-12, review r13-A item A9)

Oracle, all four loggers (probes in `scratch/r13s/`; fixture `r14_routine_local_scope.json`, 41 cases on each of the four loggers, including the accepted boundaries: global declared before/after the Sub, `Public`/`Const` inside a routine used in main, the same local name in two routines).

- A `Dim` inside a `Sub`/`Function` is visible only in that routine; a Sub/Function parameter likewise
  (and it is not a Sub-local `Dim` of an earlier routine that hides, only a later parameter list or
  `Dim` may reuse the name). Used anywhere else it is an undefined name, reported exactly like any
  other: CR6/CR1000X `Invalid, or out of place expression:   Y = 2.` / `Invalid symbol in expression:
  <text>.` (assignment target / any other position), CR300/CR350 `Y is not defined.` (an indexed use
  `Y(2)` also prints `Dimensions not declared`). Observed in main code, a `Scan`, an `If` condition,
  another Sub, a position before the declaring Sub, a global `Const` value, a `Public` dimension and
  a `DataTable` `Sample`. Names that stay visible: a name also declared outside any routine (before or
  after the Sub), a `Public`/`Const` inside a routine (both are global, below), a name declared by
  another routine's `Dim` of its own, a use before the `Dim` inside the same Sub, and a predefined
  name whose `Dim` was refused (`Dim Sec` -> `Sec is a predefined CONST.`, `Sec` still resolves).
  Implementation: `symbols.out_of_scope_nodes` (ids of the offending `Name`/`Index` nodes) carried by
  `expressions.KnownNames.out_of_scope` and honoured by `_undefined_names_walk`, `_dims_walk` and
  `_names_and_dims`. Not done: a Sub-local name used in a position that does not go through the
  generic undefined-name walk (the same positions that already under-report any undefined name);
  `PortSet(1, Y)` prints the `Invalid port` line after the undefined-name line, as for any undefined `Y`
  (the real order is the reverse; pre-existing).
- `Public` and `Const` inside a routine declare **globals** (`Z = Y` in main code compiles after
  `Sub Foo` / `Public Y`), so they take part in every duplicate check as a global declaration would:
  an earlier routine's `Const N` conflicts with a later `Sub B(N)`, `Dim N` (in a routine or in
  `BeginProg`), global `Public N`, global `Const N` or another routine's `Const N`/`Public N` (`N is
  already declared as a CONST.`); a routine-body `Public N` conflicts with an earlier global `Const N`
  or with `Const N` in an earlier routine (`... CONST.`), with an earlier or later `Public`/`Dim N`
  (also in another routine) and with a later global `Const N` (`N already declared as a variable.`);
  `Public N, N` in one Sub reports the second. A routine `Dim N` and a parameter `N` coexist with a
  routine-body `Public N` (no message). Implementation: `structure.check_pass1` registers and checks
  them through `_check_duplicate_declaration` (without the name-collision reports `symbols.
  check_local_name_collision` already makes) and keeps the CR300/CR350 "first CONST conflict only" rule.

## Round 14: `!name` on a non-pointer variable, CR6/CR1000X (`var_types.check_pointer_deref`, fixture `tests/fixtures/r14_pointer_deref.json`)

Closes the last CHM-corpus difference (`strcomp_3`; CR6 and CR1000X now 506/507, the 507th is an oracle error).
Oracle, 2026-10-12, CR6 and CR1000X; CR300/CR350 accept every one of these programs (a bare `!x` there is only
the generic fallback, already reproduced), so the check is off on `single_pass` profiles.

- **It is a pass-1 error.** Message tracer (`trace_errors.py cr6`): `<name> must be declared as a pointer type, ...`
  is emitted by the same statement-parse stage as the pass-1 expression errors, and the generic
  `Invalid, or out of place expression: <echo>.` follows from the second reporter (the fallback path). That
  settles the placement left open in round 13: the pair is an ordinary pass-1 error, printed in line order with the
  other pass-1 errors (`For x = 1`, `Units x`, `x = 1 +` with a declared target), and, like any pass-1 error, it
  hides all of pass 2 (`x = Zork`, `Foo Bar`, `Call Zork`, `y = 1 +` with an undeclared `y`, `EndIf` without `If`,
  unclosed blocks, `Sample` outside a table, both before and after it). No new gating code was needed; the pass
  runs next to `check_ctype` in `checker/__init__.py`.
- **Which operand.** The first `!operand` in textual order (target first: `!x = !f` names `x`) whose operand is a
  declared variable without a pointer type; an operand that already is a pointer (`p As Long!`) is skipped
  (`s1 = !p + !x` names `x`). The name is printed as declared (`!foo` for `Foo` prints `Foo`), arrays
  (`!q`, `!q(1)`), `String` and locals/parameters of the statement's own routine (a local shadows a global; a
  parameter of a `Sub`/`Function`) all count; `!(x)` and `@x` are silent. An assignment whose own target is
  unresolved (`Zork = !x`) is silent here (pass 2).
- **Echo** is the statement's usual fallback echo: an assignment/bare statement echoes `stmt.text` with its
  indentation and without the comment (`      str1 = !I .`); an `If`/`ElseIf` the condition up to `Then` with
  the trailing space (`!x = 1 `); `Randomize(!x)`, `Delay(0,!x,1)`, `Move(x,1,!f,1)`, `PortSet(1,!x)`,
  `While !x = 1` and `Call Zork(!x)` the argument or condition alone; the segment after a `:` keeps its leading space.
  A bare `!x` / `!x y` gets the pair too.
- **Not implemented (still wrong, documented):** `For x = !f To 3` (echo ` !f`), `Do While !x`, `Select Case !x`
  / `Case !x` (followed by `Case outside Select.` / `EndSelect w/o Select`), `Scan(!x,...)` (the pair precedes
  `Variable illegal in Parameter`), `CallTable !x` (only `Could not find DataTable !x.`), a one-line `If` whose
  *body* holds the `!x`, table-declaration instructions, aliases (`!Fa` is the pointer error but `!fa` is `Illegal !
  operation`, case-sensitive), `!x.y` (names `x`), and the undeclared forms: `!nope`/`!K` (a constant) are
  `Illegal ! operation`, then `<destination> must be declared as a pointer type...`, then the generic. `F1 = !c`
  with `c As Long!` assigned to a `Long` function is `Assigning a pointer to the wrong type of pointer 8000 101`
  (not implemented). A pointer misuse inside a misplaced `DataTable` prints only the placement error.

## Round 14 review fixes (R14-1, R14-2, R14-5, R14-7, R14-10)

Oracle-confirmed on all four loggers, 2026-10-12 (fixtures `r14_for_header_member`,
`r14_end_sub_scope`, `r14_end_sub_open_if`, `r14_cascade_inline_if`, `r14_include_incomplete_prefix`):

- **`For` header operands may be dotted members.** `For I = 1 To T.X(1)`, `For I = T.X To 3`,
  `... Step T.X`, `To Cfg.n` (structure member), `To Status.SerialNumber(1,1)` compile; the
  header scan merges `Name.Member` into one operand so the assignment-only `X = 1 .` rule does
  not fire. Garbage after the member (`To T.X 3`) is still `Incomplete expression: T.X 3.`
  (CR6/CR1000X). Known, separate: `To Status.RecNum` is `Could not find Status.RecNum(1).` in
  the real compiler (we accept); `To T. ` compiles (we print a symbol message); CR300/CR350
  echo the half only (`T.X 3`), we echo the statement.
- **Two-word `End Sub`/`End Function` closes the routine's scope** for `scope_tables` (local
  `Dim` arrays no longer leak into main code) and reports `If without EndIf inside of Sub.`
  like `EndSub`.
- **`include_error_at` is re-indexed by `_with_inline`**, so earlier one-line `If` bodies do not
  start the CR300/CR350 `Too many parameters` cascade early.
- **`<file>` prefix** also applies to the pass-1 messages of an included file's one-line `If`
  bodies and called block keywords (`If X Then Y = Scan(1)`, `If Scan(1) Then`, `If X Then Y`,
  `If X Then Y = 1 +`, `If X = Then`, `If X Then Status = 1` on CR300/CR350): CR6/CR1000X
  `<l.crb> line N:`, CR300/CR350 `<CPU:l.crb> line N:`. Open: `If X Then Scan(1)` in an include
  is `Invalid or missing parameter.` in the real compiler (we differ, with or without include).
## Round 14: logical-assignment left operands, parenthesised targets, `Public.X` (2026-10-12)

Oracle, all four loggers, fixture `tests/fixtures/r14_logical_assign_operands.json` (probes `scratch/r14/`).

- Any left operand may lead into `A AND B = C`: `X > 2 And Y = 1`, `X + 1 And Y = 1`, `1 And Y = 1`,
  `-X And Y = 1`, `Not X And Y = 1`, `A(1) > 2 And Y = 1`, `X > 2 And Y(1) = 1`, `S > "a" And Y = 1`, all
  words (`Or`, `Xor`, `Imp`, `Eqv`). The accepted-target rules of "Logical-prefixed assignment
  targets" still apply to the target after the last word. Implementation: `parser._try_logical_assign`
  (tried for a statement starting with a number, string, `(`, `-`, `+`, `Not`, or a name/atom followed by
  a binary operator or logical word).
- CR300/CR350 reject a statement that starts with a group, `(X > 2) And Y = 1` -> `Invalid, or
  out of place expression: X > 2.` (kept: not accepted there).
- A parenthesised target is a variable: `X And (Y) = 1`, `X And ((Y)) = 1`, `X And (A(1)) = 1`, `X And
  (T.X) = 1` compile; `(Y+1)`, `(1)`, `(K)` (K a `Const`) are `Invalid assignment in expression`.
- A non-variable between the last word and the first `=` (`X And Y + 1 = 1`, `1`, `-Y`, `Not Y`,
  `Abs(Y)`, `(Y+1)`, a `Const`) is `Invalid assignment in expression: <stmt>.` when every name is
  defined (`structure._logical_bad_target`; the `Abs(Y)` and `Const` shapes on CR300/CR350, and the
  undefined-name variants, are not modelled).
- CR6/CR1000X: a statement starting with a parenthesised variable is an assignment (`(Y) = 1`, `(Y) += 1`,
  `(A(1)) = 1`); CR300/CR350 reject it (`Invalid, or out of place expression: Y.`).
- Assigning to a program `Const` (`K = 1`, `K += 1`, `(K) = 1`) is `Invalid assignment in expression`
  (all four loggers; `structure._assigns_user_const`).
- A trailing `;` after a logical-prefixed assignment is skipped on CR6/CR1000X (`X AND Y = 1 ;`);
  CR300/CR350 say `Invalid symbol in expression` (not modelled, both reject).
- `Public.X` names the declared variable `X`: `Public.X = 1`, `Public.X(1) = 1`, `Public.X += 1`, `X And
  Public.X = 1`, `Public.X And Y = 1` compile; `Public.Nope = 1` / `Public.K = 1` (not a variable) are
  `Could not find Public.Nope(1).` + `Invalid assignment in expression: <stmt>.`. Not done: reading
  `Y = Public.Nope` really is `Could not find Public.Nope(1).` (we accept); `X And Dim.X = 1`,
  `X And Settings.X = 1`, `X And Q.X = 1` are `Could not find ...` + invalid assignment (we reject with
  the generic fallback).

## Round 14: `Abs(X` without the closing parenthesis (2026-10-12)

Oracle, all four loggers (identical), fixture `tests/fixtures/r14_abs_unclosed.json`: `Abs`, `AbsLong`,
`ArrayLength`, `TypeOf`, `LineNum` and `Signature` compile with their argument group left open at
the end of the statement: `X = Abs(X`, `X = Abs(X + 1`, `X = 1 + Abs(X`, `X = Abs(X And Y`, `If Abs(X
Then` (block and inline), `If Abs(X + 1 Then`, `Do While Abs(X`, `Select Case Abs(X`, with `Then`
ending the group in an `If`/`ElseIf`. Every other function keeps `No Matching, or out of place )` /
`Expression expects ,` (`Round`, `Sin`, `Int`, `Len`, `Mid`, `Left`, ...). Implementation:
`parser._close_open_call` adds the missing `)` when the call is the only open group, holds no comma
and is not empty. Still false rejects (really accepted, all four loggers; their shapes differ per
function and are not modelled): `While F(X Then` (the `Then` is swallowed), nested `Abs(Abs(X`,
`X = Abs(X,` / `Abs(X Y` for `Abs`/`AbsLong`/`ArrayLength`/`TypeOf` (`LineNum`/`Signature` say
`Incomplete expression`), an empty `LineNum(` / `Signature(`, `A(LineNum(X) = 1`. `If Abs(X, 1 Then`
is `Incomplete expression: Abs(X, 1 .` (we say `No Matching`).
## Round 14 multi-report order (undeclared arguments of one instruction, CR6/CR1000X)

Oracle, 2026-10-12, CR6 and CR1000X (`tests/fixtures/r14_multi_undeclared_args.json` 185 cases and `r14_multi_undeclared_args_b.json` 70 cases): when an
instruction call names more than one undeclared variable, the real compiler reports every one of them
(`Invalid symbol in expression: <arg>.`, one line each), not only the first; opencrbasic stopped at the first
position (`_report_statement`). The order is per instruction, not left to right: `SplitStr(a,b,...)` reports `b`
then `a`, `GPS(a,c,0,100,e)` reports `e` then `a`, `AVW200` position 4 then 0, `CDM_TCSE` 7 then 2, `PortBridge`
1 then 0, `SortSpaIndexed(a,6,b)` and `TotalRun(a,1,b,...)` a then b. The fuzz cluster was 58 reproducers
(`SortSpaIndexed`, `TCDiff`-style `SendVariables`/`GetVariables`/`SDMIO16`/`SDMAO4A`/`SplitStr`/`TotalRun`/
`TCPActiveConnections`). Found with a sweep: for every instruction with a CHM example, every variable-like
argument replaced by an undeclared name and compared real vs ours, then all single/pair/triple subsets of the
good ones. The orders are entered in `expressions._MULTI_REPORT_ORDER` (positions are independent in every probed
subset; a position missing from an entry is not reported by the real compiler):

- Added: `ArgosDataRepeat (0,3)`, `AvgRun (0,2)`, `AVW200 (4,0)`, `PRT (0,2)`, `StrainCalc (0,2,3)`,
  `CDM_TCSE (7,2)`, `Debug (2,3,4)`, `DewPoint`, `SendVariables (0,1,8)`, `EC100Configure (0,3)`,
  `GetVariables (0,8)`, `GPS (4,0)`, `HydraProbe`, `Matrix (1,2,3)`, `PeakValley (0,1,3)`, `SatVP`,
  `TimedControl (4,5)`, `SDMCD16Mask`, `SDMGeneric (4,0)`, `SDMIO16`, `WetDryBulb`, `SerialInRecord (1,5)`,
  `SortSpa`, `SortSpaIndexed`, `SplitStr (1,0)`, `TCPActiveConnections`, `TotalRun (0,2)`, `VaporPressure`,
  `ModbusClient (0,5)`, `PortBridge (1,0)`, `SDMAO4A`.
- Also (second sweep with expression arguments and up to five call shapes per instruction): `TotalRun (0,2,4)`
  (position 4 is a condition, reported whole: `Invalid symbol in expression: Nope OR X>=200.`), `StdDevRun`/`AvgRun`
  `(0,2,4,5)`, `TCDiff (5,0)`, `FFTSpa (2,0)`. A position missing from an entry is not checked at all in the
  multi path, so an entry must list every position the old first-only rule could report.
- **Positions never checked for an undeclared name (CR6/CR1000X compile clean)**: `SDMSIO4`, `MonitorComms` and
  `ModemCallBack` position 0 and `NewFile` position 2 (`NewFile(A,"CRD:p.crb",Nope)` compiles; opencrbasic rejected
  it: false rejects). CR300/CR350 still report the `NewFile` one (`Nope is not defined.`), hence
  `expressions._EXEMPT_WHOLE_ECHO`.
- Left out (the same sweep disagrees or follow-ups differ): `CovSpa` (position 0 is reported alone but dropped when
  3 or 4 is also undeclared), `FieldCal`/`FieldCalStrain` (array positions; also `... must be a declared
  variable` follow-ups), `WindVector` in a table (positions 2 then 1; CR300/CR350 report only the first and
  add `Too many parameters`, not modelled).
- Not modelled (unchanged): follow-up lines after the undeclared names that depend on the surviving argument
  types (`Illegal parameter(s)` for `Debug`, `Illegal Matrix` for `Matrix`, `... must be an Array(2,3) of type long`
  for `PortBridge`, `Source and Dest types must be the same` for `SortSpaIndexed` with one undeclared side,
  AVW200 and `TimedControl` timing messages): the cases that carry them are not in the fixture.
- CR300/CR350: the same sweep found no ordering difference besides `MQTTPublishTable` (`Zq5` then
  `Too many parameters:  Zq6.`) and `CovSpa` (an extra `Dimensions not declared` for an undeclared array).

## Round 14 placement before arguments (`TimerInput`, `MenuItem`, `MenuRecompile`; CR6/CR1000X)

Oracle, 2026-10-12, CR6 and CR1000X (`tests/fixtures/r14_placement_before_args.json`; message tracer `trace_errors.py
cr6`: the `... must be used within ...` message is emitted from inside the instruction's compile function, called by
the pass-2 statement dispatcher). `TimerInput` outside a `Scan` and `MenuItem`/`MenuRecompile` outside
`DisplayMenu..EndMenu` reject the placement **before** resolving their arguments, so an undeclared name in the same
statement gets no `Invalid symbol in expression` (opencrbasic printed it after the placement error; 13 fuzz
reproducers, mostly `MenuRecompile("x",Undeclared)`). Later statements are reported as before (a `MenuRecompile` in a
`Scan` does not stop pass 2; `TimerInput` does, as already modelled). `PulseCount` is different: it resolves its
arguments first, then prints a port/channel error, then the placement message (`Zq0` / `Invalid port 1 ...` /
`PulseCount must be used ...`; not modelled, opencrbasic prints the placement message first). `Quadrature` with an
undeclared first argument gives only `Too many parameters:  1.0.` on CR6/CR1000X (matches) but `... 1.` on CR300/CR350
(float echoed whole, not modelled). A one-line `If ... Then TimerInput(...)` outside a `Scan` also gets the placement
error and stops pass 2 (opencrbasic missed it). CR300/CR350 are unaffected (`TimerInput is not defined.` and
`Nope is not defined.` both print).
Implementation: `checker.__init__._placement_rejected` gives `expressions.check` a skip set;
`structure.scan_only_outside`/`check_stray_controls` now walk a one-line `If`'s statements.

## Round 14 one-line `If` condition kept under the cascade (CR300/CR350)

Oracle, 2026-10-12, CR300 and CR350 (`tests/fixtures/r14_inline_if_cascade.json`, 50 cases, all four loggers):
`If Nope Then GetVariables(Other,ComSDC7,...)`, `If Nope Then Delay(1,Other,mSec)`, `... PanelTemp(Other,250)` print
`Nope is not defined.` for the condition, then the body's own lines (`Other is not defined.`, `Too many
parameters:  ComSDC7.`). `cr3xx.check_too_many_cascade` rewrites by physical line, so the body's rewrite dropped the
condition's line and the `If` itself (whose line holds the body's diagnostics) was taken as the cascade trigger.
Now the `If` counts only its condition's lines, the body is rewritten alone, and the condition's lines are put
back in front (`_inline_owner`, `_names_in`; 7 fuzz reproducers, `iproute_1`/`getvariables_1` family).
Still different (not in the fixture): `If A = 1 Then Delay(1,Nope,mSec)` prints `Nope is not defined.` then
`Too many parameters:  mSec.` on CR300/CR350; opencrbasic's lone-line rule for the `Delay` follow-up (`per_line > 1` guard
in `cr3xx`) still skips an `If` line.

## Round 14 table references (`SendData`, `SendTableDef`, `GetRecord`, `ResetTable`, `FileMark`)

Oracle, 2026-10-12, all four loggers (`tests/fixtures/r14_table_references.json`, 136 cases). These instructions
name a table and the real compiler fails when none exists (opencrbasic compiled them clean: false accepts; 12 fuzz
reproducers incl. `SendData (ComRS232,0,4094,Table1)` before `BeginProg`). Message tracer: `Cannot find DataTable %s.`
(0x487c32 via 0x48359e) and `Could not find ...` come from the instruction's compile function during pass 2.

| Instruction (argument) | Message | Ends pass 2 |
|---|---|---|
| `SendData`, `SendTableDef` (4th) | `Cannot find DataTable <text>.` | no, every one is printed in line order |
| `GetRecord` (2nd), `ResetTable` (1st) | `Could not find DataTable <name>.` | yes, like an unknown `CallTable` |
| `FileMark` (1st) | `Could not find FileMark table <name>.` | yes |

- `SendData`/`SendTableDef` echo the argument's text trimmed (`T1 + 1`, `Nope()`, `"x"` with its quotes, `T1.A`, a
  variable `A`); `GetRecord`/`ResetTable`/`FileMark` take a name or a string without quotes (`GetRecord(A,"x",1)` ->
  `x`). `Status` is accepted as a table (`Public`, `Settings`, `DataTableInfo` assumed alike, not probed).
- Several ending messages and unknown `CallTable`s: only the earliest is printed (`CallTable Nope2` then
  `GetRecord(A,Nope,1)` prints only the first, and the reverse), later statements are not compiled (a following
  `NextScan with no Scan.` or `SendData` of another missing table is gone). A `SendData` message is followed by later
  statements normally (`A = Zork`, a second `SendData`, an unknown `CallTable`).
- Before `BeginProg` the message precedes `SendData illegally occurs before BeginProg.` on the same line.
- CR300/CR350 also stop at `GetRecord`/`ResetTable`/`FileMark` (the late scan-placement walk is cut there) and
  treat `Cannot find DataTable` like `Could not find ...`: hidden by any other error elsewhere in the file
  (`drop_calltable_error_with_other_errors`), and the late walk still runs beside it (`NextScan with no Scan.`,
  `SendData illegally occurs before BeginProg.`).
- Not modelled (probed, same family): `AcceptDataRecords` (3rd; `Could not find DataTable X.` then `Illegal table
  name`), `GetDataRecord` (9th, CR6/CR1000X), `INSATData`/`EnviroSatData`/`GOESData` (2nd; `Could not find DataTable X`
  without a period), `ArgosData` (3rd; `Could not find DataTable ` with an empty name), `WorstCase` (`Could not find
  WorstCase table X.`), and a table declared *after* the statement (also not found). `EditTable(Nope,1)` crashes the
  real compiler (page fault, no verdict). `SendData(...,T1)`/`GetRecord(...,T1)` do not count as a call of `T1` for
  the "declared but never called" warning (opencrbasic treats them as a call: unfixed).

## Round 14 declaration tails, unregistered aliases and failed constants

Oracle, 2026-10-12, all four loggers (`tests/fixtures/r14_declaration_tails.json`, 214 cases).

- **False reject fixed: an unmatched closer ends a `Const`/`Public`/`Dim` line.** `Const Pi = 1)`, `Const Pi = 1) + 2`,
  `Const Pi = {1}}`, `Public X = 1)`, `Dim X As Long = 1)`, `Public Y(2) = {1,2}})` compile clean on all four loggers (the
  value ends at the first unmatched `)`/`}`, the rest of the line is ignored, like a top-level comma). opencrbasic printed
  `Invalid, or out of place expression: Const Pi = 1).` (CR300/CR350: a three-line cascade). `Alias L = Pi)` and `Units L = m)`
  were already accepted.
- **CR300/CR350: an `Alias` that prints `Alias is illegally dimensioned.` registers no name.** Empty `Alias Q() = A`,
  a parenthesised index item `Alias Q(1,(1+2)) = A`, `Alias Q((1)) = A`, `Alias Q((1+2),1) = A`, as the undefined-index
  one already did; a later use of `A` is `A is not defined.` (6 fuzz reproducers, `quadrature_1`, `sdi12recorder_2`). The
  out-of-range ones (`Alias Q(0) = A`, `Q(5)`, `Q("a")`, `Q(1,2,3)`) print `Illegally dimensioned aliased name`, or nothing,
  and still register `A` (`expressions._known_names`).
- **CR300/CR350: a `Const` whose value is a bad built-in call is not declared.** `Const Pi = 4*ATN()`, `ATN()`,
  `4*ATN(1,2)`, `4*SIN()` print the pass-1 error, then every later use of `Pi` is `Pi is not defined.` (as for `Const P = Nope`,
  `_failed_consts`). Syntax failures such as `1 +` and `1 2` were already unregistered.
- Not fixed (found while probing): `Const Pi = (1` prints `No Matching, or out of place ) in expression: (1.`
  (opencrbasic: `Const with no assignment.`, all four loggers); CR6/CR1000X `Const Pi = 1 / 0` is
  `Divide by zero in expression: 1 / 0.` (opencrbasic compiles it, CR300/CR350 accept it).

## Round 14 `Call` of an undefined `Sub`: arguments first

Oracle, 2026-10-12, all four loggers (`tests/fixtures/r14_call_undefined_sub.json`, 92 cases). `Call Nosub(Zq0,Zq1)` with
undeclared arguments resolves the arguments before the link step: CR6/CR1000X print `Invalid symbol in expression: <arg>.`
for each undeclared argument (the argument's whole text: `1+Zq0`, `Zq0(1)`, `Zq1()`), then `Sub Nosub not defined.` (a program
without any `Sub`), or the link-time `Nosub is not declared as a Subroutine.` at the end; CR300/CR350 print `Zq0 is not defined.`
per name (and `Dimensions not declared: Zq0(1).`) and then no `Sub ... not defined.` at all (name resolution is a pass-1
error there, which skips the link step). Declared arguments still give just the `Sub` message. Implemented by calling
`_report_call_arg_names` (the same reporter used for a call of a declared `Sub`) before `_unlinked_call` (6 fuzz
reproducers: `Call  Table (SerialT)`, `Call run(X_max_sub,X,X_ID)`).

## Round 14 truncate warnings (`IfTime`, `TimeIntoInterval`)

Oracle, 2026-10-12, all four loggers (`tests/fixtures/r14_truncate_warnings.json`, 104 cases). An `IfTime`/`TimeIntoInterval`
call that fails its argument count (3 are required) prints `line N: Warning: Parameter will truncate to the nearest integer.`
first: once per missing argument and once per argument holding a variable, then the arity error. `A = IfTime(0)`: two,
`If IfTime(0,Hr) Then`: one, `A = IfTime(A,Hr)`: two, `A = IfTime(A,B)`: three, `A = IfTime()`: three (then the empty-call
`Invalid, or out of place expression:`), `A = IfTime(A,B,Min,3)`: two (then `No Matching, or out of place )`). A valid call is
quiet unless an argument is a variable: `If IfTime(A,1,Min) Then` compiles with two of these warnings per variable
(both passes); not modelled (the order against other output is unprobed). 9 fuzz reproducers. `warnings.add_truncate_warnings`.

## Round 14 follow-ups

### Trailing dot and letter-led members (fixture `tests/fixtures/r14_trailing_dot_members.json`)

Oracle, all four loggers, 2026-10-12 (`expressions.check_digit_members`, `parser._parse_atom`):

- **`T.` / `Status.` is a valid operand** (a data table or `Status` followed by a dot and no
  member) in assignments, `If` conditions and every `For` header position
  (`For I = 1 To T. `, `... Step T. `, `For I = T. To 3`, `X = T. + 1`, `If T. Then`). Any other name
  (`X.`, `A.`, `Zork.`) is the single line `Could not find X.` (CR300/CR350
  `Could not find DataTable X.`). The parser keeps the trailing dot on the name on every logger
  (CR300/CR350 formerly only kept it for the first token of a statement, which stays, `instruction.`
  prose lines), and a blank between the dot and the next name ends the member: `T. To 3`,
  `T. Then`, `T. And 1` compile, `T. X` is adjacent operands (`Incomplete expression`), while
  `T.To` / `T.Then` (no blank) are members (`Could not find T.To(1).`).
- **A letter-led member of a plain declared name** is `Could not find X.` exactly like the digit-led
  one (`X.Y`, `A.Y`, `S.Y`, `D.Y`, `K.Y` for a `Const`, an `Alias` name, a pointer `P As Long!`, a `Sub`
  parameter, a `Function` name, `X.Y.Z`; as an assignment target also `Invalid assignment in
  expression`; CR300/CR350 `Could not find DataTable X.`). Structure-typed names accept any member
  (not modelled).
- **Not implemented (documented):** member existence of a data table or `Status` (`T.Nope`,
  `Status.RecNum`, `To Status.RecNum` -> `Could not find Status.RecNum(1).`, accepted here). The
  `Status` field set depends on the logger and on program features (the CR6 TDF list plus
  `SkippedSlowScan`/`SlowProcTime`/..., CR300/CR350 have a different, smaller list with e.g.
  `IPInfo`, `Version`); 398 candidate names were probed per logger and the valid sets differ, so a
  rule would risk false rejects. CR6/CR1000X `Zork.X` for an undeclared name is `Could not find
  Zork.` (ours: `Invalid symbol in expression`); `T.(1)` compiles (ours rejects); `T..X` is
  `Could not find T..X(1).`; `A(1).Y` is `Incomplete expression: <statement>.` (accepted);
  CR300/CR350 `To T. 3` echoes only the broken half (`T. 3`), ours the whole statement (task 3).

### CR300/CR350 `For` header: only the broken half is echoed (fixture `r14_for_header_halves_cr3xx`)

Oracle, all four loggers, 2026-10-12. CR300/CR350 print the same half message as CR6/CR1000X
(`structure._for_header_parts`): `To T.X 3` -> `Incomplete expression: T.X 3.`, `Step T.X 2` -> `T.X 2`,
init `1 4` -> ` 1 4` (leading blank kept), `To (3` -> `No Matching, or out of place ) in expression:
(3.`, `To 3 +` -> `Invalid, or out of place expression: 3 +.`. The unknown-word lines before it
(`Step is not defined.`) only come from the `To`/`Step` half, never from a broken init. An empty init or
`To` expression is `Invalid, or out of place expression: .` (all four). Not done: both init and `To`
empty prints two lines (we print one); `For I = 1 To Step 2` is `Invalid symbol in expression: Step 2.`
on CR6/CR1000X (we print Incomplete).

## Round 14: `;` in every expression context (2026-10-12, review r14 items R14-4, R14-6)

Oracle, fixture `tests/fixtures/r14_semicolon.json` (925 cases, four loggers). The model is the one of
round 13, widened: the real compilers skip `;` wherever an expression is read, and keep it as a token
after instruction and keyword statements.

- CR6/CR1000X skip every `;` of: a multi-line `If c ; Then` and `ElseIf c ; Then` (also `ElseIf c Then ;`),
  `Case c ;` (any position inside the case list), `Do While|Until c ;`, `Loop While|Until c ;`, `While`,
  `For` (init, `To` and `Step` values), `Select Case`, `Const` (value side only: `Const ; K = 1` and
  `Const K ; = 1` are rejected), `Sub S ;` / `Function F(a) ;` (trailing only), `Public.X = 1;`, and any `;`
  inside parentheses of a call or instruction (`Delay(1 ;,1,Sec)`, `Call Bar(1 ;)`, `Bar(1 ;)`; not in a
  `Sub` header: `Sub S(a ;)` is `Invalid Variable declaration ;.`). The rest of the statement is then
  judged as if the `;` were absent, with the raw text echoed: `X = 1 ; Y = 2` and `Do While X = 1 ; Y = 2`
  are `Incomplete expression`. Not skipped: a `;` before the statement keyword (`; If c Then`), `Do ; While c`,
  `Loop ; Until c`, and a `;` glued to a keyword (`Then;`, `Next;`, `EndSelect;`, `Else;` in a one-line `If`:
  one longer word, `Incomplete expression: X Then;.`).
- All four loggers: `EndSelect ;`, `Case Else ;`, `ElseIf c Then ;`, `Next ; Y`, `Sub S ;` compile. After
  `EndSelect` only the first gap-free group is skipped (`EndSelect ; ;` is `Too many parameters:  ;.`).
- Kept as a token (all four loggers): an instruction call after its last parameter is a gap-free extra
  parameter, `Delay(1,1,Sec) ;` and `Battery(X) ;` are `Too many parameters:  ;.` (`;;` for `;;`), as is
  `Exit Do ;`. `Call Foo ;` links to the sub `Foo;` (`Foo; is not declared as a Subroutine.`). Not modelled
  (rejected on both sides, other text): `Else ;`, `Do ;`, `Loop ;` (`Illegal parameter ;, expecting constant
  integer.`), `EndIf ;`/`End Sub ;` (`Too many parameters:  ;.`), `Delay ;`, `CallTable T ;`, `Delay(1) ;`.
- The pass-1 expression scan (`structure._pass1_scan`) reads a statement without the `;` the parser skipped,
  which gives `X = 1 ; Y = 2` its `Incomplete expression` echo.
- CR300/CR350 (`Invalid symbol in expression:` instead of the fallback): a `;` in an If/ElseIf/Case/Do/Loop/While/
  Select condition echoes the condition alone (`Do While X = 1 ;` -> `X = 1 ;.`, one-line `If X ; = 1 Then Y = 2`
  -> `X ; = 1 .`); a `For` header or `Const` with a `;` is that single message echoing the value that holds the
  first `;` up to the next `To`/`Step` (`Step 1 ;` -> `1 ;.`, `For Y = 1 ; To 3` -> ` 1 ;.`, `Const K = ; 1` -> `; 1.`;
  `cr3xx.semicolon_headers`). A `;` before the `=` of an assignment is the generic fallback
  (`A(1;) = 2`, `X ; = 1`). `Case Else ;`, `ElseIf c Then ;` and `Sub S ;` are accepted.
- `Public.X = 1` (a Public variable through its table) compiled on all four loggers and was rejected by us
  (`Public` is in `profile.instructions`); it parses as an assignment now. An unknown member
  (`Public.Nope = 1`, as already `Y = Public.Nope` and `Status.Nope = 1`) is accepted by us and rejected by the
  real compilers (`Could not find Public.Nope(1).`), not modelled.
- Open, not `;` rules, found while probing: a one-line `ElseIf c Then Z = 1` (body after `Then`) compiles on all
  four loggers and is rejected by us; `X AND Y = 1 ;` is item R14-3; `If c Then ;` adds an unwanted
  `IF or SELECT ... without NEXT SCAN` cascade after the correct first line; `X = A(1 2)` (array) is
  `No Matching ...` on the real compilers and `Incomplete expression` here.

## Round 14 cloud review follow-up (2026-10-12)

**R14C-1, fixed.** A `Sub`/`Function` parameter declared `As <StructureType>` keeps its members:
`Sub S(C As Pt)` with `Z = C.x` or `C.x = 1` compiles on CR6/CR1000X (`Return C.x` in a `Function`
too), while `Z = C.y` is `In structure C, could not find .y` (oracle, 2026-10-12). Round 14's
letter-led member rule (`expressions.check_digit_members`) and the logical-assign struct test
(`structure._logical_assign_state`) took such a parameter for a plain variable (`Could not find C.`);
both now skip it, and `structs.check_members` checks its members like a declared structure variable
(only when no `Public`/`Dim` declares the same name). CR300/CR350 accept the assignment form; reading
`C.x` inside the routine crashes `cr300comp.exe`/`cr350comp.exe` (a reference fault, not recorded).
Fixture cases `*-r14c1-struct_param_*` in `tests/fixtures/r14_trailing_dot_members.json`.

**R14C-2, probed: not a defect except one shape.** The real compilers skip a `;` inside the
parentheses of `PortSet(C1 ;,1)`, `PortSet(C1,1 ;)`, `PortSet(102 ;,1)`, `Delay(0,10 ;,mSec)`,
`Battery(X ;)`, `PanelTemp(X,60 ;)`, `X = Sin(1 ;)`, `SerialOpen(ComRS232 ;,9600,0,0,50)`,
`Scan (1 ;,Sec,3,0)`, `Sample(1,X ;,FP2)` and `DataTable (T,1 ;,-1)` / `(T,1,-1 ;)` (CR6 and CR1000X,
all compile), so the blank model holds for them; the fixture's `SerialOpen(Com1 ;,...)` ->
`ComPort 0 illegal.` stays the exception. The `DataTable` name slot differs and is modelled
(`parser._drop_semicolons` keeps only that `;`, `_parse_segment` handles it):
`DataTable (T ;,1,-1)` (also `( T ;` and with a fourth argument) is `Invalid, or out of place
expression: ;.` then `Too many parameters:  -1.` (the `;` takes a parameter slot, like the second
word of `DataTable (Ten Sec,...)`), and a glued `DataTable (T;,1,-1)` declares a table named `T;`,
so `CallTable T` is `Could not find DataTable T.` (cases `*-r14c2-*` in
`tests/fixtures/r14_semicolon.json`). A built-in function written as a statement with a trailing
`;` (`SerialOpen(ComRS232,9600,0,0,50) ;`, `Timer(1,Sec,0) ;`) compiles on CR6/CR1000X (it was a
false reject).

CR300/CR350 (`parser._cr3xx_argument_semicolon`): a `;` inside an instruction's argument list is
`Invalid symbol in expression: <argument up to the ;>.` (`Sample(1,X ;,FP2)` -> `X ;`, `Delay(0,10;,mSec)`
-> `10;`); at a parameter that ends the argument list (`cr3xx._ABORTING_PARAMS`, `Delay`'s code 28)
the next argument follows as `Too many parameters:  mSec.` and later arguments are not reported.
The statement is otherwise read without the `;` (`Scan (1 ;,Sec,3,0)` still opens the scan). On a
`DataTable` line the name slot behaves as on CR6 (`T;` names the table `T;`; `T ;` is `Invalid
symbol in expression: ;.` then `Too many parameters:  1.`) and these errors start the `Too many
parameters` cascade (`Diagnostic.trigger`), unlike on other instructions. Instructions whose first
parameter is an expression (code 7: `Case (1 ;)`, `If`) keep the round-13 echo of the group. A
built-in function written as a statement with a `;` anywhere (`Timer(1 ;,Sec,0)`,
`SerialOpen(...) ;`) is `Invalid symbol in expression: <line>` with the line terminator, like the
assignment form.

**R14C-3, probed: not a defect.** `Y = Abs(X) + Abs(Y`, `If Abs(X) > Abs(Y Then Y = 1`, `While Abs(X`,
`Loop Until Abs(X`, `ElseIf Abs(X Then` and `Case Abs(Y` compile on all four loggers, as
`parser._close_open_call` accepts them (cases `*-r14c35-a*` in `tests/fixtures/r14_abs_unclosed.json`).

**R14C-4** (the all-logger `MODEMCALLBACK` exemption is inert on CR300/CR350): no change.

**R14C-5, fixed.** `Dim.X = 1` is `Could not find Dim.` (CR300/CR350 `Could not find DataTable Dim.`)
then `Invalid assignment in expression: <line>.` on all four loggers (`structure.check_pass1`, next
to `Public.Nope = 1`; pass 2 leaves the statement alone). Found with it: an assignment whose target
is a bad member reports that target only (`X.Q = X.R + 1` with `Public X`: one `Could not find X.`,
then `Invalid assignment`; `Y = X.R + X.S` still reports each access). Not modelled: a `Dim.X` read in
a value on CR6/CR1000X (`Y = Dim.X` is `Could not find Dim.`; we echo `Invalid symbol in expression`).
Cases `*-r14c35-d*`/`x*` in `tests/fixtures/r14_trailing_dot_members.json`.

**R14C-6, fixed.** `cr3xx._inline_owner` scans back over the statements of the same source line
instead of a fixed eight, so a one-line `If` body with more `:`-separated statements keeps its owner.

## Round 13 cloud review follow-up, R13C-3..8 (2026-10-12)

Probed on all four loggers (cases `*-r13c-*` in `tests/fixtures/r14_review_r13c.json`):

- **R13C-3** (`Include "CPU:../Lib.crb"` from a `sub/` program): compiles, as we do. Not a defect.
- **R13C-4** (an include error inside the `Scan` body, CR300/CR350): the following `Delay(1,1,Sec)`
  gets `Too many parameters:  1.` on the real compilers too. Not a defect.
- **R13C-5, fixed.** An unclosed built-in call in a condition echoes the condition alone on all
  four loggers: `If Round(X, Then` is `Invalid, or out of place expression: Round(X, .` (CR300/CR350
  printed `No Matching` with the whole line), `While Round(X` is `Expression expects , : Round(X.`
  (CR300/CR350 printed the whole line), and the one-line `If Round(X Then Y = 1` is `Expression
  expects , : Round(X .` (CR6/CR1000X printed the fallback with the whole line).
- **R13C-6, fixed.** A `TCPOpen` connection count written as a non-integer literal (`3.0`, `2.0`,
  `2.5`, `1E0`, `0.0`) is `Max number of concurrent connections must be 1..N` whatever its value,
  then `Parameter must be a constant integer: <statement>.` on CR6/CR1000X; CR300/CR350 print only
  the second line (with the line terminator). `Const N = 3.0` as the count matches already.
- **R13C-7** (`For I = 1 To 10 Step`) and **R13C-8** (`X = C.5` with `Const C`, `X = Ya.5` with an
  `Alias`): match the real compilers. Not defects.

## String literal as a constant port (2026-10-12, fuzz false accepts)

A string literal where a constant port is expected folds to INT_MIN on all four loggers, whatever
its text (`"fuzz"`, `"102"`, `"C2"`, `""`, `"1.5E2"`): `PortSet("fuzz",1)` is `Invalid port
-2147483648, use C1..C4 or U1..U12.` (CR1000X `... use C1..C8.`, CR300/CR350 `Invalid port specified
-2147483648`), `SPIOpen("x",...)` the same (CR1000X adds its SPI line), `SW12("x",0)` is `Invalid SW12
port -2147483648.` on CR6/CR350, and a CR1000X `VoltSe` channel is `Invalid channel -2147483648, must
use 1..16.` (the `abs` of INT_MIN stays negative). Modelled with the `string_int_min` flag on the
probed terminal kinds (`checker/terminals.py`) and `string_port_int_min` in `measure.json`. Not
modelled: CR6 `VoltSe(X,1,mV1000,"abc",...)` is `Invalid port, must be C1..C4 or U1..U12.`; CR300
`SW12("x",0)` is `Too many parameters:  0.`; CR1000X `SW12("x",0)` crashes the real compiler.
Cases in `tests/fixtures/r14_string_ports.json`.

## Numeric `DataType` codes in output instructions (2026-10-12, fuzz false accepts)

A constant `DataType` that is a number rather than a data-type name is accepted when it equals a
valid code and rejected otherwise with `Illegal data type parameter %d in %s instruction.` (the
value truncated, `2.5` -> 2). Sweep of `Sample(1,X,n)` for n = 0..30 on all four loggers: CR6/CR1000X
accept 2, 3, 6, 7, 9, 11, 18, 20, 24, 25, 28 (and -1, 2.5); CR300/CR350 accept 6, 7, 9, 11, 18, 20, 21,
22, 24, 25, 28, so `Sample(1,X,(1+2))` (= UINT4 on CR6) fails there. `instr_data.check_output_datatype`
reports the codes that fail with that line alone, for `Sample`, `Average` and `Maximum` (the probed
instructions): CR6/CR1000X 4, 5, 8, 10, 12, 13, 15, 19, 21, 22, 23, 26, 27; CR300/CR350 2, 3, 4, 5, 8,
10, 12, 13, 14, 15, 19, 26, 27. Like the index bounds it accumulates with pass-2 errors on CR6/CR1000X,
appears only in an otherwise clean file on CR300/CR350, and hides the duplicate-field check. Not
modelled: 0, 16, 29, 30 and values above 30 (some add `Table T declared with no output fields.`, in a
pattern not worked out: 255 prints only that line, 256 and 263 differ by family); 1, 17 (`Reps 1
illegal; with BOOL8 and UINT1 data types ...`), 14/23 (`Float to NSEC expects source array size >= 7`);
a string `DataType` crashes all four real compilers. Cases in `tests/fixtures/r14_output_datatype.json`.

## More compile-time bounds (2026-10-12, fuzz false accepts)

Oracle, all four loggers unless noted (cases in `tests/fixtures/r14_bounds_conditions.json`):

- A condition that is exactly one Float array element with literal subscripts is bounds-checked like
  an instruction argument: `If A(3) Then`, `If A(0) Then`, `ElseIf A(5) Then`, `While A(3)`,
  `Do While A(3)`, `Loop Until A(0)` -> `Variable A out of bounds.`; `If A(3) > 1 Then`, `If A(1+2)`,
  a Long array and `Y = A(3)` compile. An array declared `B()` is one element (`If B(2)` out of
  bounds on CR6/CR1000X; CR300/CR350 `Dimensions not declared: B(2) .` as before).
- Type code 24 arguments (`SDMIO16`'s second, `PortPairConfig`'s second) are bounds-checked for
  Float and Long arrays.
- `SplitStr(F(i),...,Reps,...)` into an element of a one-dimensional non-String array is out of
  bounds when `i + Reps - 1` exceeds the size, whatever the option; a String array compiles
  (`expressions.check_splitstr_element_reps`). A parenthesised dimension (`G((1+2)) As String * 10`)
  makes a Float array, so it is checked.
- `Calibrate(Dest)` needs at least 6 elements from `Dest` to the end of the array on CR6/CR1000X and
  51 on CR300/CR350, else `Variable Dest out of bounds.` (`expressions.check_calibrate_size`).

All are reported like the index bounds (with pass 2 on CR6/CR1000X, in an otherwise clean file on
CR300/CR350).

## `SlowSequence` with a `Scan` still open (2026-10-12, fuzz false accepts)

A `SlowSequence` reached while a `Scan` is open is `Missing NextScan.` on the `SlowSequence` line
(oracle, all four loggers), with no `Scan without NextScan` for it afterwards. On CR6/CR1000X it ends
pass 2 like a placement error (an earlier `Invalid symbol` stays, a later one is not reported, and a
pass-1 error hides it: `structure.check_pass2`); on CR300/CR350 it ends the late placement walk
(`check_scan_placement_late`). The `Scan never reached at run time.` warning of an earlier, unclosed
scan is still printed by the real compilers next to the error, which is not modelled when the file
has other errors (fuzz `cr6/false_accept/46663179c624`). Cases in
`tests/fixtures/r14_slowsequence_open_scan.json`.

## `SerialOpen` transmit delay and `ArgosError` destination (2026-10-12, fuzz false accepts)

`SerialOpen`'s fourth argument (`TXDelay`, µs): a constant outside 0..14000000 is `%d usec delay
before transmit is illegal, must be 0..14000000` (`-1`, `(0-1)`, `-5`, `14000001`), a variable is
`Parameter must be a constant integer: <statement>.` (CR300/CR350 with the line terminator), on all
four loggers (`cr3xx.check`, next to the baud rate). `ArgosError`'s argument must be a String variable
on CR6/CR1000X: a literal or an expression (`ArgosError("fuzz")`, `ArgosError((1+2))`) is `A string
variable destination is required` like a Float or Long variable (`var_types`). Cases in
`tests/fixtures/r14_serialopen_txdelay_argos.json`.

## `MenuPick` after a `MenuItem` naming a plain `Const` (2026-10-12, fuzz false accepts)

CR6/CR1000X (oracle; CR300/CR350 have no menus): when the preceding `MenuItem` names a `Const`
declared outside `ConstTable..EndConstTable`, every `MenuPick` for it fails. With a `ConstTable`
anywhere in the program (before or after) it is `When using a ConstTable constant in MenuPick, the
constant name must be the first parameter.`, even for `MenuPick(D,1,2)`; with none it is `Value in
preceding MenuItem must be a variable or a Const within ConstTable..EndConstTable.` (both on the
`MenuPick` line; `instr_data.check_table_and_menu_placement`). Cases in
`tests/fixtures/r14_menupick_plain_const.json`.

Not modelled: CDM port arguments (`CDM_PulsePort`, `CDM_SW12`) give `Unrecognized port!` by a
per-device port set (`CDM_A108` takes ports 1 and 2 for `CDM_PulsePort` and none for `CDM_SW12`; a
string, `0` and `-1` always fail); two fuzz false accepts stay open.

## `MoveBytes` option, CDM settling time, `InstructionTimes`, `MQTTPublishConstTable` (2026-10-12)

Oracle-probed, fuzz false accepts (cases in `tests/fixtures/r14_movebytes_cdm_insttimes_mqtt.json`):

- `MoveBytes`'s sixth argument outside 0..4 is `Invalid transfer option %d, must be 0..4.` (all
  four loggers; `instr_rules.check_option_values`).
- `CDM_TCSE` (tenth argument) and `CDM_VoltSe` (eighth) settling time, not 0 and outside 100..100000:
  `Settling time: %d, out of range (100 - 100000, or 0 for default).` (CR6/CR1000X). Other CDM
  measurements are not probed.
- `InstructionTimes(Dest)`: a Float, String or Boolean destination is `Destination variable must be
  type LONG` wherever it is; a Long one is accepted before `BeginProg` only, and after it is
  `TimesOfInstructions() must be declared before BeginProg.` (all four loggers; `var_types`).
- `MQTTPublishConstTable` outside `ConstTable..EndConstTable` (before `BeginProg` or in a `Scan`) is
  `MQTTPublishConstTable instruction must be in a ConstTable`, pass 1, and on CR300/CR350 it starts
  the `Too many parameters` cascade (`structure.check_pass1`).

## `GOESData`/`ArgosData` extra arguments, `TCPOpen` handle type on CR6/CR1000X (2026-10-12)

`GOESData` (all four loggers) and `ArgosData` (CR6/CR1000X) take five arguments; a sixth is `Too many
parameters:  <token>.` (their table-name parameter, type code 6, kept them out of the generic check).
`TCPOpen`'s fifth argument (the handle) that is a Float or String variable, a literal or an
expression is `Parameter must be a variable of type long` on CR6/CR1000X too, first on its line
(`instr_rules.check_tcpopen_handle_type`; CR300/CR350 had it). Not modelled: with a non-Long handle
and no count argument CR6/CR1000X also print `Max number of concurrent connections must be 1..1`.
Cases in `tests/fixtures/r14_goes_argos_tcpopen.json`.

## Unknown `Status.X` / `Public.X` field reads (2026-10-12)

Reading a Status field the logger does not have is `Could not find Status.<name>(N).` on all four
loggers (oracle; `N` is the first literal subscript, 1 when there is none: `Status.Nope(3,2)` ->
`(3)`), once per access, in assignment values, conditions and instruction arguments; `Public.X` for
an undeclared `X` is `Could not find Public.X(N).` the same way. The readable names differ per logger
and are not simply the SetStatus/SetSetting lists: `TimeStamp` and `Record` read everywhere,
`CardBytesFree` only on CR6/CR1000X, `RecNum` only on CR300, `CalGain(1)` not on CR300/CR350,
`IPAddressEth` not on CR350. `data/**/status_members.json` holds each logger's accepted set from an
oracle sweep of 379 candidates (every logger's SetSetting/SetStatus names, the minimal Status table,
every `Status.` name in the help corpus and `crbasic/`); a name outside it is reported
(`expressions.check_digit_members`). A valid name outside the sweep would be a false reject: extend
the sweep when one is found. `Public.TimeStamp`, `Public.Record` (generic table fields) are valid
reads. On CR6/CR1000X the lines come from `expressions.check_digit_members`; on CR300/CR350 they are
reported only in an otherwise clean file (`check_status_member_reads_late`; a later `WatchdogTimer is
not defined.` hides `Status.CardBytesFree` in `crbasic/SbdPayload_tester.CR6`). Assignment targets
(`Status.X = ...`) are unchanged. Not modelled:
CR300/CR350 `Battery(Status.Nope)` also prints `Illegal destination variable type, expecting Float.`
Cases in `tests/fixtures/r14_status_public_reads.json`.
### Arity of a one-line `If` body (fixture `r14_inline_if_arity`)

Oracle, all four loggers, 2026-10-12: the pass-1 parameter-count checks (`instr_rules.check`,
`check_too_many`) never saw the body of a one-line `If`/`Else`, so every wrong-count instruction there
was accepted: `If X Then Scan(1)` / `Scan` / `Scan(1,Sec)` / `SubScan(1)` / `Delay` / `PortSet(1)` are
`Invalid or missing parameter.`, `Scan(1,Sec,3,0,1)` is `Too many parameters:  1.`, `Delay(0,1,mSec,5)`
`... 5.`. Both passes now run over `_with_inline(program)`. A well-formed `If X Then Scan(1,Sec,3,0)`
is `IF or SELECT statement illegally has SCAN without NEXT SCAN.` (`check_block_closers`). CR300/CR350
cascade (`check_too_many_cascade`): a body is not "after" its own `If`, and a short body after an
earlier cascade trigger keeps its shortfall. Not done: inside a `Scan` the real compiler adds
`Scan without NextScan or nested Scans.` before the `IF or SELECT` line (ours prints the pair reversed on
CR6/CR1000X, only the second on CR300/CR350); `If X Then SubScan(1,uSec,3)` outside a Scan
(`SubScan used outside of Scan .. NextScan.`); the other block keywords as a body (`Do`, `While X`,
`For ...`, `Select Case`, `Case 1`: `DO without LOOP.`, `FOR without NEXT.`, `IF or SELECT without EndIf
or EndSelect.`, `Case outside Select.`; `If X Then Sub` is `Too few parameters in instruction.`) are
accepted; a short body on CR300/CR350 after an earlier undefined name loses its
`Invalid or missing parameter.` line (real keeps it).

## Round 15: one-line `ElseIf c Then <anything>` (2026-10-12)

Oracle, all four loggers (fixture `tests/fixtures/r15_oneline_elseif.json`, 15 shapes x 4): everything
after the `Then` of an `ElseIf` is ignored by the compiler. `ElseIf C Then Z = 1`, `... Then Z = Q Q Q`
(an undeclared or malformed body), `... Then CallTable T`, `... Then Scan(1,Sec,0,0)`, `... Then Then
Then`, `... Then Z = 1 Else Y = 2` all compile, so an undeclared name in the body is not reported
(`... Then Z = 1 : Y = Q` reports only the `Y = Q` after the colon, a separate statement). A body of
`EndIf` is just more ignored text (the `If` stays open: `IF or SELECT without EndIf or EndSelect.`).
The condition before `Then` is checked as always (`ElseIf Q Then Z = 1` -> `Invalid symbol in
expression: Q .` / `Q is not defined.`), a stray one-line `ElseIf` is `Case with no Select.`.
Implementation: `parser._parse_elseif` drops the tokens after `Then`. (Closes the "one-line
`ElseIf c Then Z = 1`" false reject.)

## Round 15: unclosed `Abs`-like groups beyond the flat `Abs(X` (2026-10-12)

Oracle, all four loggers unless noted (fixture `tests/fixtures/r15_abs_unclosed.json`, 81 shapes x 4;
~190 shapes probed, `parser._close_open_call` / `_close_open_extra`). Groups of `Abs`, `AbsLong`,
`ArrayLength`, `TypeOf` ("S1") and `LineNum`, `Signature` ("S2"); `E` is an expression (operands,
binary operators, closed groups):

- **Condition keywords**: in `While`, `Do While|Until`, `Loop While|Until` the first `Then` and
  everything after it is ignored: `While C Then`, `While Abs(C) Then`, `While Abs(C Then Z = 1`,
  `Do While C Then` compile (CR6/CR1000X too; the stray `Then` is not even a name on CR300/CR350).
  Other functions keep their own errors (`While Round(C Then` -> `Expression expects , : Round(C .`).
  A group still open after the cut (`While (C Then`) stays `No Matching ...` (we echo the `Then`;
  the real echo is `(C `, not modelled). `While (C` alone is a pre-existing false accept.
- **S1, one open group**: `Abs(E`, `Abs(E,` (trailing comma), `Abs(E1 E2` (two expressions side by
  side: `Abs(X Z`, `Abs(1 2`, `Abs(X + 1 Z`, `Abs(X Z + 1`) compile; `Abs(E1 E2,`, `Abs(X, Z`,
  `Abs(X,,`, `Abs(X Z W`, `Abs(,X`, `Abs(X Abs(Z` do not (`Incomplete expression: <stmt>.`, or
  `Invalid, or out of place expression` for `Abs(,X` on CR6/CR1000X). A closed call inside is just
  part of `E`: `Abs(Abs(X) Z`, `Abs(Abs(X),` compile, `Abs(Abs(X),Z` does not. On CR6/CR1000X the
  second expression is not resolved at all (`Abs(X Q`, undeclared `Q`, compiles); CR300/CR350 name
  it (`Q is not defined.`, also for `Abs(X Then`) -- not modelled, we drop it (false accept).
- **S1 nested**: `Abs(Abs(X`, `Abs(Abs(Abs(X`, `Abs(X + Abs(Z`, `Abs(LineNum(X`, `Abs(Signature(`,
  `AbsLong(Abs(X`, `TypeOf(LineNum(X`, `ArrayLength(Abs(X` compile (also with the trailing comma or
  second expression of the innermost group: `Abs(Abs(X Z,`, in `If`/`While`/`Select Case`
  conditions too: `If Abs(Abs(C Then Y = 1`, `While Abs(Abs(C Then`); `Abs(Abs(`, `Abs(X Abs(`,
  `Abs((X`, `(Abs(X`, `Round(Abs(X` (`Expression expects ,`), `Abs(Round(X` do not; an S2 call that
  contains an S1/S2 one (`LineNum(Abs(X`, `LineNum(LineNum(X`, `Signature(Abs(`) is
  `Incomplete expression` (messages only; they already were rejected).
- **S2 empty**: `LineNum(`, `Signature(`, `LineNum(,`, `1 + LineNum(`, `Abs(LineNum(` compile
  (`Y = Abs(` and `Y = Abs(Abs(` stay `Invalid, or out of place expression`); `LineNum( + 1`,
  `LineNum(X,`, `LineNum(X) + LineNum(` are `Incomplete expression` (`LineNum( + 1` is a
  pre-existing false accept).
- **`LineNum(X)` closes the group around it**: `Y = A(LineNum(X)`, `Y = Sin(LineNum(X)`,
  `Y = (LineNum(X)`, `Y = A(1 + LineNum(X)`, `Y = A(LineNum(X) + 1`, `Y = A(Signature(X)` compile
  (one open group, one call, no comma, no juxtaposed operand), and the statement
  `A(LineNum(X) = 1` (also `= Y`, `= Z + 1`, `Signature`) compiles; `A(LineNum(X))` / `A(LineNum(X)) = 1` /
  `Y = A(LineNum(X)) ` (properly closed!) and `A(LineNum(X) + 1 = 1` do not (the last is `Invalid
  assignment in expression`). The same with `Abs` instead of `LineNum` is `No Matching ...` /
  `Invalid assignment in expression`. Implemented by closing the group at the end (or, for the
  statement form, reading it as `A(1) = ...`).
- Message-only differences left (both sides reject): `If Abs(X, 1 Then Y = 1` is
  `Incomplete expression: Abs(X, 1 .` (condition echo), `A(Abs(X) = 1`, `A(Sin(X) = 1`, `A(1 = 1`,
  `A(X = 1` are `Invalid assignment in expression: <stmt>.` on CR6/CR1000X, `Y = Abs(X, Z` and
  `Y = Abs(X,Z,Z` are `Incomplete expression`.

## Round 15: block keywords in a one-line `If` body (2026-10-12)

Oracle, all four loggers (fixture `tests/fixtures/r15_inline_if_block_words.json`, 40 shapes x 4):
`Do`, `For`, `Select Case`, `While` as the body (or `Else` body) of a one-line `If` open their block like
a statement of their own, and `Loop`, `Next`, `EndSelect`, `End Select`, `Wend` there close one:
`If C Then Do` ... `Loop` (also `Loop Until X`, `Do While X`), `If C Then Y = 1 Else For Z = 1 To 2` ...
`Next`, `If C Then Select Case X` ... `EndSelect`, `Do` ... `If C Then Loop`, `For` ... `If C Then Next`,
`If C Then Do Else Y = 1` ... `Loop` all compile (false rejects before); with no partner they give the
usual `DO without LOOP.` / `FOR without NEXT.` / `IF or SELECT without EndIf or EndSelect.` /
`LOOP without DO.` / `NEXT without FOR.` (`SubScan used outside of Scan .. NextScan.` for a `SubScan` outside a
`Scan`: a real reject we already give for the plain statement; `If C Then SubScan(...)` inside a `Scan`
behaves like the plain one). A `SubScan` with no measurement (`No Measurements in SubScan.`, CR300
`SequentialMode SubScan interval must be > 1 mS.`) is not modelled in either form.
Implementation: `parser._hoist_block_statements` moves such body statements out of the `If` behind it.
Still false accepts (checked, pre-existing, not block-keyword hoisting): `While X` without `Wend` and a
stray `Wend`, `If C Then Case 1` outside a `Select`, `If C Then Exit Do` outside a loop, `If C Then Scan(...)`
(`IF or SELECT statement illegally has SCAN without NEXT SCAN.`).
## Round 15: `Call` name with spaces, `Call S(a,b,` with a trailing comma

- Spaces and tabs inside the Sub name of a `Call` are dropped: `Call r  un(A,B,C`, `Call ru n (A,B,C)`, `Call R UN(...)`,
  `Call o ther` call `run`/`other` (all four loggers; `Call T ab1` and `Call n ope(A)` already gave `Tab1`/`nope is not
  declared as a Subroutine.`). A bare `r un(A,B,C)` is NOT merged (`Invalid, or out of place expression`). The parser merges
  consecutive identifier tokens after `Call` when the run ends at `(` or at the end of the line (`Parser._parse_call`).
- A `Call S(a,b,c,` or bare `S(a,b,c,` whose parenthesis is never closed does not count the empty slot after the last
  comma: it compiles for a 3-parameter Sub, `S(a,b,c,,` counts 4 arguments. Closed `S(a,b,c,)` counts 4 as before.
- Left open (message diffs): the number of `Call argument required.` lines (`Call run(A` two, `Call run(` and `Call run` three, we print one)
  and an undeclared name in an unclosed `Call run(A,B,C,D` (`Invalid symbol in expression: D |.` / `D is not defined.` precede the count line).
- R15, `tests/fixtures/r15_call_name_spaces.json` (audited 0 differing), fuzz false reject `cr1000x/false_reject/f4932fe73ffd`.

### Round 15: `never reached at run time` after a `Scan` before `BeginProg`, and `Expr` for a bare non-Sub name (CR6, CR1000X)

- A `Scan(...,0)` that comes before `BeginProg` (`Scan illegally occurs before BeginProg.`) does not start dead code:
  `Scan (1,min,3,0)` / `NextScan` / `Foo` (also with `x = 1`, `Delay`, `CallTable`, `EndProg_Mut`, a later
  `BeginProg`) gives only the placement error. CR300/CR350 already behaved like this because of their other errors. 11
  `Warning: Call never reached` reproducers (`EndProg_Mut` after `NextScan`, e.g. `cr1000x/message_diff/48a8799dad3c`).
- After a real endless `Scan` a bare statement that is no declared Sub/Function is reported `Expr never reached at run time.`
  (`Nope`, `Nope(x)`, `Nope x`, `Abs(x)`), after its `Invalid, or out of place expression`; a bare declared variable
  (`x`, `x(1)`) gets that error and no warning. Declared Subs keep `Call never reached`.
- R15, `tests/fixtures/r15_unreachable_warning.json` (CR6/CR1000X only, audited 0 differing). Left on CR300/CR350
  (pre-existing, other mechanism): `Abs(x)` is `Expr` there, `Foo(1)` (wrong argument count) and `Nope.x` still print the
  warning, and `Foo` / `BeginProg` (second) loses it.

## Round 15: `While`/`Wend` are `Do`/`Loop` for block balance (2026-10-12)

Oracle, all four loggers (fixture `tests/fixtures/r15_while_wend.json`, 18 shapes x 4): `While` opens and
`Wend` closes the same block as `Do`/`Loop`, and the two pairs mix freely: `Do` ... `Wend` and `While X`
... `Loop` compile, `While X` alone is `DO without LOOP.` (also `While X : Y = 1`, `Do While X` ... `Wend`
with no partner after another `Wend`), a stray `Wend` is `LOOP statement illegally has SCAN without NEXT
SCAN.` + `LOOP without DO.` inside a `Scan` (`LOOP without DO.` outside), nested `While`/`Do` mix
(`While X` / `Do` / `Wend` / `Loop` compiles). Implementation: `structure.check_block_closers` and
`check_end_blocks` map `WHILE`->`DO`, `WEND`->`LOOP` (`_DO_ALIASES`; the former skip of programs using
`While` is gone) and `_unparsed_opener` knows an unparsable `While` condition still opens its block.
Message-only differences left (both sides reject, same as the `Do`/`Loop` forms): `If C Then Wend` / `If C
Then Loop` order (real: `LOOP without DO.` then `IF or SELECT statement illegally has SCAN ...`),
`While X` / `EndIf` on CR300/CR350 (`END IF without IF ...` only), a stray `Wend`/`Loop` inside a `Scan`
that is then closed (the real compiler adds `Warning: CallTable never reached at run time.`; CR300/CR350
also keep the two closer lines), `While` alone (`Invalid or missing expression.`, we say `... parameter.`),
`Wend X` on CR300/CR350 (`Too many parameters:  X.`).

## Round 15: stray `Case`, `Exit`, `EndSub` and `Sub` as the body of a one-line `If` (2026-10-12)

Oracle, all four loggers (fixture `tests/fixtures/r15_inline_if_stray.json`, 48 shapes x 4): the body (or
`Else` body) of a one-line `If` is checked like the plain statement. `If C Then Case 1` (also `... Else Case 1`,
`... Case 1 : Y = 2`) outside a `Select` is `Case outside Select.` (inside one it compiles); `If C Then Exit Do`
/ `Exit For` with no open loop of that kind is `Exit Do without DO.` / `Exit For without FOR.` (`Do` ... `If C
Then Exit For` ... `Loop` is still rejected, `While` counts as a `Do` loop); `If C Then Exit Sub|Function`
outside a routine is `Exit Sub without Sub or nested subs.`, `If C Then EndSub` is `EndSub without Sub or nested
subs.`; `If C Then Sub S` / `Function F` anywhere between `BeginProg` and `EndProg` (also after the last
`NextScan`, after `SlowSequence`) is `Sub declaration inside If..EndIf.` + `Sub|Function is declared in the
middle of an executing sequence` (inside another `Sub`, `Sub declared within a Sub.` first). Before `BeginProg`
the `If` itself is the error (`If illegally occurs before BeginProg.`; `Case outside Select.` still wins on
CR6/CR1000X). Implementation: `check_pass1` (inline `Case`), `stray_controls` (inline `Exit`/`EndSub`, and
`While`/`Wend` now count as `Do`/`Loop` loops), `first_misplaced_sub` (anchors on the `If`).
Message-only differences left: CR300/CR350 `If C Then Case 1` before `BeginProg` / after `SlowSequence` (the plain
`Case 1` has the same extra `Too many parameters:  Sec.` from the next `Scan` line), `Sub S` / `If C Then EndSub` inside
a `Sub` (`If without EndIf inside of Sub.`).

## Round 15: `SubScan` rules and `NextScan`/`SubScan`/`NextSubScan` in a one-line `If` body (2026-10-12)

Oracle, all four loggers unless noted (fixture `tests/fixtures/r15_subscan_nextscan.json`, 276 cases; shapes
whose message differs are left out, see the last item):

- **Hoisting** (extends `parser._INLINE_BLOCK_WORDS`): `NextScan`, `SubScan`, `NextSubScan` in a one-line `If`
  body (or its `Else` body) open and close their block like statements of their own. `Scan` ... `If C Then
  NextScan` closes the `Scan` (a false reject before: `Scan without NextScan.`); `If C Then SubScan(1,Sec,1)`
  ... `NextSubScan` and `SubScan` ... `If C Then NextSubScan` compile. With an endless `Scan(...,0)` the real
  compiler warns `EndIf never reached at run time.` at the `If`'s line (the `If`'s own end follows the
  `NextScan`); when more text follows the `NextScan` on that line (`If C Then NextScan : Y = 1`) there is no
  warning at all, and later statements are not flagged either (`warnings._check_reachability`, hoisted
  statements carry `hoisted_last`). A finite `Scan(1,Sec,0,10)` gives no warning. `If C Then NextScan` with no
  `Scan` is `NextScan with no Scan.`. The CR300/CR350 cascade (`check_too_many_cascade`) now treats an `If` whose
  body was hoisted out as a one-line `If` (its shortfall line stays with the hoisted `SubScan(1)`;
  `r14_inline_if_arity` `ar-6-ss1` green again).
- **`SubScan used outside of Scan .. NextScan.`**: a `SubScan` (valid arguments) with no open `Scan`, or in a
  `Sub`/`Function` body, e.g. after the last `NextScan`, also `If C Then SubScan(1,Sec,1)`. A placement error:
  it ends pass 2 (a later `Y = Q` is not reported on CR6/CR1000X; on CR300/CR350 the pass-1 `Q is not
  defined.` wins) and the `SubScan never reached` warning is not printed. (The earlier note in this file that
  we gave this message for the plain statement was wrong: it was never implemented.)
- **`Cannot Nest SubScans.`** at the inner `SubScan`; compilation continues, the inner block is a real
  block (its `NextSubScan` closes it, no `Next SubScan without SubScan.`).
- **`No Measurements in SubScan.`** CR6/CR1000X only (CR300/CR350 accept), at the `NextSubScan` line, for a
  `SubScan` that adds no time of its own (a nested one's time does not count: the outer block is reported). It
  accumulates with other errors in the body (`Y = Q` -> `Invalid symbol ...` then this line). Zero-time bodies
  observed: assignments, `CallTable` (a `SubScan` holding only `CallTable` is reported), `SerialIn`,
  `Delay(1,1,mSec)` (option 1; `Delay(0,1,mSec)` counts as a measurement), `Battery` on the CR6 (the CR1000X
  accepts it); `PanelTemp`, `VoltDiff`, an `If C Then PanelTemp(...)` or a `For` around it are measurements.
  Implementation `structure.subscan_controls` (merged into `stray_controls`' stops in `check_stray_controls` and
  `check_scan_placement_late`): reported only when every statement of the body is in the observed zero-time
  set; any other instruction, `Call` or unparsed statement suppresses the message (false accepts remain for
  those, e.g. `Sample` inside a `SubScan`, which the real compiler also counts as zero).
- Message-only differences left (both sides reject; pruned from the fixture): a one-line `If C Then
  Scan(...)`: the real order is `Scan without NextScan or nested Scans.` then `IF or SELECT statement
  illegally has SCAN without NEXT SCAN.` (ours: reversed on CR6/CR1000X, only the second on CR300/CR350), and
  with a following `NextScan`, CR300/CR350 say `NextScan with no Scan.`; a body line after it, `If C Then Y = 1`
  while that `If` is open, adds a second `IF ... illegally has SCAN` in the real compiler; `Sample` in a
  `SubScan` outside a table (`... No Measurements in SubScan.` after the Sample error).

## Round 15: unclosed `Abs`-like groups, side-by-side expressions per nesting level (2026-10-12)

Oracle, all four loggers (fixture `tests/fixtures/r15_abs_unclosed_second.json`, 61 shapes x 4 minus the
message-only pairs): follow-up of "unclosed `Abs`-like groups beyond the flat `Abs(X`".

- **`n` open `Abs`-family groups take up to `n + 1` side-by-side expressions, or `n` before a trailing
  comma**: `Abs(X Z` (n=1), `Abs(Abs(X Z Z`, `Abs(Abs(1 2 3`, `Abs(Abs(X + 1 Z Z`, `Abs(X + Abs(Z Z Z`
  (n=2: three), `Abs(Abs(Abs(X Z Z Z` (n=3: four) compile; one more does not (`Abs(Abs(Abs(X Z Z Z Z`,
  `Abs(X Z Z`); `Abs(X,` | `Abs(Abs(X Z,` compile, `Abs(X Z,` | `Abs(Abs(X Z Z,` do not. Before this only two
  expressions were accepted (`Abs(Abs(X Z Z` was a false reject). A side-by-side expression that is a call (`Abs(X
  Q(1)`, `Abs(X Sin(Q)`, `Abs(Abs(X Z Abs(Z)`) is `Incomplete expression: <stmt>.` on all four loggers (a false
  accept before).
- **CR300/CR350 resolve the names of the dropped expressions**: `Y = Abs(X Q`, `Abs(X Then`, `If Abs(X Q Then`
  `Y = Abs(X Q + 1`, `Y = Abs(Abs(X Z Q`, `While Abs(X Q` give `Q is not defined.` / `Then is not defined.` (also
  `Else`), the loop-block error of the `While` is then hidden. CR6/CR1000X do not resolve the first token of a
  dropped expression (`Y = Abs(X Q` compiles), but they do the names after an operator: `Y = Abs(X Z + Q`,
  `Y = Abs(X Q + R` are `Invalid symbol in expression: <stmt>.` -- not modelled (false accept, CR6/CR1000X).
  A dotted name (`Abs(X Q.R`) and numbers/strings are never reported. Implementation: `parser._Closed`
  carries the dropped tokens, the statement gets `dropped_tail`, `cr3xx.check_dropped_tail` reports them.
- Message-only differences left: when the group is rejected, CR300/CR350 still print `Q is not defined.` ahead
  of `Incomplete expression:` for the second expression (`Y = Abs(X Q,`, `Y = Abs(X Q) `, `Y = Abs(X Q Then`,
  `Y = Abs(X Q(1)`, `Y = LineNum(X Q`); `Y = Abs(X (Q)` adds `Dimensions not declared:`.


## Round 15 message-diff families (oracle, 2026-10-12)

### CR300/CR350: a table-only instruction outside a table does not resolve its source (`tests/fixtures/r15_table_only_undefined_source.json`)

`Sample(1,Tangent,FP2)`, `Average(1,Tangent,FP2,False)`, `Maximum(...)`, `Totalize(...)` outside any
`DataTable ... EndTable`, with `Tangent` undeclared, give only `<Instruction> must be used inside a table
declaration.` on all four loggers; CR300/CR350 no longer print `Tangent is not defined.` first (CR6/CR1000X already
did not). Several statements each get their placement line, `EndTable without DataTable.` follows as before, and an
earlier error elsewhere (`x = Nope`) still hides the placement lines and leaves `Tangent` unreported (the existing
`Too many parameters:  Tangent.` cascade line on CR300/CR350 is unchanged). Inside a table the undefined source is
still `Tangent is not defined.`. `instr_data._check_stage2a` reports an undefined table source on CR300/CR350 only
inside a `DataTable` body.

### The reachability warning and the walk-ending `Could not find DataTable` (oracle, 2026-10-12; `tests/fixtures/r15_unreached_and_unknown_table_walk.json`)

`Warning: <X> never reached at run time.` (the statement after an infinite `Scan(...,0)` .. `NextScan`) is printed by the
pass-2 walk, so:

- CR6/CR1000X: any pass-1 error (declaration errors, `Too many parameters`, an arity or parse error such as
  `x = Sin(1,2)`) hides it, wherever the error is; so does a stage-2a error (`Battery(3)`), also when it comes after the
  dead statement. An `Invalid symbol in expression`, a held `Status.TimeStamp(1,1,1)` index error and a nested `Scan`
  do not.
- Errors that end the walk (`Scan illegally occurs before BeginProg.`, an unknown `CallTable`) cut the warnings behind
  them, and a dead `CallTable` of an unknown table prints its `Could not find DataTable X.` without the warning of its own
  (all four loggers).
- CR300/CR350: an unknown `CallTable` ends the walk like `GetRecord`/`ResetTable` of a missing table, so a later
  `NextScan with no Scan.`/`EndSub without Sub ...` is not printed; in the other order (a stray `NextScan` first) the
  `Could not find DataTable X.` of the later statement is not printed. `Could not find` and a placement error: the
  textually first one wins.
- Not modelled: a nested `Scan` still loses the warning (`Scan without NextScan or nested Scans.` then the warning).

### CR300/CR350: an argument with adjacent operands in any position of an instruction call (oracle, 2026-10-12; `tests/fixtures/r15_cr3xx_call_adjacent_arg.json`)

Found with a sweep of every CR300 instruction at every parameter position (`Instr(valid, x y, valid)`; CR350 identical).
`Instr(a, b c, d)` reads the arguments before the bad one normally, looks up the first two operands of the bad argument
(`Zork Zork2 Zork3` names two, `x Zork Zork` one), prints `Incomplete expression: <argument>.` (leading blanks dropped,
trailing ones kept before a comma) and then, by the parameter's type code:

- constant-only parameters (codes 1, 5, 20, 38, 39): `Variable illegal in Parameter: <argument>.` when one of those two
  operands is a variable or an undefined name (`PanelTemp(x,1 x)`, `Abs(x) 2`; not `1 2`, `1 2 x`, `K 2`, `True 2`,
  `2 Abs(1)`, `Pi 2` is an undefined name here); never `Too many parameters`;
- every other resolved parameter (codes 2, 8, 24-29, 33, 40, 41, 47, 48): `Too many parameters:  <first token of the next
  argument>.` when an argument follows (`"a b"` gives `"a`, `Sin(x)` `Sin`, `x+1` `x+1`), else nothing;
- the arguments after it are never read; an earlier argument with an undefined name or adjacent operands of its own is the
  existing abort/cascade path (not this shape).

Not modelled: the deferred source parameter of a table instruction (code 11, `Sample (1,Str  ing,String)`), name
parameters (code 6), `)` closing early, and the same statements on CR6/CR1000X (`Move(x,x y,` unterminated, `PanelTemp(x,1 2 x)`
differ there). `cr3xx._call_adjacent_arg_messages`.

### CR300/CR350: `Variable illegal in Parameter` hides the shortfall (oracle, 2026-10-12; same fixture)

`Sample(x,FP2)` (the `Reps` argument dropped), `Delay(y)`, `VoltDiff(x,y)`, `Average(x,y,FP2)` print only `Variable illegal in
Parameter: x.`, not `Invalid or missing parameter.`/`Too few parameters in instruction.` first (CR6/CR1000X already so),
and, like an undefined name, it hides the shortfalls of the later statements too (`Delay(y)` then `PanelTemp(x)`; the
earlier one is kept). Implemented by `drop_shortfall_with_names` counting these lines as names.

### CR300/CR350: adjacent operands in a table source (code 11), inside a table (oracle, 2026-10-12; `tests/fixtures/r15_cr3xx_table_source_adjacent.json`)

`Sample (1,ReadD  est2,String)`, `Average (1,Zork Q,FP2,False)`, `Maximum`, `Minimum`, `Totalize`, `StdDev` in a `DataTable`
body with adjacent operands in the source print the source's undefined words and `Incomplete expression: <source>.`, nothing
else (no `Variable illegal`, no `Too many parameters`); a shortfall (`Sample (1,x y)`) prints only the shortfall message.
The deferred source and the table name are not looked at while the earlier arguments are checked, so
`Sample (1,StringR,Str  ing)` (undefined source, adjacent third argument) gives the third argument's lines only
(`Str`, `ing`, `Incomplete expression`, `Variable illegal in Parameter`). Outside a table the placement message alone is
printed (not modelled for the unparsed shape: `Sample (1,x y,String)` outside a table still prints our fallback);
`SampleMaxMin` and `Moment` add their own earlier messages (not modelled).

### CR300/CR350: an assignment to or from an over-indexed array with undefined subscripts (oracle, 2026-10-12; `tests/fixtures/r15_assign_over_indexed_names.json`)

For `Public R(4), Q(3,3)`: `R(4,Zork) = 1`, `R(Zork,1) = x`, `Q(1,Zork,2) = 1`, `x(1,Zork) = 1` (more subscripts than
dimensions, an undefined name among them) print the undefined names and then `Invalid, or out of place expression:
<statement>.`; the value is not read (`R(4,Zork) = Zork2` names `Zork` only). A scalar `String` takes up to three subscripts
(`s(1,Zork,2) = "a"` names only `Zork`). The read form `x = R(4,Zork)` prints the names and then `Dimensions not declared:
<statement>.`, and the names stop after the first over-indexed subscript (`x = R(4,Zork) + R(5,Zork2)` names `Zork`).
CR6/CR1000X: unchanged (the generic fallback / `Invalid symbol`). `structure._over_indexed_target`,
`expressions._names_through_over_index`.

### A continuation line at the end of the file (oracle, 2026-10-12; `tests/fixtures/r15_eof_continuation.json`)

A statement whose last physical line ends with ` _` and is the last line of the file (with or without a final newline) is not
read at all, on all four loggers: `Public x` / `x = Nope + _` gives only `line 2: No BeginProg in Program.`, and
`BeginProg` / `x = 1 + _` compiles. The line still counts for the end-of-program messages (`line 2`, the file's last line).
A further line after the `_` (even an empty one) is joined as before and the statement is read, reported on that line.
`source.load_text`; `tests/lsp/test_positions.py` no longer expects the underscore to be dropped from a kept statement.

### Adjacent operands inside a built-in call's arguments, all four loggers (oracle, 2026-10-12; `tests/fixtures/r15_call_adjacent_operands.json`)

`x = Sin(x y)`, `x = Round(x y, 1)`, `x = Left(s, 1 2)`, `x = IIF(x y, 1, 2)`, `x = FileClose(x y)` and the same calls as
statements (`Sin(x y)`, `SerialOut(ComUSB, x y, "", 0, 0)`) print `Expression expects , : <statement>.` when the argument with
the adjacent operands is not the function's last (`nargs` of the function), else `No Matching, or out of place ) in
expression: <statement>.`; the statement is echoed whole, as for the existing call-statement rule (28 function shapes
compared on all four loggers). The self-closing calls (`Abs`, `AbsLong`, `ArrayLength`, `TypeOf`, `LineNum`, `Signature`)
keep `Incomplete expression: <statement>.` (also as a CR300/CR350 call statement). With an undefined name in the argument:
CR6/CR1000X `Invalid symbol in expression: <statement>.` also for the call statement (`Sin(Zork Y)`; ours printed the
generic fallback); CR300/CR350 name it first (`x = Sin(Zork Y)`: `Zork is not defined.` then the message).
`structure._scan_expr` (the `expects_comma` verdict, per-parenthesis function name and argument counter),
`cr3xx.check_unknown_line_words`. Not modelled: a user `Function`/array named like the call, `x = Sqrt(x y)` on CR300/CR350
(`Sqrt` is no function there: `Dimensions not declared`), `TimeIntoInterval`'s truncation warnings.

### `Attempt to initialize variable out of bounds.` with an illegal dimension (oracle, 2026-10-12; `tests/fixtures/r15_init_bounds_illegal_dims.json`)

After `Illegally dimension: <declaration>` the initializer is still counted: an illegal dimension (zero, negative, a string)
counts as 1 elements, and `Warning: Attempt to initialize variable out of bounds.` follows when the list is longer than the
product (`Dim X(3,-1) = {1,...,9}`, `Public X(0) = {1,2}`, `Public X("fuzz") = {1,2}`, `Dim X(-1,-1) = {1,2}`); no warning for
`Dim X(-1) = {5}`, `Dim X(3,-1) As Long = {1,2,3}` or a huge dimension (`X(999999999999,3)`). All four loggers.
`structure._check_init_bounds`. Not modelled: an illegal dimension on a later variable of a multi-variable `Dim` echoes only
`Dim Y = 3` on the real compilers.

### FieldCal `KnownVar and MeasVar must be declared the same size` for two arrays of different sizes (oracle, 2026-10-12; `tests/fixtures/r16_fieldcal_size.json`)

Review r15-cloud R15C-1. `FieldCal(Option,MeasVar,...,KnownVar,...)` with `V(4)` and `K(2)` fails with
`K and V must be declared the same size.` on all four loggers for options 1, 2 and 3, whether spelled by element
(`V(1)`/`K(1)`), whole (`V()`/`K()`) or mixed; the comparison is on declarations, not on the elements used. No code change
(`instr_rules.check_fieldcal_variable_args` already agrees).

### EOF continuation inside an included file (oracle, 2026-10-12; `tests/fixtures/r16_include_eof_continuation.json`)

Review r15-cloud R15C-2. A ` _` on the last line of an included file (`Public LibV _`, with or without a trailing newline or
CRLF) drops that statement exactly as in the main file: `LibV` is then undefined in the main program (CR6/CR1000X
`Invalid symbol in expression`, CR300/CR350 `LibV is not defined.`). A blank line after the continuation joins it to the empty
line and the declaration survives; a continuation in the middle of the file swallows the next line
(`<Lib.crb> line 2: Invalid Variable declaration Public.`). All four loggers; no code change (`source._Loader` already agrees).

### Unclosed one-argument built-in group around `LineNum`/`Signature` (oracle, 2026-10-12; `tests/fixtures/r16_abs_unclosed_builtin.json`)

Review r15-cloud R15C-3. `Y = F(LineNum(X) + 1` compiles for every one-argument built-in `F` probed (`Sqr`, `Log`, `Ln`, `Log10`,
`Exp`, `Int`, `Fix`, `Frac`, `Sgn`, `Cos`, `Tan`, `ATN`, `Len`, `Floor`, `Ceiling`, `Abs`, also around `Signature`), with or
without the trailing `+ 1`. Multi-argument `Round`/`Mid` keep `Expression expects ,`, a non-function name (`A`, `ArcSin`) is
undefined, and `Sqr(Sqr(LineNum(X) + 1` stays `No Matching, or out of place ) in expression`. All four loggers; the
`parser._close_open_extra` rule is therefore not a false accept for these. No code change.
## Round 16 message-diff families (2026-10-13)

### Table names: over 20 characters, operator expressions, and what a bad name does not hide (fixture `tests/fixtures/r16_table_names.json`)

Follow-up to "Table names starting with a digit" (all four loggers, oracle 2026-10-12, 116 cases).
- A `DataTable` name (bare, or a string without spaces) longer than 20 characters is `DataTable name X is N chars too
  long.` (`N` = length - 20) at the `DataTable` line; it wins over the digit messages (`123456789012345678901`,
  `1234567890123456789.1`). Same model as the digit message: CR6/CR1000X one per table, a pass-1 error hides it;
  CR300/CR350 only the first (`instr_data.finalize_illegal_table_names`). The table is registered under the name cut to 20
  characters (`Table Abcdefghijklmnopqrst declared with no output fields.`; `CallTable <full name>` is then `Could not
  find DataTable <full name>.`). Not done: that renaming of later references (ours still resolves the full name).
- A digit-led operator expression is read as raw text: `DataTable((1+2),...)`, `(5-2)`, `(2*3)`, `(6/2)`, `(2^3)`,
  `(1+2+3)`, `(1+x)` and unparenthesised `1+2` are `Table name 1+2 illegal, starts with a number.` (parentheses dropped).
  Spaces or inner parentheses (`( 1 + 2 )`, `((1+2))`, `2*(1+2)`, `(1 MOD 2)`) give unrelated `Too many parameters` /
  `Invalid, or out of place expression` lines: not done. `(x+2)` is a legal table named `x+2`.
- CR6/CR1000X: the table-name message does not gate the tier-C checks. A bad port/channel, `Sample must be used inside a
  table declaration.` and `Table T declared with no output fields.` (also for the illegal table itself, with the truncated
  name) are printed after it (`checker/__init__.py` gate, `instr_data.is_table_name_diag`). On CR300/CR350 the name error
  replaces those messages and `No BeginProg in Program.` too (they are `late` in `finalize_illegal_table_names`; the
  cr350 `Invalid port specified N` is treated alike).

### `GOESField` outside a table or without a `GOESTable` (fixture `tests/fixtures/r16_goes_field.json`)

All four loggers (oracle 2026-10-13, 72 cases):
- `GOESField(...)` outside `DataTable..EndTable` is `GoesTable must be used inside a table declaration.` (the
  `GOESTable` text, same entry as `TABLE_ONLY_INSTRUCTIONS`); it also replaces `No BeginProg in Program.` in a fragment.
- Inside a table with no earlier `GOESTable`: `GoesTable not active.` at the `GOESField` line, and at the `EndTable` line
  one `GoesField declared without a GoesTable declaration.` per such `GOESField`. CR6/CR1000X forget the `GOESTable` at
  each new `DataTable`; CR300/CR350 keep it for the rest of the file (one probe: a second table without it is clean).
  A table whose only fields are `GOESField`s gets just `Table T declared with no output fields.`
- `GOESTable` alone does not produce a field: `DataTable..GOESTable(..)..EndTable` is `Table T declared with no output
  fields.` (it was in `_FIELD_PRODUCING_INSTRUCTIONS`). A `GOESField` before the table's `GOESTable` crashes the real
  compiler (exit 5), no verdict. Implemented in `instr_data.check_table_structure`.

### `ApplyAndRestartSequence` bookkeeping (fixture `tests/fixtures/r16_apply_restart_sequence.json`)

All four loggers (oracle 2026-10-13, 140 cases). `EndApplyAndRestartSequence` before any `ApplyAndRestartSequence` is
`ApplyAndRestartSequence not declared` (each one; a second `End` after a declared `Apply` is accepted); a second `Apply`
(closed or not, also in `BeginProg`) is `ApplyAndRestartSequence already declared`; a first `Apply` in executable code
(contexts of `first_misplaced_sub`: Sub/Function, DataTable, BeginProg before the first Scan, Scan, If/For/Do) is
`... is declared in the middle of an executing sequence` and ends the compile there. They are tier-C-like: hidden by
pass-1 errors and by CR300/CR350 name errors, printed in line order with other tier-C findings, they replace
`No BeginProg in Program.` and the unclosed-block summary. `structure.check_apply_restart_sequence`. Not done: the
instructions with an argument (`Too many parameters:  1.`).
## Auto-allocation placement: which sizes, and AcceptDataRecords (round 16)

`Cannot autoallocate memory for DataTable T called outside of Scan, e.g., called by Sub.` (docs
above) applies only to a table the logger really auto-sizes: on CR300/CR350 size `-1` (a `-1000` is
a fixed 1000 and compiled with the message before: false reject, fixed), on CR6/CR1000X every
negative or out-of-range size (`tables.declared_size(...) is None`).

`AcceptDataRecords(addr, table_no, Table)` outside every `Scan` (before, after, in a `Sub`, in a
literal-zero `Scan`) gets the same message at the statement for an auto-sized `Table`, on all four
loggers (oracle 2026-10-13); and a `Table` with a `DataInterval` whose Lapses is not 0 (default
10; also `DataInterval(0,0,..)`) gets `Data Table cannot be interval driven.` at the statement
whether it is inside a `Scan` or not (after the first message when both apply). Lapses 0 and
fixed tables are fine. Fixture `tests/fixtures/r16_accept_autoalloc.json`. Known difference: an
`AcceptDataRecords` after the last `NextScan` also prints `AcceptDataRecords never reached at run
time.` here; the real compiler prints no such warning for it (reachability layer, not fixed).

## Round 16 (fuzz seeds 409-412, 2026-10-13)

### `Delay` interval outside the Long range (oracle, 2026-10-13; `tests/fixtures/r16_delay_long_interval.json`)

CR6/CR1000X: a constant `Delay` interval of 2^31 or more (`Delay(0,999999999999,Sec)`, `Delay(0,2147483648,min)`,
`Delay(0,3600000000,uSec)`) draws no `Measurement Delay Exceeds 60 min.` and no `Scan interval too short`; the delay is not
counted at all (a false reject, `cr6/false_reject/7ee5e9c9ebee`). Up to `2147483647` the old rules hold (and a few values just
below, e.g. `2147483647 uSec`, give `Program too big for task memory` instead, not modelled). CR300/CR350 differ again (a huge
`mSec`/`uSec` interval is `Max Delay is 24 Hours` there, not modelled). `instr_measure._delay_interval_us`,
`scan_time.instruction_units`.

### `Public x As String * 16(5) = {...}`: dimension after the string size (oracle, 2026-10-13; `tests/fixtures/r16_string_size_dims.json`)

The dimension may follow the size of a `String` declaration, with an initializer and with more than one dimension
(`Public x As String * 16(5) = {"a","b"}`, `String * 16(5,2)`); `{...}` longer than the product gives `Warning: Attempt to
initialize variable out of bounds.`. All four loggers. Before, the size and initializer were parsed as one expression and
rejected with `Invalid symbol in expression` (`cr6/false_reject` of seed 410). Only a numeric size is split this way
(`parser._parse_vardecl`). Left open (false accepts): `Public x(3) As String * 16(5)` is `Incomplete expression: 16(5).`,
`= { }` is `Variable initialization of x has bad syntax.`.

### `Units A  B = ms`: words after the first target are ignored (oracle, 2026-10-13; `tests/fixtures/r16_units_extra_words.json`)

Everything between the first `Units` target and the `=` is ignored when the target is a name or indexed name: `Units PP  S = ms`,
`Units PPS + 1 = ms`, `Units PPS As Long = ms`, `Units PPS) = ms` compile on all four loggers (`cr1000x/false_reject` of seed
412 via `Units PP  S`, an inserted-space mutant). `Units A, B = ms` checks every name (`Can't assign units to B, ...`), and a
trailing comma is ignored. Not modelled: `Units 3 Q = ms` (`Can't assign units to 3`), `Units = ms` (`Missing or illegal
variable name in Units declaration.`), duplicates in one list (`PPS already assigned Units ms.` twice). `parser._parse_units_segment`,
`instr_data.check_units_targets`.

### Undefined names in an unclosed `Sub` call (all four loggers, round 16)

Fixture `r16_call_unclosed_undefined.json` (`structure._report_call_arg_names`). `Call Sub(A,B,Zork` with the `)` missing at the
end of the line compiles when every name resolves; an undefined name in it is reported exactly as in the closed call
(CR6/CR1000X `Invalid symbol in expression: <arg>.`, CR300/CR350 `Zork is not defined.`), the last argument echoed like a
tail piece: trailing blanks kept, `\n` before the `.` only at the real end of line (`Z_ID .` before a `'` comment).
Not modelled (pre-existing, separate): `Call S(A,Z,3 : Z = 1` compiles on all four loggers (the colon ends the call) but
we reject it; `Call S(A,Z,Z_ID +`, `Call S(A,Z,Q(1`, and a trailing comma with undefined names give other messages.
Fuzz reproducer cr1000x `false_accept/58c9cc38619c` (`Call run(Z_min_sub,Z,Z_ID` after the `Const` was deleted).

### Over-indexed array reads in expressions (all four loggers, round 16)

Fixture `r16_over_indexed_expr.json` (`expressions._dim_limit`, `_over_indexed`; `structure._report_call_arg_names`). A declared
variable read with more subscripts than it may have is `Dimensions not declared: <echo>.`: CR300/CR350 beyond its declared
dimensions (already modelled for assignments, `If`, `Do`, instruction arguments), **CR6/CR1000X beyond three** (`B(1,1,1,1)`;
`S3(1,2)` for a 1-D `S3` compiles there). Echoes: assignment/`Fn(..)` statement the whole statement; `If`/`ElseIf`/`Do`/`Loop`/
`While` the condition; instruction argument the argument (`Delay(1,B(1,1,1,1),mSec)`); `For` the `Start`/`End`/`Step` header
part (` B(1,1,1,1)` keeps the leading space of `Start`); `Select Case` the selector (two spaces) and then, because the
selector opens no block, `Case outside Select.` per `Case` and `EndSelect w/o Select`; `Case` the whole item list after
`Case ` (`1, 2 To B(1,1,1,1)`); a `Sub`/`Function` call argument the argument alone (all four loggers; before this CR300/
CR350 also accepted it), one line per offending argument. New in this round on CR6/CR1000X: all of the above; on all four:
`For`, `Select`/`Case`, `Call` arguments. Not modelled: an undefined name after the extra subscript (`B(1,1,1,Undef)` is
`Dimensions not declared` on CR6, ours `Invalid symbol`), a built-in function used as a statement on CR300/CR350
(`SerialOut(.., B(1,1,1,1), ..)`), CR6 `Variable S3 out of bounds.` for `Call Lg(a, S3(1,2))`. Fuzz reproducers cr6
`false_accept/bde4d924a9fc`, cr1000x `cca13e3dfce5`.

### User `Function` called wrongly inside a `Sub` call argument; `SerialOpen` argument count (round 16)

Fixtures `r16_sub_call_function_arity.json` (`expressions.check_function_call_counts`) and `r16_serialopen_arity.json`
(`_ARITY_RANGE`). A user `Function` with too many arguments inside an argument of a `Sub` call (`Call Lg("a", Ok, "got=" +
FormatLong(Fn(1,2,"fuzz"), "%d"))`, `Lg("a" + Pn(1,2,3) + "b")`, also across `_` continuations, which leave two blanks) gives
`No Matching, or out of place ) in expression: <that argument>.` on all four loggers; too few gives `Call argument
required.` and `Invalid, or out of place expression: <that argument>.` (no newline before the `.`). The echo is the argument
alone, not the statement. `SerialOpen` takes 5 or 6 arguments (CR350 5 to 8): more is `No Matching, or out of place )`,
fewer `Expression expects ,` (CR350 used to accept 4 or fewer). Fuzz reproducers cr1000x `false_accept/ee26b0ad7cda`, `8fec3d32a4c5`,
cr6 `95e2a4d5d433`, `fe18dc272183`, `9d2dd45508e6`, `db82630bee84` (the last four need their include libraries).
Not modelled: `SerialOpen(COMC1)` prints an extra `Internal error: unable to compile program.` here (the real CR6 does not).

## Round 16 message-diff families, second worker (2026-10-13)

### `ESSInitialize` with a cut-off string before `BeginProg` (fixture `tests/fixtures/r16_ess_unterminated.json`)

All four loggers (oracle 2026-10-13, 32 cases). `ESSInitialize("private,"fuzz")`, `ESSInitialize(")`, `ESSInitialize("a,"b",1)`
(an unterminated string inside the first argument, before any top-level comma) read its argument raw, so the statement is
`ESSInitialize illegally occurs before BeginProg.` like `ESSInitialize("a")`; it was `No BeginProg in Program.` here (the
statement is `Unparsed`, `parser._parse_text_segment`). Inside `BeginProg` it compiles (unchanged). Other instructions
with an optional first parameter (`Calibrate(")`) are *not* the same: not generalised, and ours rejects `Calibrate(")` inside
`BeginProg`, which the real compilers accept (CR6 and CR300 probed; not fixed, no corpus case). Not done: `ESSInitialize(x,")`
on CR300/CR350 omits `x is not defined.`; `ESSInitialize("a,"b)` on CR300/CR350.
`structure._before_beginprog_name`, `structure._quote_in_first_arg`.

### `Alias` of a variable declared only later in the file (fixture `tests/fixtures/r16_alias_before_decl.json`)

All four loggers (oracle 2026-10-13, 28 cases). `Alias G(1) = R` / `Public G(6)` is `G not yet declared so cannot be aliased.`
at the `Alias` line, with or without `BeginProg` after (ours accepted the complete program: the check looked at every
declaration in the file, not at those above the `Alias`). An out-of-range index on such a name prints the same single line;
a second `Alias` of the same name after the declaration is judged on its own. `structure.check_pass1`
(`first_decl`, declaration position versus `Alias` position; only the "declared later and not earlier" case is changed).

### `EndDialSequence`/`EndModemHangup`/`EndVoice` with no opener, CR6/CR1000X (fixture `tests/fixtures/r16_unmatched_sequence_closers.json`)

Oracle 2026-10-13, 48 cases on all four loggers. `EndDialSequence(x)` with no open `DialSequence` is `Missing DialSequence().`,
`EndModemHangup` with no open `ModemHangup` is `Missing ModemHangup().`, `EndVoice` (there is no `Voice` instruction) is
`EndVoice without Voice`; each closer prints, in line order, in any context (top level, `BeginProg`, `Scan`, `Sub`), and
pass 2 goes on (a later `y = Zork` is still reported; hidden by a pass-1 error like other pass-2 messages). The closers are
independent: `DialSequence(2)` / `EndModemHangup` is still `Missing ModemHangup().`; a second closer after a closed pair
is reported. CR300/CR350 keep `EndDialSequence is not defined.`. `instr_data.check_table_and_menu_placement`
(`_SEQUENCE_CLOSERS`). Not done: a nested `DialSequence` (`Missing EndDialSequence().` + `DialSequence is declared in the
middle of an executing sequence`) or `ModemHangup` (`ModemHangup(1) previously declared.`), `DialSequence` inside
`BeginProg`/`Scan` (`... declared in the middle of an executing sequence`), `EndModemHangup` in `BeginProg` (adds
`BeginProg within a ModemHangup sequence.`), an `EndDialSequence` inside a `DataTable` (then `Sample illegally occurs
before BeginProg.`); `EndModemHangup(x)`/`EndVoice(x)` (`Too many parameters:  x.`).

### Table-only instructions outside a table beside an unknown `CallTable` or a CR300/CR350 placement stop (fixture `tests/fixtures/r16_table_only_before_stop.json`)

Oracle 2026-10-13, 68 cases on all four loggers. `Sample`/`DataInterval`/`Average` outside a `DataTable` print `X must be used
inside a table declaration.` in line order together with: an unknown `CallTable T` (`Could not find DataTable T.`, which ends
the walk: a table-only instruction after it is not reported), on all four loggers; and on CR300/CR350 a placement stop of the
late walk (`NextScan with no Scan.`, `Scan`/`SerialIn`/`Expr illegally occurs before BeginProg.`), an orphan `EndTable`
(`EndTable without DataTable.`, which also ends the walk). Ours dropped the table-only message whenever such an error
existed (the `Could not find` error gated tier C; CR300/CR350 skipped tier C once the walk had failed). Now the unknown-table
error is exempt from the tier-C gate on all loggers (`unknown_table_diags` in `checker/__init__.py`; CR300/CR350 also cut tier C
after it, and `cr3xx._hides_nothing` lists the message), and `structure.check_scan_placement_late` adds the
`check_table_structure` findings of the statements before its stop. A later undefined name (`x = Zork` after the
stop) is not printed, as before.

## Colon inside parentheses (oracle 2026-10-16, cr6/cr1000x/cr300/cr350)

The real compilers split a logical line at every `:` outside a `"..."` string; parentheses do
not protect it. `Call S(A,Z,3 : Z = 1` is the statement `Call S(A,Z,3` (an unclosed call is
completed at end of statement) followed by `Z = 1`; `Call S(A,Z : Z = 1` is `Call argument
required.`; `Z = Sqr(A : A)` is `No Matching, or out of place ) in expression:     Z = Sqr(A `
then `Invalid, or out of place expression:  A).`. Each later segment is echoed with its leading
space. Implemented by dropping the paren-depth test from `parser._split_text_segments` and
`_split_colon_segments`. Fixture `tests/fixtures/r17_call_colon.json`. Known remaining
differences on exotic shapes: `Call S( : Z = 1` (real reports three `Call argument required.`),
CR300/CR350 `Call S(A : Z, 3)` (real omits our extra `Call argument required.`), `If .. Then :
Call S(..( : ..` on CR300/CR350 (we add `IF or SELECT without EndIf`).

## Round 18: `Const Name(subscript) = ...`

Oracle (all four loggers, 2026-10-16; fixture `tests/fixtures/r18_const_array.json`): a subscripted
`Const J(3) = 5` compiles, before `BeginProg`, after it, and inside a `Sub`, and declares the scalar
constant `J` whose **value is the first subscript** (probed through `Public X(J-k)` dimension legality:
`J(3)`, `J(7)`, `J(2,3)`, `J(2,3,4)`, `J(2+1)`, `J(K)` with `Const K = 4`, `J(2.5)`, `J(-1)`). Everything
else is ignored once the `=` is present: further subscripts (`J(3,a)`, `J(2,3 = 1`), `As <anything>`
(`As Bogus` compiles), and the whole value (`= a`, `= 1 +`). The first subscript is checked like a scalar
`Const` value (`J(a)` is the undefined-symbol message, `J(Y)` with a `Public Y` is `Variable used in CONST
expression.`, not implemented, as for scalar `Const`). Without any `=` (`Const J(3)`, `Const J(2,3) As
Long`) it is `Const with no assignment.` plus `Too many parameters:  J.` (the subscript list is dropped
from the echo). Implemented in `parser.py` (`_const_array`) and `structure._check_unparsed_pass1`.

Not handled (false rejects remain): `J()`, `J(,3)`, `J (3)` (space), `J(3 = 5`, an unterminated string
after the `=`, `J(1+)` echo, `Const J As Long(3)` (`Long(3) unknown Const type`). Separate pre-existing
gap: `X = J(1)` on any scalar `Const` is `Incomplete expression: X = J(1).` on the oracle; we accept.

## Round 19: `Const` leftovers (oracle, all four loggers, 2026-10-17; fixture `tests/fixtures/r19_const_leftovers.json`)

Dimension probes (`Public Y(J-k)`) establish `J`'s value; `J` "undefined" is `Invalid symbol in expression: J.`.
- Empty subscripts are skipped: `Const J() = 7` and `J(,) = 7` are `J = 7`; `J(,5) = 7`, `J(,,5)`, `J(, 5)` are
  `J = 5`; `J( = 1` is `J = 1` (value of the `=`). Parser: `_parse_const`.
- `J(5 = 7` (unclosed): the first subscript is the expression `5 = 7`, so `J = 0`; an `=` anywhere in the subscript
  satisfies the "needs an `=`" rule. Same for a closed one.
- `Const J (5) = 7` (space) and `J As Long (5) = 7` is `Const J = (5) = 7`: the `=` is implied, the value is the
  comparison (`J = 0`/`-1`). `Const J (5) = ` echoes `(5) = .` (value ending in `=` joins the dangling-operator echo).
- `As <word>`: the type is the whitespace-and-comma-delimited word (`Long(5)`, `Long=7`, `Long!`, `String*10`); a
  word that is not Long/Float/Double/String/Boolean is `<word> unknown Const type`, `J` is defined as 0 and the
  rest of the line (value included) is ignored. Previously `J` took the value and `As Long=7` was accepted.
- A first subscript ending in an operator (`J(1+) = 1`, `J(+)`, `J( 1+)`, `J(1 + )`, `J(1+, 5)`) is `Invalid, or out
  of place expression: <subscript text up to , or )>.`, `J` undefined; `J(5, 1+)` compiles.
- Unterminated string on a `Const` line (`parser._const_unterminated`): no `=` anywhere -> the no-assignment pair
  (`Const J "abc`, `Const J(3) "abc`); a closed non-empty first subscript before the quote ignores the rest
  (`J(3) = "abc`, `J(3, "abc) = 1`); `J() = "abc` / `J(,) = "abc` -> `String without end quote` +
  `Invalid, or out of place expression: "abc.`; a top-level comma after the `=` ends the statement
  (`Const J = 4, "abc` compiles); everything else (`J = "abc`, `J = 5 "abc`, `J As Long = "abc`, `J(3 = "abc`,
  `J("abc) = 1`, `J, K = "abc`) is `CONST needs assignment.`, `J` undefined.
- CR300/CR350: `CONST needs assignment.` taints later statements like the other declaration errors (`X is not
  defined.` on a later use; also true for `Const = X`).

Not handled: `J (+) = 7` (echo `(+) = 7.`), `J As Long (5 = 7` (`No Matching, or out of place ) in expression: (5 = 7.`),
`Long[5]` type word (lexer error), `Const "abc` (`Too many parameters:  "abc.`), `J(a+)` (`Invalid symbol in expression:
a+.`), `J(3 4)` (`Incomplete expression: 3 4.`), and every false accept of the brief (`Const K = Y` with a variable,
`J("a")`, `J(J)`, `X = J(1)`) -- not attempted.

## Round 20: `Const` leftovers (oracle, all four loggers, 2026-10-18; fixture `tests/fixtures/r20_const.json`)

- Value (after `=`, or the implied one of `Const J (5) = 7`) with an unmatched `(` (`(5`, `((5)`, `(5 = 7`,
  `(5 4`): `No Matching, or out of place ) in expression: <value>.`; with an undefined name CR6/CR1000X give
  `Invalid symbol in expression: <value>.` instead, CR300/CR350 `a is not defined.` first. A value with misplaced
  operators and balanced parentheses (`(+)`, `* 5`, `5 * * 3`, `(5 +)`) is `Invalid, or out of place expression:
  <value>.`, and an undefined name in it (`a+`, `1+a+`) is reported the same way as above. Adjacent operands
  (`Const J = a b`, `J(3 4) = 7`, `J(3 4 = 7`, `J(3 4, 5) = 7`) are `Incomplete expression: <operands>.` (subscript
  echo ends at its `,`/`)`); undefined names replace it on CR6/CR1000X. `J(3 4)` without `=` stays the
  no-assignment pair. Parser: `_parse_const`, `_const_sub_unparsed`; messages: `structure._check_unparsed_pass1`.
- `As Long[5]` (a `[` in the type word): `Long[5] unknown Const type` with `=`, no-assignment pair without.
- `Const "abc` (unterminated string as the name): no-assignment pair with `Too many parameters:  "abc.` (the word up
  to a space); with `=` anywhere `Invalid variable or constant name: "abc.`; `Const 5 "abc` is the pair with `5`.
- `Const J As String * 10 = "a"` (also `As Long * 10 = 3`): `Invalid, or out of place expression: * 10 = "a".`
  (echo from the `*` to the next top-level `,`). Without `=` it stays the no-assignment pair.
- A name used before its declaration is undefined: itself (`Const J = J`, `J(J)`), a later `Const` (`K = L` then
  `Const L = 2`) or a later `Public` (`expressions._not_yet_declared`). Not matched: CR300/CR350
  `Const J As Long = J` adds `Variable illegal in Parameter: J.`.
- Not implemented (observed): `Variable used in CONST expression.` for an earlier-declared variable in a value or
  first subscript (`Const K = Y`, `Y + 1`, `Z(1)`, `-Y`, `J(Y)`, `Status.Battery`); the Const is still declared
  (a later duplicate is `already declared as a CONST.`), CR6/CR1000X then report `Illegally dimension: Public Q(K)`
  for a use as dimension, CR300/CR350 do not. `X = J(1)` / `X = S(1)` on a Const is `Incomplete expression: X =
  J(1).` (CR300: with the ` |` echo quirk); `Const K = Min(2,3)` is `Incomplete expression: Min(2,3).`. We accept.

## Optional measurement outputs (round 25)

The CR6 bridge builder `FUN_00513550` reads argument 15 and passes a flag to the shared
`FUN_005147a0` destination resolver. The resistance builder `FUN_005177a0` reads argument 14
for `Resistance` and argument 11 for `Resistance2W`/`Resistance3W`. Each enables the extra output
only when the converted integer equals **1**. The omitted-argument sentinel does not enable it.
These are observations of the supplied compiler versions, not a statement about logger runtime.

Reference probes confirm:

- `BrHalf4W` and `BrFull6W` on CR6/CR1000X require `2 * Reps` Float elements with argument 15
  equal to 1, otherwise `Reps`.
- CR6 `Resistance`, `Resistance2W`, and `Resistance3W` require `Reps + 1` Float elements with
  their final option equal to 1, otherwise `Reps`. This is one extra element overall, not per rep.
- `1.5` converts to 1 and enables the extra output. `True` (-1), -1, 2, 0, False and 0.9 do not.
  The local `returnv1.htm` help says any nonzero value enables the output; the reference compilers
  instead follow the equality test visible in the decompilation.
- Negative Reps still reports `Invalid reps N, must be > 0`. Destination checking uses the
  adjusted count: resistance Reps -1 with option 1 needs zero elements and emits no additional
  bounds error, whereas Reps -2 still does. This distinction is covered explicitly.
- Destination bounds use the remaining elements after a literal array offset, including a
  multidimensional offset. Long destinations do not receive this Float-specific check.
- CR300/CR350 bridge probes use their valid `mV2500` range: omitting the option compiles,
  while supplying it reports `Too many parameters`. Resistance instructions are unavailable
  on CR1000X/CR300/CR350. These profile differences are retained in the fixture.

The shared bridge builder also checks `MeasPEx` after resolving the destination. For valid negative
(burst) channels on CR6/CR1000X, a positive `MeasPEx` unequal to `Reps` reports
`When channel < 0, measurements per excitation must equal reps.`. Round 25 implements this for
`BrHalf4W`/`BrFull6W`; other callers and invalid/nonpositive MeasPEx remain outside this change.

Evidence: `tests/fixtures/r25_measurement_outputs.json`, replayable with `tools/fixture_audit.py`.
Round 26 resolves all seven bare-alias destination observations; see
[measurement alias destinations](#measurement-alias-destinations-round-26). Extreme repetition-count
integer overflow, expression-valued array offsets, and other bridge channel-allocation rules remain open.


## Measurement alias destinations (round 26)

Reference observations, 2026-10-21: `tests/fixtures/r26_measurement_aliases.json` preserves 282
probes on all four profiles, including unresolved cases as strict expected failures. On this
fixture the round-25 implementation matches 111 cases; round 26 matches 141. The seven alias
cases in `r25_measurement_outputs.json` now also match and are no longer expected failures.

The implementation is limited to bare, undimensioned Float aliases in the CR6/CR1000X
measurement Reps checker. It resolves the backing declaration's storage, subtracts the alias's
starting offset, and applies the existing output-count rule (including optional bridge and
resistance outputs). An alias list advances by each preceding name's declared size. Literal,
named-constant and multidimensional starting offsets are covered. Long aliases retain the
existing absence of this Float-specific bounds check.

The diagnostic name preserves the backing variable declaration spelling when the alias starts at the first element;
otherwise it is the declared alias name. Target/use capitalization does not change that spelling. For example, `Public X(6): Alias X=Y` with VoltSe Reps 7
reports `Variable X out of bounds.`, while `Alias X(4)=Y` with Reps 4 reports
`Variable Y out of bounds.`. Reps 3 fits the latter. These are compile-time observations only.

Reverse-engineering trail: CR6 `FUN_005147a0` passes the adjusted output count to
`FUN_005077e0`, which resolves the destination via `FUN_004a2f60`; the bounds path reports through
`FUN_0050a540`. The evaluator/storage paths retain variable metadata for the diagnostic. This
trail motivated the probes; the profile-specific rules above are based on their observed results.
No decompiled implementation was copied into Python.

The 141 remaining fixture differences are intentionally retained:

- Dimensioned alias names and indexed alias uses are not correctly registered/resolved by the
  expression checker (72 observations).
- An alias of an alias has a different declaration diagnostic (24).
- Alias declaration validation incorrectly sees a later local shadow of its backing variable
  (12). The new storage lookup binds at the alias declaration, but the earlier declaration
  validation still blocks these programs.
- Scalar routine-parameter measurement bounds are not modelled (2).
- CR300/CR350 Long alias type diagnostics differ (12); measurement bounds/channel-count
  diagnostics remain incomplete (19).

CR300/CR350 also differ from CR6/CR1000X in the observed bare-alias measurement capacity: an
alias starting at `X(4)` of `X(6)` still accepts Reps 6 on CR300. The analogous CR350 case reports
only its channel limit. Do not extend the CR6 offset subtraction to these profiles. Indexed
aliases introduce additional bounds rules and remain outside this implementation.

Round-26 validation also refreshed stale repository expectations after the production Shunt
removal: a fresh CR6 reference run reports Main `ProgSignature = 16571`, and its TDF/list bytes
match opencrbasic exactly. `tests/fixtures/tdf/repo_Main.json`, the signature expectation, and the
include-link assertion now reflect the current Main source. No production source changed.

The benchmark startup also relied on Main having a warning: the LSP intentionally does not
publish an initial empty list for a clean, closed entry (`Session._publish`). It now opens the
existing injected-error variant before waiting for its first diagnostic, then alternates clean
and erroneous edits. Its initialization-to-first-publication measurement therefore includes
opening and analyzing that variant; it is not directly comparable to older closed-entry timings.

## Alias source binding and rejection order (round 27)

Reference observations, 2026-10-22: `tests/fixtures/r27_alias_declarations.json` preserves 152
cases across CR6, CR1000X, CR300 and CR350. The round-26 implementation matches 36; round 27
matches 140, with the remaining 12 retained as strict expected failures. In the older
`r26_measurement_aliases.json`, 23 additional observations now match (164 total, 118 expected
failures). The reference expectations were preserved; only resolved expected-failure markers
were removed.

Reverse-engineering trail: CR6 `FUN_004b8e70` searches variable records in declaration order.
Inside a routine the search starts at the routine's variable-record boundary (the value loaded
from the current routine record at offset `0x3c`); outside routines it excludes local records.
After finding the source record, the handler checks its alias marker at offset `0x68` before
parsing source subscripts or creating RHS names. A source with an alias marker reports
`Cannot Alias an Alias.` and returns. Decompiled code was used to choose probes and was not
copied into the implementation.

The four-profile probes establish these rules:

- Alias source declarations must precede the alias in the same scope. A global alias is checked
  against global dimensions, independently of a later local variable with the same name. An
  array declared in another routine cannot supply an alias source. Inside a routine an alias
  cannot use a global source, even though ordinary expressions can use that global variable.
- Previously registered aliases cannot be aliased again. This diagnostic takes precedence over
  invalid, empty or undefined source indices, an invalid RHS dimension, and a numeric RHS name.
  Rejecting a chain does not register its RHS: a later alias of that RHS reports that it is not
  yet declared.
- Registration depends on where an earlier alias failed. An out-of-bounds source offset reports
  `Illegally dimensioned aliased name` but still registers a bare RHS alias; a subsequent chain
  therefore reports `Cannot Alias an Alias.`. An invalid RHS dimension aborts before that name
  is registered. In a list, names before a failing item remain registered.
- Alias-name and source-offset duplicate tracking is separate for each routine and for globals.
  Leaving a routine restores the global tracking state. Name collisions against globals have
  additional rules that are not implemented by this change.

The pass-1 checker now builds alias source dimensions from preceding declarations in the
current scope and reuses its existing registration and dimension validation. It keeps separate
alias tracking for globals and each routine. The measurement storage resolver from round 26
is unchanged; correcting declaration validation allows eleven of its twelve local-shadow
observations to match. The remaining CR350 case also has an existing measurement-channel gap.

Remaining gaps: the new fixture retains four routine-parameter source observations and eight
RHS/global-name collision observations. Routine parameters are valid alias sources in the
reference compiler but are not registered by this alias check. A local alias whose RHS collides
with a global name has profile-dependent diagnostic/registration behavior. The older fixture
still retains twelve CR300/CR350 chain observations because the later undefined-name and
parameter-count cascades differ, although the chain diagnostic itself now matches. Dimensioned
and indexed measurement aliases, Long alias type diagnostics and scalar measurement parameters
remain outside this round. These are compiler observations, not logger runtime evidence.

Round-27 validation also refreshed `tests/test_signature.py` against fresh CR6Comp `-s` results
for the repository programs after the GPS and shared bench changes: PT1000 tester 45535, Main
60721, TDR tester 60752, SBD tester 47622, FinApp tester 11702. The first FinApp signature attempt
timed out; its retry returned a successful compile and signature. This was a stale test
expectation, not a change to the signature algorithm or production CRBasic.

## Routine-parameter alias storage (round 28)

Reference observations, 2026-10-22: `tests/fixtures/r28_parameter_aliases.json` preserves 368
cases across CR6, CR1000X, CR300 and CR350. The round-27 checker matches 12; round 28 matches
284, with 84 neighboring gaps retained as strict expected failures. All four routine-parameter
source observations in the round-27 fixture now also match (144 of 152 total). None of the
new fixture's previously exact cases was lost, and the fixture comparison found no newly
introduced false accepts or false rejects.

Reverse-engineering trail: the routine declaration path in CR6 `FUN_004baf00` records the start
of the routine's variable records before parsing parameters. It parses their dimensions and
registers each parameter via `FUN_004d3560`, the same variable-record allocator used for normal
declarations. `FUN_004b8e70` then finds these records in the routine-local source search and
rejects an alias record before validating its indices, as traced in round 27. Its forward
record search also explains why a later local declaration with the parameter's name does not
replace the parameter's alias capacity. The rules below are based on four-profile reference
probes; no decompiled implementation was copied into Python.

Implemented scope: untyped/Float and Long parameters with positive, constant dimensions up to
the existing dimension limit. Alias validation receives an isolated declaration view of those
parameters. A scalar and an empty `P()` both have one element; `P(4)` has four; multidimensional
parameters retain their complete shape. User constants must already be declared at the header;
predefined constants remain available. Named-constant dimensions and positive fractional
sizes (truncated to an integer, e.g. `3.9` becomes three) are covered. Optional parameters,
functions, routines declared after `EndProg`, global shadowing and routine isolation were
probed. The source record's dimensions continue to apply when the routine later declares a
local of the same name. The synthetic declaration view does not change ordinary expression,
measurement, table or editor binding resolution.

Parameter alias indices remain constant-only. A runtime index from a global, another parameter,
an Optional parameter or a String parameter reports `Variable illegal in Parameter: ...` then
`Alias is illegally dimensioned.`. Unknown and String-literal indices retain the existing
profile-specific diagnostics. A valid named Const index is accepted. Existing alias-chain,
duplicate-source and partial-registration checks also apply to parameter sources.

The 84 expected failures retain separate rules rather than generalizing this view:

- 48 String parameter alias cases: byte-storage capacity, character subscripts and alias-list
  dimensions need their own model.
- Eight Boolean/Double parameter source cases: these types were observed but are not enabled
  by this Float/Long implementation.
- Twelve invalid/nonconstant parameter-dimension cases: the header diagnostics and ordering
  remain unmodelled. Such parameters are excluded from the new view, preserving rejection
  instead of allowing a valid alias to hide an invalid header.
- Twelve forward-Const dimension cases: a later Const must not enable parameter alias storage.
  These programs retain rejection, while their header diagnostic text remains unmodelled.
- Four rejected Const-shadow parameter cases: the alias index checker adds diagnostics after
  the reference has retained the earlier Const and reported only the header conflict.

Twelve separate verdict tests require forward-Const dimension programs to remain rejected,
even while their exact header diagnostics are strict expected failures. These guards were
added after the final full-suite collection and passed in a targeted run.

The exploratory probe also recorded 80 measurement-destination observations in
`scratch/r28_probe.json` (untracked). Scalar Float parameters have a one-element measurement
capacity; explicit array parameters have their declared capacity. Long destination types and
CR300/CR350 diagnostic cascades differ. These measurement rules are not changed here, and the
two old scalar measurement-parameter observations remain expected failures. Compilation and
these probes do not establish logger runtime behavior.

## Routine-parameter header dimensions (round 29)

Reference observations, 2026-10-22: `tests/fixtures/r29_parameter_headers.json` records 180
cases (45 programs on CR6, CR1000X, CR300 and CR350; every case has a reference verdict).
Round 29 matches 176, with four strict expected failures. The 36 Boolean/Double,
invalid-dimension, forward-Const and Const-shadow expected failures of
`r28_parameter_aliases.json` now match; the 48 String cases are resolved by
[String alias storage](#string-alias-storage-round-29). The
fixture audit reruns the r26-r29 fixtures with zero differing cases, and the four-profile
`difftest.py --group all` numbers equal round 28's.

Reverse-engineering trail (Codex, `scratch/codex/re-r29-param-headers.md`, CR6.14.2.0): the
routine declaration path `FUN_004baf00` starts each parameter's three dimensions at 1, parses
the type (`FUN_004ba960`), evaluates each written dimension through the constant-expression
helper `FUN_004b8700`, and replaces a failed or `< 1` dimension with 1 before allocating the
record (`FUN_004d3560`). The allocator's Const-collision helper `FUN_004b6480` does not stop
allocation, and expression tokens try constants (`FUN_004b5db0`) before variables. This was a
hypothesis until the probes below confirmed it; no decompiled code was copied.

Rules implemented in `structure._alias_parameter_declaration` (called from `check_pass1`):

- Header dimensions are constant expressions resolved in declaration order. Only earlier
  user Consts and earlier variables are visible; a Const or `Public` declared later is an
  undefined name (`Invalid symbol in expression: K.`; CR300/CR350 `K is not defined.` then
  `Variable illegal in Parameter: K.`). The echo is the dimension's text with leading blanks
  removed (`P( K )` -> `K .`).
- An earlier variable or an earlier parameter of the same header reports `Variable illegal in
  Parameter: K.`.
- A constant dimension truncating below 1 (`0`, `-1`, `0.9`) reports `Illegally dimension:` with
  the whole header, one line per bad dimension, for Sub and Function; `1.9` is accepted. A
  trailing comment leaves the space before it in the echo.
- Every reported dimension recovers to 1 and the parameter stays registered, so `Sub S(P(0))`
  accepts `Alias P(1)` and rejects `Alias P(2)` with `Illegally dimensioned aliased name`; the
  other dimensions keep their size (`P(2,-1)` accepts `Alias P(2,1)`).
- Boolean and Double parameters are alias sources with logical element bounds (`P(4) As Double`
  rejects `Alias P(5)`; `P(2,3) As Double` accepts `P(2,3)` and rejects `P(3,1)`).
- CR6/CR1000X check every parameter of every header. CR300/CR350 stop the header after the
  first parameter with a bad dimension: later parameters are not registered (`Q not yet declared
  so cannot be aliased.`) and the parameter two on is named (`Too many parameters:  R.`, also
  after `Variable illegal in Parameter`, so `cr3xx.drop_too_many_after_variable_illegal` now
  exempts routine headers). After one failed header, CR300/CR350 neither check nor register
  later headers; the existing cascade then rewrites the file, and a tainted header whose only
  parameter has dimensions (`Sub T(Q(-1))`, `Sub T(Q(K))`) prints nothing, so the cascade's
  header-token scan now ignores parenthesized dimensions.
- An alias index naming an earlier Const resolves to the Const even when a parameter has the
  same name (`Const J=5` / `Sub S(P(4),J)` / `Alias P(J)=Y` -> the header conflict, then
  `Illegally dimensioned aliased name`); other runtime and undefined index names keep their
  diagnostics after that conflict.
- Each parameter's `As` type is checked (`Variable Type Banana not supported`) before its
  dimensions, and the return type after the last parameter: `Sub S(P(0),Q As Banana)` reports
  the dimension first, `Sub S(P(0) As Banana)` the type first. On CR300/CR350 a type error also
  stops the header and names the parameter two on (`Sub S(P As Banana,Q(0),R(0))` -> `Too many
  parameters:  R.`), and a bad dimension stops it before a later type or the return type.
- More than three dimensions report `Variable P(1 dimension exceeds max of 3.` (the first
  dimension's text) and no per-dimension check; the parameter stays registered (as a scalar,
  `Alias P=Y` and `Alias P(1)=Y` pass, the recovered shape is otherwise unprobed). On
  CR300/CR350 it stops the header (`Sub S(P(1,1,1,1),Q(0))` reports only that line).
- A String length that does not fold reports like a dimension (`P As String * K` -> `Invalid
  symbol in expression: K.`; an earlier parameter `Q` -> `Variable illegal in Parameter: Q.`)
  and the parameter stays registered with alias range checks skipped. CR300/CR350, and the
  earlier-parameter form on every profile, add `line <last>: Out of memory.`, not modelled.

Probes that contradicted the Codex predictions: its probe 20 on CR300/CR350 (the header stops
at `P(0)`, so `Q(K)` is never evaluated and `Too many parameters:  R.` follows) and the
multi-routine probes (later headers are silent on CR300/CR350). Its other predictions matched
on all four profiles, with CR300/CR350 differing only by the known undefined-name pair.

Remaining gaps: assignment to a Const-shadow parameter (`J=1` reports `Invalid assignment in
expression: J=1.`; four fixture cases). Dimensions above the dimension limit, String-literal
dimensions and nonconstant expressions that are neither undefined nor variables keep the
round-28 behavior (no header diagnostic; parameter not in the alias view). Whether a type error
or more than three dimensions also silence later CR300/CR350 headers is unprobed. Whether an earlier non-header error also silences later CR300/CR350 headers is unprobed.
String parameter storage is covered by [String alias storage](#string-alias-storage-round-29).

## String alias storage (round 29)

Reference observations, 2026-10-22: `tests/fixtures/r29_string_parameters.json` records 320
cases (80 programs on CR6, CR1000X, CR300 and CR350; every case has a reference verdict): 36
shapes each as a String routine parameter and as a `Public` String, plus eight `Public`/local
`Dim` edge programs. All 320 match. The 48 String expected failures of
`r28_parameter_aliases.json` now match, so that fixture has no expected failures left. The four
profiles gave identical verdicts and diagnostics; parameters, `Public` and local `Dim` Strings
behave the same.

Reverse-engineering trail (Codex, `scratch/codex/re-r29-string-params.md`, CR6.14.2.0): the type
parser `FUN_004ba960` rounds a String's length to its slot bytes, the allocator `FUN_004d3560`
inserts the byte length as a storage dimension, and the alias handler `FUN_004b8e70` measures
alias positions in 4-byte storage words. This was a hypothesis until the probes confirmed it; no
decompiled code was copied.

Rules (`structure._alias_string_slots`, used by `_check_alias_dims`; String parameters now enter
the alias view built by `_alias_parameter_declaration`):

- A String of length `L` (default 23) occupies `S = ceil(max(4, L+1) / 4)` words per element
  (`* 3` -> 1, `* 15` -> 4, `* 16` -> 5, default -> 6). The source holds `T = elements * S` words.
- A subscript count above the declared rank selects a character: `Alias is illegally
  dimensioned.` and the statement ends without registering its names (scalar `P(1)`, vector
  `P(1,1)`, `P(1) As String` with `P(1,1)`, matrix `P(2,3,1)`; a later `Alias Y=Z` then reports
  `Y not yet declared so cannot be aliased.`).
- Otherwise the element flat offset `f` (round 7 formula) starts the list at word `q = (f-1)*S`;
  `Illegally dimensioned aliased name` when `q` is outside `[0, T)` (`P(0)`, `P(4)` of `P(3)`).
- An explicit RHS count `n` must satisfy `1 <= n <= T + q` (`P(3)=Y(25)` passes on `P(3) As
  String * 16`, `Y(26)` fails). Unlike numeric sources, this also applies when `q < 0`
  (`P(0)=Y(10)` reports only the start error, `Y(11)` adds `Alias is illegally dimensioned.`).
  A bare RHS name skips it: `P(1) As String * 16` with `P(0)=Y` reports only the start error and
  registers `Y` (a later `Alias Y=Z` is `Cannot Alias an Alias.`).
- `L` converts like a C long: outside its range (`1e10`, `1e999`, `-1e999`) it is
  -2147483648, so the slot is one word (`String * 1e999` accepts `P=Y` and rejects `P=Y(2)`).
- Each list item advances `q` by `n * S` (`P=Y(2),Z` on three slots passes, `P=Y(3),Z` and
  `P=Y(14),Z` report the start error for `Z`, which is still registered: a later `Alias Z=Q` is
  `Cannot Alias an Alias.`).

Probes that contradicted the Codex model: none. All 24 of its predictions held on all four
profiles. The existing element-unit handling of `Public` Strings agreed with the reference only
where element and word units coincide; it is now replaced by the word model, not duplicated.

Review fixes (Codex review `scratch/codex/review-r29.md`, observations 2026-10-22):
`tests/fixtures/r29_review_fixes.json` records 104 cases (26 programs on all four profiles);
92 match and 12 are strict expected failures (`Public P As String * K`, which reports nothing,
and the `Out of memory.` line above). All five review findings were confirmed except finding 3's
mechanism: `String * 1e999` compiles, but through the long conversion, not a minimum allocation.
An infinite `Public` String length also raised an implementation exception in the storage and
ESS estimators (`tables._as_int`, `ess._const_int`); both now fall back to their default.

Remaining gaps: Strings with three declared dimensions and lengths below 1 are unprobed; a
nonconstant `Public` String length is not reported (`Invalid symbol in expression: K.`). Compilation does not establish
logger runtime behavior; the reference accepts `Y(n)` counts beyond the remaining slots.

## Dimensioned alias destinations (round 30)

Reference observations, 2026-10-22: `tests/fixtures/r30_dimensioned_aliases.json` preserves 180
cases (40 programs on all four profiles, plus 10 CR300/CR350 plain-array and channel-span
programs). The round-29 implementation matches 8 of them (the two declaration-error programs);
round 30 matches all 180. In `r26_measurement_aliases.json`, 92 expected failures now match: the
72 dimensioned/indexed cases, the 19 CR300/CR350 bounds/channel cases, and the CR350 local-shadow
case whose only remaining gap was the channel span (26 expected failures remain).

Notation: `T` backing elements, `b` the alias's zero-based start, `n` its declared size
(`Alias X=Y(n)`), `k` a literal destination index, `C` the instruction's output count.

- `Alias X=Y(n)` registers `Y` for expressions. A name whose declaration reports
  `Alias is illegally dimensioned.` (`n > T + b`, or `Y(2,2)`) is not registered; CR300/CR350
  then report `Y is not defined.` at its uses.
- Declaration accepts `n` up to `T + b` (`X(3)=Y(8)` on `X(6)` compiles, `Y(9)` fails), so
  the declared size can exceed the storage that remains. Measurement capacity is separate.
- A destination `Y(k)` first requires `1 <= k <= n` on all four profiles (`Y(0)`, `Y(3)` of
  `Y(2)` fail even with backing storage left).
- CR6/CR1000X: then `C <= T - b - (k - 1)` (bare `Y`: `k = 1`). Same rule as the round-26 bare
  alias, now with the index.
- CR300/CR350: then `C <= T - (k - 1)`: the alias start is ignored, as for round-26 bare aliases
  (`X(4)=Y(3)`, `VoltSe(Y(3),4,...)` compiles on CR300, fails on CR6). Plain arrays keep their
  start (`VoltSe(X(2),6,...)` on `X(6)` fails). Only VoltSe was probed; the check is limited to it.
- The name is the backing declaration's spelling when `b = 0`, else the alias name; one message
  even when both the index and the span fail.
- A subscripted alias inside an expression argument (`VoltSe(Y,1,...,Y(3)+1,0)`) and plain
  statements (`Y(3)=1`, `X(1)=Y(3)`, `Y=1`) compile: no static bound applies there.
- CR300/CR350 VoltSe channel: the last channel used, `SEChan + Reps - 1`, is checked and shown
  (`Channel 7 exceeds number of valid channels.` for channel 6, Reps 2; CR350 has 4 SE channels,
  CR300 6). The destination bounds message precedes it on the same line.

Implementation: `symbols.declared_names` registers single-dimension RHS names;
`expressions._known_names` removes those that `structure._check_alias_dims` (run into a scratch
list) did not register before `Alias is illegally dimensioned.`, on single-pass profiles. `instr_measure._measurement_aliases` now records
`(T, b, name, n)` for bare and dimensioned names; `_alias_overrun` applies the index and the
profile's capacity rule, and `_dest_overrun` is shared by the CR6/CR1000X path and a new CR300/
CR350 VoltSe path (inserted before the line's other diagnostics). The channel span is a third
item, the Reps position, on the CR300/CR350 `VOLTSE` entry of `terminals.json`.

RE trail (Codex, `scratch/codex/re-r30-dim-aliases.md`): CR6 `FUN_004b8e70` creates the alias
record with its own shape at the backing start; `FUN_004b9f40` gives expression descriptors the
backing capacity `T - b` with the alias's declared dimensions; the destination evaluator
`FUN_004a2f60` checks the start against the dimensions and the span against the capacity. All
twenty-two of its CR6 predictions matched on CR6 and CR1000X. Its CR300/CR350 formula `T - j` was
confirmed, including the untested boundary (`X(3)=Y(2)`, `Y(2)` Reps 5 compiles on CR300).
Codex did not anticipate that the CR300/CR350 channel message reports the last channel of the span,
nor that opencrbasic lacked the plain-array CR300/CR350 VoltSe bounds check. No decompiled code was
copied.

Review fixes (Codex `scratch/codex/review-r30.md`, seven predicted defects; reference
observations 2026-10-22 in `tests/fixtures/r30_review_fixes.json`, 25 programs on all four
profiles, 100 cases: 92 match, 8 expected failures). All seven were confirmed:

- Registration follows the declaration check: names before the failing list item stay registered
  (`Alias X=Y(2),Z(99)` on `X(6)`: `Y=1` compiles, `Z=1` is `Z is not defined.`; `Y(99),Z(2)`
  registers neither). The scratch check now sees only the preceding declarations of the alias's
  scope (`symbols.scoped_view` of the statement prefix) and their String storage
  (`structure._alias_string_slots`): `Alias X=Y(2)` on `X As String * 16` or on `X(6)` with an
  unrelated routine-local `Dim X` compiles; `Y(9)` on the String still fails.
- A failed `Alias X=K(2)` leaves `Const K` defined.
- A Const destination index (`X(K)`, `Y(K)`) is checked like a literal (all four profiles,
  `Variable X out of bounds.`); `Const K=1.5` stays unchecked and compiles.
- A Float routine parameter's header shape backs its aliases: `Sub S(P(4))` / `Alias P=Y(2)`:
  `VoltSe(Y(3),1,...)` and `VoltSe(Y(1),5,...)` report `Variable P out of bounds.`; a bare
  `Alias P=Y` with Reps 5 does too.
- CR300/CR350 VoltSe channel span truncates a fractional Reps (`2.5` on channel 6:
  `Channel 7 exceeds number of valid channels.`).
- A nonfinite terminal constant (`Reps=1e999`) no longer raises. The reference saturates it
  (`1e999` is 2147483647: CR300/CR350 `Variable X out of bounds.` and `Channel 2147483647
  exceeds ...`; `-1e999` gives `Invalid reps -2147483648, must be > 0.`; CR6/CR1000X add channel
  and `Scan interval too short` lines); not modelled, the eight expected failures.

Gaps: other CR300/CR350 measurement instructions (destination bounds and channel span not
probed); CR1000X channel span; runtime destination indices; multi-index uses of a dimensioned
alias; dimensioned Long/String aliases; a subscripted bare alias (`Alias X=Y`,
`VoltSe(Y(2),...)`); saturated nonfinite Reps; Long/String parameter alias destinations. These
are compiler observations only.

## Alias name collisions, Const-first assignment and String lengths (round 31)

Reference observations, 2026-10-23: `tests/fixtures/r31_alias_collisions.json` records 514 cases
(129 programs on CR6, CR1000X, CR300 and CR350; two further runs, `Public Q` / `Public P(K) As
String * Q` on CR6 and CR350, timed out in the reference compiler and are not recorded). The
round-30 checker matched 128; round 31 matches 512, with two strict expected failures. All 24
targeted expected failures now match: 8 in `r27_alias_declarations.json`, 4 in
`r29_parameter_headers.json` and 12 in `r29_review_fixes.json`; none of these three fixtures has
expected failures left.

Reverse-engineering trail (Codex, `scratch/codex/re-r31-B.md`, CR6.14.2.0): `FUN_004b8e70`
registers an alias's RHS record and then scans earlier variable records for its name; expression
tokens try constants (`FUN_004b5db0`) before variables; the type parser `FUN_004ba960` ignores a
failed String length, and the end of pass 1 requests the variable segments (`FUN_004b6840`,
allocator `FUN_00508be0`). The exact retained length behind `Out of memory.` was not derived. The
rules below come from the probes; no decompiled code was copied.

Alias RHS names (`structure._alias_name_collisions`, used by `_check_alias_dims`):

- In a routine, an RHS name that is an earlier global `Public`/`Dim` (also one declared by a
  routine-body `Public`), a global alias or a `Const` reports `Y already declared as a variable.` /
  `Y is already declared as a CONST.`, like a global alias. A local `Dim Y`, a parameter `Y`, a
  later global or another routine's local does not collide; a global still collides when a local
  or parameter of the same name exists. `Alias X = Sec` in a routine reports `Sec is a predefined
  CONST.` as at global scope.
- Every bare list item is checked, at global scope as well (`Alias X=Q,Y` and `Alias X=Y,Y`
  report each colliding name; the earlier "first item only" observation, `= Fresh, LevelFt`, has
  a blank after the comma): an item after a comma with a blank before it (`= Q, Y`), a dimensioned item (`Y(1)`) or one with
  trailing words (`Y 3`) does not collide. Each item reports after its own start-dimension error
  (`Alias X(2)=Q,Y` on `X(2)`: `Illegally dimensioned aliased name`, then the collision) and
  before later items' errors.
- A colliding name skips `Duplicate Alias Name` (a repeated `Alias X=Y` gives `Duplicate Alias`
  then the collision). In a routine the name is registered as a local alias: a later `Alias Y=Z`
  there is `Cannot Alias an Alias.`. At global scope it is not (source lookup finds the earlier
  global first, so `Alias Y=Z` compiles).
- CR300/CR350 (`cr3xx.drop_after_alias_collision`): after the first alias `already declared as a
  variable.` no later line reports anything (later aliases, `Public`, `EndSelect`, `Scan`
  arguments, a second routine); the rest of its own statement still does. A `Const` or
  predefined collision does not do this. "Later" is the expanded source order: a collision in an
  `Include`d file also drops the main file's lines after the `Include` (review, 2026-10-24,
  `r31_review_fixes.json` `f8a`; CR6/CR1000X keep them).

Const-first assignment (`check_pass1`, assignment branch and one-line `If` bodies): an assignment
to a name declared by an earlier `Const` is `Invalid assignment in expression: J=1.` (CR300/CR350
with the line's newline before the period, not in a one-line `If` body), whether or not a
parameter, local `Dim` or global of that name exists, and also when that `Const` itself failed
(`Public J` / `Const J=7`). `J(1)=1` gives `Invalid, or out of place expression: J(1)=1.`, also in a one-line `If` body
(`If 1 Then J(1)=1`, `... Else J(1)=1`, no newline in the echo on CR300/CR350; review `f5`). A
`Const` declared after the assignment does not apply (`Sub S(J)` / `J=1` / `EndSub` / `Const J=2`
compiles); reading the Const (`K=J`) is accepted.

String lengths (`_check_decl_exprs`, `_report_decl_expr`, `storage.string_length_oom_line`):

- A `Public`, global `Dim` or local `Dim` String length is checked like a header length, before
  the variable's dimensions, with only earlier names visible: undefined -> `Invalid symbol in
  expression: K.` (CR300/CR350 `K is not defined.` then `Variable illegal in Parameter: K.`); an
  earlier variable, local or parameter -> `Variable illegal in Parameter: Q.`. An alias name is a
  variable from its `Alias` statement on (`Alias X=Z` / `Public P As String * Z`); before it, or
  at global scope for a routine's alias, it is undefined (review `f4`). The echo is the
  text after `*` and one blank, up to an `=` (`*  Q ` -> ` Q `, `* Q = "abc"` -> `Q `). CR300/
  CR350 then stop the declaration and name the variable two on (`Too many parameters:  U.`).
- Every String length reported as `Variable illegal in Parameter` (a variable on every profile,
  an undefined name on CR300/CR350; `Public`, `Dim` or routine parameter) adds `Out of memory.`
  once, at the last statement (`EndProg`), after the other pass-1 errors; with an initializer it
  is at the declaration's line. An undefined length on CR6/CR1000X has no OOM. A segment OOM and
  a length OOM give one message.

Probes that contradicted the Codex model: CR300/CR350 drop not just the chain diagnostic but
every later line; list items after the first collide too; in a routine a global alias gives the
collision, not `Duplicate Alias Name`; its probe 8 (`K=J` with `Sub S(J,K)`) adds `K is not
defined.` on CR300/CR350. Its probes 1-7, 11, 12 and 16-20 held as predicted; 9 and 10 were only
run as variants (`c03`, `c02`), and 13-15 were covered in round 29.

Remaining gaps: on CR300/CR350 a parameter conflicting with a Const stops the header, so later
parameters are undefined in the body (`Sub S(J,K)` / `K=J` -> `K is not defined.`, the two
expected failures). Whether the OOM with an initializer also ends the compile, several poisoned
declarations with initializers, and String lengths naming another routine's locals are unprobed.
CR6 and CR350 did not finish `Public Q` / `Public P(K) As String * Q` within 60 s. These are
compiler observations only.

## Alias cascades, non-Float alias destinations and parameter capacity (round 31)

Reference observations, 2026-10-23: `tests/fixtures/r31_alias_cascades.json` preserves 300
cases (73 programs on all four profiles, 4 on CR6/CR1000X only; one CR300/CR350 pair was rerun
after a runner failure). The round-30 implementation matches 165 of them; round 31 matches 288
(12 expected failures; `cr6-e09`/`cr6-e10`, the CR6 BrHalf overlap, match through the bridge span
validator of "Bridge channel spans and settling-time timing (round 31)"). All 26 remaining expected failures of `r26_measurement_aliases.json` now
match.

- CR300/CR350: `Cannot Alias an Alias.` starts the `Too many parameters` cascade like the other
  declaration errors. The next `Scan(10,Sec,0,0)` reports `Too many parameters:  Sec.`. A
  declaration after the failed alias (`Public W`, `Dim W(3)`) is dropped (`W is not defined.`).
  A later instruction reports its second argument (`VoltSe(Z,1,...)`: `Too many parameters:  1.`,
  no channel check). The unregistered chain name stays undefined (`VoltSe(Y,5,...)`:
  `Y is not defined.`, then `Too many parameters:  5.`). The chain on a scalar `Public A` or
  `Dim A`, and `Alias Z(1)=Y`, behave the same. CR6/CR1000X report only the alias line.
- CR300/CR350: an alias takes its declared source's type, a routine's alias in that routine only
  (`Sub AA` / `Alias X=Z` of a Float, `Sub BB` / `Alias Y=Z` of a Long: `VoltSe(Z,...)` in `BB`
  fails, in `AA` compiles; review `f1`, `_destination_types` maps each routine statement to its
  routine's aliases). An alias of a Long, Boolean or String
  variable is no Float destination: `VoltSe(Y,R,...)` gives `Illegal destination variable type,
  expecting Float.` then `Too many parameters:  R.`, with no destination-bounds or channel check
  (Reps 5, 6, 7, 9 included). This holds for bare, offset (`X(2)=Y`), dimensioned (`X=Y(6)`),
  indexed (`Y(2)`), scalar-source and `Dim`-source aliases. `Battery(Y)` gives only the type
  line. A second such VoltSe after the first (the cascade has started) reports it again. A Float
  alias (`Alias F(6)=G`) compiles. CR6/CR1000X compile all of these.
- CR300/CR350: a routine's own parameter hides a global alias of that name. `Sub S(Y)` with a
  global Long alias `Y` compiles `VoltSe(Y,1,...)`, and so does `Function F(Y)`. A routine
  without that parameter sees the alias (`Sub S` / `VoltSe(Y,1,...)`: the type error). A
  routine-local `Dim Y` hides it too. A Long alias of a routine-local `Dim L As Long` is rejected.
- CR300/CR350: a Long or String parameter is no Float destination (`Sub S(Q As Long)` /
  `VoltSe(Q,1,...)`: the type error, `Too many parameters:  1.`, and `Too many parameters:
  Sec.` on the later `Scan`). CR6/CR1000X compile it.
- All four profiles: a Float routine parameter's capacity is its header shape, one element when
  none (`Q()` too, review `f9`), whatever the call passes (`Call S(X)` with `Public X(6)`). `Sub S(Q)` / `VoltSe(Q,2,...)`
  gives `Variable Q out of bounds.`; `Q(2)` Reps 3, `Q(2,2)` Reps 5, `Q(N)` with `Const N=2`
  Reps 3, `Q(3)` / `Q(2)` Reps 3 and `Q(3)` / `Q(4)` fail; Reps equal to the capacity compiles.
  The same holds for `Function F(Q)`, for two VoltSe lines, and on CR6/CR1000X for TCDiff and
  BrHalf (`Q(2)`, Reps 3). A parameter named like a global alias (`Alias X=Y`, `Sub S(Y)`) uses
  the parameter (`Variable Y out of bounds.`). CR6 also reports its channel check (`Invalid
  channel 1, must use U1..U12.`). CR350 also reports its span (`Channel 5 exceeds number of valid
  channels.`) after the bounds line.
- CR6/CR1000X: a subscripted scalar parameter `Q(1)` with Reps 2 is out of bounds. CR300/CR350
  report `Dimensions not declared: Q(1).` and `Too many parameters:  2.`, as for a scalar
  `Public P` (not modelled for parameters). `Q()` behaves as such a scalar there (`VoltSe(Q(1),
  ...)`, and `Q(2)=1` -> `Invalid, or out of place expression: Q(2)=1` plus the line's newline;
  review `f9d`-`f9i`, not modelled). `Sub SS(Q() As Long)` gives `Invalid symbol in expression:
  As.` (CR300/CR350 `As is not defined.` first; `f9k`, not modelled).

Implementation: `cr3xx._DECLARATION_TRIGGERS` gains `Cannot Alias an Alias.`.
`cr3xx._destination_types` extends `symbols.declared_variable_types` with each alias name of a
declared source; a name declared as a variable keeps its own type, and an alias of an alias passes
nothing on. `check_float_destinations` lays the enclosing routine's parameter types over it.
`_report_rewritten_first_arg` uses it too, for the cascade-rewritten statements.
`symbols.scope_tables(parameters=True)` (used only by `instr_measure.check_reps`) keeps a
parameter's header shape and type in place of hiding the name, so `_dest_overrun` bounds it.
`_measurement_aliases.lookup` already refuses an alias shadowed by a parameter.

RE trail (Codex, `scratch/codex/re-r31-A.md`): CR6 `FUN_004b8e70` rejects an alias source that is
itself an alias. `FUN_004baf00` builds a parameter with dimensions `(1,1,1)` unless the header gives
them. `FUN_004b9f40` searches the routine's parameters before nonlocal names. `FUN_004a2f60` compares
the requested span with the descriptor capacity. Its 24 predictions matched where probed, including
`CT1` (`VoltSe(Z,1,...)` after the failed chain). It did not predict the following: CR300/CR350 also
bound a parameter (it scoped rule C to CR6/CR1000X); the declaration after a failed alias is
dropped; a non-Float parameter is rejected; and a parameter hides a Long alias on CR300/CR350. No
decompiled code was copied.

Gaps (the 12 expected failures): CR300/CR350 subscripted scalar parameters (`Dimensions not
declared`, which needs parameters in `symbols.declared_dim_counts`); a Sub header after the cascade
start keeps its parameters (CR300/CR350 report `Q is not defined.`); a later-line error (`Call
argument required.`, PulseCount outside Scan) gates the earlier destination bound. Not probed: other CR300/CR350 Float-destination instructions
with non-Float aliases beyond VoltSe and Battery, routine-local aliases of parameters, `ByRef`
and `Optional` parameters. These are compiler observations only.

## Measurement destination and channel spans (round 31)

Reference observations: `tests/fixtures/r31_measurement_spans.json` records 1014 cases on all four
profiles (CR6 269, CR1000X 239, CR300 256, CR350 250). 978 come from the round-31 probes of
2026-10-23 (`scratch/r31/w6/`, untracked): VoltSe, VoltDiff, TCSe, TCDiff, BrHalf, BrHalf3W,
BrHalf4W, BrFull, BrFull6W, Therm107/108/109, CurrentSE, PeriodAvg and PulseCount, each with
destination fits and overruns (`d_*`), alias destinations (`a_*`), channel spans at and past the
last terminal (`c_*`, `px_*`), start-channel values (`s*`), Reps 0, negative and fractional Reps,
and destination-plus-channel order (`dc_order`). The other 36 (`*-idx-*`, 2026-10-24,
`scratch/r31/w10/`) pin an index past the dimension. The round-30 checker matched 522 cases;
round 31 matches 960, with 54 strict expected failures. Four CR1000X PulseCount cases of
`r24_dest_counts.json` also match. This family had no Codex report: every rule below is fitted to
the real compilers' output, and no decompiled addresses are claimed.

Destination bounds (`instr_measure.check_reps`):

- CR300/CR350: every instruction above checks its Float destination like the round-30 VoltSe
  rule (`_SINGLE_PASS_REPS`), before the line's other diagnostics. Reps 0 or negative gives
  `Invalid reps %d, must be > 0.`, except that a negative Reps on Therm107-109 and PeriodAvg prints
  no Reps line and overruns the destination (`Variable X out of bounds.`). A negative PulseCount
  Reps gives both lines.
- CR6 now also checks PeriodAvg, CurrentSE and Therm107-109; CR1000X also checks Therm107-109.
  On these two profiles a thermistor has no output-count check (an overrun compiles). An alias
  destination indexed past the alias size is still out of bounds (`a_index`). The former
  single-pass Therm entry of `check_dest_bounds` moved here.
- CR6 CurrentSE with Reps above 1: `Reps >1 not allowed in this device.` after the bounds line.
- An index past the dimension (`Therm109(A(6),...)` or `VoltSe(A(6),...)` on `Public A(5)`) is
  found by both this rule and the CR300/CR350 late index check (`expressions.
  check_instruction_index_bounds`). The real compilers print the line once, also when another line
  has an error (`idx-th_other_err*`). `checker._place_index_bounds` now drops the index check's copy
  on CR300/CR350 too, so a destination line printed on that line wins (the `PortGet` reordering
  stays CR6/CR1000X). Unlike W6's first guard (no destination message past the dimension), this
  keeps a routine parameter `Q(4)` of `Q(3)` (`r31_alias_cascades.json` `e04`, all four profiles),
  which the index check does not see. It also keeps CR6 `VoltSe(A(11),1,..,13,..)`
  (`portget_order_wrap.json` case 11), where the destination line precedes the channel line.

Channel spans (`terminals.py`; an instruction entry `[pos, kind, reps_pos, width]`, a kind's
`span` key):

- CR6 (`u_terminal_se`, `u_terminal_diff`) and CR1000X (`se_chan`, `diff_chan`): with Reps above
  1, the start of the last repetition, `start + (Reps-1)*width*step`, must stay in range. `step`
  is 2 on a differential kind; `width` is 2 for BrHalf3W, BrHalf4W and BrFull6W. Otherwise the
  line is `Channel N exceeds number of valid channels.`, where N is the first channel past the
  start's range: 13 on CR6, 17 (SE) or 9 (diff) on CR1000X. This covers VoltSe, VoltDiff, TCSe,
  TCDiff, Therm107-109 and the CR1000X bridges. A fractional Reps truncates (`2.5` acts as 2).
- CR6 start above U12 (`213`, `250`): reported as a span, `Channel 13` / `Channel 50` (value
  minus 200). A differential kind reports an even value as an invalid odd start first (`BrFull`
  `250`: `Invalid start channel for Differential Measurement...`).
- CR6 bridges take only their start channel from here. Their span belongs to the bridge span
  validator ("Bridge channel spans and settling-time timing (round 31)"), because the
  last-repetition fit misses a 4W/6W second measurement group (see there).
- CR300/CR350: the last channel used, `start + max(Reps,1)*width - 1`, is tested and shown (VoltSe
  Reps 2 on SE 6: `Channel 7`), with width 2 for BrHalf3W, BrHalf4W and BrFull6W. Therm107-109 use
  `therm_chan` (1..6 on CR300, 1..4 on CR350) and print the bare line `Channel exceeds number of
  valid channels.`. CurrentSE checks its SE span twice (two identical lines, observed). PulseCount
  (`pulse_chan_span`) must keep Reps channels from the start inside 1..2, else `Invalid PulseCount
  reps.`.
- CR1000X PulseCount (`pulse_chan_cr1000x`: `C1..C8` 101-108, `P1`/`P2` 301-302) must keep its
  repetitions inside the start's own family, else `Invalid PulseCount reps.` (Reps 201 from `C1`
  fails although 301 is `P1`). The CR6 counterpart is `instr_measure._check_pulse_span` (next
  section).
- A span check stays inside the start's own range (`terminals._check_arg`). A kind with
  `truncate` tests the integer part of a fractional channel: CR1000X CurrentSE (`402.5`,
  `-402.5`, `402.9` are RG2; `400.5` reports `Invalid channel 400`) and PulseCount (`108.5`,
  `302.9` compile, `100.5` fails), CR300/CR350 PulseCount (`6.9` compiles, `0.5` fails).
- CR300/CR350 PulseCount channels (`pulse_chan_span`): CR300 1..6, 8, 9; CR350 1..8; anything
  else (`0`, `-1`, `7` on CR300, `212`, `412`) is `Invalid PulseCount channel.`. The Reps
  families are `1..2`, `3..6` and single channels above (CR300 `8`, `9`; CR350 `7`, `8`). An
  invalid start is spanned as channel 1 (`invalid_start`): Reps 2 adds nothing, Reps 3 adds
  `Invalid PulseCount reps.` (review `f2-3xx-*`, 2026-10-24; this also removes the former
  false rejects `3`/`5` with Reps 2).
- The destination line precedes the channel line on every profile (`dc_order`).

Integration (round 31, W10): W6 and W9 both modelled `Channel %d exceeds` for the CR6 bridges and
`Invalid PulseCount reps.` for the CR1000X. The integrated tree printed those lines twice (CR6
bridges in this fixture; CR1000X PulseCount here, in `r31_settling_overlap.json` and in
`r24_dest_counts.json`). One owner each is kept, as stated above. The 28 CR6 BrFull/BrHalf4W/BrFull6W expected failures (excitation span and
overlap) now match through W9's validator. The CR300/CR350 settling-range line that W9 added must
not hide `-t` entries (`cli._SINGLE_PASS_KEEPS`, `docs/cli.md` "-t"; `timing_r16` `cr300_measure`
and `reps_large`).

Remaining gaps (the 54 expected failures):

- CR300/CR350 `ExChan exceeds number of excitation channels.` (25).
- CR300 PeriodAvg `Invalid channel: %d.` / `Reps exceeds valid channels.`, and the CR6/CR1000X
  PeriodAvg negative Reps (11).
- Thermistor negative Reps and burst-channel spans (9). Two further CR6 Therm107 cases show the
  bridge-validator overlap and channel lines, which the thermistors do not run.
- Pulse port and channel-0 messages (5): CR300 `SE1 is not a valid pulse count port.`, CR1000X
  `Selected PulseCount config option is not compatible with channel type.`, CR300/CR350 VoltDiff
  channel 0 `Invalid channel number.`.
- CR1000X BrHalf excitation past `Vx4` (1).
- A CR1000X BrHalf `mV2500` range-code line, printed twice by the real compiler. It is also absent
  at round 30, so it belongs to another family (1).

Not probed: Resistance, the AM25T, and CR6 negative burst channels on the bridges.

## Bridge channel spans and settling-time timing (round 31)

Fixture `tests/fixtures/r31_settling_overlap.json` (801 reference observations on all four
profiles, 2026-10-23; 749 exact, 52 strict expected failures; 254 matched before this round) plus
43 flipped cases of `r24_dest_counts.json`. CR6 evidence was read from the decompiled CR6Comp (addresses below) and
confirmed on the real compilers; CR1000X rules are fitted to observations only (no cr1000xcomp
addresses). `tools/trace_times.py` recorded 269 CR6 bridge/CurrentSE time additions
(`scratch/r31/trace.json`, untracked); the model reproduces all of them.

**Bridge span validator** (CR6 only; `instr_measure._check_bridge_span`; `FUN_00516170`, called
from the shared analog builder `FUN_005147a0` for positive channels). With `s`/`e` the zero-based
`U` index of the measurement and excitation channel, Reps `R`, MeasPEx `M` (below 1 counts as 1),
`g` measurement groups and stride `d` (BrHalf 1/1, BrHalf3W 2/1, BrFull 1/2, BrHalf4W and
BrFull6W 2/2):

- `meas_last = s + (R-1)*g*d + (g-1)*d`, `ex_last = e + ceil(R/M) - 1`.
- `s < e <= meas_last` -> `Measurement channels and excitation channels overlap.` (equal starts
  and an excitation start below the measurement start are not reported; the binary's second
  branch, `e < s and meas_last <= ex_last`, cannot hold for Reps >= 1). RevEx and RevDiff do not
  enter it.
- Then the first further excitation channel past U12, else (Reps > 1) the first further
  measurement channel past U12, -> `Channel %d exceeds number of valid channels.` (Reps 1 never
  reaches the measurement loop: `BrHalf3W(X,1,..,U12,..)` and `BrFull6W(X,1,..,U11,..)` compile,
  `r31_measurement_spans.json` `c_r1`/`px_r1`; the integrated code first lacked this gate.) The
  start channel itself (`0`, `-5`, `213`, `250`, an even differential start) stays with
  `terminals.py`, which skips its own last-repetition span for these five CR6 bridges
  (`terminals.CR6_BRIDGES`): that fit misses the second group of the last repetition
  (`BrHalf4W(ZZ,3,..,U3,U1,1,..)` -> `Channel 13`, sweep `ov-4w-r3-m1-u3-*`). (`FUN_005160a0`
  with a raw index; the excitation failure returns before the measurement loop).
- Both follow the line's destination bounds error and precede later lines. 152 CR6 sweep
  programs matched the inequalities exactly, including the boundaries (`U1`/`U3` starts, Reps 1-3,
  MeasPEx 1-3, every `U1..U12` excitation). CR1000X (separate `Vx` terminals) never reported it;
  CR300/CR350 report other excitation/channel-count messages (not modelled).

**PulseCount Reps span** (`_check_pulse_span`, CR6 only; `FUN_00510250`). The port index `i`
(`C1..C4` 0-3, `U1..U12` 4-15, `C5..C16` as `U`; from 201 up the converter `FUN_00515ff0` keeps
`code % 200 + 3` when `code % 200 <= 12`, so `412` is `U12`, `400` `C4`, `612` `U12`; review `f2`)
plus `Reps-1` must not exceed the family limit,
4 for `i < 4`, else 16 (one past C4 and U12, as observed). Else `Invalid PulseCount reps.` after
the bounds error. CR1000X (fitted: `P1,P2`, `C1..C8`, the same limits) is the `pulse_chan_cr1000x`
terminal span of "Measurement destination and channel spans (round 31)"; both workers modelled it
and the integration kept one (the duplicate printed the line twice). CR300/CR350 also report it but
add `... is not a valid low level AC input port.` (not modelled).

**CSAT3 negative Reps** (CR6 `FUN_00436d60`): Reps is a signed byte; a negative byte (Reps
128-255, -1..-128 modulo 256) fails the allocation before the destination is resolved ->
`Out of memory.`, and the compile ends there: errors of later statements (and later `const_ranges`
findings, deferred index bounds and early warnings such as `Scan never reached at run time.`) are
dropped, earlier ones stay (`checker/__init__.py`, after tier C; review `f7`).

**Settling time in the scan budget** (`scan_time.analog_units`; `FUN_00516740` returns the value
+810 without clamping, `FUN_00524f90` computes in 32-bit `int`, `NextScan` `FUN_0046a4b0` compares
unsigned). The settling argument is converted as C `(long)` (a string or a value past Long is
-2147483648) and counted whatever its range check says; every step wraps to 32 bits, the float to
int conversions give -2147483648 out of range, the final division truncates toward zero. A
negative total fails the unsigned `interval < total` test and prints a negative amount:
`VoltSE(...,"a",...)` -> `by -2148 Sec.` (`-2147574 mSec`, `-36 Min`); two such calls -> `-4295`;
Reps 2 doubles back to a small positive total (no message), Reps 3 does not. Probed values:
5999999, 6000000, 6000001, 1E8, 1.5E9, 2^31-1 (wraps on the +810), 2^31, -1, -20, -1E9, strings.
A settling time that is a float infinity (`1E39`, `1E308`, `INF`; `1E38` and `1E20` are not)
shows as `Settling time 2147483647 too long` on all four profiles (`inf_max` in `measure.json`)
and times like `-2147483648`; `-1E300` stays -2147483648 (review `f6-st*`). The bridge timing's
float casts overflow to infinity instead of raising (`scan_time._f32`, fN1 `1E300`, review
`f6a`; a float-zero fN1 such as `1E-50` times as 0). The fN1 value rule is in
`docs/instructions.md` "Invalid fnotch, minimum allowed".

**Bridge and CurrentSE timing** (`scan_time._bridge_units`): one analog call per MeasPEx group
(one call with all Reps when MeasPEx equals Reps, else `ceil(R/M)` calls of `M` and the
remainder), each doubled in-call for RevEx, RevDiff (BrFull/4W/6W) and the second measurement
group (BrHalf3W/4W/6W). Autorange and open-detect ranges are modelled only for one-group bridges
(BrHalf, BrFull); burst channels are not. CR6 `CurrentSE` ignores MeasOff (and the "C" range) in
its timing and allows Reps 1 only; CR1000X `CurrentSE` times as `VoltSE` (MeasOff adds one
measurement). 158 programs of ten identical bridge/CurrentSE lines in `Scan(1,mSec)` matched the
reported excess exactly on CR6 and CR1000X (this resolves single 100 us units per call). At the
model's first quiet `mSec` interval and one below it, 16 single-bridge scans gave the same budget
error / background-calibration warning / clean verdicts except one CR1000X `BrHalf` at 50 Hz,
where the warning still shows one mSec higher (the CR1000X warning threshold is a fit,
`docs/instructions.md` "Scan time budget").

**Settling ranges added**: CR6/CR1000X `BrHalf3W` (9th argument), `BrHalf4W`/`BrFull6W` (11th),
20..6000000. CR300/CR350 `VoltSE`/`VoltDiff` (6th), `TCSE`/`TCDiff` (8th), `BrHalf` (9th),
`BrFull` (10th): 0 or 10..50000 (`... min time is 10.`/`... max time is 50000.`), after the fnotch
warning. **CR1000X `CurrentSE` channel**: `RG1`/`RG2` (401/402, also negative, fractions
truncated) else `Invalid channel %d, must be RG1 or RG2.` before the settling message.

Remaining gaps: on CR6 the 4W/6W settling, `Excitation exceeds 2500.` and `Invalid measurements
per excitation` messages precede the overlap line but the checker prints the overlap first (or
does not model them); the CR1000X calibration-warning gap above; a CR1000X channel literal past Long shows as -2147483648 (not modelled);
`Resistance` and the other callers of the span validator, burst-channel timing and the
two-group autorange timing are not modelled; the two `r24` "lone first table bounds line" cases
(a multi-index `Sample` source whose evaluator bounds error does not gate pass 2) remain.

Review of the integrated round-31 diff (Codex, `scratch/codex/review-r31.md`, nine findings with
predicted outputs): `tests/fixtures/r31_review_fixes.json` records 420 reference observations of
2026-10-24 on all four profiles (the nine programs, their neighbours and the channel, fN1 and
settling sweeps; 406 exact, 14 strict expected failures, the CR300/CR350 dimensionless-parameter
indices and `Q() As Long` above). All nine findings were confirmed; the rules are noted in the
owning sections above as "review `fN`". Not predicted by Codex: CR300/CR350 PulseCount channel
validity and families, `truncate` on CR1000X PulseCount, negative and float-zero fN1, settling
infinity, later early warnings after the CSAT3 OOM, and routine-scoped aliases in String lengths.

## Parenthesized statements (round 33)

Oracle, all four loggers, 2026-10-24; fixture `tests/fixtures/r33_geus_false_rejects.json`. One
pair of parentheses around a whole assignment or instruction compiles as that statement, on its
own line or as a one-line `If`/`Else` body: `(w = 0)`, `If c Then (w = 0) Else (w = 1)`,
`((w = 0))`, `(L = 0)`, `(s = "a")`, `If c Then (Delay(0,1,mSec))` (SequentialMode, as without the
parentheses). A group that is not a statement keeps its error: `(w)` and `(w + 1)` are
`Invalid, or out of place expression`, `(5 = w)` is `Invalid assignment in expression`, CR6/CR1000X
`(Nope = 0)` is `Invalid, or out of place expression: (Nope = 0).` and CR300/CR350 only
`Nope is not defined.` The parser unwraps the group in `_parse_segment` when the inner tokens parse
to an assignment or instruction. GEUS `GIOS_WAT_BR_V4_0.CR1X` (`Then (wind_direction = 0)`) was a
false reject from this.

Not modelled: CR6/CR1000X report an unclosed `(w = 0` as `No Matching, or out of place ) in
expression: (w = 0.` and `(w = 0)(w=1)` as `Incomplete expression: (w = 0)(w=1).`; CR300/CR350
accept both. Whether the logger executes the parenthesized assignment is not observed on hardware.

## Aliases named Status, Public or Settings (round 33)

Oracle, all four loggers, 2026-10-24; same fixture. `Alias D(3)=Status` (or `=Public`, `=Settings`)
makes the name a plain value throughout the program, also above the `Alias` line:
`Sample(1,Status,FP2)`, `X = Status` and `Status = 2` compile, like a variable of that name already
did. `Status.Battery` and `Public.X` still read the system tables. GEUS
`Archive/QAS_B_LoggerProgramV2.CR1X` (`Alias D2out(3)= Status`, `Sample (1,Status,FP2)`) was a false
reject from this. Not modelled: CR300/CR350 report `X = Status(1)` on the scalar alias as
`Dimensions not declared: X = Status(1)` (a best-effort false accept).

## Alias names that are CRBasic names (round 33)

Oracle, all four loggers, 2026-10-24; same fixture (groups `aname` and `kw`). The reference accepts
an `Alias` named after an instruction (`Battery`, `VoltSe`, `DataTable`), a built-in function
(`Sin`), a system table (`Public`, `Status`, `Settings`), a special value (`INF`; `NAN` from
`alias_predefined_name.json`) or a keyword (`Then`, `Else`, `To`, `Step`, `As`, `EndIf`, `Next`,
`Dim`, `AND`, `OR`, `NOT`, `XOR`, `MOD`). It rejects names in the logger's predefined-CONST table
(`If`, `Scan`, `Case`, `Sub`, `Function`, `TRUE`, and on CR300/CR350 also `Long`, `Boolean`,
`Double`, `IEEE4`) with `<name> is a predefined CONST.`. CR6/CR1000X also accept `Double` as a
`Public`, `Dim`, `Const` or parameter name ([predefined constant name
collisions](#predefined-constant-name-collisions)).

The accepted set is the scope of opencrbasic check `OCB001` (`checks.py`, `crbc --strict`; human
request 2026-10-24): such a name shadows the original, and the result is easy to misread
([aliases named Status](#aliases-named-status-public-or-settings-round-33)). Controls: none of the 19
FirnMelt programs fires; of the 66 GEUS programs only `QAS_B_LoggerProgramV2.CR1X`
(`Alias D2out(3)= Status`) does.

## Positions outside the multi-report order (round 34)

Oracle, CR6/CR1000X, 2026-10-24; fixtures `tests/fixtures/r34_undeclared_positions.json` and
`tests/fixtures/r34_multi_order_sweep.json`. `_MULTI_REPORT_ORDER` comes from round-14 sweeps with
every argument undeclared, so it lists only the positions reported in that case. A position missing
from it is still checked when it is the only bad one: `EC100Configure(R,7,Nope,9600)` and
`SendVariables(...,Nope,...)` are `Invalid symbol in expression: Nope.` (three replay false
accepts). The checker now checks the listed positions first and, when none reports, the remaining
general positions in ascending order, stopping at the first. A sweep over the help examples replaced
each unlisted position of each listed instruction with an undeclared name (57 mutants, both
profiles): every verdict and first error agrees, except the two below, now modelled.

- `StdDevRun` position 8 (StdDevType) is never resolved: `StdDevRun(...,D(Nope))` compiles. `AvgRun`
  position 7 is resolved.
- `TimedControl` Size and Interval (positions 0 and 1) must be constants: a variable or an undeclared
  name gives `Size must be a constant.` / `Interval must be a constant.` (both when both are bad;
  `check_timedcontrol_constants`). A declared variable there was a false accept.
- `SDMSIO4` position 0 is resolved unless the value count (position 7) is 0; round 14 saw only a
  zero count. With N values from `D(i)`, `i + N - 1` past a one-dimensional `D` (a scalar holds one)
  is `Variable D out of bounds.` (`check_sdmsio4_count`).

## DataTable fields, Settings names and Status targets (round 34)

Oracle, all four loggers, 2026-10-24; fixture `tests/fixtures/r34_table_settings.json`. These were
the 2026-10-24 probe false accepts (`scratch/r32/lint/`).

- An unknown DataTable field read, `X = Tbl.NoSuchField(1,1)` or `Tbl.NoSuchField`, is
  `Could not find Tbl.NoSuchField(1).`; fields, `Record`, `TimeStamp` and `Output` compile. The field
  names come from `tables.build_tables` (the TDF naming, byte-identical on the corpus) plus
  `tables.TABLE_PROPERTIES`; when the tables cannot be built nothing is checked. CR6/CR1000X only in
  the checker; CR300/CR350 are not modelled.
- An unknown `Settings.` name, read or assigned, is `Could not find Settings.Nope(1).`; the names are
  the `SetSetting` list of `settings_fields.json`, all of which read clean on CR6/CR1000X (sweep in
  the same fixture). An assignment adds `Invalid assignment in expression: <statement>.`
- An assignment to an unknown `Status.` field gives the same two lines as for `Settings`.

## Code after EndSequence, SubMenu bodies (round 34)

Oracle, CR6/CR1000X, 2026-10-24; fixture `tests/fixtures/r34_false_accepts.json`.

- Between an `EndSequence` and the next `SlowSequence` (or `EndProg`), a statement that would be
  rejected before `BeginProg` is `<Name> illegally occurs after EndSequence.` with the same name
  (`Scan`, `Delay`, `Expr` for an assignment), and pass 2 stops (`check_pass2`, `after_endseq`). A
  second `SlowSequence` right after `EndSequence` compiles; a `Scan` after a `NextScan` with no
  sequence compiles with the `never reached` warning.
- `SubMenu .. EndSubMenu` is not a deferred body: `Delay` or `X = 1` inside it before `BeginProg`
  is rejected as at the top level of `DisplayMenu`. `SUBMENU` left `_BODY_BLOCK_STARTS`.

## SerialInRecord, SerialOpen and running-statistic instances (round 34)

Oracle, CR6/CR1000X, 2026-10-24; same fixture.

- `SerialInRecord(Port,Dest,StartWord,NumberBytes,EndWord,...)` with a constant port that no
  `SerialOpen` in the program opens (in a `Sub` or on a later line counts; a false `#If` does not):
  `Number of bytes %d must be less than input buffer 0`, also for 0 bytes. With any `SerialOpen`
  of that port it compiles, whatever its buffer size. A variable port is not checked.
- NumberBytes, StartWord and EndWord all nonzero with StartWord or EndWord negative:
  `StartWord, NumberBytes and EndWord cannot all be not used`, before the buffer line. This rule is
  fitted to 13 probes, not derived from the binary.
- `SerialOpen` with a variable `BufferSize` is `Parameter must be a constant integer:
  <statement>.`, like a variable `TXDelay`.
- The number of distinct instances of `MinRun`/`MaxRun` is position 5 and of
  `AvgRun`/`TotalRun`/`StdDevRun` position 6; the instance number follows. A missing instance
  number is `Missing instance number`; a variable or `0` count is `Optional number of distinct
  instances must be a constant of type long.` (round 6 had this for `AvgRun` only).
- CR1000X `PulseCount` on `P1`/`P2` twice in a scan is `P1 already used as PulseCount in this
  scan.` without the `Pulsecount: channel ... in use` line that a `C` terminal adds.
- `Public C As Long2,` (trailing comma) is still `Variable Type Long2 not supported`.

## Fresh-seed findings (round 35)

Oracle, CR6/CR1000X, 2026-10-25; fixture `tests/fixtures/r35_fresh_seeds.json`. Seeds 421-424 with
the round-35 operators (typos, neighbouring numbers, swapped arguments) and the GEUS programs as
CR1000X seeds found three false rejects and these false-accept families.

- **A second `AVW200`.** After an earlier `AVW200` in the program, an undeclared name at positions 1
  and 2 (ComPort, NeighborAddr) compiles; at 3 to 10 the reference prints garbled or value messages
  (one echoes uninitialised bytes), at 0 and 11 to 13 `Invalid symbol`. Positions 1 and 2 are not
  resolved then. Round 34's check of positions outside the multi-report order had made this a false
  reject.
- **32-bit conversions.** A `String * N` whose padded storage length passes INT_MAX
  (`* 2147483647` and up) compiles; `* 2147483584` is still `Out of memory.` Such a variable and a
  table field that size are left out of the storage model. A CR1000X differential channel compiles
  when `|channel| + 7` passes INT_MAX (from 2147483641, any Reps) or the constant is outside int32
  (`overflow_add` in `terminals.json`); a single-ended channel outside int32 is reported as
  `Invalid channel -2147483648, must use 1..16.` (`huge_int_min`). CR6 prints other messages for
  these values (both reject; not modelled).
- **Aliases at constant-only positions.** An `Alias` name where a constant is required is a
  variable: `Sample(1,IEEE4,Ux)`, `VoltSe(A(),Vy,...)`, `Delay(Ux,1,Sec)`, `PanelTemp(X,Vy)`, `Public
  B(Vy)` are `Variable illegal in Parameter`. An alias of a predefined constant (`Alias X = Sec`,
  itself an error) stays the constant.
- **String Reps.** A string as a measurement's Reps (`PulseCount(R(1),"2",C1,...)`, even `""`) is
  INT_MIN: `Invalid reps -2147483648, must be > 0`, then the destination bounds of a negative Reps.
- **`Delay(2,0,Sec)`** is `Invalid time specified.` (option 2 with interval 0; 1 and -1 compile).
- **Running statistics.** `MinRun`, `MaxRun`, `TotalRun` and `StdDevRun` bound their destination by
  Reps like `AvgRun`, including a routine's scalar parameter (`Sub R(Q)` / `MinRun(Q,2,...)`).

Not modelled: a negative `Delay` option-0 interval (`Program too big for task memory`, repeated; an
allocation model), and `Scan interval too short ... by N` is one second high on our side for long
delays (`Delay(0,3600,Sec)` in a 10 s scan: 3590 on the reference, 3591 here).

## Relevant-instruction seeds (round 36)

Oracle, 2026-10-25; fixture `tests/fixtures/r36_relevant_seeds.json` (2,414 observations, 51
strict xfail). Seeds 425-428 ran with `tools/fuzz.py --relevant`: help examples only where every
instruction and function they use is also used by a FirnMelt or GEUS program (49 CR6 and 56
CR1000X examples, next to the repository and GEUS seeds). They found 13 false accepts and no false
reject; 12 are fixed below.

### Timer arguments (round 36)

`Timer(TimNo, Units, Option)`: Units and Option must be integer-typed constants, on all four
loggers. A variable, a function call (`Abs(2)`), `/`, `^`, a float literal, `INF`/`NAN`, or a
`Const` whose value is not an integer is `Parameter must be a constant integer: <statement>.` (the
`If` condition alone for an `If`; CR300/CR350 end the echo with a newline). An integer `Const` with
a float value (`Const K = 2.0`) is accepted. Above INT_MAX the value is the constant-integer error
on CR6/CR1000X; CR300/CR350 accept magnitudes below 2^32 as out-of-range options. `&H` literals
wrap (`&HFFFFFFFF` is -1). An integer Option outside 0..4 (`7`, `-1`, `True`, `K+5`, `1>0`) is
`Legal options are 0..4`. TimNo's integral typing is modelled in
[round 46](#timer-number-and-nonfinite-integral-arguments-round-46). Inside an output instruction
outside a table the table-only error wins. Not modelled: CR300/CR350 also print the constant-integer line after
`Nope is not defined.`

### IIF arm types (round 36)

An `IIF` whose second and third arguments are one String-typed and one numeric is `type mismatch`
on its line, wherever the call sits (assignment of any type, `If` condition, instruction argument,
nested `IIF`), on all four loggers. String-typed: a string literal or `Const`, a String variable, a
String function (`Left`, `Chr`), `+` with a String side (`"1"+1`, `S+1`). Both arms String or both
numeric compile, and the condition may be a string.

### Settings name must be constant (round 36)

`SetSetting`/`SetStatus` with a first argument that is not a constant string (a number, `(1+2)`, a
variable, a String variable, `NAN`, `True`, `Status.X`, `S+"x"`, `CHR(80)+"..."`, an undeclared
name) is `Status Field name must be constant.`, on all four loggers; after `Invalid symbol` for an
undeclared name. Not modelled: CR6 `SetSetting((1+2), ComC7)` reports only this line (we also
report `ComC7`, which CR6 does not define).

### String operands of INTDV (round 36)

`INTDV` and `\` reject String operands like `*`, `/` and `MOD`: `Illegal String operand` (left),
`Illegal operand of type STRING` (right), all four loggers.

### SerialIn ComPort codes (round 36)

A constant `SerialIn` ComPort converts to a 32-bit long; its absolute value must be 1..18 or
32..47 on CR6, 1..14 or 32..47 on CR1000X, else `ComPort N illegal.` with that absolute value
(`-2147483648` as is, for strings and overflow). Floats truncate (`0.5` is 0, `15.9` is 15). Other
serial instructions check ports differently: `SerialOpen` has its own set
([round 37](#serialopen-comport-codes-round-37)), `SerialBrk` has its own named set,
`SerialOut`/`SerialFlush`/`SerialClose`/`SerialInBlock` accept 49 and 100.

### EmailRelay argument count (round 36)

`EmailRelay` takes 4, 5, 9 or 10 arguments: fewer and 6 to 8 are `Expression expects , :`, 11 and
more `No Matching, or out of place )`, all four loggers.

### If( without a blank (round 36)

`If(c) Then` and `ElseIf(c) Then` are checked like `If (c) Then`; the echo starts at the `(`.

### Output data type codes (round 36)

`Sample` data types 0..255, swept alone and beside a second field (CR6 and CR1000X identical, and
`Average`, `Maximum` and `WindVector` agree on the probed codes):
- legal: 2, 3, 6, 7, 9, 11, 18, 20, 24, 25, 28 and 128..255 except the silent set below;
- `Illegal data type parameter N in <Instruction> instruction.`: every other code in 0..127 except
  1, 14 and 17. Of those, 0, 16, 29..43, 48..56, 60..63 and 94 also produce no field;
- no message and no field: 156..159, 180..183, 204..211, 220..223, 236..239, 252..255;
- a table whose only fields are fieldless is `Table T declared with no output fields.` at
  `EndTable`;
- 1 (UINT1) and 17 (BOOL8) with odd constant Reps: `Reps N illegal; with BOOL8 and UINT1 data
  types, reps must be evenly divisible by 2`.

`WindVector`'s DataType is its fourth argument. Not modelled: values outside 0..255 (256..260 and
65536 are illegal, 511, 65535 and -1 follow other rules); `Sample type UINT1 can only sample a
variable of type UINT1.`; `Float to NSEC expects source array size >= 7` and `NSEC type is legal
only with Sample()` for 14. CR300/CR350 keep the round-17 set.

### ExciteV channels on CR1000X (round 36)

CR1000X `ExciteV` takes only `Vx1..Vx4` (601..604): any other constant, including 1..4 and a
string, is `Invalid channel, must use Vx1..Vx4.` (`measure.json` `const_ranges`).

### Flagged

- `7b14df1ed0f8` (CR1000X): `SerialOpen(10,...)` claims the port that a later
  `SDI12Recorder(...,C3,...)` uses. Fixed in round 37 ([terminal ownership](#terminal-ownership-round-37)).
- Mode difference `e7e12f2d1011`: `TimeIntoInterval(60,"fuzz",sec)` warns twice `Parameter will
  truncate to the nearest integer.`; not modelled.

## Terminal ownership (round 37)

Oracle, CR6/CR1000X, 2026-10-25; fixture `tests/fixtures/r37_terminal_ownership.json` (640
observations, 3 strict xfail, all CR300/CR350 cascades). The reference keeps one owner role per
terminal; a claim in another role is `<Instr>: channel <T> already in use or unavailable.` on the
claiming statement (`instr_rules.check_terminal_conflicts`). Roles:
- serial: a constant `SerialOpen` ComPort (`ComC1`, `9`, a `Const`, parenthesized; CR6 also
  `ComU1..ComU11`) claims both terminals of its pair (`ComC1`: C1 and C2) for the whole program,
  before any other claim. So an `SDI12Recorder(...,C3,...)` reports even when `SerialOpen(ComC3,...)`
  comes later in the file, in another scan, in a `Sub`, or after `SerialClose`. A variable ComPort
  claims nothing; `#If 0` code does not count;
- SDI-12: `SDI12Recorder` and `SDI12SensorSetup` (only valid SDI-12 terminals; `101` is `C1`);
- digital output: `PortSet` (`C`/`U` names or `101..`/`201..`; `1` is its own `Invalid port`) and
  `WriteIO`, one claim per set mask bit 0..3 (CR6, C1..C4) or 0..7 (CR1000X, C1..C8), each
  conflicting bit reported in bit order;
- pulse: `PulseCount`'s channel.

The other claims are in textual order, `If`/`Else` branches, scans and `Sub` bodies alike; the
later statement reports. Two statements in one role never conflict here (`SDI12Recorder` twice,
`PortSet` then `WriteIO`, `SerialOpen` twice); `PulseCount` reuse keeps its own rule
([PulseCount terminal reuse](instructions.md#pulsecount-terminal-reuse-cr6cr1000x)). `ReadIO`,
`PortPairConfig` and `PWM` on a serial terminal compile.

A conflict hides nothing: later tier-B, bounds, range and same-line errors still print, and the
conflict prints first on its line. `PortSet`, `WriteIO` and `PulseCount` conflicts print even after
an earlier error. An SDI-12 conflict prints only when no error, another conflict included,
precedes it, so a second `SDI12Recorder` conflict is silent. A pass-1 error hides them all.

An `SDI12Recorder` and an `SDI12SensorSetup` on one terminal, in either order, is `SDI Recorder and
Sensor cannot use the same port.` on the later one, also after an earlier conflict.

Not modelled: `WriteIO` mask bits past the C terminals (CR6 bit 4 and up compiled beside
`ComU5`), `TimerIO`, CDM and other port instructions. CR300/CR350 report `Port/Channel: C1 already
in use or unavailable, cannot be used in SDI12Recorder.` (CR350 accepted `PulseCount` after
`SDI12Recorder` or `PortSet` on its terminal); best effort, not modelled.

### SerialOpen ComPort codes (round 37)

A constant integer `SerialOpen` ComPort must be 1..18 or 31..99 (CR6) or 1..14 or 31..99 (CR1000X)
(swept -20..129 and wider values). Any other 32-bit integer, `True` and `-ComC1` included, is
`ComPort N illegal.` then `No Matching, or out of place ) in expression: <statement>.`; a
non-integer or a value past 32 bits is `ComPort 0 illegal.` then `Parameter must be a constant
integer: <statement>.` Both are pass 1 and end the statement: its baud, delay, buffer and arity
errors are not reported, nor is an argument-count `Expression expects , :` after the older
variable/string ComPort errors. A bare `SerialOpen()` keeps its `Invalid, or out of place
expression`. CR300/CR350 not probed.

## Fresh-seed findings (round 38)

Oracle, 2026-10-25; fixture `tests/fixtures/r38_fresh_seeds.json` (688 observations, 17 strict
xfail: 12 CR6/CR1000X message-level rows listed below, 5 CR300/CR350 cascades). `--relevant`
seeds 429-432 (CR6 429/430, CR1000X 431/432, 500 mutants each) found 2 false accepts, no false
reject, 44 message and 3 warning differences; both false accepts and the families below are fixed.

### SDI12Recorder NAN fill (round 38)

CR6/CR1000X: an integer constant `SDI12Recorder` seventh argument (NAN fill) must be -1, 0 or
1..room, room being the elements from the numeric destination's first one to the end of its
storage (`A()` of `A(12)`: 12; `A(2)`: 11; a scalar: 1; `M(2,1)` of `M(3,4)`: 8; `Long` arrays
alike). An alias of a Float array counts to the end of its backing array (`Alias A(3) = Third`:
10). Otherwise `Illegal NAN fill locations, legal is -1 or 1..<room>(room in the destination
array)`, without a period. Non-integers (`1.5`, `-1.5`), values past 32 bits, strings and `True`
compile; a variable is `Variable illegal in Parameter`. String destinations are not checked, and a
single index on a multidimensional destination is not modelled. The message is an aside like the
index bounds (`instr_rules.check_sdi12_nan_fill`): tier-B, range, bounds and placement errors on
other lines still print, and it follows `Invalid SDI-12 port` on its own line. CR300/CR350 not
probed.

### SDI12Recorder destination bounds (round 38)

`SDI12Recorder`'s destination (type code 27) is index-checked like `Move`'s, on all four loggers:
Float, Long and String arrays, index 0, `A(1,2)` and a scalar `B(2)`; CR300/CR350 report the
over-subscripted forms as `Dimensions not declared`.

### Single index on a multidimensional array (round 38)

A single constant index on a multidimensional array is checked against the first dimension, not
the element count: `Battery(M(4))`, `PanelTemp(M(4),60)`, `Move(M(4),...)`, `ReadIO`, `PortGet`,
`SplitStr`, `Erase` and `SDI12Recorder` of `M(3,4)` are `Variable M out of bounds.`, `M(3)`
compiles (all four loggers). This replaces the element-count rule of
`expressions.check_instruction_index_bounds`; no earlier fixture observation contradicted it.

### Dotted fields at constant positions (round 38)

At a constant-only position (an output `DataType`, `PanelTemp`'s integration, a measurement's
range or reference) a `Status.`, `Settings.`, `Public.` or table field is `Variable illegal in
Parameter: <text>.`, like a plain variable, ahead of an unknown-field `Could not find` (all four
loggers; `Sample(1,UINT2,status.SerialNumber)` from GCNETv3_1.CR1X).

### Odd UINT1/BOOL8 Reps order (round 38)

The round-36 odd-Reps message prints first on its line: before a same-line `Invalid symbol in
expression` (it now goes with the line-leading findings), and before a source overrun, which is a
stage-2a error ending the stage (`Sample(9,B,1)`, `Sample(String,S,1)`; CR6/CR1000X).

### Undefined table source ends the table checks (round 38)

CR6/CR1000X: stage 2a checks table-output statements in order and stops at the first whose source
names something undefined, so a later `Illegal Reps` or source overrun is not reported and pass 2
reports the name. Stage-2a findings outside tables (`Battery((1+2))`) still win.

### Arity beside undefined names (round 38)

CR6/CR1000X: a built-in call with the wrong argument count whose own arguments name something
undefined is not an arity error; the statement goes to pass 2 and gets `Invalid symbol in
expression` (`S = Right(Nope)`, `X = 1 + Right(Nope)`, `If Sin(Nope,1) Then`). `Sin(1,2) + Nope`
keeps the arity error, as does a name in a surplus argument the parser never reads
(`Right(S,10,Nope)`, `TimeIsBetween(0,15,60,sec,Nope)`, `MQTTPublish(...,Nope)`'s `Incomplete
expression`); an arity error on another line still hides it. Instruction arguments
keep the arity error: at a constant-only position (`PanelTemp(X,Sin(Nope,1))`) nothing else would
report it. CR300/CR350 report both lines and are unchanged.

### Logical String operands in a chain (round 38)

`If X AND S AND Y` (S a String) reports `String is illegal here` once per logical operator next to
the string, so twice (all four loggers). A statement whose findings are all this message now
reports each; mixed findings keep reporting nothing.

### Accepted or flagged (round 38)

- Message-level, strict xfail: `Battery(Sin(Nope,1))` and `PanelTemp(X,Sin(Nope,1))` (the arity
  error instead of `Invalid symbol`); `Sin(Status.Nope,1)` (the `Could not find` line first);
  `Sample type UINT1 can only sample a variable of type UINT1.` (round 36); a `GetRecord` of an
  unknown table beside an out-of-bounds destination (order).
- `TimeIntoInterval` with an undefined argument prints several `Parameter will truncate to the
  nearest integer.` warnings before the same first error (nine, six or three for argument 1, 2
  or 3 in an `If`; four in an assignment). A cascade that does not mislead; accepted.
- Fragments that start at an orphan `Case Else` (`114315fdb7cf`, `e406ca2fdda2`): the reference
  reports `Else with no If.`, `EndSelect w/o Select` or an earlier `Sub ... not defined` depending
  on what precedes the fragment. Not a realistic mistake; flagged.

## First-error families (round 39)

Oracle, CR6/CR1000X unless noted, 2026-10-25; fixture `tests/fixtures/r39_first_errors.json` (224
observations, 12 strict xfail, all message-level and listed below). Taken from the saved replay's
CR6/CR1000X first-error misses (58) and earlier first locations (13); 26 and 7 of them were fixed.

### Unresolved statements and orphan EndSelect (round 39)

An assignment or bare call that names something undefined (target, value or an unknown bare call)
is left to pass 2, and from then on pass 1 no longer reports `EndSelect w/o Select`. Pass 2 reports
the statement first and treats the orphan `EndSelect` as a stray `EndIf`: `END IF without IF or END
SELECT without SELECT.` A declared target with a placement error (`X = 1` before `BeginProg`), an
undefined `If` condition, an explicit `Call` and a syntax error (`X = 1 +`) leave `EndSelect w/o
Select` as it was (`structure.check_pass1`, `check_block_closers`). A `Select Case` whose
condition fails to parse still opens its block in pass 2. Not modelled: undefined names in
instruction arguments (`Battery(Nope)`) and unknown `Status.` fields, which also turn it off, and
the `EndSelect illegally occurs before BeginProg.` line after a `Sub` (strict xfail).

### #If conditions (round 39)

All four loggers: an empty `#If` is `Invalid expression in #If statement.`; a dangling operator
`Invalid, or out of place expression: <text>.`; an unclosed `(` `No Matching, or out of place )
in expression: <text>.`; two operands `Incomplete expression: <text>.`; an undefined name `Invalid
symbol in expression: <text>.` (CR300/CR350 `<name> is not defined.`). The condition ends at `:`
or a mid-line `Then`. These replace the parser's own wording, which no compiler prints.

### Table output outside a table (round 39)

A `Sample`/`Average`/... outside a table reports only `<Instruction> must be used inside a table
declaration.`: stage 2a's `Illegal Reps`, source overrun and expression-source checks apply only in
a table body.

### Units targets last (round 39)

`Can't assign units to <name>, ...` comes after everything else: a missing `BeginProg`, an
unclosed `Scan` or `If`, and a stage-2a error each replace it. It now runs only when nothing else
was reported.

### Arity read left to right (round 39)

The round-38 deferral reads each expression left to right: an undefined name read before the
failing call defers the statement to `Invalid symbol in expression` (`X = Nope + Sin(1,2)`,
`Round(Nope, Sin(1,2))`, a `Call` argument `"len=" + FormatLong(Undefined, "%d") + FormatLong("%d")`),
while `Sin(1,2) + Nope` and `Round(Sin(1,2), Nope)` keep the arity error. Each argument of a `Sub`
call is its own expression (`Call Foo(Nope, Sin(1,2))` keeps it). Instruction arguments are still
not deferred (`Battery(Nope + Sin(1,2))`, strict xfail).

### Nested DataTable after unresolved names (round 39)

`DataTable without EndTable.` (a header inside an open table, any name) is raised when the nested
table closes. An unresolved name met first wins and pass 2 reports it: any name in the open table
(its sources resolve at the nested header), or a non-source argument of the nested table
(`DataInterval(0,Nope,...)`). The nested table's own sources resolve after its close and do not
(`Sample(1,Nope,FP2)` there still gives only `DataTable without EndTable.`). Not modelled: an
unparsed word inside the first table (`EndTable_Mut`) is printed before it (strict xfail).

## Messages and stale recordings (round 40)

Oracle, CR6/CR1000X unless noted, 2026-10-25; fixture `tests/fixtures/r40_messages.json` (122
observations, 4 strict xfail: two later-diagnostic and two order rows, listed below).

### Stale sibling recordings (round 40)

121 saved reproducers compile against the live `crbasic/` files (`' fuzz-needs-siblings`). Their
recorded reference output goes stale when those files change: the SBD schema v5 work added
`GPS_Lat` to `SbdPayloadLib.crb`, and five recordings still showed the pre-v5 first error.
`tools/fuzz.py` now stores `siblings_sha256` (a SHA-256 over the sibling files) with a recording;
`fuzz_replay.py --recorded` reports a reproducer whose digest differs, or is missing, as
`stale_recording` (operational, no verdict, never a regression); `--rerecord-stale` reruns the
reference for those with the current files and stores the result and digest. All 121 were
re-recorded on 2026-10-25: five results changed (the `GPS_Lat` rows); `75f68395c4e8` stays stale
because the reference faults under Wine on every try. The reproducers themselves are unchanged.

### Include prefix of pass-2 symbols (round 40)

`Invalid symbol in expression` from the pass-2 statement compile (a call, assignment, instruction
or `If` in a `Sub` of an included file) has no `<file>` prefix, with `Include` and
`IncludeSection` alike; from pass 1 (`Const K = Nope`, `Public Y(Nope)`) and for an arity error it
keeps the prefix.

### Over-subscripted arrays are pass 1 (round 40)

More than three subscripts (`Dimensions not declared`) is a pass-1 error: it hides the pass-2
`Invalid symbol in expression` errors on any line, the asides and tier C, while the structural
errors after it (`Case outside Select.`, `EndSelect w/o Select`) still print. Not modelled: beside
a pass-1 arity error it is also printed (strict xfail; only the arity error is printed by us).

### Constant destinations beside constant-only arguments (round 40)

`Invalid Variable or Array: <constant>` (a constant where a destination variable is needed) is
reported in the same stage as the constant-only argument errors (`Invalid symbol` or `Variable
illegal in Parameter` there), in statement order and before them on one line: `PanelTemp(15000,Nope)`
gives both. Other pass-1 errors still hide it. Not modelled: `SortSpaIndexed(6,A)` prints both
lines in the other order (strict xfail).

### Stray #Else, #ElseIf and #EndIf (round 40)

All four loggers: one with no open `#If` is an unknown `#` statement, `Unknown instruction: <line>`,
which ends the compile, rather than `#EndIf without #If.`.

### Scan-only placement order (round 40)

`PulseCount must be used within a Scan...NextScan.` follows a symbol error of its own statement
(`PulseCount(Nope,1,C1,...)` outside a `Scan`).

## Message families (round 41)

Oracle, 2026-10-25; fixture `tests/fixtures/r41_messages.json` (189 observations, 42 strict xfail:
24 CR6/CR1000X `CType` follow-up lines and 18 CR300/CR350 rows, all after a matching first error).

### #If echo up to Then (round 41)

A malformed `#If` condition echoes the text up to `Then` with its spacing (`Invalid symbol in
expression: Nope < 1 .`, two spaces kept when written); without `Then` the trimmed text. On
CR300/CR350 each undefined name is reported (`#If Nope < 1 Then`: `Nope is not defined.`). This
corrects round 39, which trimmed the space.

### Const names split by whitespace (round 41)

`Const A B = 1` (all four loggers) takes `A` as the name and the rest (`B = 1`, `B As Long = 0   `,
`B C = 1`) as the value: CR6/CR1000X `Invalid symbol in expression: <rest>.`, CR300/CR350 each
undefined name. Not modelled: the CR300/CR350 `Incomplete expression` after names, and our extra
`Invalid, or out of place expression` there.

### Const arity beside undefined names (round 41)

The round-39 left-to-right rule holds in a `Const` value on all four loggers: an undefined name
read before a wrong-arity call gives `Invalid symbol` alone on CR6/CR1000X (the name and the arity
error on CR300/CR350); a wrong-arity call read first (`4*ATN(1,Nope)`, `ATN(1,2) + Nope`) gives
only the arity error.

### CType type arguments (round 41)

The type must be `Long`, `Float`, `Double` or `String`, or a constant equal to the logger's
`UINT1`, `IEEE4` or `IEEE8` code (1, 9, 18 on CR6/CR1000X; a literal 9 is illegal on CR300, whose
`IEEE4` differs). Anything else is `Illegal type` (pass 1), all four loggers: `Boolean`, `UINT2`,
`UINT4`, `FP2`, `Bool8`, `NSEC`, `Sec`, `0`, `2`..`8`, `-1`, `1.5`, a variable, a string, an
undefined name. On CR6/CR1000X an undefined first argument (`CType(Nope,Long)`) is `Illegal type`
too. A variable or string type was a false accept. Not modelled: the statement's own message after
it (`Invalid symbol in expression`, or `Parameter must be a constant integer: <statement>.`), which
our pass-1 placement hides (strict xfail).

### Flagged (round 41)

Spaces inserted inside a number or name by the fuzzer (`SerialOpen(ComC5,11520  0,...)`,
`DataTable (Table  1,...)`, `StationName=MtLoganCR1  000X`): the reference reads the first part
(`11520 is illegal baud rate.`, `Too many parameters:  -1.`) where we give a generic message.
Not a realistic mistake; left.

## SerialBrk ports (round 42)

CR6's `SerialBrk` statement builder is `FUN_004731f0` (instruction thunk `0x4019d3`):
`FUN_005077e0` resolves the port, `FUN_00507ee0` tests whether it is a numeric constant,
and the builder compares the converted long against 9..16. Its message is
`Invalid COM port, must use COMC1, COMC3, or COMU1..COMU7` (no final period).
The independent implementation uses the shared long conversion and a pass-2 leading finding.
It does not copy the decompiled implementation.

Four-profile evidence, observed 2026-10-26: `tests/fixtures/r42_serialbrk.json` (172
observations). CR1000X accepts 9..12 and reports
`Invalid COM port, must use COMC1, COMC3..COMC7` otherwise. The numeric boundary sweep covers
0, 1, 8..18, 31, 32 and 99. Negative ports retain their sign; floats truncate toward zero
(9.9 accepts, 8.9 rejects; 16.9 accepts only on CR6). Named numeric constants and arithmetic
use the same rule. NAN, a numeric float overflow and `&HFFFFFFFF` reject. A hex literal
above eight digits was accepted in the probed case; shared conversion leaves that form unchecked.
Strings (literal and named constant) and runtime variables bypass this validation. CR300/CR350
accept all tested numeric ports, including invalid CR6/CR1000X codes, and receive no new check.

The port diagnostic precedes an unresolved Break argument on the same line, coexists with
unresolved assignments before and after it, and also applies to a one-line `If` body. Too few
or too many arguments suppress this builder check. A `SerialBrk` in an expression is rejected
by the existing symbol check and never reaches this statement builder. Controls cover those
shapes and valid calls on every profile. These are compiler observations; runtime port behavior
has not been tested. Constant function calls and unprobed malformed-statement cascades are
outside this round's rule.

## Folded include filenames (round 43)

A fresh CR6 seed-433 mutation, `d1cd86d16934`, replaces a filename with `(1+2)` in
`IncludeSection("Definitions",(1+2))`. The reference reports
`Include file 3 must have valid program file extension.` while the old loader/parser silently
kept the unexpanded include and reported missing definitions later. Minimized probes also expose
false accepts when the program does not use the omitted definitions.

CR6's read loop `FUN_004b6840`, cases `0xa6`/`0xa5`, resolves the Section/filename with
`FUN_004ba5f0` and `FUN_004ba850`, then validates the extension with `FUN_004b66f0` before
opening a file. That resolution happens while reading, before pass-2 symbol diagnostics.
The implementation adds a bounded parser check against the constants defined so far; literal
include expansion and folded-name validation share `source.bad_include_extension`.

Observed on 2026-10-26, four profiles: `tests/fixtures/r43_include_names.json` has 148 observations
(138 match, 10 strict xfail). Both `Include` and `IncludeSection` accept constant expressions as
filenames. Small numeric constants, named numeric constants and `Abs(-3)` render as numeric
text, including seven significant digits (`1.23456789` -> `1.234568`), exponent notation,
`True` -> `-1`, `&HFFFFFFFF` -> `-1`, `NAN` -> `-nan`, and `INF` -> `inf`.
CR6/CR1000X reject the resulting invalid extensions; CR300/CR350 generally warn that the file
cannot be opened, but reject the three-character extension in `1.234`. Prior string constants
with invalid extensions are checked too. The check is silent in inactive `#If` branches and
after the first `EndProg`. CR6/CR1000X include errors hide pass-2 unresolved assignments on
either side; CR300/CR350 retain their warning alongside an unresolved assignment.

This does not add loading for filenames expressed as constants or string expressions with a
valid extension. Existing literal-path loading remains in the source loader. Controls include
literal and expression-shaped valid filenames, but their acceptance alone does not demonstrate
that their file contents were included. Remaining fixture gaps: variable/undefined filenames on
CR6/CR1000X (four), integer overflow rendered as `NAN` (four), and the pre-existing CR300/CR350
literal `"x"` warning difference when `x.crb` exists (two).

Raw probe matrices stay in ignored `scratch/r43/observations.json` and `extra.json` (156 total
observations). Eight are deliberately outside the deterministic fixture: four have corrupted
replacement-character filename echoes (`CHR(51)` and `-0.0` on CR6/CR1000X), and four have a
control-character filename for a variable/undefined name on CR300/CR350. These are observed
reference outputs, not a proposed rule for emulating memory artifacts. Their verdicts were
usable; their filename text is not a sound deterministic regression expectation. No logger
runtime test was performed.

## Unreadable one-line If conditions (round 44)

Fresh CR1000X seed-434 `0818efcb5653` splits `BattThreshold` in
`If BattVolt <= Batt  Threshold Then SW12(SW12_2,0)`. The reference reports
`Invalid symbol in expression: BattVolt <= Batt  Threshold .`; the old checker echoed the whole
line as an out-of-place expression. A minimized four-profile matrix confirms that CR6/CR1000X
scan an unreadable one-line condition like the corresponding block condition.

Reverse-engineering trail: CR6 `If` instruction thunk `0x40625d` resolves to `FUN_00450b20`,
which delegates to the common conditional builder `FUN_0045a9e0`; its parameter code is 7.
`FUN_004b6840` finds `Then` through `FUN_004bdec0` and separates the inline body before the
parameter resolver `FUN_004baf00`'s case-7 expression scan (`FUN_004b5c50`). This supports
reusing the existing condition scanner, rather than adding a competing expression grammar.
No decompiled implementation is copied.

`_inline_condition_stmt` supplies a condition-only view to pass-1 and pass-2 fallback scanning
for CR6/CR1000X `Unparsed` Ifs with the parser's `expected THEN` or `expected ')'` failure.
Original syntax records and source ranges remain available. It does not alter parsed inline
bodies or the CR300/CR350 path. Whitespace before `Then` remains in the diagnostic echo.

Evidence, 2026-10-26: `tests/fixtures/r44_inline_conditions.json`, 132 observations on four
profiles; 114 exact, 18 strict xfail. Covered boundaries include adjacent known operands,
unknown first/second operands, a missing operand before `Then`, comma/extra closing parenthesis,
grouped operands, no space after `If`, extra spaces, an `Else` body, following/preceding symbol
errors, before-`BeginProg` placement, and block-form/valid-condition controls. Left-to-right
classification is preserved: `If Nope Y Then Y=1` is a pass-2 symbol error, while `If X Nope
Then Y=1` is a pass-1 incomplete expression because the adjacent second operand is already a
syntax error. A pass-1 condition error hides later symbol errors; an unknown condition followed
by another unresolved assignment reports both. CR300/CR350 retain their existing condition
and inline-body diagnostics.

Explicit remaining fixture gaps: quoted `Then` inside a condition operand (eight observations,
all four profiles), malformed one-line `ElseIf` (four), pre-existing `Public = 1` parsing/gating
(four), and a later inline-body symbol diagnostic after an unknown CR6/CR1000X condition (two).
These do not change acceptance in the observed matrix. No logger runtime behavior was tested.

## Quoted routine-parameter names (round 45)

Oracle, 2026-10-27; fixture `tests/fixtures/r45_quoted_parameters.json`: 172 observations across
CR6, CR1000X, CR300 and CR350, 132 exact and 40 strict expected failures.

A complete quoted token without whitespace or parentheses in a `Sub` or `Function` parameter
position is accepted as a literal declaration name, including its quotes. `Sub S("Q")` does not
introduce the ordinary identifier `Q`: an `X = Q` body reports `Invalid symbol in expression:
X = Q.` on CR6/CR1000X and `Q is not defined.` on CR300/CR350. The header itself is accepted.
An unused quoted parameter, a call supplying its argument, a typed or dimensioned quoted
parameter, an empty quoted name, an embedded comma or doubled quote, and an optional quoted
parameter with a default all retain the accepted header. Ordinary parameters before and after
it, globals and constants with the unquoted name, and routine-call arity retain their behavior.
This fixes fresh round-42 reproducer `cr6/message_diff/0a9af24e1266`'s misplaced header error.

CR6 trace: `Sub` and `Function` share thunk `0x403ff8` -> builder `FUN_00451f70`.
The pass-1 resolver `FUN_004baf00`, cases 12/35, reads the header using `FUN_004b9700`, splits
its declaration using raw whitespace delimiters, and registers it through `FUN_004d3560`.
The latter checks an initial digit and otherwise copies the name up to whitespace or `(`;
it does not remove quotation marks. This supports retaining the raw token spelling in
`parser._parse_param`, without changing ordinary identifier parsing or expression strings.

The bounded rule excludes whitespace and parentheses inside a quoted token: the reference
interprets them through its raw declaration/dimension parser. Its diagnostic for initializing a
required quoted parameter is also unmodelled, so the existing rejection is retained rather than
accepting that header. Other strict gaps preserved in the matrix are numeric/signed parameter
names, empty parameter slots, whitespace-split declarations, and unterminated quoted parameters.
The 40 expected failures are these ten four-profile boundaries; none is silently treated as
matching. No logger runtime behavior was tested.


## Timer number and nonfinite integral arguments (round 46)

Oracle, 2026-10-27; fixture `tests/fixtures/r46_timer_integral.json`, 392 observations,
380 exact and 12 strict expected failures, with hashes of all four reference compiler binaries. Fresh seed-438 reproducer
`cr1000x/message_diff/71107f867ad9` exposed an earlier `Parameter must be integral.` at
`Timer(1.5,Sec,2)`, hidden by our later unmatched-block diagnostic. The minimized form was a
false accept on all four profiles.

`Timer`'s first argument joins the existing shared integral-argument classifier. Float-typed
constants (`1.5`, `1.0`, `1E0`, `1/1`, `2^0`, arithmetic containing a float literal, fractional
named constants) report `Parameter must be integral.` in pass 1, even when another statement
has an undefined name or an unmatched block. Multiple calls accumulate. The message precedes
the same call's Units/Option or arity error, unless an earlier unresolved name in the same
expression stops parsing before the call. This holds in a statement, assignment, condition,
inline body or nested expression. An explicit `Call Timer(...)` remains a subroutine call.

Integer literals, integer arithmetic, whole-valued named constants, runtime variables,
`Abs(1.5)`, a string TimNo, and signed/hexadecimal integer controls retain their observed
acceptance. The shared classifier preserves the profile boundaries: `2147483648` is rejected
on CR6/CR1000X but accepted on CR300/CR350; `2147483776` still rounds into the latter range,
`2147483777` does not. `&H80000000` wraps and is accepted. These are compile observations,
not evidence that every accepted timer number is useful at runtime.

At the same integral-argument positions, bare or signed `NAN`/`INF` are rejected on every
profile. The fixture also probes `IfTime`'s first argument and `SerialOpen`'s baud argument,
plus aliases and routine parameters named `NAN`/`INF`; those spellings still resolve as the
nonfinite literal at these argument sites. A `Const` with that spelling does supply its value;
`Const NAN = 1` and `Const INF = 1` compile in the probed TimNo position. This literal check is bounded to the shared
integral-argument walk; the existing HTTP/FTP timeout classifier is unchanged. Arithmetic
containing nonfinite names is outside this literal rule.

CR6 trace: expression function `FUN_004945c0` (`Timer`, expression code 0) sets integer
conversion mode for TimNo and calls `FUN_0049efb0` -> `FUN_0049edf0`. The latter reports
`Parameter must be integral.` for float constant expression types `0x23`/`0x223`.
`FUN_004945c0` then resets the mode before reading Units and Option, explaining their distinct
constant-integer messages. No decompiled code was copied into the implementation.


The TimNo check respects expression context: an earlier unknown name wins, and a table-only
output outside a table keeps its placement diagnostic. One-line `If` bodies now reach the
existing table-structure check through the shared flattened statement view; this also closes
the pre-existing false accept in the inline-output control. CR6/CR1000X storage named `Timer`
(an earlier visible alias, a local `Dim`, or a parameter) is an array reference, so this function
argument rule does not apply there. A later alias or one in another routine does not shadow
the call. CR300/CR350 retain their built-in resolution in these probes. Invalid alias declarations
do not introduce the shadow.

The 12 strict gaps all retain the reference's first error and verdict: CR300/CR350 later
destination-type output (2), later integral messages after an unknown name in an assignment,
inline body or earlier Sub argument (6), later integral messages after a bad DataTable trigger
on CR6/CR1000X (2), and an extra CR300/CR350 dimensions message for a local `Dim Timer` (2).
The table-trigger case defers TimNo so it cannot hide the earlier header error. These later
cascades remain bounded gaps, not matched observations.

## Constant destinations inside a one-line If (round 52)

Oracle, CR6/CR1000X, 2026-10-27; fixture `tests/fixtures/r52_inline_constant_destination.json` (44
observations, all matched). Reproducer: round-51 fuzz `e261e8e17146` (CR1000X, GEUS `GCNETv2_7`
mutant). A foreign `FuzzVar = 1` (never declared) sat inside an `If .. ElseIf` chain and line 900
was `If FanU_on Then SDI12Recorder(1,C3,FanU,"XLs!",1.0,0)`. The reference prints only
`line 900: Invalid Variable or Array: 1.`; we printed `line 880: Invalid, or out of place
expression: FuzzVar = 1.`

- **Not an acceptance.** The undeclared assignment is rejected on its own on all four profiles (`If x
  Then / Fuzz = 1 / EndIf` and `Fuzz = 1`: `Invalid, or out of place expression: Fuzz = 1.`,
  CR300/CR350 `Fuzz is not defined.`), in every block position probed (top level, an `If` body, after
  a nested `EndIf`, before an `ElseIf`). It is a pass-2 error. A constant destination
  (`Invalid Variable or Array`) belongs to stage 2a and hides pass 2 (round 39/40), on either side of
  the assignment. So the undeclared assignment is only hidden, never accepted: no false-reject risk.
- **The gap was ours.** Stage 2a looked only at top-level statements, so a constant destination in a
  one-line `If` (`If x Then PanelTemp(3,15000)`, `... Else Battery((1+2))`, `Erase(5)`,
  `AddPrecise(3,1)`) was not seen at all: a false accept alone, and the wrong first location beside
  any pass-2 error. The reference reports the constant destination there, in statement order with
  the top-level ones, and it hides an undeclared assignment before or after it and an undeclared
  argument after it (`PanelTemp(Nope,250)`).
- **Implemented.** `instr_data._check_stage2a` walks the one-line `If` statements too
  (`_with_inline`) but applies only the destination check to them; its other checks (`WindVector`,
  `Debug`, table-output `Reps` and bounds) stay on top-level statements, unobserved inline.
  `instr_rules.check_precision_variable` (`AddPrecise`/`MovePrecise`) walks them as well. Accepted
  controls: an inline `PanelTemp(y(1),250)`, `AddPrecise(P(1),1)`, `Battery("a")` and
  `Battery(x+1)` compile; `Battery(Nope)` stays `Invalid symbol in expression: Nope.`; a short
  `PanelTemp(15000)` gives `Invalid Variable or Array: 15000.` then `Invalid or missing parameter.`
- **Not modelled (CR300/CR350, best effort).** The inline forms of the destination checks are not
  reported there: `Erase(5)`/`RealTime(5)` inline compile in our checker but print `Invalid Variable
  or Array: 5.`; inline `PanelTemp(15000)` and `Battery(3)` print `Illegal destination variable type,
  expecting Float.`, in statement order with the other errors. Also unobserved: inline `Sample`/`WindVector`/`Debug` stage-2a errors.

## Check configuration and report file (round 52)

M3 framework pieces for the opencrbasic checks ([channels](../PLAN.md#three-diagnostic-channels)).
No reference probes: the reference channel is untouched. The contract is tested in
`tests/test_check_config.py` and `tests/test_checks.py`. Code: `checkconfig.py`, `checks.analyze`,
`checks.report`, `cli._run_checks`.

**Result field.** `checks.analyze(result, entry, config, cli=, lsp=)` fills
`CompileResult.findings` and `CompileResult.suppressed` (a `Finding` carrying the suppression's
`reason`). Nothing enters `Diagnostics.items`, so `CompileResult.ok` and the error filter are
unchanged. The language server does not run checks yet. For M6 it calls `analyze` with its
settings as `lsp={"OCB001": "warning"}` (`checkconfig.parse_overrides` validates them) and reads the
two fields; the configuration file's content belongs in the LSP cache key.

**`opencrbasic.toml`.** Found as the nearest ancestor directory of the entry file that holds it, or
`--config FILE`. Format as in PLAN (`[checks]`, `[profile.<logger>.checks]`,
`[entry."<path>".checks]`, `[[suppress]]`); precedence CLI > LSP > entry > profile > `[checks]` >
built-in default (`checks.DEFAULT_SEVERITY`). Severities are `error`, `warning`, `info`, `off`
(`off` drops the finding). Entry keys and `suppress.file` are paths relative to the directory of the
configuration file, compared case-insensitively (CRBasic file names are); `suppress.file` is a glob
(`fnmatch`; `*` crosses `/`). An included file is matched as written in the `Include` (drive prefix
removed), relative to the entry's directory. `line` is optional. An unknown top-level key, check
code, severity or logger profile, a missing or blank `reason`, an unreadable file or invalid TOML is
a `ConfigError`.

**CLI.** `--strict` (stderr lines, exit 3 for an error-severity finding), `--report FILE`,
`--config FILE`, `--check CODE=SEVERITY` (repeatable, highest precedence). `--config` and `--check`
act only with `--strict` or `--report`. `--report` alone runs the checks but prints nothing on
stderr and never changes the exit status. Checks run only when the reference accepted the program.
A configuration error prints `opencrbasic: configuration error: ...` on stderr, runs no checks and
is recorded in the report; stdout, `CR6cmple.xxx` and the 0/1 verdict are unchanged. With
`--strict` an accepted program then exits 4. That exit code is a decision of this round, not in
PLAN: a CI gate must not read an unreadable policy as clean.

**Report schema (`schema_version` 1).** One JSON object: `program`, `logger`;
`reference` = `{ok, mode, diagnostics: [{severity, file, line, message}]}` (all reference
diagnostics, errors and warnings); `strict` (whether `--strict` was given); `config` =
`{file, error}`; `findings` = `[{code, severity, file, line, message}]` after severity and
suppression; `suppressed` = the same plus `reason`. `file` is the entry's name, or the included name
as written. A rejected program has empty `findings`.

Not done: coverage notices and the config-aware LSP wiring (M6). A missing entry file returns before the report is written, so an older report file stays in place. A configuration file on another Windows drive than the entry matches no entry or suppression glob (absolute paths are compared).

## Foldable builtins with the wrong argument count in a Const (round 52)

`Const K=CHR()` made the constant folder index an empty argument list; the parser caught the
`IndexError` and kept the whole declaration as an `Unparsed` statement, so we printed the full line
plus `Expr illegally occurs before BeginProg.` (round-51 fuzz `cfb47653ff50`, CR1000X). The folder
now treats any foldable builtin (`CHR`, `ABS`, `INT`, `SQR`, `LEN`, all unary) called with other than
one argument as not constant, and the existing arity check reports the value only. Reference, all
four loggers (2026-10-27): `CHR()`, `ABS()`, `LEN()`, `4*ATN()` give `Invalid, or out of place
expression: <value>.`; `CHR(13,10)` and `SQR(4,1)` give `No Matching, or out of place ) in
expression: <value>.`; CR6/CR1000X stop there, CR300/CR350 continue. Fixture
`tests/fixtures/r52_const_builtin_arity.json` (32 observations, all matched).

**Stdout bytes (round 52).** `crbc` wrote stdout as UTF-8 text while the message file is Latin-1
bytes, so a UTF-8 `°` in an echoed source line came out double-encoded (`Â°`) on stdout only
(round-51 fuzz `5a642d9a0c61`). Stdout is now Latin-1 too, so both carry the source bytes unchanged
(`tests/test_cli.py`).

## DataInterval before a table Reps error (round 52)

Oracle, 2026-10-27; fixture `tests/fixtures/r52_datainterval_first_error.json` (56 observations, all
matched; CR6/CR1000X for every case, CR300/CR350 for four). Origin: round-51 fuzz case
`DataInterval (-1,15,Sec,10)` followed by `Minimum (999999999999,Batt_volt,FP2,False,False)`: the reference
printed `DataInterval TintoInt and Interval parameters must be non-negative` before `Illegal Reps
-2147483648`; we printed only the second.

- **Cause.** Every `early` finding (`Scan`/`DataInterval` value errors) was dropped whenever stage 2a
  (`instr_data.check_stage2a`) reported anything. That is right for the `Scan` errors and for
  `Invalid Variable or Array` (below), but not for the `DataInterval` non-negative error.
- **Rule (CR6/CR1000X).** The negative-`DataInterval` check is part of the table walk, in statement order.
  It is printed when it precedes the first stage-2a table error (`Illegal Reps <n>`, `Variable X out of
  bounds.`, `Variable X, index i, reps n, out of bounds.`, `Out of memory.` from a table `WindVector`),
  across tables too (two negative intervals before a Reps error print both). A `DataInterval` after the
  failing statement, in the same or a later table, is not printed (`Sample (0,..)` then `DataInterval
  (-1,..)` gives only `Illegal Reps 0`). Either argument negative, or `TintoInt` above 2^31-1, counts
  (`DataInterval (0,-15,..)`, `(2147483648,15,..)`); a non-negative `DataInterval` adds nothing. The
  `Scan` value errors stay hidden by the Reps error (`Scan (-1,Sec,0,0)` plus `Sample (0,..)` gives
  `DataInterval` and `Illegal Reps` only). CR300/CR350 have no such check and are unchanged. Implemented in
  `checker/__init__.py` where `early` is cleared after stage 2a, by statement order.
- **Constant destinations first.** A constant where a destination variable is required
  (`PanelTemp(1,15000)`, `Battery(2)`) is found over the whole program before any `Reps`/bounds check
  and replaces them, wherever it sits (a `Sub` before the table, or the main program after it): only
  `Invalid Variable or Array: 1.` (all of them, in order) is printed, and the negative `DataInterval` is
  hidden as well. Previously the single stage-2a walk met the table's `Illegal Reps` first and stopped.
  `_check_stage2a` now runs the destination loop first and returns if it reported. An undefined name or
  an unresolved assignment in the body (`PanelTemp(Nope,15000)`, `Nope = 1`) is not a destination error:
  the `DataInterval` and `Illegal Reps` lines are still printed.
- **Already matched, kept as controls.** `Sample (1,Ptemp,Bogus)` hides the `DataInterval` error (an
  invalid-symbol finding); `Sample (1,Nope,FP2)` does not.
- **RE trail.** None needed; the order was derived from probes only. The decompiled function of the
  message is `FUN_004f11d0` (see the `check_data_interval` docstring).
- **Gaps.** `WindVector (-5,..)` in the main program (outside a table) gives the reference's
  `WindVector must be used inside a table declaration.`, not our `Out of memory.` (seen while probing; not
  modelled, not in the fixture). A `Scan` value error ahead of a later stage-2a error could not be built in a
  valid program, so whether it survives like `DataInterval` is unobserved.
- **Replay (2026-10-27, recorded reference, all four loggers).** 27 cases still miss the reference's first
  error, none sharing this rule: `Could not find DataTable` (2), `EndTable without DataTable.` (2),
  `Illegally dimension` (2), `Invalid symbol in expression: UndeclaredFuzzName123.` (2), `Table with
  DataInterval must be called within Scan..NextScan.` (cr1000x, 2: a `CallTable` of a `DataInterval` table
  outside `Scan`; the placement message is missed), `Invalid Variable or Array: (1+2).` (cr1000x, hidden by
  our pass-1 arity error; the reference prints both), `DataInterval must be used inside a table
  declaration.` (cr300), `Warning: Invalid fnotch ...` (cr300), and one each of `END IF without IF...`,
  `EndSub without Sub...`, `Expr illegally occurs before BeginProg.`, `GoesTable not active.`, `I2COpen must
  precede I2CWrite.`, `IF or SELECT statement illegally has SCAN without NEXT SCAN.`, `Illegal ! operation`,
  `Invalid, or out of place expression: EndTable2.`, `LongX unknown Const type`, `Miss match of types`,
  `SDM instruction must be in Scan..NextScan...`, `SetSetting illegally occurs before BeginProg.`,
  `Structure inside Structure`, `Variable RTD_C out of bounds.`.

## Include names that are not constants (round 53)

Round-53 fuzz (`6143340e8db3` CR6, `9521ec4fb272` CR1000X: `IncludeSection("Functions",
UndeclaredFuzzName123)`) showed that the reference evaluates the file argument of `Include` and
`IncludeSection` when it is not a literal, where we accepted the program (false accept). Oracle,
all four loggers, 2026-10-27 (fixture `tests/fixtures/r53_include_runtime_names.json`):

- CR6/CR1000X: a name that is not declared *before* the statement (a later `Public` does not count)
  gives `Invalid symbol in expression: <argument>.`; a declared variable or any call (`Len(S)`)
  gives `Variable illegal in Parameter: <argument>.`; both lines when both occur (`S+Undecl`). Then
  `Include file NULL must have valid program file extension.`. Extra arguments after the file name
  change nothing.
- CR300/CR350: `<name> is not defined.` per undeclared name, then always `Variable illegal in
  Parameter: <argument>.` and the warning `Cannot open include file  .`. The later `Scan` line the
  reference prints in the probe template is missing in ours (best effort, `not_modelled`).
- A string `Const` (`"CPU:lib.crb"`) is opened as a file name with the device stripped (`Warning:
  Cannot open include file  lib.crb.`); loading files named by expressions stays outside the
  round-43 bound (`not_modelled`). An undeclared *section* name (`IncludeSection(Undecl, ...)`)
  page-faults CR6Comp (no verdict).

Code: `parser._runtime_include_name`, reached from the round-43 `_folded_include_error` when the
argument does not fold.

## Bridge MeasPEx and empty type dimensions (round 54)

Two false accepts from the round-54 fuzz (seeds 445/446).

**MeasPEx below 1** (`953f1271f9a9`, CR1000X, GEUS `BrHalf(...,0,...)`). `BrHalf`, `BrHalf3W`,
`BrFull` (MeasPEx is argument 6) and `BrHalf4W`, `BrFull6W` (argument 7) reject a constant MeasPEx
below 1 with `Invalid measurements per excitation <value>` (no final period), on CR6 and CR1000X
(oracle 2026-10-28; 0 and -1 rejected, 1 and 2 accepted). Rows in `const_ranges` of
`data/measure.json` and `data/cr1000x/measure.json`. When the same line also has the CR6 channel
overlap error the reference prints MeasPEx first and we print it second (`not_modelled`). CR300/CR350
were not probed with valid ranges and are unchanged. Fixture
`tests/fixtures/r54_bridge_meas_per_excitation.json`.

**`As Float() = ...`** (`628c8b23311c`, CR6, mutated `pointervariable_1.CR6`). Empty parentheses
after the type are accepted on their own (`Public A As Float()`), but an initializer after them gives
`Invalid, or out of place expression:  =.` on all four loggers, for any type and for `Public` and
`Dim`; the scalar's `Attempt to initialize variable out of bounds.` warning follows when the list has
more than one value, and later variables and declarations are checked normally. `As Float(3) = {...}`
stays valid. Code: `parser._parse_vardecl`. Fixture `tests/fixtures/r54_empty_type_dims_init.json`
(48 observations, all matched).

## Juxtaposed dimension operands (round 54)

Round-53 fuzz (`5d03371b1388`, CR1000X: `Dim GPSstring(1  8) As String * 15`) showed that two operands
side by side in a declaration dimension are a pass-1 `Incomplete expression: 1  8.`, where we printed
`Invalid symbol in expression: GPSstring(1  8) As String * 15.` (the dimension list failed to parse and
the whole statement became `Unparsed`). Oracle, all four loggers, 2026-10-28 (fixture
`tests/fixtures/r54_dim_juxtaposed.json`, 200 cases, 190 matched):

- **Echo.** The text of that one dimension: leading blanks dropped, trailing blanks kept, inner spacing
  verbatim (`Public G( 1  8 )` -> `1  8 .`, `Dim G(2,1  8)` -> only `1  8`). Three operands, a string
  (`"a" 8`), a `Const` name, a float, a parenthesised operand (`1 (8)`, `(1) 8`) and an expression
  operand (`1+2 8`) all give the same message. Every bad dimension is reported, in source order, also
  across variables (`Public G(1 8), H(2 3)`). `Public G(1 Mod 8)` and `Dim G(1 + 8)` compile.
- **Names win.** An undefined operand anywhere in the dimension (`Zork 8`, `1 Zork`, `1 As`) gives only
  the existing undefined-name message of `_report_decl_expr` (`Invalid symbol in expression:
  <dimension>.`); CR300/CR350 `<name> is not defined.`, then `Incomplete expression:`, then `Variable
  illegal in Parameter:`. A variable operand (`x  8`) adds `Variable illegal in Parameter:
  <dimension>.` after the `Incomplete` line, on all four loggers.
- **Not fatal for the declarations.** The statement is still declared: a later `Public G` is `G already
  declared as a variable.`, later declaration, `Alias`, `Const` and `Sub` header errors are reported as
  usual, and a later variable in the same list is declared (`Dim G(2), H(1  8), K(2)` then `Public K`).
  Being a pass-1 error it hides every pass-2 (body) error, as any pass-1 error does; later body errors
  and uses of `G` are silent.
- **Recovered size.** A juxtaposed dimension is kept as size 1, probed through the alias bounds:
  `Dim G(2  8)` / `Alias G(1) = Z` passes and `Alias G(2) = Z` is `Illegally dimensioned aliased name`;
  `Public G(3, 1 8)` accepts `Alias G(3,1)` and rejects `G(3,2)` and `G(4)`; `Public G(1 8, 3)` rejects `G(3)`.
- **Everywhere a dimension list is read.** `Public`, `Dim` in a routine, and the dimensions of a `Sub`
  or `Function` parameter (`Sub S(a(1 8), b(2 3))` reports both, recovered to 1) give the same text.
  `Static` was not probed (a `Static` inside a routine is `Invalid, or out of place` in the probe template).
- CR300/CR350: the same messages. After a failed dimension they do not report the later dimension
  errors of the file (`Public G(1 8)` then `Sub S(a(1 8))`), a pre-existing gap that also shows for
  `Illegally dimension` and undefined names (`not_modelled`).

Not modelled (`not_modelled` in the fixture): dimensions after a String size (`As String * 15(1 8)`,
echoed `1 8` by the reference, `15(1 8)` by the String-size rule), and a String size followed by a
parenthesis after the dimensions (`Public G(1 8) As String * 15(2)`: the reference first reports
`Incomplete expression: 15(2).`). `Public G(1 8) As Float = {1,2}` matched including the
out-of-bounds warning. `Dim H(x +)` (an unrelated parse error in a dimension) still differs from the
reference and was left alone.

Code: `parser._parse_dim` (a `Juxtaposed` node, a subclass of `ArrayLiteral` so the name walkers see every
operand; `VarDecl.junk_dims` keeps the dimensions as written while `dims` holds 1 in their place),
`structure._report_juxtaposed_dim`, called from `_check_decl_exprs` and `_alias_parameter_declaration`.
No decompilation was needed; all rules come from probes.

## Tables named after predefined constants (round 54)

A `DataTable` whose name is a predefined symbol (`True`, `False`, `mV5000`) leaves that symbol a
value: the reference compiles `DataTable(True,1,-1)` together with `Public B As Boolean = True`,
before or after the table, on all four loggers (oracle 2026-10-28). We treated every user table name
as a value-less name and so rejected each later `True` (a false reject, found through Codex research
on round-53 fuzz `7d9dbda67c22`, where a swapped `DataTable(True,FinApp_Starts,-1)` hid the real
`Invalid symbol in expression: FinApp_Starts.`). `expressions._value_less_tables` now drops the
profile's predefined symbols from the user table names; `Status` and `Public` keep their rule, and an
ordinary table name used as a value (`Public D = T`) is still rejected. A table named `Sec` lacks the
reference's `Table Sec, is declared but never called.` warning (`not_modelled`). Fixture
`tests/fixtures/r54_table_named_constant.json`.

## FieldNames owner line (round 54)

Fixture `tests/fixtures/r54_fieldnames_owner.json` (`tests/test_r54_fieldnames_owner.py`): 266
reference observations on CR6, CR1000X, CR300 and CR350 (recorded 2026-10-28; 242 matched, 24
`not_modelled`). Saved fuzz reproducer `message_diff/83c0a809c514` (CR1000X, `WindVector` followed by
`FieldNames(WindDirection","WindSpeed)`) now matches.

Rule (probed on all four compilers). The builder of a field-producing instruction reads the run of
`FieldNames`/`FieldClassify`/`FieldOrigin` records directly after it and resolves each `FieldNames`
argument then, so everything that comes from resolving the argument carries the **owner's line**,
not the `FieldNames` line:

- CR6/CR1000X, undefined name or an unbalanced-quote argument (`WindDirection","WindSpeed`, which we
  parse as `Unparsed`): `line <owner>: Invalid symbol in expression: <arg>.` followed by the two
  semantic lines `FieldNames cannot be a variable.` and `FieldNames canot be NULL` (the argument has
  no constant string value). The group is printed *before* the owner's own argument errors on that
  line (`Average(1,Zed,FP2,False)` + `FieldNames(NoSuchName)`: the `NoSuchName` group, then
  `Invalid symbol in expression: Zed.`). Two consecutive `FieldNames` records give two groups in
  record order, all on the owner line. `FieldClassify("x")`/`FieldOrigin("x")` between owner and
  `FieldNames` do not change the owner (CR6/CR1000X; CR300/CR350 do not know them and fail at their own
  line, which hides the semantic lines).
- All loggers: the semantic lines of a numeric, variable, empty-string or String-variable argument
  are on the owner line.
- CR300/CR350 resolve the argument while reading (`NoSuchName is not defined.` at the `FieldNames`
  line, unchanged).
- Parsing/arity errors stay on the `FieldNames` line (`FieldNames("A","B")` gives `Too many
  parameters:  "B".`, `FieldNames()` gives `Invalid or missing parameter.`).
- **A `FieldNames` with no owner is never resolved**: after `DataInterval` or `CardOut` (also when an
  owner and an earlier `FieldNames` precede that record), or first in the table, `FieldNames(NoSuchName)`,
  `FieldNames(1)`, `FieldNames(S)` and the unbalanced-quote form all compile cleanly on CR6/CR1000X;
  `FieldNames(1)` and `FieldNames(S)` also compile on CR300/CR350 (their `is not defined.` is a
  reading-time error and stays).

Owners confirmed with the compilers: `Sample`, `Average`, `Totalize`, `StdDev`, `Moment`, `Maximum`,
`Minimum`, `Median`, `WindVector`, `Covariance`, `Histogram`, `FFT`, `RainFlow` (CR6/CR1000X: the
undefined-name group; all four: the numeric argument). `SampleMaxMin` and `LevelCrossing` were
probed with arguments that failed first (not counted). `SampleFieldCal` is **not** an owner: its
`FieldNames(1)` was never resolved (the compiler printed `SampleFieldCal with no FieldCal in
program.` and `Table T declared with no output fields.`; the second line is a separate gap, we do
not print it). `ETsz`, `ETClearSky` and `MQTTPublishTable` are unprobed and are excluded from the owner
set.

RE trail (a hypothesis that the probes confirmed, not a source of rules): `FUN_004ec300` is the shared
builder helper; it skips a consecutive run of the three metadata record ids and resolves the
`FieldNames` argument through `FUN_00507e70` while the compiler's current source line is still the
owner's (Codex read-only research, round 54).

Code: `instr_data.fieldnames_owners` (the owner map, also used by `check_fieldnames`) and
`instr_data.move_fieldnames_symbol_errors` (called in `checker/__init__.py` next to the `Invalid
symbol` renaming; moves or drops the pass-2 findings and appends the semantic pair). Before this round
`check_fieldnames` took the last *instruction* of any kind as the owner, so a `FieldNames` after
`DataInterval` or first in the table was reported (a false reject) and one after `FieldClassify` got
the wrong line.

Remaining gaps (all `not_modelled` in the fixture): a numeric `FieldNames` followed by another one
with an undefined name (the first one's semantic line is printed by tier C after the later group; the
reference order is record order); `FieldNames(X+1)` (a variable inside a numeric expression: the
reference says `FieldNames cannot be a variable.`, we compile it); the unbalanced-quote argument on
CR300/CR350 (reference `<name> is not defined.` then `Incomplete expression: <arg>.`, we print the
generic fallback). CR6/CR1000X page-faulted (exit 5, no verdict) on `FieldNames(NoSuchName)` followed
by `FieldNames(1)`. The overrides in `tables.py`/`bindings.py` still apply an unowned `FieldNames`.

## Unknown time units and Settings port words (round 55)

Two false accepts from the round-55 fuzz (seeds 447/448), oracle 2026-10-28:

- **`TimeIntoInterval`/`IfTime` with a unit outside 0-6** (`64dd660d49e3`, CR1000X, GEUS
  `TimeIntoInterval(9,Min,10)` with swapped arguments). The unit scales both values to 0, so the
  `Interval (%d) must be > 0 and TimeInto Interval (%d) must be < Interval.` check always fails, also
  for `(2,3,99)`, `(0,3,99)` and `(-1,3,99)`; `Month` (6) keeps its own handling. All four loggers.
  `intervals._check_call`. Fixture `tests/fixtures/r55_timeinto_unknown_units.json` (68 observations).
- **`SetSetting("Baudrate(...)")` indexes** (`b74fe6cc73d0`, CR6, `"Baudrate(C  OMC1)"`). Only the first
  whitespace-separated word of the index counts: `COMC1 x` and ` COMC1` are `COMC1`; `C  OMC1` (the
  word `C`) and `COMC1+1` are unknown and give `Illegal dimension 0`; `1 2` is `1`.
  `instr_rules._check_port_index`. Fixture `tests/fixtures/r55_settings_index_word.json`.

## Slow-scan warnings, Scans inside blocks and VibratingWire diagnostics (round 55)

Codex review of worker B's round-54 PipeLine data, confirmed on CR6/CR1000X (oracle 2026-10-28):

- `Delay(0,0,...)` (zero duration) and `PortSet(...,...,1)` (processing task) in a slow scan start no
  measurement task, so the `Slow Seq N, Scan M, may skip scans` warning is absent; `Delay(0,10,mSec)`
  and `PortSet(C1,1)` keep it. Data: `slow_scan_no_task` in `pipeline_tasks.json`. Fixture
  `r55_slow_scan_no_task.json`.
- **False reject fixed:** a `Scan` inside `For`, `If` or `Do` starts unconditional; we carried the outer
  block depth into the scan and rejected `PanelTemp` (and on CR1000X `ExciteV`) as `not allowed inside
  of a conditional statement in PipeLine Mode.` The depth is now saved at `Scan` and restored at
  `NextScan` (`structure.pipeline_task_errors`). Fixture `r55_scan_in_block_pipeline.json`.
- CR6 `VibratingWire`: a numeric variable as the 8th (diagnostics) argument gives `Invalid diagnostic
  parameter.`, a number `Variable name 1 illegal, starts with a number.`; String variables, String
  array elements and string literals compile. An undefined name prints `Invalid symbol in expression`
  and then the diagnostic line, which we lack (`not_modelled`). `instr_rules.check_vibrating_wire_diagnostics`.
  Fixture `r55_vibrating_wire_diagnostics.json`.

## Round-56 fuzz rules

Seeds 449/450 found five CR1000X false accepts, all fixed (oracle 2026-10-28, CR6 and CR1000X):

- **Minimum excitation.** `BrHalf`, `BrHalf3W`, `BrFull` (ExmV argument 7) and `BrHalf4W`, `BrFull6W`
  (argument 8) reject an excitation of 0 to 19 mV with `Minimum Excitation is 20mV.`; 20 and negative
  values compile (`926c96e8b6f8`). `const_ranges` rows with `lower_gate` 0. Fixture
  `r56_spatial_bridge_rules.json`.
- **`Minimum` data type 0** (`Minimum(1,X,False,...)`, `c48e3c64f3d6`) gives `Illegal data type parameter
  0 in Minimum instruction.` like `Maximum`/`Average`/`Sample` (`_DATA_TYPE_ARG`). Fixture
  `r56_output_datatype_zero.json`; `Minimum(1,X,False,False,FP2)` prints no `declared with no output
  fields` line in the reference (`not_modelled`).
- **Spatial instructions** (`8006c5939583`). Destination bounds also apply to Long destinations
  (`SortSpaIndexed(I,6,IO)` with `I(6) As Long` is `Variable I out of bounds.`); `MaxSpa`/`MinSpa` need
  a Float destination (`Dest type must be Float.`, after the bounds line); `SortSpa` needs equal source
  and destination types (`Source and Dest types must be the same.`; `SortSpaIndexed` already had it).
- **`Sample(...,UINT1)`** of a variable (or `Alias`) that is not UINT1 gives `Sample type UINT1 can only
  sample a variable of type UINT1.` after the odd-reps line (`142a355f568b`). Fixture
  `r56_sample_uint1_source.json`.
- **UINT1 padding warning.** A UINT1 variable whose element count is not a multiple of 4 gets `Warning:
  Adjusting dimensionality of UINT1 so number of bytes evenly divisible by 4` on its declaration line,
  one per variable, also in a failed compile; CR300/CR350 print nothing. An `Alias` of a UINT1 scalar also
  warns (`not_modelled`). Fixture `r56_uint1_dimension_warning.json`.

- **`WindVector` reps bounds** (`bf74cfafbba5`, `WindVector(&HFF,WS,WD,...)` with `WS(2)`, `WD(2)`). The
  direction array is checked first with `Variable WD out of bounds.` alone, then the speed array like a
  `Sample` source (`Variable WS out of bounds.`, `Variable WS, index 1, reps 255, out of bounds.`); a
  scalar counts as one element. `instr_data._check_stage2a`. Fixture `r56_windvector_bounds.json`.
