# First-error review (M2)

Every CR6/CR1000X saved fuzz reproducer whose reference rejects and whose first error differs from
ours (same location with another message, a missed first error, or an earlier first location), as
the PLAN M2 exit criterion asks. Input: the round-57 saved replay (128 rows). Families and the
`kind` were proposed by Codex `gpt-6.1-sol` (read-only, `scratch/r58/codex-m2.log`) and spot-checked
by hand (8 of 77 families); they are a classification, not reference observations. `kind`:
`misleading` = the reference names a different cause, `location` = another line, `wording` = same
cause, other text. `now` is the impact class in the round-58 replay (`exact` = fixed).

Still differing in round 58, relevant area: 48 misleading, 19 location, 21 wording; outside it: 28, 4, 5. Fixed since the input: 3.

| relevant | kind | family | reproducers (now) |
|---|---|---|---|
| yes | location | CallTable: DataInterval table called outside Scan | `3c4c2a011112` (we miss the reference's first error), `e22f52a890f1` (we miss the reference's first error) |
| yes | location | Battery: expression supplied as destination before later arity error | `b850f32262af` (we miss the reference's first error) |
| yes | location | Inline ResetTable: unresolved table checked in one-line If | `cda46acf1c74` (exact) |
| yes | location | FieldClassify: diagnostic attributed to preceding output owner | `d406b0207cdc` (we miss the reference's first error) |
| yes | location | Array reference: four subscripts inside ASCII condition | `494a797d22ea` (we miss the reference's first error) |
| yes | location | SetSetting: placement before BeginProg | `d108f88aa075` (we miss the reference's first error) |
| yes | location | Preprocessor: unmatched #If has an unlocated diagnostic | `f4e404561ce1` (we miss the reference's first error) |
| yes | location | Oversized declaration: saved line-1 attribution requires verification | `dd9cc650482d` (we miss the reference's first error) |
| yes | location | EndSub: orphan closer before malformed later header | `04754b91e0a1` (we miss the reference's first error) |
| yes | location | Continued routine-call arguments: diagnose the interrupted logical line | `15c0ffa7f461` (we miss the reference's first error), `af36b61a5e8d` (we miss the reference's first error) |
| yes | location | Sample of structure array: unfinished program suppresses earlier output bounds | `1765297afceb` (we report an earlier first location) |
| yes | location | Assignment: before-BeginProg error before later builtin arity failure | `5ad6e2faf8d6` (we miss the reference's first error) |
| yes | location | Array declaration: malformed as2 tail reaches later SortSpaIndexed type check | `b8a536b0b626` (we report an earlier first location) |
| yes | location | Nested DataTable: later unresolved Scan units suppress pairing diagnostic | `c4e3b64bbaea` (we report an earlier first location) |
| yes | location | Const: unknown declared type before missing BeginProg | `de121180e9d3` (we miss the reference's first error) |
| yes | location | EndTable: orphan closer before later Sample bounds | `fee97b435d76` (exact) |
| yes | location | StructureType: nested declaration rejected immediately | `0c4ac9d4ea46` (we miss the reference's first error) |
| yes | location | Unknown statement inside table before nested-table pairing error | `35b82c7d3d73` (we miss the reference's first error) |
| yes | location | Pointer dereference: undeclared RHS pointer operand | `cabcfa049316` (we miss the reference's first error) |
| yes | misleading | Alias: split source identifier resolved before alias-shape rejection | `171af87a935f` (same first location, different message), `36ca5746ad3f` (same first location, different message), `fa08d71c788b` (same first location, different message), `fbcc75c138fb` (same first location, different message), `1112ca94caa1` (same first location, different message), `77c6b4106904` (same first location, different message), `9b464f8050ed` (same first location, different message), `ab571ca664c5` (same first location, different message) |
| yes | misleading | BrHalf: invalid Range before trailing required-argument shortage | `01fd06fdb670` (same first location, different message) |
| yes | misleading | SerialOpen: baud callback before malformed numeric tail | `33f655758088` (same first location, different message), `6484ab6457b8` (same first location, different message), `3c772bc81211` (same first location, different message) |
| yes | misleading | StationName: split raw name leaves excess parameter text | `395582d8224e` (same first location, different message) |
| yes | misleading | Measurement instruction: Pipeline placement before names or missing BeginProg | `70a657faa894` (same first location, different message), `71e8e0489a40` (same first location, different message) |
| yes | misleading | Average: unresolved source before missing BeginProg at EOF | `75a81e272b10` (same first location, different message) |
| yes | misleading | VoltSE: incomplete constant argument before undefined destination | `7d67e53a0e92` (same first location, different message) |
| yes | misleading | DataTable: split name tokenization | `876ca9e34605` (same first location, different message), `e2e261651e4b` (same first location, different message), `840dfab819a2` (same first location, different message) |
| yes | misleading | SerialOpen: malformed ComPort prefix reaches port validation | `8db9658226d1` (same first location, different message) |
| yes | misleading | String destination parameter: validation before missing/excess arguments | `a41eadfe9dff` (same first location, different message), `8830a5aedd4a` (same first location, different message) |
| yes | misleading | FieldNames: excess raw-string argument before unterminated-quote fallback | `af04b066eb87` (same first location, different message), `f8bc16ed60b6` (same first location, different message) |
| yes | misleading | SerialIn: trailing required parameter before unresolved destination | `b4eddc0242fe` (same first location, different message) |
| yes | misleading | One-line If: placement before inline-body symbol resolution | `cb4871f692f9` (same first location, different message), `faf19469b65a` (same first location, different message) |
| yes | misleading | Variable/return type: unsupported type precedes shape or dimension findings | `ce418b0107bc` (same first location, different message), `dc8ef8bccf71` (same first location, different message), `385e5195ea93` (same first location, different message), `c6ca4c0fceb6` (same first location, different message) |
| yes | misleading | SerialOpen: unknown TXDelay changes argument consumption before CommsMode check | `16b8e7a5e759` (exact) |
| yes | misleading | Function header: malformed return As tail uses declaration diagnostic | `0f2aa052111c` (same first location, different message) |
| yes | misleading | If condition: bounds error before EOF block/routine pairing | `3d412625d8c2` (same first location, different message) |
| yes | misleading | Malformed Expr statement: actual statement error before EOF structural fallback | `53e5dbd01845` (same first location, different message), `931794460026` (same first location, different message), `f691eb7afa7a` (same first location, different message) |
| yes | misleading | TimeIntoInterval: interval validation before excess fourth argument | `3a948311392e` (same first location, different message) |
| yes | misleading | DataTable: malformed trigger expression retains expression diagnostic | `4ce782a860c6` (same first location, different message) |
| yes | misleading | DataInterval: signed argument validation before missing BeginProg | `74e259c13199` (same first location, different message), `ec07e9b06cd8` (same first location, different message) |
| yes | misleading | Unknown bare routine call: fallback before nested builtin arity | `8b9b6c965c5a` (same first location, different message) |
| yes | misleading | Routine parameter: malformed Optional keyword exposes trailing declaration | `8c9bb8e61e00` (same first location, different message) |
| yes | misleading | Routine parameter: parenthesized numeric-leading name | `a5ce30c8f8f6` (same first location, different message) |
| yes | misleading | SplitStr: malformed source diagnosed before unresolved destination | `ba1b3ee92608` (same first location, different message) |
| yes | misleading | Sample: malformed output type before unresolved member source | `e84fe90a6711` (same first location, different message) |
| yes | misleading | Next: split Scan token follows ordinary For/Next handling | `ed1cc693d9fa` (same first location, different message), `faa80a7207d1` (same first location, different message) |
| yes | wording | Dotted table reference: missing base named specifically | `b08378af611b` (same first location, different message), `ae52bdd5ebf7` (same first location, different message), `eb43a31508be` (same first location, different message), `72e1588c966c` (same first location, different message), `de1a8dcac79f` (same first location, different message) |
| yes | wording | Diagnostic echo: UTF-8 degree sign becomes mojibake | `1848fd1d95d1` (same first location, different message), `32f0c233f130` (same first location, different message) |
| yes | wording | Declaration dimension diagnostic: first segment versus whole-line echo | `3e418cd0c877` (same first location, different message), `963aae58f08b` (same first location, different message) |
| yes | wording | Long continued call: diagnostic echo truncation | `2c0b869a0a14` (same first location, different message), `5ff16ab82ca0` (same first location, different message) |
| yes | wording | Declaration: numeric-leading invalid-name echo | `1d9d25657d36` (same first location, different message) |
| yes | wording | Implicit routine call: Call versus Expr placement label | `35ad04a0f94e` (same first location, different message), `e8fd3e681e0f` (same first location, different message), `e94ffbdfec4e` (same first location, different message) |
| yes | wording | Explicit Call: nested arity diagnostic echoes argument rather than whole call | `962ab269d293` (same first location, different message) |
| yes | wording | Control condition: condition-only versus whole-statement echo | `a069d5c59801` (same first location, different message), `b4d42d0eac01` (same first location, different message), `9da6844f83f4` (same first location, different message) |
| yes | wording | Inline Return: empty versus unresolved-operand echo | `a596f1d8cacd` (same first location, different message) |
| yes | wording | Detached continuation expression: generic syntax label | `ae171e341a7f` (same first location, different message) |
| no | location | GOESField: active GOESTable required | `10ca1aaf308e` (we miss the reference's first error) |
| no | location | I2CRead/I2CWrite: prior I2COpen for selected port | `30dcc2cd4892` (we miss the reference's first error) |
| no | location | CSAT3B: SDM Scan placement before unresolved table | `038558b1033f` (we miss the reference's first error) |
| no | location | CheckPort: port validation before earlier orphan EndIf | `835ef408b21e` (we report an earlier first location) |
| no | misleading | CheckPort: empty call reaches port-range validation | `4a74895f32cf` (same first location, different message), `6f002bbe6754` (same first location, different message), `6f002bbe6754` (same first location, different message), `9c21b8dfe265` (same first location, different message) |
| no | misleading | SendVariables: first destination argument before later source lookup | `5486bfe505f0` (same first location, different message) |
| no | misleading | Menu instructions: enclosing DisplayMenu before arguments | `84a221517963` (same first location, different message), `7f916ecbef37` (same first location, different message), `e77eaa3ef4cc` (same first location, different message) |
| no | misleading | DNPVariable: source resolution before event-group dependency | `875fb092d22d` (same first location, different message), `15389f40689c` (same first location, different message), `5cde0ec61f37` (same first location, different message), `70500b7417f7` (same first location, different message) |
| no | misleading | Matrix: source shape before unresolved result | `bb0c9b9e1683` (same first location, different message) |
| no | misleading | CType: malformed token consumption reaches Illegal type | `eb6fad797531` (same first location, different message), `1aa55f1705d5` (same first location, different message), `3b16c0d475d6` (same first location, different message), `7214e5b36e9d` (same first location, different message) |
| no | misleading | AcceptDataRecords: table lookup before missing BeginProg | `08fa9332d936` (same first location, different message), `961836f88a83` (same first location, different message) |
| no | misleading | MenuPick: ConstTable item's name required as first parameter | `533da3db60a5` (same first location, different message) |
| no | misleading | SDMCD16Mask: Scan placement before unresolved data arguments | `05d27ab01be2` (same first location, different message), `54e000367f53` (same first location, different message), `c8e968a6fac0` (same first location, different message) |
| no | misleading | SendData: parameter shortage before unresolved argument | `65de263ef89c` (same first location, different message) |
| no | misleading | SerialInRecord: byte count versus unopened port buffer before destination lookup | `8f0284a7b655` (same first location, different message) |
| no | misleading | TDR100: Points range before unresolved destination | `c2e533318796` (same first location, different message) |
| no | misleading | TCDiff: converted Reps positivity before destination capacity | `3da710901444` (same first location, different message) |
| no | misleading | DisplayLine: excess raw-string argument before malformed-quote fallback | `004369d312af` (same first location, different message) |
| no | wording | Gzip: malformed quote/argument boundary gets a different syntax label | `3cf5c9209ff1` (same first location, different message), `a94a7b1583b7` (same first location, different message) |
| no | wording | AcceptDataRecords: unresolved destination table named generically versus specifically | `7b4d465ac222` (same first location, different message) |
| no | wording | PortsConfig: invalid binary literal diagnostic label | `72bbbb1c7e9b` (same first location, different message), `c6fc3897c0ad` (same first location, different message) |

## Round-60 fresh relevant inputs

On 2026-10-30, CR6 seed 461 and CR1000X seed 463 each generated 1,000 `--relevant` mutants.
Shrinking was disabled. Full seed programs and both outputs are retained locally under
`scratch/r60-m2-20261030/` (`fuzz-461-current`, `fuzz-463`). The CR6 output directory had old
shrunk runs in it; `fuzz-461-current/selection.json` identifies the 19 observations written by
this run and copied without altering their bytes. Operational failures are recorded in each
summary and never treated as compiler rejections.

Fixed in this round: `d96283c6048a`, `cfcd4f0c74bb`, `17ed22164f2d` (CR1000X): an odd packed-output
Reps error was missing before an alias-source overrun. Decimal counts found in follow-up probes
also skipped the odd-count check ([rule and fixture](checker.md#packed-table-output-reps-before-alias-bounds-round-60)).

New first-error candidates still need work. This supersedes the round-59 claim that no realistic
relevant first-error difference is open; M2 must not be declared complete from the old review.

| Family | Reproducers / evidence | Next check |
|---|---|---|
| Malformed user-routine argument or interrupted continuation | CR6 `a31a74073f76` deletes the last line of a continued call; `15bc1b5cb20a` duplicates a continued call's first line; `e816abac4b12` changes `_` to `__Mut`; CR1000X `53f4ed873761` moves a continuation line | Probe single-edit call boundaries and the diagnostic echo; four-profile minimal probes in `probes.json` already confirm `S("a" "b")` should report `Incomplete expression` on its argument, rather than the whole-statement fallback |
| Missing nested `IIF` argument | CR6 `d6479ed3f595`: reference `type mismatch`, ours `Expression expects ,` | Isolate `IIF("1","0")` in a declared String routine's argument; the other mutation only changes a string literal's spacing |
| SDM Delay in a slow sequence before an unrelated symbol error | CR6 `9abdc4c03311` swaps `Delay` arguments and misspells a later name | Probe the swapped Delay alone, then place one symbol error on either side; retain the two-edit input without presuming it blocks M2 |
| Long diagnostic echo truncation | CR6 `b74e610652da` | Already the same cause and location; compare the reference's truncated echo separately from semantic first-error agreement |

Other differing inputs remain listed without a realism verdict: CR6 `36a0db0a73f4`,
`1822097cb726`, `fd7e1a3c87c5`, `cde4bf39c14b`, `63ad67153e0f`, `5819ebfc0297`; CR1000X
`042b1b41474a`, `04f6730e62aa`, `dce5f0264db1`, `977954b9e182`. They insert statements into
continuations, split tokens, move quoted string fragments, or combine edits. Classify or isolate
them before deciding whether they fall under the M2 fix scope. The accepted-program difference
`9d5b78fd0b36` is a missing reachability warning after deleting `SlowSequence`; both compilers
choose PipelineMode, so it is not a mode difference.

## Round-61 bounded Sub-call follow-up

The minimal missing-comma family from round 60 is fixed for parenthesized calls to declared
Subs, including explicit `Call`, literal/declared-variable operands and multiple malformed
arguments. The argument expression now owns the diagnostic on all four profiles. The strict
fixture `r61_sub_arguments.json` retains neighboring forms and valid-call/arity controls;
[the checker notes](checker.md#malformed-sub-arguments-round-61) identify the bounded rule.

At the end of round 61, the interrupted-continuation family remained open: `a31a74073f76`, `15bc1b5cb20a`,
`e816abac4b12` and `53f4ed873761` remain candidates. Unknown-name ordering, nested function
syntax, inline calls and trailing tokens remain separate paths. No realism decision or saved
replay baseline was changed. M2 remains open.

## Round-62 Function-expression follow-up

CR6 `a31a74073f76` now matches exactly. `LogDebug` is declared as a Function in `BenchLib.crb`;
the deleted continuation line therefore exposes a Function expression error, which echoes the
whole statement. A new CR6 reference run with current siblings confirmed the saved output.
The bounded rule covers unparsed calls and assignments to one-parameter user Functions,
including profile-specific newline and final unresolved-scalar behavior. Explicit `Call` stays
on its existing argument path. [Checker rule and evidence](checker.md#user-function-expression-syntax-round-62).

Round-60 corpus replay now also matches CR6 `1822097cb726`, `e816abac4b12`, `36a0db0a73f4`,
`15bc1b5cb20a` and CR1000X `dce5f0264db1` exactly. At the end of round 62, the missing nested `IIF` argument (`d6479ed3f595`) and CR1000X
moved-continuation case (`53f4ed873761`) still differed. Round 63 below supersedes the IIF
entry; remaining candidates require isolated reference messages before extending the rule.

The strict round-62 fixture preserves unfinished Sub and nested-built-in syntax gaps. No saved
replay baseline or realism decisions were changed. The other round-60 families still require
triage; this does not close M2.

## Round-63 omitted-IIF-arm follow-up

CR6 `d6479ed3f595` now matches exactly. Its two-argument IIF compares a String second arm with
the numeric type of the omitted final arm, producing `type mismatch` before the arity error.
The condition can be String-typed without causing this mismatch. The bounded four-profile
rule uses the existing arm-type checker; explicit empty slots, excess arguments and unparsed
unclosed calls remain gaps. [Checker evidence](checker.md#iif-omitted-final-arm-round-63).

The moved-continuation CR1000X `53f4ed873761` remains the next candidate from the prior handoff;
isolate its expression and argument boundaries before extending the parser. M2 remains open,
and no saved replay baseline or realism decisions were changed.

## Round-65 fresh inputs

CR6 seed 469 and CR1000X seed 470, 1,000 `--relevant` mutants each on the round-65 code (`scratch/r65/`,
local only): no verdict or mode difference; one warning-only row (`3460d8b2af3f`, a background
calibration warning). First-error differences and their scope decision:

| Reproducer | Edit | Decision |
|---|---|---|
| CR6 `6b414530e683` | `IncludeSection` arguments swapped, plus a typo | realistic: the swap alone differed (an include error stops later includes); fixed in round 66 |
| CR6 `15c21b705919` | statement inserted and arguments swapped | two edits |
| CR6 `d6ad808bb722` | `!poi  nter_float` | token split |
| CR6 `ecedd69cfed8` | `(1+2)` as a parameter name | fuzz argument |
| CR6 `fd72d45e4006` | two string fragments replaced in `LogDebug` | two edits, fuzz arguments |
| CR1000X `13b8482c22e4` | an undeclared fuzz name as the `TimeIntoInterval` interval: the known truncation-warning cascade (round 38) | fuzz name, listed |
| CR1000X `65b2988d5dff` | statement inserted and arguments swapped | two edits |
| CR1000X `dc8a3fe3d9cf` | `SWVX` arguments swapped twice | outside the set, two edits |
| CR1000X `f34788dba408` | `BinaryTxArra  yPointer` | token split |

## Round-64 fresh inputs and round-60 classification

On 2026-10-30, CR6 seed 465 and CR1000X seed 466 each generated 1,000 `--relevant` mutants
without shrinking (`scratch/r64/fuzz-465`, `fuzz-466`, local only). Seed 465: 987 exact, 13
message differences, no verdict or mode difference. Seed 466: 975 exact, 18 message
differences, one false accept and one warning-only difference.

Fixed in round 64 ([checker notes](checker.md#leading-plus-before-an-assignment-round-64) and
the four sections after it):

- false accept `9f6e9f17660e` (CR1000X): an overflowing `WindVector` Reps;
- false accept found while isolating round-60 `9abdc4c03311`: an SDM `Delay` after
  `SlowSequence` (`Delay(Sec,10,1)`);
- false reject found while isolating `53f4ed873761`: `+ X = 1` compiles on all four loggers;
- first errors: a statement starting with `+`/`-` (`53f4ed873761`, a continuation moved below
  its call), user Function calls of any arity and unknown names in them (the realistic forms
  of `72bdefe62a70`, `c714fdb4123a`, `0401490eb4b4`, `977954b9e182`), and unterminated strings
  in user Function calls (`28e7e2792d84`).

Remaining fresh round-64 differences and their scope decision:

| Reproducer | Edit | Decision |
|---|---|---|
| CR6 `4766e9bf8f62` | fuzz argument `(1+2)` added inside `AND(...)` | fuzz argument |
| CR6 `6be023dd77b2` | `As2 Boolean` plus a numeric change | two edits; token mutation |
| CR6 `7c1363851149` | continuation inserted and line duplicated | continuation noise |
| CR6 `83af3f066a32` | dropped argument plus a changed number | two edits |
| CR6 `c02281bcfee9`, `cad5a5f07cbc` | `(1+2)` as a parameter name | fuzz argument |
| CR1000X `2e8850943606` | `SWVX` (outside the relevant set) plus a fuzz name | outside the set |
| CR1000X `50df788549ce` | two argument edits on one `If` line | two edits |
| CR1000X `56aeb71f5975` | `Case_Mut` | token mutation |
| CR1000X `5cdbe6d87d43` | `VoltDiff(True ,...)`: our echo drops the blank before `.` | wording, same cause |
| CR1000X `c040fe436921` | continuation inserted and line deleted | continuation noise |
| CR1000X `effff7c28b0b` | `SerialOpen` with swapped variable arguments: a missing `CommsMode Option 0 not supported on Com310` warning; both compilers choose SequentialMode | warning only |

Round-60 inputs without a decision until now: CR6 `fd7e1a3c87c5` (a comment inserted into a
continuation and a fuzz name), `cde4bf39c14b` (a statement inserted into a continuation and an
overflowing `Const`), `63ad67153e0f` (`_X` after a continuation and swapped arguments),
`5819ebfc0297` (`End  Prog`), `36a0db0a73f4`, `1822097cb726` and CR1000X `dce5f0264db1`
(statements inserted into continuations), CR1000X `042b1b41474a` (a fuzz string as an `Alias`
dimension; the realistic `Alias A(16,2) = X` matches), `04f6730e62aa` (`Sub S  tationInit`),
`9abdc4c03311` (two edits; the realistic `Delay` swap is fixed above) and `b74e610652da` (same
cause, truncated echo): none is a realistic single mistake under the 2026-10-29 scope.

Also not modelled, found while probing: a one-argument built-in with adjacent operands
(`Abs(X Y)`), a quote after a call's closing parenthesis (`F("abc")"`) and `WindVector`
Reps 2147483647 (`Out of memory.`); all are strict gaps in the round-64 fixtures.

## Round-59 realism triage

Human decision 2026-10-29 ([PLAN](../PLAN.md#v10-milestones)): M2 fixes only mistakes a programmer
could realistically make. Each row above was re-applied to its whole seed program (the fuzz edit
kept, the shrinker's deletions undone; `scratch/r59/unshrink.py`) and compiled with both compilers in
round 59. 75 of the 128 rows match there (two emptied conditions, `b4d42d0eac01` and
`9da6844f83f4`, after the condition-echo fix): their difference came from the truncated program the shrinker
left. Realistic single-edit variants of the rest were probed and fixed
([checker notes](checker.md#round-59-realistic-first-errors)). These 53 rows still differ on the whole
program; none is a realistic single edit in the relevant area. Reasons are from the edit text
(`scratch/r59/classify.py`), checked by hand.

| reason | family | reproducer |
|---|---|---|
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Array reference: four subscripts inside ASCII condition | `494a797d22ea` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | DataInterval: signed argument validation before missing BeginProg | `74e259c13199` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | DataInterval: signed argument validation before missing BeginProg | `ec07e9b06cd8` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | FieldClassify: diagnostic attributed to preceding output owner | `d406b0207cdc` (cr1000x) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Long continued call: diagnostic echo truncation | `2c0b869a0a14` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Long continued call: diagnostic echo truncation | `5ff16ab82ca0` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Routine parameter: parenthesized numeric-leading name | `a5ce30c8f8f6` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | String destination parameter: validation before missing/excess arguments | `8830a5aedd4a` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | TimeIntoInterval: interval validation before excess fourth argument | `3a948311392e` (cr6) |
| a fuzz argument (`(1+2)`, `"fuzz"`, `UndeclaredFuzzName123`, `999999999999`) | Variable/return type: unsupported type precedes shape or dimension findings | `dc8ef8bccf71` (cr1000x) |
| a statement inserted into a continued line | Continued routine-call arguments: diagnose the interrupted logical line | `15c0ffa7f461` (cr6) |
| a statement inserted into a continued line | Continued routine-call arguments: diagnose the interrupted logical line | `af36b61a5e8d` (cr6) |
| a statement inserted into a continued line | Control condition: condition-only versus whole-statement echo | `a069d5c59801` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `171af87a935f` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `36ca5746ad3f` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `fa08d71c788b` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `fbcc75c138fb` (cr1000x) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `1112ca94caa1` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `77c6b4106904` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `9b464f8050ed` (cr6) |
| a blank inside a token | Alias: split source identifier resolved before alias-shape rejection | `ab571ca664c5` (cr6) |
| a blank inside a token | DataTable: malformed trigger expression retains expression diagnostic | `4ce782a860c6` (cr6) |
| a blank inside a token | DataTable: split name tokenization | `876ca9e34605` (cr1000x) |
| a blank inside a token | DataTable: split name tokenization | `e2e261651e4b` (cr1000x) |
| a blank inside a token | DataTable: split name tokenization | `840dfab819a2` (cr6) |
| a blank inside a token | Declaration: numeric-leading invalid-name echo | `1d9d25657d36` (cr6) |
| a blank inside a token | Dotted table reference: missing base named specifically | `ae52bdd5ebf7` (cr1000x) |
| a blank inside a token | EndSub: orphan closer before malformed later header | `04754b91e0a1` (cr6) |
| a blank inside a token | Next: split Scan token follows ordinary For/Next handling | `ed1cc693d9fa` (cr1000x) |
| a blank inside a token | Next: split Scan token follows ordinary For/Next handling | `faa80a7207d1` (cr6) |
| a blank inside a token | Sample: malformed output type before unresolved member source | `e84fe90a6711` (cr6) |
| a blank inside a token | SerialOpen: baud callback before malformed numeric tail | `33f655758088` (cr1000x) |
| a blank inside a token | SerialOpen: baud callback before malformed numeric tail | `6484ab6457b8` (cr1000x) |
| a blank inside a token | SerialOpen: baud callback before malformed numeric tail | `3c772bc81211` (cr6) |
| a blank inside a token | SerialOpen: malformed ComPort prefix reaches port validation | `8db9658226d1` (cr1000x) |
| a blank inside a token | StationName: split raw name leaves excess parameter text | `395582d8224e` (cr1000x) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `eb6fad797531` (cr1000x) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `1aa55f1705d5` (cr6) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `3b16c0d475d6` (cr6) |
| outside the relevant set | CType: malformed token consumption reaches Illegal type | `7214e5b36e9d` (cr6) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `4a74895f32cf` (cr1000x) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `6f002bbe6754` (cr1000x) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `6f002bbe6754` (cr6) |
| outside the relevant set | CheckPort: empty call reaches port-range validation | `9c21b8dfe265` (cr6) |
| outside the relevant set | CheckPort: port validation before earlier orphan EndIf | `835ef408b21e` (cr1000x) |
| outside the relevant set | Gzip: malformed quote/argument boundary gets a different syntax label | `3cf5c9209ff1` (cr1000x) |
| outside the relevant set | Gzip: malformed quote/argument boundary gets a different syntax label | `a94a7b1583b7` (cr1000x) |
| outside the relevant set | Menu instructions: enclosing DisplayMenu before arguments | `e77eaa3ef4cc` (cr6) |
| outside the relevant set | MenuPick: ConstTable item's name required as first parameter | `533da3db60a5` (cr6) |
| outside the relevant set | PortsConfig: invalid binary literal diagnostic label | `72bbbb1c7e9b` (cr6) |
| outside the relevant set | PortsConfig: invalid binary literal diagnostic label | `c6fc3897c0ad` (cr6) |
| outside the relevant set | SendData: parameter shortage before unresolved argument | `65de263ef89c` (cr6) |
| outside the relevant set | TCDiff: converted Reps positivity before destination capacity | `3da710901444` (cr1000x) |
