# CRBasic instruction parameter type codes

How `CR6Comp.exe` parses each instruction's argument list, decoded from `FUN_004baf00` (the
generic parameter parser, Ghidra decompilation, `/opt/re/out/decomp.c:196319-197641`) and checked
against the real compiler under Wine (`tools/oracle.py`). Every instruction's signature in
`src/opencrbasic/data/instructions.json` (`params`) is a list of these codes, one per declared
parameter, taken from the table at `0x60c310` (`docs/binary.md`). Machine-readable form:
`src/opencrbasic/data/type_codes.json`.

Confidence labels used below: **oracle** = an experiment in section 3 reproduces the claim against
the real exe; **decompiled** = read directly from `FUN_004baf00` or a helper it calls, not
independently run; **inferred** = derived from the CHM parameter description and the code's
observed handler, not traced to the byte level.

## 1. How the argument list is split and parsed

Each instruction's `params` string (e.g. `"1,39,1,1"`, from the 16-byte table record at
`0x60c310`) is turned into a small int array by `FUN_004ba3a0`: it walks the comma-separated text
with `strtok`/`atoi` and appends a trailing `0`. A code `>= 100` means "parameter `code-100`,
but optional"; `FUN_004baf00` normalises it back down (`local_e0 = code<100 ? code : code-100`)
and remembers whether the original was optional by comparing that normalised value against the
raw one it just read.

`FUN_004baf00` itself is one big loop over that int array, one iteration per declared parameter.
Two nested `switch` statements key off the normalised code (0-based, so code `N` is `case N-1`):
a small first switch that, for about a third of the codes, calls a lightweight "skip to the next
token" step (`FUN_004ba440`, see below) before the parameter's real handler runs; and a second,
much larger switch that is the parameter's actual handler. Most handlers funnel into one of a
handful of shared routines reached by `goto` (Ghidra's `switchD_004bb2f0_caseD_*` labels), so the
per-code differences are mostly which flag word gets passed into the shared "resolve a value"
routine, not different code paths.

Two different tokenizers are in play, both operating on the same pass-1 source buffer for the
current line (parentheses around the whole argument list have already been skipped by the caller
that invokes `FUN_004baf00` once per statement, at `0x4b6840`):

- **`FUN_004ba440`** (a pre-step for a few codes) and **`FUN_004ba4d0`** (its string-literal-aware
  twin, used ahead of quoted-string parameters) are shallow: skip the previous token by its
  already-known length, skip any run of `(`, space, comma (or, for `FUN_004ba4d0`, tab) , then
  either recognise a leading `"` and re-split on the matching quote (warning `Unmatched " in
  parameter: %s...` if the following text isn't exactly `"`-terminated where expected) or fall
  back to `strtok(NULL, ",:() =\n\t>")`. This delimiter set (comma, colon, both parens, space,
  `=`, newline, tab, `>`) is the general "end of a simple token" set used throughout the parser.
- **`FUN_004ba5f0`** is the one nearly every value/expression/variable code calls first. It does
  `strtok(NULL, "\r\n")` to grab the *rest of the line* as raw text, then hands it to
  **`FUN_004b9700`**, the real depth-aware extractor: it skips leading spaces/commas/tabs,
  optionally eats one leading `(`, then scans character by character, incrementing a depth counter
  on `(` or `{` and decrementing it on `)` or `}`. The token ends at a NUL, at a `)`/`}` that would
  take depth negative (an unmatched closer), or at a comma seen at depth 0 - so a comma inside a
  parenthesised sub-expression or an array index does not split the argument. On success it
  overwrites the terminating comma in the source buffer with `\r` (so a later plain
  `strtok(...,",:() =\n\t>")` scan across the same buffer, used by the "too many parameters" check
  and by the Sub/Function declaration list, does not resplit on it) and returns both the extracted
  token and where the next one starts.

Once a code has its token text, one of a few things happens to it, depending on the code (see the
table): it is hard-parsed as a keyword/constant (`FUN_004b5db0`), constant-folded as a numeric
expression (`FUN_004ee4f0` for a fast named-constant lookup, falling back to `FUN_004b8700` for a
full compile-time expression), resolved as a variable/array/table reference or general runtime
expression (`FUN_004b5c50`, a thin wrapper over the real expression/variable compiler
`thunk_FUN_00493b10`), or simply copied verbatim as text (`thunk_FUN_00572c50`) for later
compilation in a different pass.

**Key helpers**, briefly:

- `FUN_004ba3a0` (`0x4ba3a0`) - splits the instruction's `codes` string into the int array
  `FUN_004baf00` iterates.
- `FUN_004b5db0` (`0x4b5db0`) - resolves a single identifier token to a predefined integer
  constant (block-type keywords, `Scan`/`SubScan`, thermocouple/range/data-type symbols, `True`/
  `False`, ...), or fails; used wherever a code needs a keyword rather than a numeric expression
  (codes 4, 16, 17, 19).
- `FUN_004ee4f0` (`0x4ee4f0`) - fast path: looks the token up by exact name in a linked list of
  already-defined named numeric constants (predefined symbols, prior `Const`s) and returns its
  stored value, or `0` if not found. Tried before the full expression compiler for code 1/39.
- `FUN_004b8700` (`0x4b8700`) - the compile-time constant-expression evaluator: parses and folds
  a numeric expression (literals, named constants, `+ - * /` etc.) to a `float` at compile time.
  Rejects any live (non-constant) variable reference with `Variable illegal in Parameter: %s.`
  (confirmed, section 3). Its own recursive-descent parser has a separate ~24-entry reason table
  (`NO ERROR`, `Invalid symbol in expression`, `Incomplete expression`, `Divide by zero in
  expression`, ...) reported as `<reason>: <token>.`; those messages are not part of
  `FUN_004baf00`'s own catalogue (below) and are not re-enumerated exhaustively here.
  `FUN_004ba690`/`FUN_004ba810`/`FUN_004ba850` are thin wrappers that call `FUN_004b5c50` with the
  "must be constant" flag (`0x80000000`) and turn a "the result was actually a variable" condition
  into the same `Variable illegal in Parameter: %s.` message; codes 5/38/20 use this path instead
  of `FUN_004b8700` directly.
- `FUN_004b5c50` (`0x4b5c50`) - the general symbol/expression resolver used by most variable-,
  array-, table-, and general-expression-typed codes. A thin wrapper over
  `thunk_FUN_00493b10` (the real expression/variable compiler, not traced further here) with a
  flags word that selects constraints (constant-only, array-shape, which symbol namespace, a
  write-target marker, ...). On failure it reports `<reason>: <token>` (or, for long tokens,
  `<reason>: <token>...`) from the same NO-ERROR-style reason table `FUN_004b8700` uses.
  Empirically (section 3), when the flags ask for "variable or array" it also accepts a plain
  expression and silently creates a hidden compiler-generated variable to hold it, rather than
  requiring a bare name - but rejects a bare numeric/string literal outright
  (`Invalid Variable or Array: %s.`, raised by the caller after checking
  `thunk_FUN_00507ee0`, not by `FUN_004b5c50` itself).

**Tokenizer-level errors from `FUN_004baf00` itself** (its own message catalogue, from
`docs/messages.tsv`, function `FUN_004baf00`; 26 distinct texts):

```
Too few parameters in instruction.
%s is not a variable
Illegal parameter %s, expecting constant integer.
Invalid or missing expression.
Variable not named.
Subroutine name %s illegal; cannot be numeric.
Subroutine name %s... is too large; max is %d characters.
%s is missing the ) character.
All arguments after the first optional parameter must be optional.
Invalid Variable declaration %s.
Illegally dimension: %s
Variable %s dimension exceeds max of %d.
Required parameter %s cannot be initialized.
Optional parameter %s requires initialization.
Attempt to initialize variable out of bounds.        (warning)
Variable initialization of %s has bad syntax.
Illegal END or EXIT %s.
Illegal NEXT %s.
Parameter %s must be a constant.
Return outside of Function.
Invalid or missing parameter.
Structure inside Structure
EndStructure without Structure
Invalid Variable or Array: %s.
Too many parameters:  %s.                             (sic - two spaces before %s)
Invalid or malformed function argument.
```

`Too many parameters:  %s.` is emitted once, after the whole parameter loop finishes: if any
non-empty text remains in the statement's buffer, that leftover text is quoted verbatim (oracle-
confirmed, section 3). `Too few parameters in instruction.` is the loop's own top-of-iteration
guard (fires when the previous token's tokenizer left nothing at all and the current code is not
one of the tolerant ones, see the "b4" note under code 4) - real, but this session's oracle probes
for it were inconclusive (section 4): every multi-parameter instruction tried instead hit a more
specific check inside the parameter's own handler (`Invalid or missing parameter.` /
`Invalid or missing expression.`) before the loop-level guard could fire.

## 2. Type codes

`kind` mirrors `src/opencrbasic/data/type_codes.json`. Codes 15, 21, 22, 23, 31, 37 (bare), 43, 44
and 48 (bare) are not used by any CR6 instruction (`instructions.json` never emits them as a plain
code, though 37 and 48 exist as their optional forms, 137 and 148); they are omitted from the
table below except where noted. "Words" is how many 4-byte argument words the code contributes to
the compiled record for the *pass1* stream (`[id|nwords<<10][word...]`, `docs/binary.md`); "var"
means it depends on the token's text length or a repeated sub-list.

| Code | kind | Meaning | Example instructions | Accepted forms | Errors (exact text) | Words | Confidence |
|---|---|---|---|---|---|---|---|
| 1 | const_expr | Compile-time constant numeric expression - the single most common code (746 uses): reps, sizes, ranges, masks, most "Constant" CHM parameters. | `Scan`, `SubScan`, `Pulsecount` Reps, `WriteIO` Mask, `Delay` Option | Named constant (fast path `FUN_004ee4f0`) or a full constant expression (`FUN_004b8700`): literals, `Const`s, predefined symbols, arithmetic. A quoted numeric string (`"5"`) is also accepted and folds to that number. Rejects any variable reference. | `Variable illegal in Parameter: %s.` (from `FUN_004b8700`); expression-compiler errors such as `Invalid symbol in expression: %s.` for an undeclared name; instruction-specific checks layer on top (e.g. `Scan`'s own `Scan Interval parameter must be a non-negative integer`). | 1 | oracle |
| 2 | expr | General runtime expression/value - may be a variable, unlike code 1. | `ExciteV` ExmV, `Pulsecount` Mult/Offset, `Randomize`, `CPIStandby` | Any expression: literal, variable, arithmetic; also accepted a bare string literal in testing (no type check at this layer). Routed through the shared `switchD_004bb2f0_caseD_1` handler with flags `0`. Required unless the code is the optional form (102). | `Invalid or missing parameter.` if nothing follows a required code 2. | 1 | oracle |
| 3 | for_header | `FOR` loop header: `Var = start TO end [STEP step]`. Only instruction: `FOR`. | `FOR` | Parses the whole rest of the line: `strtok` to end-of-line, then literal (post-uppercasing) substring search for `" TO "` and `" STEP "` to split start/end/step; the loop variable itself is resolved first via `FUN_004b5c50`. Step defaults to a `0.0` sentinel (read as "1") when `STEP` is absent. | Falls through to the general expression/declaration errors of whichever sub-parse fails (no code-3-specific text found). | 4 (var, start, end, step) | decompiled |
| 4 | const_int_kw | Constant integer / keyword, tolerant of being entirely absent at end-of-line. | `Do`/`Loop`/`Else` (optional `While`/`Until` marker), `Calibrate`, `CDM_SW12`/`CDM_SW5`, `PortGet` | `FUN_004b5db0` resolves the token to a predefined integer constant (e.g. `While`/`Until`). If the line has already ended (`param_1 == NULL`) when this code is reached, *all* remaining declared codes for the instruction are silently filled with a `-0.0` sentinel and parsing of the statement ends - this is what lets bare `Do`/`Loop`/`Else` (no trailing condition) compile. This is also why this code (and 16, 17, 36, 37, 45, 46) is exempt from the loop-level `Too few parameters in instruction.` check (the "b4" group in `FUN_004baf00`). | `Illegal parameter %s, expecting constant integer.` (oracle-confirmed, e.g. `Do Frobnicate X>1`) | 1 | oracle |
| 5 | const_expr_only | Compile-time constant, strictly - always rejects a variable (paired with 38, which additionally scales by a time-unit constant). | `CDM_ACPower`, `CDM_BrFull`/`BrHalf`/..., `CDM_PanelTemp`, `FFT`, `VibratingWire` | Same constant-folding as code 1 but via `FUN_004ba690`/`FUN_004ba810` (flag `0x80000000` into `FUN_004b5c50`); code 38 additionally rounds the value by a stored time-unit divisor (`DAT_0060af9c`) after parsing, for arguments expressed relative to a preceding unit-scan interval. | `Variable illegal in Parameter: %s.` (oracle-confirmed) | 1 | oracle |
| 6 | identifier | A name token, whose meaning depends entirely on the instruction: Sub name to call, table name, or (for `Units`) a `Var = UnitString` pair captured as one code. | `Call` (sub name), `DataTable` (table name), `Units` (`Var=Unit`), `TimerInput`, `GOESData`, `WorstCase`, `FileMark` | Raw remaining text via `strtok`, stored verbatim (packed 4 bytes/word, `ceil(len/4)+1` words) rather than compiled here; each consuming instruction's own pass-2/link step interprets and validates it later (e.g. `Sub %s not defined.`/`%s is not declared as a Subroutine.` for `Call`, is not in `FUN_004baf00`'s own catalogue). | `Invalid or missing expression.` if nothing follows (oracle-confirmed for bare `Call`... only reached via the later linking step, see section 3); `Nothing after '=' in units definition: %s.` / `Need '=' in units definition: %s.` for `Units` (oracle-confirmed, from `Units`'s own sub-parser `FUN_004b98f0`, not `FUN_004baf00`). | text, `ceil(len/4)+1` words | oracle |
| 7 | bool_expr | General expression used as a condition; no compile-time type check (CRBasic truthiness: any nonzero is true). | `If`, `WHILE`, `SELECT`, `CASE`, `Expr`, `Do`/`Loop`/`Else` condition | Any expression - a bare string literal was accepted without error in testing. Shares the `switchD_004bb2f0_caseD_7` handler (flags depend on caller: plain `0` here) with the array-checked "variable or array" codes, but for a non-optional slot it does not itself insist on a variable. | `Invalid or missing parameter.` if missing where required; underlying expression-compiler errors otherwise, e.g. `Invalid, or out of place expression: .` (oracle-confirmed for `If Then` with no condition). | 1 | oracle |
| 8 | variable_or_array | Destination variable or array (no index required) - "Dest" in most measurement instructions. | `Battery`, `PanelTemp`, `VoltSe`, `Pulsecount` Dest | Resolved via `FUN_004b5c50` (`switchD_004bb2f0_caseD_7`, flags `0\|3`) then checked with `thunk_FUN_00507ee0`. A bare array name with no index is accepted. A plain expression (e.g. `X+1`) is also accepted - the resolver silently creates a hidden variable to hold it. A bare numeric/string literal is rejected. | `Invalid Variable or Array: %s.` (oracle-confirmed for `Battery(5)`); `Invalid or missing parameter.` if omitted. | 1 | oracle |
| 9 | var_decl | `Dim` variable declaration: `Name[(dims)] [As Type] [= init \| {init,...}]`. Only instruction: `DIM`. | `DIM` | Shares the big declaration parser with codes 10/12/35 (see below); for `Dim`/`Public` there is no `OPTIONAL`/required-parameter distinction (that only applies to Sub/Function argument lists), but array dims, `As Type`, and a `{...}` initializer list all apply. | `Variable not named.` (oracle: not directly probed, but the same "no name given" path as code 10, decompiled); the full declaration-parser catalogue below. | var (name + dims + optional initializer list) | decompiled |
| 10 | var_decl | `Public` variable declaration; identical parser to code 9. | `PUBLIC` | Same as code 9. | Same as code 9; `Variable not named.` if the name is missing entirely (decompiled). | var | decompiled |
| 11 | deferred_text | Raw expression text, captured verbatim and compiled later (during `DataTable` field finalisation, not here) - "Source" parameters of table statistics functions. | `Totalize`, `Average`, `Maximum`, `Minimum`, `SampleMaxMin`, `StdDev`, `Moment`, `Sample` | Any text up to the next top-level comma, stored as-is (same verbatim-copy mechanism as code 6); accepted a scalar variable, a literal, and a full expression (`X+1`) equally in testing, since no semantic check happens at this layer. | None from `FUN_004baf00` itself; type/constant errors for the captured text surface later, at table-field compile time (not investigated here). | text, `ceil(len/4)+1` words | oracle |
| 12 | sub_params | `Sub` parameter-list declaration (parenthesised `Name As Type [OPTIONAL = default], ...`). Only instruction: `Sub`. | `Sub` | Same declaration parser as 9/10/35, entered with a live parameter-count pointer so the `OPTIONAL`/required rules apply: once one parameter has `OPTIONAL = default`, every later one must too, and every `OPTIONAL` parameter must have `= default` while every required one must not. | `All arguments after the first optional parameter must be optional.`, `Required parameter %s cannot be initialized.`, `Optional parameter %s requires initialization.`, plus the shared declaration errors below. | var | decompiled |
| 13 | expr_list | Call-argument list: comma-separated general expressions up to a closing `)`. Only instruction: `Call`. | `Call` (arguments) | Repeats: skip separators, resolve each item via `FUN_004b5c50` (flags `0x180`), until `)`; a bare comma is treated as a skipped/default argument. | `Invalid or malformed function argument.` if an item fails to parse. | 1 (count) + N | decompiled |
| 14 | none | No argument at all - closes a `Sub`/`Function` body. | `EndSub`, `EndFunction` | Consumes nothing; just clears the "current sub" pointer. | none | 0 | decompiled |
| 16 | end_exit_kw | `End`/`Exit` target keyword, optional (bare `End`/`Exit` is valid). | `End`, `Exit` | `FUN_004b5db0` resolves the token to a block-type constant (`Prog`, `Sub`, `Function`, ...); absent is fine (defaults). | `Illegal END or EXIT %s.` for an unrecognised keyword (decompiled). | 1 | decompiled |
| 17 | next_kw | `NEXT`'s optional trailing keyword (`Scan` or `SubScan`); a token that isn't a recognised keyword is silently treated as absent rather than erroring (likely so a same-line following statement isn't misread as an argument). | `NEXT` | `FUN_004b5db0`, checked against exactly two allowed resolved values. | `Illegal NEXT %s.` only when the token *did* resolve to a constant but not one of the two allowed ones (decompiled). | 1 | decompiled |
| 18 | raw_line_text | Rest-of-line text, captured verbatim (menu label). Only instruction: `MenuPick`. | `MenuPick` | Everything to end of line, via the same verbatim-copy mechanism as codes 6/11. | none specific | text, `ceil(len/4)+1` words | decompiled |
| 19 | const_int_kw | Constant integer/keyword (e.g. bridge `RevEx`-style flag); failure to resolve is silently ignored (no error) rather than reported. | `BrHalf`, `BrHalf3W`, `BrFull` (`RevEx`) | `FUN_004b5db0`; on success also feeds a flag consumed by the following code-20 (`DataType`) validation. | none observed (decompiled: failure path stores nothing and raises no message) | 1 | decompiled |
| 20 | datatype_kw | Data-type/range/units keyword, constant-only, validated against a per-instruction allowed set. | `PanelTemp` Units, `CurrentSe`/`Thermistor`/`Resistance2W`/`Resistance3W`/`AM25T` DataType, `BrHalf`/`BrHalf3W` DataType | Constant-only resolution (`FUN_004ba810`, same "must be constant" path as code 5), then `thunk_FUN_00523010(instr_id, word, ...)` checks it against the instruction's own legal-value list. | `Variable illegal in Parameter: %s.` if a variable is given (same mechanism as code 5, inferred); additional per-instruction "illegal value" messages from `thunk_FUN_00523010` not enumerated here. | 1 | inferred |
| 24 | expr | General expression/value, same shared handler as 40 (flags `0x100`) but without the extra "this argument writes a value" bookkeeping flag 40 sets. | `TGA300Config`, `PortPairConfig`, `RTCData`, `SDMCD16AC`, `SDMIO16`, `ModemCallback`, `ModBusSlave` | Via `switchD_004bb2f0_caseD_1`; not array-checked (no `Invalid Variable or Array` gate) unlike 8/29/27/33/47/48. | `Invalid or missing parameter.` if required and absent. | 1 | inferred |
| 25 | expr | General runtime expression/value - second most common code (146 uses) after 1. | `MuxSelect` MuxChan, `WriteIO` Source, `ReadIO` Mask, `SDI12SensorSetup`, `GPS`, `TGA300Config` | Any expression: variable, literal, or arithmetic - all three accepted in testing without a type check at this layer (`MuxSelect(...,"abc",...)` compiled). A non-constant argument here is one of the triggers `docs/modes.md` lists for forcing Sequential mode. | `Invalid or missing parameter.` if required and absent. | 1 | oracle |
| 26 | expr_or_name | General expression/reference, flagged variant of 25 (`switchD_004bb2f0_caseD_19`, flags `0x800`) - used where the token may need to resolve against a different namespace (e.g. a table or module name) rather than a plain variable. | `SDI12SensorSetup`, `CPIFileSend`, `CPIAddModule`, `GOESSetup`, `SDI12Recorder`, `TableFile` TableName | Same mechanism as 25, different flag word passed to `FUN_004b5c50`; exact namespace/constraint difference not isolated at the byte level. | Same family as 25/2 (`Invalid or missing parameter.`, expression-compiler errors). | 1 | decompiled |
| 27 | variable_or_array | Variable/array reference, array-checked like 8/29 (`switchD_004bb2f0_caseD_7`, flags `0x180`). | `SDI12SensorResponse`, `SDI12Recorder`, `Read`, `Move` Dest, `MoveBytes`, `Erase`, `GetRecord` | A plain variable is accepted; a bare literal is rejected the same way as code 8. In testing, a *table name* was **not** accepted here (`Read(Tbl1)` failed with `Invalid symbol in expression: Tbl1.`) even though `Read`'s CHM description suggests a table-like argument - only a variable/array compiled. | `Invalid Variable or Array: %s.` (inferred from the shared handler; not independently triggered this session for a code-27 instruction specifically); `Invalid or missing parameter.` if absent. | 1 | oracle |
| 28 | expr | General expression/value, same shared handler as 27/2 (`switchD_004bb2f0_caseD_1`, flags `0x180`) but via the non-array-checked path - accepts a variable syntactically even where the *instruction* then imposes its own stricter "must be constant" rule. | `FactCal`, `PWM`, `CDM_Delay`, `CDM_FactCal`, `Delay` Interval, `I2CRead`/`I2CWrite`, `SPIRead` | Any expression at this layer; e.g. `Delay(0,X,Sec)` parses fine at the code-28 level but `Delay`'s own compile step then rejects the non-constant interval separately. | `Invalid or missing parameter.` if required and absent; instruction-specific follow-on checks are separate messages (oracle-confirmed for `Delay`: `Measurement delay must have constant interval.`, `Delay interval cannot be of type String.`). | 1 | oracle |
| 29 | variable_or_array | Destination variable/array, array-checked (`switchD_004bb2f0_caseD_7`, flags `0x100`) - the stricter sibling of code 8. | `ReadIO` Dest, `CDM_VW300Dynamic`/`CDM_VW300Config` Dest, `MQTTPublishMeta` | Same "variable/array or promotable expression, no bare literal" rule as code 8. | `Invalid Variable or Array: %s.` (oracle-confirmed for `ReadIO(5,1)`). | 1 | oracle |
| 30 | expr_pair | Two words per argument: an internal word (flags `0x800`) followed by the value itself (flags `4`, via `switchD_004bb2f0_caseD_1`). Only instruction: `VoiceSpeak`. | `VoiceSpeak` | Not independently isolated; the extra leading word's purpose (beyond being written before the main value) was not determined. | none observed | 2 | decompiled |
| 32 | none | No-op marker: consumes no argument text at all (same `goto` as unused code 23). Only instruction: `RainFlowSample` (first parameter). | `RainFlowSample` | n/a | none | 0 | decompiled |
| 33 | variable_or_array | Variable/array reference, array-checked with an extra 2-word allowance when a string/array-length flag (`0x1000`) is set. | `MinionsTest`, `SerialIn`, `VoicePhrases`, `ArgosError`, `CWB100Routes` | Same family as 8/27/29/47/48; the extra words likely carry a string buffer length/array bound for the receiving variable. | `Invalid Variable or Array: %s.` (inferred). | 1 (+2 when flagged) | inferred |
| 34 | expr_typed | `Return`'s expression, whose accepted "shape" (scalar/array/flags) is chosen from the enclosing `Function`'s declared return type before falling into the shared `switchD_004bb2f0_caseD_1` handler. | `Return` | Any expression matching the enclosing Function's return type; must be inside a `Function`. | `Return outside of Function.` (decompiled). | 1 | decompiled |
| 35 | sub_params | `Function` parameter-list declaration; identical parser to code 12. | `Function` | Same as code 12. | Same as code 12. | var | decompiled |
| 36 | none | No argument; toggles internal Select/Case bookkeeping. Only instruction: `EndSelect`. | `EndSelect` | n/a | none | 0 | decompiled |
| 38 | const_expr_only | See code 5 (its paired, unit-scaled sibling). | `ACPower`, `CDM_ACPower` | See code 5. | See code 5. | 1 | decompiled |
| 39 | const_expr | See code 1 (its paired sibling): identical parsing, plus an extra rounding step against a stored time-unit divisor - used for `Scan`'s `Units` parameter and similar. | `Scan` (Units), `CH400Status` | See code 1. | See code 1. | 1 | decompiled |
| 40 | expr | General expression/value, same shared handler as 24 (flags `0x100`) but additionally sets an internal "this parameter is an output/disable-style reference" flag consumed elsewhere (plausibly feeding the non-constant-forces-Sequential rule in `docs/modes.md`). | `Totalize`/`Average`/`Maximum`/`Minimum`/`SampleMaxMin`/`StdDev`/`Moment` DisableVar, `Covariance` | Any expression, including a plain variable (`DisableVar` is documented as a Boolean-ish enable/disable variable). | `Invalid or missing parameter.` if required and absent. | 1 | inferred |
| 41 | variable_or_array | Variable/array reference (`switchD_004bb2f0_caseD_1` reached via `case 0x28`, flags `0x180`, also sets the same output-marking flag as 40). Only instruction: `Move` (Dest). | `Move` | Not independently isolated this session. | `Invalid or missing parameter.` if absent (inferred). | 1 | inferred |
| 42 | expr_special | Variant of the general expression handler with extra auto-temp-variable machinery specific to this instruction (`local_e0==0x2a` special case inside `switchD_004bb2f0_caseD_1`: builds a synthetic `_tmp_<line>_<word>_<name>` variable name from source position when the given expression isn't already a plain reference and a "needs storage" flag isn't set). Only instruction: `VibratingWire`. | `VibratingWire` | Not independently isolated; behaviour only read from decompilation. | none observed | 1 | decompiled |
| 45 | type_name | `StructureType`/`ObjectType` name declaration - registers a new named type context (same name-registration helper, `thunk_FUN_004f8090`, used for `DataTable` names). | `StructureType`, `ObjectType` | An identifier, rejects nesting. | `Structure inside Structure` if already inside one (decompiled). | var (name) | decompiled |
| 46 | none | Closes a structure/object type context. | `EndStructureType`, `EndObjectType` | n/a | `EndStructure without Structure` if none is open (decompiled). | 0 | decompiled |
| 47 | variable_or_array | Variable/array reference, array-checked (`switchD_004bb2f0_caseD_7`, flags `0x200`). | `GPS`, `CDM_BridgeFilt`, `CDM_CalRead`, `CDM_FFTFilt`, `CDM_VoltFilt` | Same family as 8/27/29/33/48. | `Invalid Variable or Array: %s.` (inferred). | 1 | inferred |
| — | — | Codes 15, 21, 22, 23, 31, 37 (bare), 43, 44, 48 (bare) have real handlers in the decompiled switch (except 15, which has no case at all and would hit the shared "unhandled" fallthrough) but are never emitted by any CR6 instruction's signature. Not documented in depth. | (none) | | | | not applicable |

### Optional variants (code + 100)

Any code `1..48` may appear as `code+100` in `instructions.json` (the table shows `101, 105, 108,
120, 124, 125, 126, 127, 129, 137, 148` in current use). `FUN_004baf00` parses these with *exactly*
the same handler as the base code (the same `switch` case - the raw vs. normalised code is only
compared to decide optionality); the only differences are:

- If the token is missing (`param_1 == NULL` or empty) where the *base* code would report
  `Invalid or missing parameter.` / `Invalid or missing expression.` / abort, the optional form
  instead writes a default value (`0.0`, or `-0.0` for the const-expr family) and sets `local_c8`
  (a "an optional parameter was just defaulted" latch that also suppresses the loop-level
  `Too few parameters in instruction.` check for whatever follows).
- Once actually present, the optional form is checked exactly as strictly as the base code -
  e.g. code 101 (optional code 1) still rejects a variable (oracle-confirmed: `WriteIO(1,X,P)`
  with `P` a variable fails `Variable illegal in Parameter: P.`, while `WriteIO(1,X,5)` compiles).

`src/opencrbasic/data/type_codes.json` encodes each optional code as its own entry with
`"optional_of"` pointing at the base code, so a checker does not need to compute `code-100` itself.

## 3. Oracle experiments

All run via `python3 tools/oracle.py <file> --no-cache` from `opencrbasic/`, real `CR6Comp.exe`
under Wine, 2026-09-25. Each program is `Public` declarations, then `BeginProg ... Scan(...) ...
NextScan EndProg` (or a `DataTable` block, where noted) wrapping one test statement; only the
distinguishing lines are shown below.

**Code 1** (`Scan`'s own first argument, i.e. code 1 with `Scan`'s extra range check on top):
```
Scan (X,Sec,0,0)              -> line 4: Variable illegal in Parameter: X.
Scan (1+2,Sec,0,0)             -> Compiled in PipelineMode.
Scan (FooBarBaz,Sec,0,0)       -> line 4: Invalid symbol in expression: FooBarBaz.
Scan ("1",Sec,0,0)              -> line 4: Scan Interval parameter must be a non-negative integer   (Scan's own check, not code 1's)
Scan (,Sec,0,0)                 -> line 4: Incomplete expression: Sec,0,0).
```
Code 1 without `Scan`'s extra check, using `WriteIO(Mask, Source)` (`[1,25,101]`):
```
WriteIO(Mode,X)      Mode a variable -> line 5: Variable illegal in Parameter: Mode.
WriteIO("5",X)        -> Compiled in SequentialMode.   (a quoted numeric string IS accepted here)
```

**Code 2**, using `Randomize(X)` (`[2]`):
```
Randomize(X)          X a Public variable -> Compiled in PipelineMode.
Randomize(5)           -> Compiled in PipelineMode.
Randomize(FooBarBaz)    -> line 5: Invalid symbol in expression: FooBarBaz.
Randomize("abc")        -> Compiled in PipelineMode.   (no type check at this layer)
Randomize()              -> line 5: Invalid or missing parameter.
```

**Code 5**, using `CDM_PanelTemp(...)`'s last argument (`[1,1,8,1,1,5]`):
```
CDM_PanelTemp(C1,1,X,1,1,Y)   Y a variable -> line 5: Variable illegal in Parameter: Y.
```
(the constant/expression forms could not be isolated from `CDM_PanelTemp`'s own "no CDM module
configured" check in this session's minimal test program; see open questions).

**Code 4**, using `Do`/`Loop`'s optional keyword:
```
Do Frobnicate X > 1 ... Loop   -> line 5: Illegal parameter Frobnicate, expecting constant integer.
Do While X > 1 ... Loop        -> Compiled in PipelineMode.
```

**Code 3** (`FOR`):
```
For I = 1 To 10 Step 2 ... Next   -> Compiled in PipelineMode.
```

**Code 6**, using `Units` and `Call`:
```
Units X = Volts        -> Compiled in PipelineMode.
Units X =               -> line 4: Nothing after '=' in units definition: X .
Call MySub               (MySub declared)      -> Compiled in PipelineMode.
Call NoSuchSub                                   -> line 5: Sub NoSuchSub not defined.   (program with no Sub/Function)
Call NoSuchSub                                   -> NoSuchSub is not declared as a Subroutine.   (any Sub/Function declared; no line, link step, `docs/checker.md` "Unresolved `Call`s")
```

**Code 7**, using `If`:
```
If X Then ... EndIf       X a variable -> Compiled in PipelineMode.
If "abc" Then ... EndIf                 -> Compiled in PipelineMode.   (no type check)
If Then ... EndIf                        -> line 5: Invalid, or out of place expression: .
```

**Code 8**, using `Battery(Dest)` (`[8]`):
```
Battery(5)               -> line 3: Invalid Variable or Array: 5.
Battery(X+1)              -> Compiled in PipelineMode.   (expression silently promoted to a hidden variable)
Battery(Arr)   Arr(3) declared, no index -> Compiled in PipelineMode.
Battery()                 -> line 3: Invalid or missing parameter.
Battery                    (no parens at all) -> line 3: Invalid or missing parameter.
```

**Code 11**, using `Totalize(Reps,Source,DataType,DisableVar)` inside a `DataTable`:
```
Totalize(1,X,FP2,X)      X scalar variable -> Compiled in PipelineMode.
Totalize(1,5,FP2,X)      literal source     -> Compiled in PipelineMode.
Totalize(1,X+1,FP2,X)    expression source  -> Compiled in PipelineMode.
```
(no type/constancy check at this layer - confirms code 11 defers to a later compile stage).

**Code 25**, using `MuxSelect(ClkPort,ResetPort,ClockPW,MuxChan,Mode)`:
```
MuxSelect(C1,C2,10,X,0)        MuxChan a variable -> Compiled in SequentialMode.
MuxSelect(C1,C2,10,"abc",0)    MuxChan a string    -> Compiled in SequentialMode.
```
(`SequentialMode` here is `docs/modes.md`'s documented consequence of a non-constant 4th argument.)

**Code 27**, using `Read(TableName)`:
```
Read(Tbl1)         Tbl1 a declared DataTable -> line 9: Invalid symbol in expression: Tbl1.
Read(NoSuchTable)                             -> line 3: Invalid symbol in expression: NoSuchTable.
Read(X)             X a Public variable       -> Compiled in PipelineMode.
```
(surprising: a plain variable is accepted and a table name is not - see open questions).

**Code 28**, using `Delay(Option,Interval,Units)`:
```
Delay(0,X,Sec)          X a variable -> line 5: Measurement delay must have constant interval.
Delay(0,"1",Sec)                     -> line 3: Delay interval cannot be of type String.
                                         line 3: Measurement delay must have constant interval.
```
(both are `Delay`'s own follow-on checks, not a code-28-level rejection - the generic parser
itself accepts the variable/string token).

**Code 29**, using `ReadIO(Dest,Mask)`:
```
ReadIO(5,1)          literal Dest -> line 3: Invalid Variable or Array: 5.
ReadIO(X,1)          variable Dest -> Compiled in PipelineMode.
```

**Code 101** (optional code 1), using `WriteIO(Mask,Source,[optional third])`:
```
WriteIO(1,X)            third omitted        -> Compiled in SequentialMode.
WriteIO(1,X,5)           third a literal      -> Compiled in SequentialMode.
WriteIO(1,X,P)           third a variable P   -> line 5: Variable illegal in Parameter: P.
```

**Code 125** (optional code 25), using `AvgRun(Dest,Reps,Source,Number,[RunReset])` inside a
`DataTable` (needs a real `Sample` too, or the table itself fails with "declared with no output
fields", unrelated to code 125):
```
AvgRun(Y,1,X,10)             RunReset omitted        -> Compiled in PipelineMode.
AvgRun(Y,1,X,10,Z)           RunReset a variable Z    -> Compiled in PipelineMode.
AvgRun(Y,1,X,10,1)           RunReset a literal       -> Compiled in PipelineMode.
```

**Tokenizer-level errors**:
```
Battery(X,X,X)                   -> line 5: Too many parameters:  X.
Dim Arr(0)                       -> line 1: Illegally dimension: Dim Arr(0)
Dim Arr(2,2,2,2)                 -> line 1: Variable Arr(2 dimension exceeds max of 3.
```

## 4. Open questions

- The exact trigger for the loop-level `Too few parameters in instruction.` message (as opposed
  to the more specific `Invalid or missing parameter.` / `Invalid or missing expression.` that
  every handler tried in this session raised instead) was not isolated. It is real (confirmed by
  `docs/messages.tsv`, function `FUN_004baf00`) and decompiled as the guard at the top of each
  loop iteration (`local_b4==0 && param_1==NULL && local_c8==0`), but every attempt to reach an
  empty-token iteration in this session hit a code-specific check first.
- Code 5/38 could not be isolated from `CDM_ACPower`/`CDM_BrFull`/`CDM_PanelTemp`-family
  instructions' own "no CDM module of this type configured" validation; the "rejects a variable"
  half is confirmed, the "accepts a folded constant expression" half is inferred from code 1's
  behaviour and the shared `FUN_004b8700` machinery rather than independently run.
- Codes 20, 24, 26, 30, 33, 40, 41, 42, 47, 48's exact flag-word semantics (what
  `thunk_FUN_00493b10` does differently for `0x100` vs `0x180` vs `0x800` vs `0x1000` vs `0x2000`
  vs `0x4000`) were not reverse to the byte level; the table above states what was directly
  observed (decompiled control flow) or inferred from the CHM parameter description of the
  instructions that use each code, not a full trace of the underlying expression compiler.
- Code 27 rejecting a table name and accepting a plain variable in `Read(...)` contradicts this
  session's initial assumption (that 27 might be a table-name code); `Read`/`Erase`'s actual CHM
  semantics were not looked up, so it is unclear what a real program is expected to pass here.
- The two-word encoding for code 30 (`VoiceSpeak`) and the auto-temp-name machinery specific to
  code 42 (`VibratingWire`) are read from decompilation only; neither was exercised against the
  real compiler.
- `thunk_FUN_00523010` (code 20's per-instruction legal-value check) and `thunk_FUN_004f8090`
  (code 6/45's name-registration helper, shared with `DataTable`) were not traced beyond
  confirming they exist and are called; their own error catalogues are not enumerated here.

### Code 4 arguments in instructions (2026-09-28)

`instr_rules.check_const_int_args` reports `Illegal parameter %s, expecting constant integer.` for
a code-4 argument of `SW12`, `PortSet`, `PortGet`, `Calibrate`, `CDM_SW12`/`CDM_SW5`/
`CDM_SWPower` and CR300/CR350 `SwVx` that is not an integer constant. That covers a variable, an
undefined name, `Nope+1`, `0.5` and `"a"`, each echoed verbatim. It is a pass-1 error: a later
`X = Q` is not reported on CR6, and an earlier one is not either. Oracle on all four loggers
(`tests/test_const_int_args.py`). `Do`/`Loop`/`Else` keep their keyword parsing. Replay: 833 of
1469 exact.
