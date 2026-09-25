# Front end (lexer, source, consteval, parser): verified quirks

Findings from building `lexer.py`, `source.py`, `consteval.py` and `parser.py` (phase 2). Every
quirk below was confirmed against the real `CR6Comp.exe` through `tools/oracle.py` (cached, under
Wine) unless marked otherwise. Minimal reproductions used to find them are not committed; the
commands are given so they can be re-run.

## Line numbers and file identity

- **Code pulled in by `Include`/`IncludeSection` is reported at its own line number within the
  included file, never the line of the `Include`/`IncludeSection` call**, and the main program's
  own line numbers are unaffected by an inclusion (each file keeps its own line counter that
  resumes correctly on return). Verified by putting an undefined symbol at a known line of an
  included file and in the main file after an `Include`: both reported their own file's line,
  matching the value regardless of how many lines the included file added.
- **A logical line built from `_` continuation reports the *last* physical line it spans, not the
  first.** `X = 1 + _` / `    ZZZBad` reports the error on the continuation's own (second) line;
  a three-line continuation with the bad token in the *middle* line still reports the *last*
  line. There is no evidence CR6Comp tracks a finer position than "the whole logical line", so
  `source.py` assigns every node from one logical line the same `line` (its last physical line),
  contrary to the plan's initial assumption of "first line" -- corrected after this evidence per
  AGENTS.md's authority order (observed behaviour over hypothesis).
- `Include`/`IncludeSection`'s device prefix (`CPU:`, `USR:`, ...) is stripped and the filename is
  matched case-insensitively against the program's directory, matching how this repository's own
  `crbasic/*.crb` libraries are already included (`IncludeSection("Definitions", "CPU:Lib.crb")`),
  which is established working behaviour, not separately re-verified here.
- A missing `Include`/`IncludeSection` target is only a **warning** (`Cannot open include file
  %s.` -- note the real message has a *double* space before `%s`; `source.py` reproduces it), and
  the inclusion contributes no lines; compilation continues.
- `Include` (not `IncludeSection`) nested inside an already-included file is a hard error
  (`Include file within an Include file is illegal.`); `source.py` does not expand a second level
  of bare `Include` and records an error instead. Nothing in this repository's corpus nests
  `IncludeSection`, and it is not observed to share this restriction, so it is expanded
  recursively without a depth limit beyond Python's own recursion.
- `Section` (as read by a `#If Section = "Name" Then` inside an `IncludeSection()`-included file)
  is bound to the `Name` argument only for the lines that call brought in. Referencing `Section`
  anywhere else is an ordinary undefined-symbol error: `Invalid symbol in expression: Section =
  "Foo" .`, not a special "empty string" value. `source.py` models this as a per-logical-line
  `section: str | None` field rather than a `Const`.

## `#If`/`#ElseIf`/`#Else`/`#EndIf`

- A skipped branch is never tokenised, let alone parsed: the corpus's `ERROR__STATIC_ASSERT__...`
  pattern (a bare identifier used as a deliberately-invalid statement, to fail the compile if an
  `#If` guard is ever wrong) works *because* the branch containing it is always the one that is
  skipped, not because a bare unrecognised identifier is itself valid syntax -- see below.
- `#If_no_remove` is treated as a synonym for `#If` (this front end has no "Conditional Compile
  and Save" concept to differentiate it from).

## Statements

- **A bare identifier alone is not a valid CRBasic statement in general** -- `Invalid, or out of
  place expression.` -- *unless* it names an already-declared `Sub` (`Zork` alone, with `Sub
  Zork()` declared earlier, compiles fine; the same bare identifier with a name CR6Comp cannot
  resolve does not). This front end's parser is deliberately more lenient here: it always parses
  a bare identifier as a zero-argument `Call`, leaving the "does this name a Sub" check to a
  future semantic pass (`checker.py`). This does not affect the corpus metric: it only differs
  from CR6Comp on inputs the oracle already rejects.
- **CR6Comp stops reading the file entirely at the first top-level `EndProg`.** Appending anything
  after it -- including text with characters CR6Comp's own lexer cannot tokenise at all (`!!!
  ### $$$`), or a second complete `Public`/`BeginProg`/`EndProg` program -- still compiles. This
  explains most of the CHM corpus's "trailing prose" cases: several help pages' examples were
  extracted as one file per page rather than one per example, so a file legitimately contains a
  complete program followed by another example's introductory prose and a second program: CR6Comp
  (and now this parser) simply never look at any of it. `parser.py`'s `_Parser.run()` stops the
  moment a top-level `Instruction(keyword="ENDPROG")` is appended.
- **An unclosed `(`/`{` that runs off the end of a logical line is implicitly closed there,
  silently** -- `Call Foo(1,2,3` with nothing further on the line compiles as `Call Foo(1,2,3)`.
  Only a *dangling comma* (`Call Foo(1,2,` -- an explicitly missing final argument) is rejected,
  and even then as a semantic error (`Call argument required.`), not a parse error: the parse
  itself still succeeds, producing an empty trailing argument slot. `parser.py`'s
  `_TokStream.expect_close` implements the same tolerance for every `)`/`}` in the grammar (an
  instruction's argument list, a grouping paren, an array literal, a declaration's dimension
  list, a parameter list); a trailing comma in a `Public`/`Dim` list gets the same treatment
  (`Public Counter As Long,` with nothing further compiles).
- **Block-form `If` may omit `Then` entirely** (`If Flag` / `EndIf`, `PortSet`/`Else`/`EndIf`
  compiles). Inline form (anything following on the line) still requires it; no corpus evidence
  either way for an inline form without `Then`, so it is still required there.
- Two-word terminators (`Next Scan`, `End If`, `End Select`, `End Sub`, `Exit Sub`, ...) need no
  special-casing in the parser at all: `NEXT`/`END`/`EXIT`/`SELECT`'s trailing word is parsed by
  the *same* generic "keyword, then a bare comma-separated list of expressions" rule as every
  other instruction, and a bare trailing word such as `Scan` parses as a plain `Name` exactly like
  a real loop counter (`Next I`) would. One-word forms (`NextScan`, `EndIf`, `EndSub`, ...) are
  separately-tabulated instruction names (`profiles.Profile.instructions`) that take the same
  generic path with zero args.
- `AND`/`OR`/`XOR`/`EQV`/`IMP` are **one precedence tier, left-associative** -- not the textbook
  "`AND` binds tighter than `OR`". `Const R = 1 OR 0 AND 0` -- if `AND` bound tighter, `R` would
  be `1 OR (0 AND 0) = -1`; guarded with `#If R <> -1 Then <fails-to-compile> #EndIf`, the guard
  *did* fire, so `R` is not `-1`; it is `(1 OR 0) AND 0 = 0`, matching left-to-right evaluation at
  one shared tier.
- `^` is **left-associative**: `2^3^2 = 64` (not `512`, which `2^(3^2)` -- right-associative --
  would give), confirmed the same way. A first implementation of the precedence-climbing parser
  got this wrong (the right operand recursed back into the full power-chain, making it effectively
  right-associative); the corpus's own `test_consteval.py`/`test_parser.py` catch it via this
  parametrised case, and `_parse_power_operand` fixes it by parsing only a single (optionally
  signed) atom on the right, letting the enclosing loop handle chaining.
- Unary minus binds **looser** than `^`: `-2^2 = -4` (i.e. `-(2^2)`), also confirmed the same way.
- `INTDV` is a word spelling of `\` (integer division) -- present in `data/functions.json` (kind
  311) but missing from an initial implementation; found by the corpus (`PowerProfileLib.crb`:
  `DayIndex = NowSec INTDV DaySecs`), which is real, already-compiling code in this repository.
- A number literal may lead with `.` and no integer part (`.111111`), confirmed real by the CHM
  corpus (`addprecise_1.CR6`) and missing from an initial lexer.
- `!x` (dereference) and `@x` (address-of) are pointer-variable operators; `!x` is valid as an
  assignment target (`!val1 = !val2`). A type name may carry a trailing `!` to declare a pointer
  (`Dim pointer_float As Float!`, also on `Sub`/`Function` parameters and return types). An
  array's dimension may follow the type instead of the variable name (`Public floatArr As
  Float(5) = {...}`). All four confirmed real and oracle-accepted via the CHM corpus
  (`pointervariable_1.CR6`, `pointervariable_3.CR6`); none of these four characters/forms were in
  this module's original operator/lexer plan.
- `StructureType Name`/`StructureType (Name)` ... `EndStructureType` (a struct-like type
  declaration) is a real, oracle-accepted feature (`structuretype_1.CR6`, `structuretype_2.CR6`,
  `splitstr_5.CR6`). Its body's member declarations (`Frequency As Float`, no `Public`/`Dim`
  prefix) are the only new statement shape it needs; `Units`/`ReadOnly` inside it are already
  covered by the general `Units` and generic-instruction handling. Modelled by reusing
  `Declaration`/`VarDecl` with `kind="STRUCTUREMEMBER"` rather than a new node.
- A handful of instructions take `Keyword=Value` instead of an argument list (`StationName=
  MtLoganCR1000X`, confirmed oracle-accepted, `stationname_1.CR6`); `_parse_generic_instruction`
  accepts a leading `=` generically rather than special-casing the one instruction observed.

## `Units`

- `UnitLabel` is free-form text, not an expression: it can be `%` (not even a valid CRBasic
  token), contain `/` (`uS/cm`), or carry a raw non-ASCII byte (a degree sign observed as-is in a
  latin-1-decoded CHM example). `Units` is therefore handled before general tokenisation: a
  logical line is first split into `:`-separated segments at the *character* level (respecting
  `(...)` depth and `"..."` strings, mirroring the token-level splitter used everywhere else), and
  any segment whose first word is `Units` has its label taken verbatim as the text after the
  first top-level `=`, never lexed. This also makes `Public TC(120) : Units TC = C` (label
  combined with another statement via `:`, confirmed oracle-accepted -- `cdmtccomp_1.CR6`) work.

## Constant expressions (`consteval.py`)

- `TRUE = -1`, `FALSE = 0` (from `data/symbols.json`); comparisons yield `-1`/`0`.
- `MOD`, `\`/`INTDV`, `<<`, `>>`, `AND`, `OR`, `XOR` operate on integer-truncated operands;
  `AND`/`OR`/`XOR` are implemented as plain bitwise ops on the (already `-1`/`0`-canonical, for
  booleans) integers, which reproduces boolean logic exactly and also reproduces the
  non-canonical-integer left-to-right case above without special-casing it.
- `+` concatenates when either operand is a string (as the real corpus's own code relies on,
  e.g. `"PROFILE " + FormatLong(...)`); `&` always concatenates (coercing numbers to text).

## Known residual gaps (oracle-accepted CHM examples not fully parsed)

Two of the 198 oracle-accepted CHM examples still produce `Unparsed` statements (196/198, 99%,
fully parse; see `tests/test_corpus.py`'s `KNOWN_GAPS`):

- `samplemaxmin_1.CR6`: `WS_ms=WS_ms+=1` -- almost certainly a documentation typo (mixing `X = X +
  1` and `X += 1`) that CR6Comp happens to still accept; not investigated further (see "Version
  control"/scope discipline -- very low value, likely never appears in real code).
- `snmpvariable_1.CR6`: `SNMPVariable(MyTemp,1.3.6.1.4.1.43592.55.1.4,Float,RONLY)` -- a raw
  dotted-OID literal (an SNMP object identifier) as one argument, which is not a general
  expression; `SNMPVariable`-specific, not investigated further.

All other CHM-corpus and repository (`crbasic/*.CR6`, `examples/*.CR6`) programs parse with zero
`Unparsed` statements when the oracle accepts them (`examples/*.CR6` are not oracle-accepted at
all, per AGENTS.md's note that they are historical prior art, so they are exercised only for
"never raises", not "fully parses").
